"""
Source ingestion workflow.

A LangGraph state machine that reads a source document and integrates
its knowledge into the wiki. Each node does exactly one thing.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Optional

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from config import settings
from infrastructure.db import DatabaseConnection
from wiki import storage
from wiki.embeddings import build_embed_input, generate_embedding
from wiki.index import IndexEntry, read_index, upsert_entries, write_index
from wiki.log import append_log_md, log_backup, log_completed, log_started, log_wrote
from wiki.pages import make_frontmatter, read_page, resolve_wikilink, write_page
from wiki.prompts import (
    contradiction_check_prompt,
    extraction_prompt,
    new_entity_page_prompt,
    new_topic_page_prompt,
    page_description_prompt,
    summary_page_prompt,
    update_entity_page_prompt,
    update_topic_page_prompt,
)


class ExtractionResult(BaseModel):
    entities: list[str]
    concepts: list[str]
    key_claims: list[str]


class Contradiction(BaseModel):
    existing_claim: str
    new_claim: str
    explanation: str


class ContradictionResult(BaseModel):
    has_contradictions: bool
    contradictions: list[Contradiction]


def _append_pages(left: list, right: list) -> list:
    """Reducer: accumulate pages_written across nodes."""
    return left + right


class IngestionState(TypedDict):
    wiki_path: Path
    source_path: Path
    thread_id: str
    project: str  # wiki project name for DB isolation
    db: Optional[DatabaseConnection]
    source_text: str
    source_hash: str
    skip: bool
    entities: list[str]
    concepts: list[str]
    key_claims: list[str]
    pages_written: Annotated[list[dict], _append_pages]


def _make_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )


def _log_path(wiki_path: Path) -> Path:
    return wiki_path / "log.ndjson"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _safe_write(state: IngestionState, path: Path, content_fn: Callable) -> dict:
    """
    Write a page to disk with WAL bookkeeping.

    Logs a backup event before modifying an existing page so rollback
    can restore it. Logs a wrote event after every write.
    """
    log = _log_path(state["wiki_path"])
    thread_id = state["thread_id"]
    is_new = not path.exists()

    if not is_new:
        old_content = path.read_text(encoding="utf-8")
        log_backup(log, thread_id, path, old_content)

    frontmatter, body = content_fn()
    write_page(path, frontmatter, body)
    log_wrote(log, thread_id, path, is_new)

    return {"path": str(path), "is_new": is_new}


def read_source(state: IngestionState) -> dict:
    """Node 1: Load the source document from disk and copy it to raw/."""
    text = Path(state["source_path"]).read_text(encoding="utf-8")

    raw_dest = state["wiki_path"] / "raw" / Path(state["source_path"]).name
    if not raw_dest.exists():
        raw_dest.write_bytes(Path(state["source_path"]).read_bytes())

    return {"source_text": text}


def hash_source(state: IngestionState) -> dict:
    """
    Node 2: Hash the source to detect duplicates.

    Falls back to local JSON cache when db is None (tests / Phase 1).
    """
    digest = hashlib.sha256(state["source_text"].encode()).hexdigest()
    db = state.get("db")

    if db is not None:
        already_done = storage.source_already_ingested(db, state["project"], digest)
        return {"source_hash": digest, "skip": already_done}

    # Fallback: local JSON cache (used when db not injected)
    cache_path = state["wiki_path"] / ".ingested_hashes.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    if digest in cache:
        return {"source_hash": digest, "skip": True}

    cache[digest] = str(state["source_path"])
    cache_path.write_text(json.dumps(cache, indent=2))
    return {"source_hash": digest, "skip": False}


def extract_entities_and_concepts(state: IngestionState) -> dict:
    """
    Node 3: Extract structured information from the source via LLM.
    """
    if state.get("skip"):
        return {}

    llm = _make_llm()
    result: ExtractionResult = llm.with_structured_output(ExtractionResult).invoke(
        extraction_prompt(state["source_text"])
    )

    return {
        "entities": result.entities,
        "concepts": result.concepts,
        "key_claims": result.key_claims,
    }


def write_summary_page(state: IngestionState) -> dict:
    """Node 4: Write a summary page for this source to summaries/."""
    if state.get("skip"):
        return {}

    source_name = Path(state["source_path"]).stem
    path = state["wiki_path"] / "summaries" / f"{source_name}.md"

    llm = _make_llm()
    response = llm.invoke(
        summary_page_prompt(
            source_text=state["source_text"],
            key_claims=state["key_claims"],
            entities=state["entities"],
            source_filename=Path(state["source_path"]).name,
        )
    )

    def make_content():
        fm = make_frontmatter(
            title=source_name.replace("-", " ").title(),
            page_type="summary",
            tags=state["concepts"][:5],
            sources=[f"raw/{Path(state['source_path']).name}"],
        )
        return fm, response.content

    entry = _safe_write(state, path, make_content)
    return {"pages_written": [entry]}


def update_entity_pages(state: IngestionState) -> dict:
    """Node 5: Create or update a page for each extracted entity."""
    if state.get("skip"):
        return {}

    llm = _make_llm()
    written = []
    related = [e for e in state["entities"]]

    for entity in state["entities"]:
        slug = _slugify(entity)
        path = state["wiki_path"] / "entities" / f"{slug}.md"
        existing_content = path.read_text(encoding="utf-8") if path.exists() else None
        relevant_claims = [c for c in state["key_claims"] if entity.lower() in c.lower()]

        if existing_content:
            prompt = update_entity_page_prompt(
                entity=entity,
                existing_content=existing_content,
                source_text=state["source_text"],
                relevant_claims=relevant_claims,
            )
        else:
            prompt = new_entity_page_prompt(
                entity=entity,
                source_text=state["source_text"],
                relevant_claims=relevant_claims,
                related_entities=[e for e in related if e != entity],
            )

        response = llm.invoke(prompt)

        def make_content(e=entity, body=response.content):
            fm = make_frontmatter(
                title=e,
                page_type="entity",
                tags=state["concepts"][:3],
                sources=[f"raw/{Path(state['source_path']).name}"],
            )
            return fm, body

        entry = _safe_write(state, path, make_content)
        written.append(entry)

    return {"pages_written": written}


def update_topic_pages(state: IngestionState) -> dict:
    """Node 6: Create or update a topic overview page for each concept."""
    if state.get("skip"):
        return {}

    llm = _make_llm()
    written = []
    source_stem = Path(state["source_path"]).stem

    for concept in state["concepts"]:
        slug = _slugify(concept)
        path = state["wiki_path"] / "topics" / f"{slug}.md"
        existing_content = path.read_text(encoding="utf-8") if path.exists() else None

        if existing_content:
            prompt = update_topic_page_prompt(
                concept=concept,
                existing_content=existing_content,
                entities=state["entities"],
                key_claims=state["key_claims"],
                source_stem=source_stem,
            )
        else:
            prompt = new_topic_page_prompt(
                concept=concept,
                source_text=state["source_text"],
                key_claims=state["key_claims"],
                entities=state["entities"],
                source_stem=source_stem,
            )

        response = llm.invoke(prompt)

        def make_content(c=concept, body=response.content):
            fm = make_frontmatter(
                title=c,
                page_type="topic",
                tags=[_slugify(c)],
                sources=[f"raw/{Path(state['source_path']).name}"],
            )
            return fm, body

        entry = _safe_write(state, path, make_content)
        written.append(entry)

    return {"pages_written": written}


def flag_contradictions(state: IngestionState) -> dict:
    """
    Node 7: Find contradictions between new claims and existing entity pages.
    """
    if state.get("skip"):
        return {}

    llm = _make_llm()
    written = []

    for entity in state["entities"]:
        slug = _slugify(entity)
        path = state["wiki_path"] / "entities" / f"{slug}.md"

        if not path.exists():
            continue

        existing_content = path.read_text(encoding="utf-8")

        result: ContradictionResult = llm.with_structured_output(ContradictionResult).invoke(
            contradiction_check_prompt(
                entity=entity,
                existing_content=existing_content,
                new_claims=state["key_claims"],
            )
        )

        if not result.has_contradictions:
            continue

        contradiction_block = "\n\n## Contradictions\n" + "\n".join(
            f"\n- **Conflict**: {c.existing_claim}\n  **vs new claim**: {c.new_claim}\n  **Note**: {c.explanation}"
            for c in result.contradictions
        )

        current = path.read_text(encoding="utf-8")
        updated = current + contradiction_block

        log_backup(_log_path(state["wiki_path"]), state["thread_id"], path, current)
        path.write_text(updated, encoding="utf-8")
        log_wrote(_log_path(state["wiki_path"]), state["thread_id"], path, is_new=False)
        written.append({"path": str(path), "is_new": False})

    return {"pages_written": written}


def create_stub_pages(state: IngestionState) -> dict:
    """
    Node 8: Create stub pages for wikilinks that don't resolve to existing pages.
    """
    if state.get("skip"):
        return {}

    written = []

    for entry in state.get("pages_written", []):
        page_path = Path(entry["path"])
        if not page_path.exists():
            continue

        try:
            page = read_page(page_path)
        except (ValueError, KeyError):
            continue

        for link_target in page.wikilinks:
            if resolve_wikilink(link_target, state["wiki_path"]) is not None:
                continue

            slug = _slugify(link_target)
            stub_path = state["wiki_path"] / "entities" / f"{slug}.md"

            if stub_path.exists():
                continue

            def make_stub(name=link_target):
                fm = make_frontmatter(title=name, page_type="entity")
                body = (
                    f"# {name}\n\n"
                    "> **Stub** — auto-created from a wikilink. "
                    "Add content when a source covers this entity.\n"
                )
                return fm, body

            entry = _safe_write(state, stub_path, make_stub)
            written.append(entry)

    return {"pages_written": written}


def update_index(state: IngestionState) -> dict:
    """
    Node 9: Rebuild index.md to reflect all pages written this ingestion.

    For each page written, asks the LLM for a one-line description,
    then upserts the entry. Stale entries (deleted pages) are dropped
    automatically by write_index().
    """
    if state.get("skip"):
        return {}

    llm = _make_llm()
    index = read_index(state["wiki_path"])
    new_entries = []

    for entry in state.get("pages_written", []):
        page_path = Path(entry["path"])
        if not page_path.exists():
            continue

        try:
            page = read_page(page_path)
        except (ValueError, KeyError):
            continue

        response = llm.invoke(
            page_description_prompt(
                title=page.frontmatter.title,
                page_type=page.frontmatter.type,
                body=page.body,
            )
        )

        new_entries.append(
            IndexEntry(
                title=page.frontmatter.title,
                description=response.content.strip(),
                page_type=page.frontmatter.type,
                page_path=page_path,
            )
        )

    updated_index = upsert_entries(index, new_entries)
    write_index(updated_index)

    return {}


def append_log(state: IngestionState) -> dict:
    """
    Node 10: Append a completed entry to log.md and log.ndjson.
    """
    if state.get("skip"):
        return {}

    source_name = Path(state["source_path"]).name
    pages_count = len(state.get("pages_written", []))

    # Human-readable entry in log.md
    append_log_md(
        log_path=state["wiki_path"] / "log.md",
        event_type="ingest",
        description=f"COMPLETED | {source_name} — {pages_count} pages written",
    )

    # Structured completed event in log.ndjson (closes the started event)
    log_completed(
        log_path=state["wiki_path"] / "log.ndjson",
        thread_id=state["thread_id"],
        source=source_name,
        pages_written=pages_count,
    )

    return {}


def embed_changed_pages(state: IngestionState) -> dict:
    """
    Node 11: Re-embed only pages touched by this ingestion.

    Reads each written page, generates title + first-400-token embedding,
    and upserts into wiki_pages. Pages whose content hash hasn't changed
    are skipped by storage.upsert_page() — no redundant re-embedding.

    Skipped entirely when db is not injected.
    """
    if state.get("skip"):
        return {}

    db = state.get("db")
    if db is None:
        return {}

    for entry in state.get("pages_written", []):
        page_path = Path(entry["path"])
        if not page_path.exists():
            continue

        try:
            page = read_page(page_path)
        except (ValueError, KeyError):
            continue

        snippet = build_embed_input(page.frontmatter.title, page.body)
        embedding = generate_embedding(snippet)

        storage.upsert_page(
            db=db,
            project=state["project"],
            page_path=page_path,
            title=page.frontmatter.title,
            page_type=page.frontmatter.type,
            tags=page.frontmatter.tags,
            snippet=snippet,
            embedding=embedding,
        )

    return {}


def should_skip(state: IngestionState) -> str:
    return "skip" if state.get("skip") else "continue"


def build_ingestion_graph() -> StateGraph:
    builder = StateGraph(IngestionState)

    builder.add_node("read_source", read_source)
    builder.add_node("hash_source", hash_source)
    builder.add_node("extract_entities_and_concepts", extract_entities_and_concepts)
    builder.add_node("write_summary_page", write_summary_page)
    builder.add_node("update_entity_pages", update_entity_pages)
    builder.add_node("update_topic_pages", update_topic_pages)
    builder.add_node("flag_contradictions", flag_contradictions)
    builder.add_node("create_stub_pages", create_stub_pages)
    builder.add_node("update_index", update_index)
    builder.add_node("embed_changed_pages", embed_changed_pages)
    builder.add_node("append_log", append_log)

    builder.add_edge(START, "read_source")
    builder.add_edge("read_source", "hash_source")
    builder.add_conditional_edges(
        "hash_source",
        should_skip,
        {"skip": END, "continue": "extract_entities_and_concepts"},
    )
    builder.add_edge("extract_entities_and_concepts", "write_summary_page")
    builder.add_edge("write_summary_page", "update_entity_pages")
    builder.add_edge("update_entity_pages", "update_topic_pages")
    builder.add_edge("update_topic_pages", "flag_contradictions")
    builder.add_edge("flag_contradictions", "create_stub_pages")
    builder.add_edge("create_stub_pages", "update_index")
    builder.add_edge("update_index", "embed_changed_pages")
    builder.add_edge("embed_changed_pages", "append_log")
    builder.add_edge("append_log", END)

    return builder


def run_ingestion(
    wiki_path: Path,
    source_path: Path,
    project: str,
    db: DatabaseConnection | None = None,
    thread_id: str | None = None,
) -> IngestionState:
    """
    Run the ingestion workflow for a single source document.

    Args:
        wiki_path: Root directory of the wiki project.
        source_path: Path to the source document to ingest.
        project: Wiki project name — used for DB isolation.
        db: Injected DatabaseConnection. Pass None to skip all DB writes
            (useful for tests and Phase 1 mode).
        thread_id: LangGraph thread ID. Auto-generated if not provided.

    Returns:
        Final IngestionState after all nodes complete.
    """
    thread_id = thread_id or str(uuid.uuid4())

    log_started(
        log_path=wiki_path / "log.ndjson",
        thread_id=thread_id,
        source=str(source_path),
    )

    checkpointer = InMemorySaver()
    graph = build_ingestion_graph().compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": thread_id}}
    initial_state: IngestionState = {
        "wiki_path": wiki_path,
        "source_path": source_path,
        "thread_id": thread_id,
        "project": project,
        "db": db,
        "source_text": "",
        "source_hash": "",
        "skip": False,
        "entities": [],
        "concepts": [],
        "key_claims": [],
        "pages_written": [],
    }

    return graph.invoke(initial_state, config)
