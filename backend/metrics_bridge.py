"""Proxy fixed loopback metrics endpoints; listener access is controlled by the host firewall."""

import asyncio
import httpx
from fastapi import FastAPI, HTTPException, Response

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
TARGETS = {"api": 8000, "symptom": 8011, "drug": 8012, "guide": 8013}
UPSTREAM_TIMEOUT_SECONDS = 5


@app.get("/metrics/{service}")
async def metrics(service: str):
    if service not in TARGETS:
        raise HTTPException(404)
    try:
        async with asyncio.timeout(UPSTREAM_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS, trust_env=False) as client:
                result = await client.get(f"http://127.0.0.1:{TARGETS[service]}/metrics/")
                result.raise_for_status()
    except (httpx.HTTPError, TimeoutError) as exc:
        raise HTTPException(503, "Metrics target unavailable") from exc
    return Response(result.content, headers={"Content-Type": result.headers.get("content-type", "text/plain")})
