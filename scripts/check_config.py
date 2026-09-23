import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents import INTENTS
from backend.config import get_settings


def resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(__file__).resolve().parents[1] / path


CORE_REQUIRED = [
    "secret_key", "siliconflow_api_key", "intent_bert_model_path",
    "intent_vector_model_path", "intent_prototypes_path",
]
INTEGRATION_REQUIRED = [
    "hospital_app_key", "hospital_app_secret", "drug_api_base_url",
    "guideline_api_base_url", "lis_api_base_url", "emr_api_base_url",
    "his_api_base_url",
]


def missing_configuration(*, strict: bool = False) -> list[str]:
    settings = get_settings()
    required = CORE_REQUIRED + (INTEGRATION_REQUIRED if strict else [])
    missing = [name.upper() for name in required if not getattr(settings, name)]
    if "YOUR_PASSWORD" in settings.database_url:
        missing.append("DATABASE_URL（请替换 YOUR_PASSWORD）")
    for name in ("intent_bert_model_path", "intent_vector_model_path"):
        value = getattr(settings, name)
        if value and not resolve_project_path(value).is_dir():
            missing.append(f"{name.upper()}（目录不存在）")
    bert_value = settings.intent_bert_model_path
    if bert_value and resolve_project_path(bert_value).is_dir():
        config_path = resolve_project_path(bert_value) / "config.json"
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            labels = {str(label) for label in payload.get("id2label", {}).values()}
            if labels != set(INTENTS):
                missing.append("INTENT_BERT_MODEL_PATH（模型标签不是当前纯 ToB 十类意图）")
        except (OSError, ValueError, TypeError):
            missing.append("INTENT_BERT_MODEL_PATH（无法读取模型标签配置）")
    if settings.intent_prototypes_path and not resolve_project_path(settings.intent_prototypes_path).is_file():
        missing.append("INTENT_PROTOTYPES_PATH（文件不存在）")
    return missing


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查 MedAgent 配置")
    parser.add_argument("--strict", action="store_true", help="同时要求全部真实医院接口配置")
    args = parser.parse_args()
    missing = missing_configuration(strict=args.strict)
    if missing:
        print("以下配置尚未填写或无效：")
        for item in missing:
            print(f"- {item}")
        raise SystemExit(1)
    print("核心配置检查通过。")
    if not args.strict:
        integration_missing = [name.upper() for name in INTEGRATION_REQUIRED if not getattr(get_settings(), name)]
        if integration_missing:
            print("提示：医院系统集成尚未完整配置，对应工具会明确失败；生产验收请运行 --strict。")
