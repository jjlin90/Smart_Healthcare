from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["development", "production", "test"] = "development"
    app_name: str = "MedAgent AI"
    secret_key: str = ""
    internal_service_secret: str = ""
    cloud_llm_allowed: bool = False
    login_max_attempts: int = Field(default=10, ge=1, le=100)
    login_window_seconds: int = Field(default=300, ge=30, le=3600)
    request_timeout_seconds: int = Field(default=120, ge=10, le=600)
    model_timeout_seconds: float = Field(default=30, gt=0, le=120)
    model_max_tokens: int = Field(default=2048, ge=128, le=8192)
    database_url: str = "sqlite+aiosqlite:///./medagent.db"
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "deepseek-ai/DeepSeek-V4-Flash"
    intent_bert_model_path: str = ""
    intent_bert_threshold: float = Field(default=0.5, ge=0, le=1)
    intent_bert_thresholds: dict[str, float] = Field(default_factory=dict)
    intent_bert_uncertainty_margin: float = Field(default=0.08, ge=0, le=0.5)
    intent_vector_thresholds: dict[str, float] = Field(default_factory=dict)
    intent_vector_model_path: str = ""
    intent_vector_threshold: float = Field(default=0.78, ge=0, le=1)
    # Deprecated: retained only for old environment files; fusion does not use top-score margin.
    intent_vector_margin: float = Field(default=0.04, ge=0, le=2)
    intent_prototypes_path: str = "evaluation/intent_prototypes.jsonl"
    mcp_server_url: str = "http://127.0.0.1:8002/mcp"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8002, ge=1, le=65535)
    symptom_agent_url: str = "http://127.0.0.1:8011"
    drug_agent_url: str = "http://127.0.0.1:8012"
    guide_agent_url: str = "http://127.0.0.1:8013"
    drug_api_base_url: str = ""
    guideline_api_base_url: str = ""
    lis_api_base_url: str = ""
    his_api_base_url: str = ""
    hospital_app_key: str = ""
    hospital_app_secret: str = ""
    session_ttl_seconds: int = Field(default=1800, ge=1)
    redis_url: str = ""
    checkpoint_ttl_seconds: int = Field(default=86400, ge=1)
    external_request_timeout: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    agent_max_iterations: int = Field(default=6, ge=1)
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "medagent-ai"

    @field_validator("intent_bert_thresholds", "intent_vector_thresholds")
    @classmethod
    def validate_intent_thresholds(cls, values: dict[str, float]) -> dict[str, float]:
        import math
        for label, value in values.items():
            if not label or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("每个意图阈值必须是 0 到 1 的有限数值")
        return values

    def production_errors(self) -> list[str]:
        """Return configuration names only; never include secret values."""
        errors: list[str] = []
        for name in ("secret_key", "internal_service_secret"):
            value = getattr(self, name)
            if len(value) < 32 or value.upper().startswith(("REPLACE", "YOUR_", "CHANGE")):
                errors.append(f"{name.upper()}：至少 32 字符的独立随机密钥")
        if self.secret_key == self.internal_service_secret:
            errors.append("INTERNAL_SERVICE_SECRET：不得与员工 JWT 密钥相同")
        if not self.database_url.startswith("mysql+aiomysql://"):
            errors.append("DATABASE_URL：生产环境需要 MySQL")
        if not self.redis_url:
            errors.append("REDIS_URL：生产环境需要共享状态存储")
        if not self.cloud_llm_allowed:
            errors.append("CLOUD_LLM_ALLOWED：需经医院批准后显式启用云端模型")
        for name in ("siliconflow_api_key", "hospital_app_key", "hospital_app_secret"):
            value = getattr(self, name)
            if not value or value.upper().startswith(("REPLACE", "YOUR_", "CHANGE")):
                errors.append(f"{name.upper()}：配置缺失或为占位值")
        for name in ("siliconflow_base_url", "drug_api_base_url", "guideline_api_base_url", "lis_api_base_url", "his_api_base_url"):
            if not getattr(self, name).startswith("https://"):
                errors.append(f"{name.upper()}：生产外部接口必须使用 HTTPS")
        return errors

    def require(self, *names: str) -> None:
        missing = [name for name in names if not getattr(self, name)]
        if missing:
            raise RuntimeError(f"缺少必要配置：{', '.join(missing)}")

    def require_cloud_llm(self) -> None:
        if self.app_env == "production" and not self.cloud_llm_allowed:
            raise RuntimeError("CLOUD_LLM_ALLOWED：生产云端模型调用需经医院批准后显式启用")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
