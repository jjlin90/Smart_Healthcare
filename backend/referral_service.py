"""Doctor-confirmed referral draft action; no model or specialist selection."""

import asyncio
import hashlib
import json
import time
from uuid import uuid4

from fastmcp import Client
from fastmcp.exceptions import ToolError

from backend.config import get_settings
from backend.evidence import tool_data
from backend.memory import conversation_memory
from backend.schemas import ReferralDraftRequest, UserContext
from backend.access import require_write_role
from backend.service_auth import delegated_context, issue_delegation


REFERRAL_SERVICE = "ReferralDraftService"


async def create_referral_draft(request: ReferralDraftRequest, user: UserContext) -> dict:
    """Keep MCP authorization, task fencing and its durable write reservation."""
    require_write_role(user, "generate_referral")
    settings = get_settings()
    arguments = request.model_dump(include={"patient_id", "department", "reason"})
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # A separate attempt scope never changes or cancels a clinical chat task.
    key = f"referral:{user.user_id}:{request.request_id}:{uuid4().hex}"
    context = {"staff_id": user.user_id, "patient_id": request.patient_id,
               "request_id": request.request_id, "agent": REFERRAL_SERVICE,
               "approved_actions": ["generate_referral"], "confirmed_arguments_sha256": digest,
               "task_id": uuid4().hex, "conversation_key": key, "plan_version": 1,
               "step_id": uuid4().hex, "deadline_at": time.time() + settings.request_timeout_seconds}
    state = {"phase": "started", "task_context": {"task_id": context["task_id"], "plan_version": 1}}
    await conversation_memory.save_checkpoint(request.patient_id, key, state)
    try:
        with delegated_context(context):
            token = issue_delegation("medagent-mcp", REFERRAL_SERVICE, ["generate_referral"])
            async with asyncio.timeout(settings.request_timeout_seconds):
                async with Client(settings.mcp_server_url, auth=token, timeout=settings.request_timeout_seconds) as client:
                    try:
                        result = await client.call_tool("generate_referral", arguments)
                    except ToolError as exc:
                        # FastMCP raises on an error result by default. Treat it
                        # as unconfirmed without exposing the upstream payload.
                        raise RuntimeError("转诊草稿调用未确认完成，请核对操作状态与医院结果") from exc
        data = tool_data(getattr(result, "structured_content", None) or result.content)
        if (not isinstance(data, dict) or not data.get("referral_id")
                or data.get("status") not in {"pending_confirmation", "draft", "待医生确认"}
                or data.get("department") != request.department):
            raise RuntimeError("转诊草稿返回内容不符合确认参数，请核对医院结果")
        return {**data, "request_id": request.request_id}
    finally:
        # Invalidate late calls. Failure here must not cause an automatic retry;
        # the MCP reservation still determines completed/pending/unknown state.
        await conversation_memory.save_checkpoint(request.patient_id, key, {**state, "phase": "closed"})
