import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings


def missing_configuration() -> list[str]:
    settings = get_settings()
    required = [
        "secret_key", "siliconflow_api_key", "local_intent_base_url",
        "hospital_app_key", "hospital_app_secret", "drug_api_base_url",
        "guideline_api_base_url", "lis_api_base_url", "emr_api_base_url",
        "his_api_base_url", "sms_verify_api_url", "sms_app_key", "sms_app_secret",
    ]
    missing = [name.upper() for name in required if not getattr(settings, name)]
    if "YOUR_PASSWORD" in settings.database_url:
        missing.append("DATABASE_URL（请替换 YOUR_PASSWORD）")
    return missing


if __name__ == "__main__":
    missing = missing_configuration()
    if missing:
        print("以下真实服务配置尚未填写：")
        for item in missing:
            print(f"- {item}")
        raise SystemExit(1)
    print("配置检查通过。")
