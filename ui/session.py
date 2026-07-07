from functools import lru_cache
from pathlib import Path
from typing import Callable

from langchain_openai import ChatOpenAI

from config import settings
from infrastructure.db import DatabaseConnection
from workflows.ingestion import IngestionState, run_ingestion
from workflows.lint import LintState, run_lint
from workflows.query import QueryState, run_query


@lru_cache(maxsize=1)
def build_resources() -> tuple[ChatOpenAI, DatabaseConnection]:
    llm = ChatOpenAI(
        base_url=settings.llm_base_url,  # type: ignore
        api_key=settings.llm_api_key,  # type: ignore
        model=settings.llm_model,  # type: ignore
    )
    db = DatabaseConnection.from_settings(settings)
    return llm, db


class WikiSession:
    def __init__(self, llm: ChatOpenAI, db: DatabaseConnection) -> None:
        self._llm = llm
        self._db = db

    def query(
        self,
        question: str,
        wiki_path: Path,
        project: str,
        history: list[dict] | None = None,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> QueryState:
        return run_query(
            wiki_path=wiki_path,
            project=project,
            question=question,
            llm=self._llm,
            db=self._db,
            history=history,
            confirm_fn=confirm_fn,
        )

    def ingest(
        self,
        source_path: Path,
        wiki_path: Path,
        project: str,
    ) -> IngestionState:
        return run_ingestion(
            wiki_path=wiki_path,
            source_path=source_path,
            project=project,
            llm=self._llm,
            db=self._db,
        )

    def lint(
        self,
        wiki_path: Path,
        project: str,
        auto: bool = False,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> LintState:
        return run_lint(
            wiki_path=wiki_path,
            project=project,
            llm=self._llm,
            auto=auto,
            confirm_fn=confirm_fn,
        )
