import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from backend import metrics_bridge
from backend.a2a_server import MedicalA2AServer, create_app


@pytest.mark.asyncio
async def test_bridge_forwards_only_fixed_metrics_and_reports_failure(monkeypatch):
    client_type = httpx.AsyncClient
    upstream = AsyncMock()
    upstream.__aenter__.return_value = upstream
    upstream.get.return_value = httpx.Response(200, content=b"sample 1\n", request=httpx.Request("GET", "http://local/metrics/"))
    monkeypatch.setattr(metrics_bridge.httpx, "AsyncClient", lambda **kwargs: upstream)
    async with client_type(transport=httpx.ASGITransport(app=metrics_bridge.app), base_url="http://bridge") as client:
        for service, port in metrics_bridge.TARGETS.items():
            response = await client.get(f"/metrics/{service}")
            assert response.status_code == 200 and response.content == b"sample 1\n"
            upstream.get.assert_awaited_with(f"http://127.0.0.1:{port}/metrics/")
        before = upstream.get.await_count
        for path in ("/api/chat", "/metrics/unknown"):
            assert (await client.get(path)).status_code == 404
        assert (await client.post("/metrics/api")).status_code == 405
        assert upstream.get.await_count == before
        upstream.get.side_effect = httpx.ConnectError("unavailable")
        assert (await client.get("/metrics/api")).status_code == 503


@pytest.mark.asyncio
async def test_specialist_metrics_are_accessible_without_opening_a2a():
    from backend.observability import MCP_TOOL_CALLS
    from prometheus_client.parser import text_string_to_metric_families
    labels = {"agent": "SymptomAgent", "tool": "load_patient_history", "status": "completed"}
    counter = MCP_TOOL_CALLS.labels(**labels)
    before = counter._value.get()
    counter.inc()
    app = create_app(MedicalA2AServer("SymptomAgent", "test", "http://localhost:8011"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        response = await client.get("/metrics/")
        assert response.status_code == 200
        samples = [sample for family in text_string_to_metric_families(response.text) for sample in family.samples
                   if sample.name == "medagent_mcp_tool_calls_total" and sample.labels == labels]
        assert len(samples) == 1 and samples[0].value == before + 1
        assert (await client.get("/.well-known/agent.json")).status_code == 401


@pytest.mark.asyncio
async def test_unhandled_http_exception_is_counted_as_500():
    from fastapi import FastAPI
    from backend.main import prometheus_middleware
    from backend.observability import HTTP_REQUESTS, HTTP_LATENCY
    app = FastAPI()
    app.middleware("http")(prometheus_middleware)

    @app.get("/test-unhandled/{item}")
    async def fail(item: str):
        raise RuntimeError("test failure")

    counter = HTTP_REQUESTS.labels("GET", "/test-unhandled/{item}", "500")
    before = counter._value.get()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        assert (await client.get("/test-unhandled/example")).status_code == 500
    assert counter._value.get() == before + 1
    assert any(sample.name.endswith("_count") and sample.value >= 1
               for metric in HTTP_LATENCY.collect() for sample in metric.samples
               if sample.labels.get("path") == "/test-unhandled/{item}")


@pytest.mark.asyncio
async def test_cancel_before_response_is_not_counted_as_500():
    from fastapi import FastAPI
    from backend.main import prometheus_middleware
    from backend.observability import HTTP_REQUESTS, HTTP_LATENCY
    app = FastAPI()
    app.middleware("http")(prometheus_middleware)
    entered = asyncio.Event()

    @app.get("/cancel-before-headers")
    async def slow():
        entered.set()
        await asyncio.Event().wait()

    counter = HTTP_REQUESTS.labels("GET", "/cancel-before-headers", "500")
    before = counter._value.get()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        task = asyncio.create_task(client.get("/cancel-before-headers"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert counter._value.get() == before


@pytest.mark.asyncio
async def test_bridge_total_deadline_returns_503(monkeypatch):
    client_type = httpx.AsyncClient
    upstream = AsyncMock()
    upstream.__aenter__.return_value = upstream

    async def wait_forever(*args, **kwargs):
        await asyncio.Event().wait()

    upstream.get.side_effect = wait_forever
    monkeypatch.setattr(metrics_bridge, "UPSTREAM_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(metrics_bridge.httpx, "AsyncClient", lambda **kwargs: upstream)
    async with client_type(transport=httpx.ASGITransport(app=metrics_bridge.app), base_url="http://bridge") as client:
        response = await asyncio.wait_for(client.get("/metrics/api"), timeout=2)
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_cancel_stream_after_headers_keeps_single_200():
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from backend.main import prometheus_middleware
    from backend.observability import HTTP_REQUESTS
    app = FastAPI()
    app.middleware("http")(prometheus_middleware)
    headers_sent = asyncio.Event()
    body_closed = asyncio.Event()

    @app.get("/cancel-stream")
    async def stream():
        async def body():
            try:
                yield b"data: initial\n\n"
                await asyncio.Event().wait()
            finally:
                body_closed.set()
        return StreamingResponse(body(), media_type="text/event-stream")

    success = HTTP_REQUESTS.labels("GET", "/cancel-stream", "200")
    failure = HTTP_REQUESTS.labels("GET", "/cancel-stream", "500")
    before = (success._value.get(), failure._value.get())
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            headers_sent.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "method": "GET", "path": "/cancel-stream", "raw_path": b"/cancel-stream",
             "root_path": "", "query_string": b"", "headers": [], "scheme": "http",
             "http_version": "1.1", "server": ("test", 80), "client": ("test", 1)}
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(headers_sent.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert body_closed.is_set()
    assert success._value.get() == before[0] + 1
    assert failure._value.get() == before[1]
