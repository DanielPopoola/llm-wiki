from typing import Any

from langchain_core.runnables import RunnableConfig


def get_llm(config: RunnableConfig) -> Any:
    llm = config.get("configurable", {}).get("llm")
    if llm is None:
        raise ValueError("llm not found in config['configurable'].")
    return llm
