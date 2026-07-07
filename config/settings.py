from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()  # populate os.environ for LangSmith and other direct env readers


class Settings(BaseSettings):
    oracle_host: str
    oracle_port: int = 1521
    oracle_password: str
    oracle_user: str = "ADMIN"
    oracle_service: str = "FREEPDB1"

    # LLM
    llm_api_key: str
    llm_model: str = "qwen/qwen-32b"
    llm_base_url: str = "https://api.groq.com/openai/v1"

    # Embedding
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    model_path: str = "path_to_model"

    # Wiki storage
    wikis_dir: str = "./wikis"

    langsmith_api_key: str
    langsmith_project: str = "llm-wiki"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_tracing: str = "true"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
