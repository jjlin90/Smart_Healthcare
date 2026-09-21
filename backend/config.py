from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MedAgent AI"
    secret_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./medagent.db"
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "deepseek-ai/DeepSeek-V4-Flash"
    intent_bert_model_path: str = ""
    intent_bert_threshold: float = 0.82
    intent_vector_model_path: str = ""
    intent_vector_threshold: float = 0.70
    intent_vector_margin: float = 0.04
    intent_prototypes_path: str = "evaluation/intent_prototypes.jsonl"
    mcp_server_url: str = "http://127.0.0.1:8002/mcp"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8002
    symptom_agent_url: str = "http://127.0.0.1:8011"
    drug_agent_url: str = "http://127.0.0.1:8012"
    guide_agent_url: str = "http://127.0.0.1:8013"
    drug_api_base_url: str = ""
    guideline_api_base_url: str = ""
    lis_api_base_url: str = ""
    emr_api_base_url: str = ""
    his_api_base_url: str = ""
    hospital_app_key: str = ""
    hospital_app_secret: str = ""
    session_ttl_seconds: int = 1800
    redis_url: str = ""
    checkpoint_ttl_seconds: int = 86400
    external_request_timeout: float = 10.0
    agent_max_iterations: int = 6
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "medagent-ai"

    def require(self, *names: str) -> None:
        missing = [name for name in names if not getattr(self, name)]
        if missing:
            raise RuntimeError(f"缺少必要配置：{', '.join(missing)}")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
