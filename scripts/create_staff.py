import argparse
import asyncio
import sys
from getpass import getpass
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.auth import hash_password
from backend.database import SessionLocal, init_db
from backend.models import PatientAccess, PatientProfile, User


CLINICAL_ROLES = {"doctor", "pharmacist"}
STAFF_ROLES = ["doctor", "pharmacist", "medical_admin", "system_admin"]


async def create(employee_id: str, username: str, role: str, patient_ids: list[str]) -> None:
    patient_ids = list(dict.fromkeys(patient_ids))
    if role in CLINICAL_ROLES and not patient_ids:
        raise SystemExit("医生和药师至少需要一个已授权患者；可重复使用 --patient-id")
    password = getpass("院内员工账号密码（至少 8 位）：")
    if len(password) < 8:
        raise SystemExit("密码至少需要 8 位")
    await init_db()
    async with SessionLocal() as db:
        exists = await db.scalar(select(User).where(User.employee_id == employee_id))
        if exists:
            raise SystemExit("该工号已存在")
        missing_patients = [
            patient_id
            for patient_id in patient_ids
            if not await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == patient_id))
        ]
        if missing_patients:
            raise SystemExit("患者档案不存在：" + ", ".join(missing_patients))
        user_id = f"user_{uuid4().hex}"
        db.add(User(
            user_id=user_id,
            username=username,
            employee_id=employee_id,
            password_hash=hash_password(password),
            role=role,
            patient_id=None,
        ))
        for patient_id in patient_ids:
            db.add(PatientAccess(user_id=user_id, patient_id=patient_id, granted_by="create_staff_script"))
        await db.commit()
    print(f"院内员工账号 {employee_id}（{role}）已创建，授权患者 {len(patient_ids)} 个。")


def main() -> None:
    parser = argparse.ArgumentParser(description="创建院内员工账号并授予患者访问范围")
    parser.add_argument("--employee-id", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=STAFF_ROLES, default="doctor")
    parser.add_argument("--patient-id", action="append", default=[], help="可重复传入；管理员角色可省略")
    args = parser.parse_args()
    asyncio.run(create(args.employee_id, args.username, args.role, args.patient_id))


if __name__ == "__main__":
    main()
