import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.database import SessionLocal, init_db
from backend.models import PatientProfile


async def upsert(args: argparse.Namespace) -> None:
    await init_db()
    async with SessionLocal() as db:
        profile = await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == args.patient_id))
        if profile is None:
            profile = PatientProfile(patient_id=args.patient_id, username=args.patient_id)
        profile.name = args.name
        profile.age = args.age
        profile.gender = args.gender
        profile.allergy_history = json.dumps(args.allergy, ensure_ascii=False)
        profile.past_medical_history = json.dumps(args.condition, ensure_ascii=False)
        profile.current_medications = json.dumps(args.medication, ensure_ascii=False)
        db.add(profile)
        await db.commit()
    print(f"患者档案 {args.patient_id} 已写入。生产环境应由 HIS/EMR 同步，不应人工维护双份数据。")


def main() -> None:
    parser = argparse.ArgumentParser(description="本地联调时从明确输入写入患者档案（不生成模拟医学数据）")
    parser.add_argument("--patient-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--age", required=True, type=int, choices=range(0, 151), metavar="0-150")
    parser.add_argument("--gender", required=True)
    parser.add_argument("--allergy", action="append", default=[])
    parser.add_argument("--condition", action="append", default=[])
    parser.add_argument("--medication", action="append", default=[])
    args = parser.parse_args()
    asyncio.run(upsert(args))


if __name__ == "__main__":
    main()
