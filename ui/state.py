import asyncio
import logging
import tempfile
from pathlib import Path

import reflex as rx
from pydantic import BaseModel

from config import settings
from ui.session import WikiSession, build_resources
from wiki.embeddings import preload_model
from wiki.project_config import get_selected_project, set_selected_project
from wiki.schema import list_wikis

logger = logging.getLogger("llm_wiki")


_llm, _db = build_resources()
_session = WikiSession(llm=_llm, db=_db)
preload_model()

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
        wikis = list_wikis(Path(settings.wikis_dir))
        self.projects = [w.name for w in wikis]

        try:
            self.selected_project = get_selected_project()
        except Exception:
            if self.projects:
                self.selected_project = self.projects[0]
                set_selected_project(self.selected_project)

    def select_project(self, name: str):
        self.selected_project = name
        set_selected_project(name)
        self.messages = []  # switching projects starts a fresh conversation
        self.error = ""

    def set_question(self, value: str):
        self.question = value

    async def handle_key_down(self, key: str):
        if key == "Enter":
            yield State.ask()

    @rx.event(background=True)
    async def ask(self):
        question = self.question.strip()
        if not question or self.is_loading or not self.selected_project:
            return

        async with self:
            self.messages.append(Message(role="user", content=question))
            self.question = ""
            self.is_loading = True
            self.error = ""

        try:
            wiki_path = Path(settings.wikis_dir) / self.selected_project
            history = [{"role": m.role, "content": m.content} for m in self.messages[:-1]]

            result = await asyncio.to_thread(
                _session.query,
                question=question,
                wiki_path=wiki_path,
                project=self.selected_project,
                history=history,
            )

            async with self:
                self.messages.append(
                    Message(
                        role="assistant",
                        content=result.answer,
                        citations=result.citations,
                        has_gap=result.has_gap,
                    )
                )
        except Exception:
            logger.exception("ask() failed for question=%r", question)
            async with self:
                self.error = "Something went wrong answering that — please try again."
        finally:
            async with self:
                self.is_loading = False

    def clear_chat(self):
        self.messages = []
        self.error = ""

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
        # Scratch location only — run_ingestion copies the source into
        # the wiki's raw/ directory itself. This class never touches
        # wiki internals directly.
        tmp_path = Path(tempfile.gettempdir()) / file.name
        tmp_path.write_bytes(data)

        self.is_ingesting = True
        self.ingest_status = f"Ingesting {file.name}..."
        yield  # push the loading state to the browser before the blocking call
        yield State.run_ingestion_event(tmp_path, file.name)

    @rx.event(background=True)
    async def run_ingestion_event(self, source_path: Path, filename: str):
        try:
            wiki_path = Path(settings.wikis_dir) / self.selected_project
            result = await asyncio.to_thread(
                _session.ingest,
                source_path=source_path,
                wiki_path=wiki_path,
                project=self.selected_project,
            )

            async with self:
                if result.skip:
                    self.ingest_status = f"⏭️  {filename} was already ingested — skipped."
                else:
                    self.ingest_status = f"✅ {filename} ingested — {len(result.pages_written)} pages written."
        except Exception:
            logger.exception("Ingestion failed for %s", filename)
            async with self:
                self.ingest_status = f"❌ Ingestion failed — see server logs."
        finally:
            async with self:
                self.is_ingesting = False

    @rx.event(background=True)
    async def run_lint(self):
        if self.is_linting or not self.selected_project:
            return

        async with self:
            self.is_linting = True
            self.lint_summary = ""

        try:
            wiki_path = Path(settings.wikis_dir) / self.selected_project
            # auto=True: a web UI has no natural place for a per-finding
            # accept/reject prompt without a bigger modal-flow feature.
            result = await asyncio.to_thread(
                _session.lint,
                wiki_path=wiki_path,
                project=self.selected_project,
                auto=True,
            )

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
            logger.exception("Lint failed for project=%r", self.selected_project)
            async with self:
                self.lint_summary = "❌ Lint failed — see server logs."
        finally:
            async with self:
                self.is_linting = False
