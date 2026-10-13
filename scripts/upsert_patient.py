import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.database import SessionLocal, init_db
from backend.models import PatientProfile
from backend.schemas import ProfileFields, WriteToolArguments


async def upsert(args: argparse.Namespace) -> None:
    # Validate before opening the database, using the same clinical limits as
    # the REST and MCP write paths.
    patient_id = WriteToolArguments(patient_id=args.patient_id).patient_id
    data = ProfileFields(
        name=args.name, age=args.age, gender=args.gender,
        allergies=args.allergy or [], conditions=args.condition or [],
        medications=args.medication or [],
    )
    await init_db()
    async with SessionLocal() as db:
        profile = await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == patient_id))
        is_new = profile is None
        if profile is None:
            profile = PatientProfile(patient_id=patient_id, username=patient_id)
        profile.name = data.name
        profile.age = data.age
        profile.gender = data.gender
        for supplied, field, value in (
            (args.allergy, "allergy_history", data.allergies),
            (args.condition, "past_medical_history", data.conditions),
            (args.medication, "current_medications", data.medications),
        ):
            if is_new or supplied is not None:
                setattr(profile, field, json.dumps(value, ensure_ascii=False))
        db.add(profile)
        await db.commit()
    print(f"患者档案 {args.patient_id} 已写入。生产环境应由 HIS/EMR 同步，不应人工维护双份数据。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地联调时从明确输入写入患者档案（不生成模拟医学数据）")
    parser.add_argument("--patient-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--age", required=True, type=int, choices=range(0, 151), metavar="0-150")
    parser.add_argument("--gender", required=True)
    for option, clear_option in (("allergy", "allergies"), ("condition", "conditions"), ("medication", "medications")):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(f"--{option}", action="append", default=None, help="可重复传入；更新时不传则保留原值")
        group.add_argument(f"--clear-{clear_option}", dest=option, action="store_const", const=[], help="显式清空该项病史")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(upsert(args))


if __name__ == "__main__":
    main()
