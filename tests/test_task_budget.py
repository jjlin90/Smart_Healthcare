import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.task_budget import remaining_seconds
from backend.service_auth import delegated_context, issue_delegation, verify_delegation


def test_downstream_timeout_uses_remaining_budget_not_new_full_window(monkeypatch):
    monkeypatch.setattr("backend.task_budget.time.time", lambda: 100.)
    with delegated_context({"deadline_at": 104.}):
        assert remaining_seconds(30.) == 4.
        assert remaining_seconds(2.) == 2.


@pytest.mark.parametrize("deadline", [99., 100., "later", True, float('inf'), float('nan')])
def test_expired_or_malformed_deadlines_stop_dispatch(monkeypatch, deadline):
    monkeypatch.setattr("backend.task_budget.time.time", lambda: 100.)
    with pytest.raises((TimeoutError, ValueError)):
        remaining_seconds(30., {"deadline_at": deadline})


def test_deadline_is_preserved_in_child_signed_delegation():
    deadline = time.time() + 5
    with delegated_context({"staff_id":"s", "patient_id":"p", "request_id":"budget-test", "deadline_at":deadline}):
        claims = verify_delegation(issue_delegation("medagent-mcp", "DrugAgent", ["query_drug_info"]), "medagent-mcp")
    assert claims["deadline_at"] == deadline
    assert claims["exp"] <= deadline + 1


async def test_expired_specialist_task_never_loads_patient_or_runs_tools(monkeypatch):
    from backend.a2a_server import MedicalA2AServer
    server = MedicalA2AServer("DrugAgent", "test", "http://localhost")
    runtime = AsyncMock()
    monkeypatch.setattr(server.runtime, "run", runtime)
    with pytest.raises(TimeoutError):
        await server.execute({"task":"查询阿司匹林", "patient_id":"p"}, {"deadline_at":time.time()-1})
    runtime.assert_not_awaited()


async def test_mcp_tool_body_cannot_exceed_parent_deadline(monkeypatch):
    import asyncio
    from backend import mcp_security
    from fastmcp.tools.base import ToolResult
    from backend.schemas import UserContext
    context = SimpleNamespace(message=SimpleNamespace(name="query_drug_info",arguments={"drug_name":"阿司匹林"}))
    claims = {"staff_id":"s", "patient_id":"p", "tools":["query_drug_info"], "deadline_at":time.time()+.05}
    monkeypatch.setattr(mcp_security, "get_access_token", lambda: SimpleNamespace(token="test"))
    monkeypatch.setattr(mcp_security, "verify_delegation", lambda *_: claims)
    monkeypatch.setattr(mcp_security, "active_staff", AsyncMock(return_value=UserContext(user_id="s", username="doctor", role="doctor")))
    monkeypatch.setattr(mcp_security, "require_patient", AsyncMock(return_value=object()))
    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
    monkeypatch.setattr(mcp_security, "SessionLocal", FakeSession)
    completed = []
    async def slow_tool(_):
        await asyncio.sleep(1)
        completed.append(True)
        return ToolResult(content="ok")
    with pytest.raises(TimeoutError):
        await mcp_security.PatientAuthorization().on_call_tool(context, slow_tool)
    assert not completed
