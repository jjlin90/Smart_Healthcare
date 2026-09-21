import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.config import get_settings
from backend.schemas import UserContext

security = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


def create_token(context: UserContext) -> str:
    get_settings().require("secret_key")
    payload = context.model_dump()
    payload["exp"] = datetime.now(UTC) + timedelta(hours=2)
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> UserContext:
    if not credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        # Token verification must fail closed when deployment configuration is
        # incomplete.  HS256 accepts an empty byte string as a key.
        get_settings().require("secret_key")
        payload = jwt.decode(
            credentials.credentials,
            get_settings().secret_key,
            algorithms=[ALGORITHM],
        )
        return UserContext.model_validate(payload)
    except (JWTError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=401, detail="登录状态已失效") from exc


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        _, rounds, salt_b64, digest_b64 = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(rounds))
        return hmac.compare_digest(actual, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False
