from typing import Any

from langchain_core.runnables import RunnableConfig


def get_db(config: RunnableConfig) -> Any:
    return config.get("configurable", {}).get("db")


def get_llm(config: RunnableConfig) -> Any:
    llm = config.get("configurable", {}).get("llm")
    if llm is None:
        raise ValueError("llm not found in config['configurable'].")
    return llm
