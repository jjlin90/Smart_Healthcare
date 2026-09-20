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
from backend.models import User


async def create(employee_id: str, username: str, patient_id: str) -> None:
    password = getpass("医生账号密码（至少 8 位）：")
    if len(password) < 8:
        raise SystemExit("密码至少需要 8 位")
    await init_db()
    async with SessionLocal() as db:
        exists = await db.scalar(select(User).where(User.employee_id == employee_id))
        if exists:
            raise SystemExit("该工号已存在")
        db.add(User(user_id=f"user_{uuid4().hex}", username=username, employee_id=employee_id, password_hash=hash_password(password), role="doctor", patient_id=patient_id))
        await db.commit()
    print(f"医生账号 {employee_id} 已创建。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--employee-id", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--patient-id", required=True, help="当前演示绑定的患者 ID；生产环境应由 HIS 授权上下文动态选择")
    args = parser.parse_args()
    asyncio.run(create(args.employee_id, args.username, args.patient_id))
