"""Short-lived delegation: staff, patient, specialist and approved tool scopes."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt

from backend.config import get_settings

request_context: ContextVar[dict[str, Any] | None] = ContextVar("clinical_request", default=None)


def validate_service_secret() -> str:
    settings = get_settings()
    settings.require("internal_service_secret")
    value = settings.internal_service_secret
    if settings.app_env == "production" and (
        len(value) < 32 or value.upper().startswith(("REPLACE", "YOUR_", "CHANGE"))
        or value == settings.secret_key
    ):
        raise RuntimeError("生产内部服务密钥无效，必须使用独立随机密钥")
    return value


@contextmanager
def delegated_context(context: dict[str, Any]):
    token = request_context.set(context)
    try:
        yield
    finally:
        request_context.reset(token)


def issue_delegation(audience: str, agent: str, tools: list[str]) -> str:
    settings = get_settings()
    signing_key = validate_service_secret()
    context = request_context.get()
    if not context or not context.get("staff_id") or not context.get("patient_id"):
        raise RuntimeError("缺少员工患者授权上下文")
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.request_timeout_seconds + 30)
    # Child MCP delegations must not extend a verified A2A parent's lifetime.
    if context.get("exp") is not None:
        parent_expiry = datetime.fromtimestamp(float(context["exp"]), UTC)
        expires_at = min(expires_at, parent_expiry)
        if expires_at <= now:
            raise ValueError("委托已过期，不能继续派发工具")
    claims = {
        **context, "sub": context["staff_id"], "iss": "medagent-internal",
        "aud": audience, "agent": agent, "tools": tools, "iat": now,
        "exp": expires_at,
        "jti": uuid4().hex,
    }
    return jwt.encode(claims, signing_key, algorithm="HS256")


def verify_delegation(token: str, audience: str) -> dict[str, Any]:
    signing_key = validate_service_secret()
    claims = jwt.decode(
        token, signing_key, algorithms=["HS256"],
        audience=audience, issuer="medagent-internal",
        options={"require_exp": True, "require_iat": True, "require_sub": True, "require_aud": True},
    )
    if not all(isinstance(claims.get(key), str) and claims[key] for key in ("staff_id", "patient_id", "agent", "request_id")):
        raise JWTError("Invalid delegation scope")
    if claims["sub"] != claims["staff_id"] or not isinstance(claims.get("tools"), list):
        raise JWTError("Invalid delegation subject")
    return claims
