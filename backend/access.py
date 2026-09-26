"""Live staff and patient authorization shared by the API and tool services."""

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import PatientAccess, PatientProfile, User
from backend.schemas import UserContext

STAFF_ROLES = {"doctor", "pharmacist", "medical_admin", "system_admin"}
WRITE_ROLES = {"doctor", "pharmacist"}
WRITE_TOOLS = {"save_patient_history", "save_medical_record", "generate_referral"}


async def active_staff(db, user_id: str) -> UserContext:
    user = await db.scalar(select(User).where(User.user_id == user_id))
    if not user or not user.active or user.role not in STAFF_ROLES:
        raise HTTPException(status_code=401, detail="账号已停用或登录状态已失效")
    return UserContext(username=user.username, user_id=user.user_id, role=user.role)


async def patient_ids(db, user: UserContext) -> set[str] | None:
    if user.role == "medical_admin":
        return None
    return set((await db.scalars(select(PatientAccess.patient_id).where(PatientAccess.user_id == user.user_id))).all())


async def require_patient(db, user: UserContext, patient_id: str):
    allowed = await patient_ids(db, user)
    if allowed is not None and patient_id not in allowed:
        raise HTTPException(status_code=403, detail="当前员工无权访问该患者")
    profile = await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == patient_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="未找到患者档案")
    return profile


def require_write_role(user: UserContext, action: str) -> None:
    allowed = {"doctor"} if action == "generate_referral" else WRITE_ROLES
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="当前员工角色无权执行该写操作")
