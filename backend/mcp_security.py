"""Authenticate MCP transports and authorize every tool against live records."""

import json
import hashlib

from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from fastapi import HTTPException
from jose import JWTError
from sqlalchemy.exc import IntegrityError

from backend.access import active_staff, require_patient, require_write_role, WRITE_TOOLS
from backend.database import SessionLocal
from backend.service_auth import verify_delegation
from backend.models import ToolExecution
from backend.schemas import validate_write_arguments


class DelegationVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = verify_delegation(token, "medagent-mcp")
            return AccessToken(token=token, client_id=claims["agent"], scopes=claims["tools"], expires_at=claims["exp"], subject=claims["staff_id"], claims=claims)
        except (JWTError, ValueError, RuntimeError):
            return None


class PatientAuthorization(Middleware):
    async def on_call_tool(self, context, call_next):
        token = get_access_token()
        try:
            if token is None:
                raise ValueError("missing token")
            claims = verify_delegation(token.token, "medagent-mcp")
            name = context.message.name
            if name not in claims["tools"]:
                raise ValueError("tool outside delegation")
            arguments = context.message.arguments or {}
            if "patient_id" in arguments and arguments["patient_id"] != claims["patient_id"]:
                raise ValueError("patient outside delegation")
            async with SessionLocal() as db:
                user = await active_staff(db, claims["staff_id"])
                profile = await require_patient(db, user, claims["patient_id"])
                if name in WRITE_TOOLS:
                    if name not in claims.get("approved_actions", []):
                        raise ValueError("write not approved")
                    require_write_role(user, name)
                if name == "check_contraindications":
                    arguments["patient_condition"] = {
                        "allergies": json.loads(profile.allergy_history or "[]"),
                        "conditions": json.loads(profile.past_medical_history or "[]"),
                        "medications": json.loads(profile.current_medications or "[]"),
                    }
                    context.message.arguments = arguments
        except (HTTPException, JWTError, ValueError, RuntimeError) as exc:
            raise ToolError("工具授权失败：请核对员工状态、患者范围和本次写操作确认") from exc
        if name not in WRITE_TOOLS:
            return await call_next(context)
        try:
            validate_write_arguments(name, arguments)
        except ValueError as exc:
            raise ToolError("写操作参数无效，尚未执行；修正参数后可重试") from exc
        operation_id = hashlib.sha256(json.dumps([claims["staff_id"], claims["request_id"], name]).encode()).hexdigest()
        argument_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        async with SessionLocal() as db:
            db.add(ToolExecution(operation_id=operation_id, argument_hash=argument_hash, staff_id=claims["staff_id"], patient_id=claims["patient_id"], tool_name=name, status="pending"))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                previous = await db.get(ToolExecution, operation_id)
                if previous is None or previous.argument_hash != argument_hash or previous.patient_id != claims["patient_id"]:
                    raise ToolError("同一写操作标识对应的参数冲突")
                if previous.status == "completed" and previous.result_json:
                    return ToolResult.model_validate_json(previous.result_json)
                raise ToolError("该写操作执行中或结果待核对，请勿重复提交")
        try:
            result = await call_next(context)
            async with SessionLocal() as db:
                row = await db.get(ToolExecution, operation_id)
                row.status = "unknown" if result.is_error else "completed"
                row.result_json = result.model_dump_json() if not result.is_error else None
                await db.commit()
            return result
        except BaseException:
            # Retain the reservation even when acknowledgement is lost. An
            # operator must reconcile with HIS before another write is allowed.
            async with SessionLocal() as db:
                row = await db.get(ToolExecution, operation_id)
                row.status = "unknown"
                await db.commit()
            raise
