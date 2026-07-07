import asyncio
import logging
import sys
import tempfile
import time
from pathlib import Path

import reflex as rx
from pydantic import BaseModel

from config import settings
from ui.session import WikiSession, build_resources
from wiki.embeddings import preload_model
from wiki.project_config import get_selected_project, set_selected_project
from wiki.schema import list_wikis

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("llm_wiki.state")

logger.debug("state.py: module import starting")
_llm, _db = build_resources()
logger.debug("state.py: build_resources() done")
_session = WikiSession(llm=_llm, db=_db)
logger.debug("state.py: WikiSession created, module import complete")
# preload_model()


ALLOWED_SOURCE_SUFFIXES = {".md", ".txt"}


class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    citations: list[str] = []
    has_gap: bool = False


class State(rx.State):
    projects: list[str] = []
    selected_project: str = ""

    messages: list[Message] = []
    question: str = ""
    is_loading: bool = False
    error: str = ""

    is_ingesting: bool = False
    ingest_status: str = ""

    is_linting: bool = False
    lint_summary: str = ""

    def load_projects(self):
        """Runs on page load. Populates the project picker, mirroring `llm-wiki list`."""
        logger.debug("load_projects(): starting")
        wikis = list_wikis(Path(settings.wikis_dir))
        self.projects = [w.name for w in wikis]

        try:
            self.selected_project = get_selected_project()
        except Exception:
            # No project selected yet — fall back to the first available,
            # same as auto-selecting a freshly created wiki would.
            if self.projects:
                self.selected_project = self.projects[0]
                set_selected_project(self.selected_project)
        logger.debug("load_projects(): done, selected_project=%r", self.selected_project)

    def select_project(self, name: str):
        self.selected_project = name
        set_selected_project(name)
        self.messages = []  # switching projects starts a fresh conversation
        self.error = ""

    def set_question(self, value: str):
        self.question = value

    async def handle_key_down(self, key: str):
        if key == "Enter":
            logger.debug("handle_key_down(): Enter pressed, dispatching ask()")
            yield State.ask()

    @rx.event(background=True)
    async def ask(self):
        question = self.question.strip()
        logger.debug(
            "ask(): entered. question=%r is_loading=%r selected_project=%r",
            question,
            self.is_loading,
            self.selected_project,
        )

        if not question or self.is_loading or not self.selected_project:
            logger.debug("ask(): early return (empty question / already loading / no project)")
            return

        async with self:
            self.messages.append(Message(role="user", content=question))
            self.question = ""
            self.is_loading = True
            self.error = ""
        logger.debug("ask(): user message appended, is_loading=True pushed to client")

        try:
            wiki_path = Path(settings.wikis_dir) / self.selected_project
            history = [{"role": m.role, "content": m.content} for m in self.messages[:-1]]
            logger.debug(
                "ask(): about to call asyncio.to_thread -> _session.query (wiki_path=%s, project=%r, history_len=%d)",
                wiki_path,
                self.selected_project,
                len(history),
            )
            t0 = time.monotonic()

            result = await asyncio.to_thread(
                _session.query,
                question=question,
                wiki_path=wiki_path,
                project=self.selected_project,
                history=history,
            )

            elapsed = time.monotonic() - t0
            logger.debug("ask(): _session.query returned after %.1fs", elapsed)

            async with self:
                self.messages.append(
                    Message(
                        role="assistant",
                        content=result.answer,
                        citations=result.citations,
                        has_gap=result.has_gap,
                    )
                )
            logger.debug("ask(): assistant message appended to state successfully")

        except Exception:
            logger.exception("ask(): exception occurred while querying")
            async with self:
                self.error = "Something went wrong — check server logs for details."
        finally:
            async with self:
                self.is_loading = False
            logger.debug("ask(): finally block done, is_loading reset to False")

    def clear_chat(self):
        self.messages = []
        self.error = ""

    # -----------------------------------------------------------------
    # Ingestion — file upload
    # -----------------------------------------------------------------

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        if self.is_ingesting or not self.selected_project:
            return

        if len(files) != 1:
            self.ingest_status = "❌ Upload one file at a time."
            return

        file = files[0]
        suffix = Path(file.name).suffix.lower()
        if suffix not in ALLOWED_SOURCE_SUFFIXES:
            self.ingest_status = f"❌ Unsupported file type '{suffix}'. Only .md and .txt are accepted."
            return

        data = await file.read()
        tmp_path = Path(tempfile.gettempdir()) / file.name
        tmp_path.write_bytes(data)

        self.is_ingesting = True
        self.ingest_status = f"Ingesting {file.name}..."
        yield  # push the loading state to the browser before the blocking call
        yield State.run_ingestion_event(tmp_path, file.name)

    @rx.event(background=True)
    async def run_ingestion_event(self, source_path: Path, filename: str):
        """The actual blocking call — split out so it can run in the background."""
        logger.debug("run_ingestion_event(): entered for %s", filename)
        try:
            wiki_path = Path(settings.wikis_dir) / self.selected_project
            t0 = time.monotonic()

            # BUG FIX: pass the callable + kwargs separately, don't call it directly —
            # calling it directly runs it synchronously on the main thread first,
            # defeating asyncio.to_thread entirely.
            result = await asyncio.to_thread(
                _session.ingest,
                source_path=source_path,
                wiki_path=wiki_path,
                project=self.selected_project,
            )
            logger.debug(
                "run_ingestion_event(): _session.ingest returned after %.1fs",
                time.monotonic() - t0,
            )

            async with self:
                if result.skip:
                    self.ingest_status = f"⏭️  {filename} was already ingested — skipped."
                else:
                    self.ingest_status = f"✅ {filename} ingested — {len(result.pages_written)} pages written."
        except Exception:
            logger.exception("run_ingestion_event(): exception occurred")
            async with self:
                self.ingest_status = "❌ Ingestion failed — check server logs for details."
        finally:
            async with self:
                self.is_ingesting = False
            logger.debug("run_ingestion_event(): finally block done")

    # -----------------------------------------------------------------
    # Lint
    # -----------------------------------------------------------------

    @rx.event(background=True)
    async def run_lint(self):
        if self.is_linting or not self.selected_project:
            return

        logger.debug("run_lint(): entered")
        async with self:
            self.is_linting = True
            self.lint_summary = ""

        try:
            wiki_path = Path(settings.wikis_dir) / self.selected_project
            t0 = time.monotonic()

            # Same fix as run_ingestion_event: pass callable + kwargs, don't call directly.
            result = await asyncio.to_thread(
                _session.lint,
                wiki_path=wiki_path,
                project=self.selected_project,
                auto=True,
            )
            logger.debug("run_lint(): _session.lint returned after %.1fs", time.monotonic() - t0)

            critical = sum(1 for f in result.findings if f.severity == "critical")
            warnings = sum(1 for f in result.findings if f.severity == "warning")
            suggestions = sum(1 for f in result.findings if f.severity == "suggestion")

            async with self:
                self.lint_summary = (
                    f"{critical} critical, {warnings} warnings, {suggestions} suggestions"
                    if result.findings
                    else "✅ Wiki is clean — no issues found."
                )
        except Exception:
            logger.exception("run_lint(): exception occurred")
            async with self:
                self.lint_summary = "❌ Lint failed — check server logs for details."
        finally:
            async with self:
                self.is_linting = False
            logger.debug("run_lint(): finally block done")
