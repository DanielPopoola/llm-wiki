from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    oracle_host: str
    oracle_port: int = 1521
    oracle_pwd: str
    oracle_user: str = "ADMIN"
    oracle_service: str = "FREEPDB1"

    # LLM
    llm_api_key: str
    llm_model: str = "qwen/qwen-32b"
    llm_base_url: str = "https://api.groq.com/openai/v1"

    # Embedding
    embedding_model: str = "nomic-ai/nomic-embed-text-v2-moe"

    # Wiki storage
    wikis_dir: str = "./wikis"

    langsmith_api_key: str
    langsmith_project: str = "llm-wiki"
    langsmith_tracing: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
