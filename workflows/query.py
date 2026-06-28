"""
Query workflow.

A LangGraph state machine that answers questions by searching the wiki
and synthesising a response with citations.

Nodes:
  1. read_index           — reads index.md to identify candidates (no DB)
  2. hybrid_search        — vector + full-text search in Oracle (Phase 2+)
  3. read_candidate_pages — loads full content of top candidate pages
  4. synthesise_answer    — LLM call; structured output with citations
  5. offer_to_save        — optionally saves answer as a new wiki page

Retrieval path chosen by conditional edge:
  db is None  → read_index
  db injected → hybrid_search

Runtime dependencies (llm, db, confirm_fn) are passed via
RunnableConfig["configurable"], not via state.
State holds only data that flows between nodes.
"""

import re
import uuid
from pathlib import Path
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from wiki import storage
from wiki.embeddings import build_embed_input, generate_embedding
from wiki.index import IndexEntry, upsert_entries, write_index
from wiki.index import read_index as _read_index
from wiki.log import append_log_md
from wiki.pages import make_frontmatter, read_page, write_page
from wiki.prompts import query_prompt, save_page_prompt

# ---------------------------------------------------------------------------
# Structured output model
# ---------------------------------------------------------------------------


class AnswerResult(BaseModel):
    answer: str
    citations: list[str]  # page titles cited in the answer
    has_gap: bool  # True if wiki doesn't cover the question well
    format_used: str  # prose | table | list


# ---------------------------------------------------------------------------
# State — data only, no runtime dependencies
# ---------------------------------------------------------------------------


def _append_history(left: list, right: list) -> list:
    return left + right


class QueryState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    wiki_path: Path
    project: str
    question: str
    candidate_paths: list[str] = Field(default_factory=list)
    candidate_pages: list[dict[str, Any]] = Field(default_factory=list)
    answer: str = ""
    citations: list[str] = Field(default_factory=list)
    has_gap: bool = False
    format_used: str = "prose"
    history: Annotated[list[dict[str, Any]], _append_history] = Field(default_factory=list)


def _get_llm(config: RunnableConfig) -> Any:
    llm = config.get("configurable", {}).get("llm")
    if llm is None:
        raise ValueError("llm not found in config['configurable']. Pass it via run_query().")
    return llm


def _get_db(config: RunnableConfig) -> Any:
    return config.get("configurable", {}).get("db")


def _get_confirm_fn(config: RunnableConfig) -> Any:
    return config.get("configurable", {}).get("confirm_fn") or (lambda _: False)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def read_index(state: QueryState, config: RunnableConfig) -> dict:
    """
    Node 1: Read index.md and return all page paths as candidates.

    Used when no DB is available. Caps at 10 pages to limit context.
    """
    index = _read_index(state.wiki_path)
    paths = [str(e.page_path) for e in index.entries if e.page_path.exists()]
    return {"candidate_paths": paths[:10]}


def hybrid_search(state: QueryState, config: RunnableConfig) -> dict:
    """
    Node 2: Vector + full-text search in Oracle.

    Embeds the question and runs hybrid search against wiki_pages,
    filtered to this project — results never cross wiki boundaries.
    """
    db = _get_db(config)
    if db is None:
        return {}

    query_embedding = generate_embedding(state.question)
    results = storage.search_pages(
        db=db,
        project=state.project,
        query_embedding=query_embedding,
        query_text=state.question,
        top_k=5,
    )
    return {"candidate_paths": [r.page_path for r in results]}


def read_candidate_pages(state: QueryState, config: RunnableConfig) -> dict:
    """
    Node 3: Load the full content of candidate pages.

    Skips pages that no longer exist on disk or have malformed frontmatter.
    """
    pages = []
    for path_str in state.candidate_paths:
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            page = read_page(path)
        except (ValueError, KeyError):
            continue
        pages.append(
            {
                "title": page.frontmatter.title,
                "path": path_str,
                "body": page.body,
            }
        )
    return {"candidate_pages": pages}


def synthesise_answer(state: QueryState, config: RunnableConfig) -> dict:
    """
    Node 4: Synthesise an answer from candidate pages.

    Structured output guarantees we get citations, gap flag, and format
    alongside the answer. Conversation history gives follow-up context.
    """
    llm = _get_llm(config)

    result = AnswerResult.model_validate(
        llm.with_structured_output(AnswerResult).invoke(
            query_prompt(
                question=state.question,
                page_contents=state.candidate_pages,
                conversation_history=state.history,
            )
        )
    )

    new_history = [
        {"role": "user", "content": state.question},
        {"role": "assistant", "content": result.answer},
    ]

    return {
        "answer": result.answer,
        "citations": result.citations,
        "has_gap": result.has_gap,
        "format_used": result.format_used,
        "history": new_history,
    }


def offer_to_save(state: QueryState, config: RunnableConfig) -> dict:
    """
    Node 5: Offer to save a useful answer as a new wiki page.

    Gap reports and citation-free answers are never saved.
    confirm_fn is injected via config — tests pass lambda, CLI passes input().
    """
    if state.has_gap or not state.citations:
        return {}

    confirm_fn = _get_confirm_fn(config)
    if not confirm_fn("Save this answer as a wiki page? [y/N] "):
        return {}

    llm = _get_llm(config)
    response = llm.invoke(
        save_page_prompt(
            question=state.question,
            answer=state.answer,
            citations=state.citations,
        )
    )

    slug = _slugify(state.question[:60])
    page_path = state.wiki_path / "topics" / f"{slug}.md"

    fm = make_frontmatter(
        title=state.question[:100],
        page_type="topic",
        tags=["saved-answer"],
        sources=state.citations,
    )
    write_page(page_path, fm, response.content)

    # Update index
    index = _read_index(state.wiki_path)
    upsert_entries(
        index,
        [
            IndexEntry(
                title=fm.title,
                description=f"Saved answer: {state.question[:60]}",
                page_type="topic",
                page_path=page_path,
            )
        ],
    )
    write_index(index)

    # Log
    append_log_md(
        log_path=state.wiki_path / "log.md",
        event_type="query",
        description=f"SAVED | {fm.title[:80]}",
    )

    # Embed if DB available
    db = _get_db(config)
    if db is not None:
        snippet = build_embed_input(fm.title, response.content)
        embedding = generate_embedding(snippet)
        storage.upsert_page(
            db=db,
            project=state.project,
            page_path=page_path,
            title=fm.title,
            page_type="topic",
            tags=fm.tags,
            snippet=snippet,
            embedding=embedding,
        )

    return {}


def choose_retrieval(state: QueryState, config: RunnableConfig) -> str:
    return "hybrid_search" if _get_db(config) is not None else "read_index"


def build_query_graph() -> StateGraph:
    builder = StateGraph(state_schema=QueryState)

    builder.add_node("read_index", read_index)
    builder.add_node("hybrid_search", hybrid_search)
    builder.add_node("read_candidate_pages", read_candidate_pages)
    builder.add_node("synthesise_answer", synthesise_answer)
    builder.add_node("offer_to_save", offer_to_save)

    builder.add_conditional_edges(
        START,
        choose_retrieval,
        {"read_index": "read_index", "hybrid_search": "hybrid_search"},
    )
    builder.add_edge("read_index", "read_candidate_pages")
    builder.add_edge("hybrid_search", "read_candidate_pages")
    builder.add_edge("read_candidate_pages", "synthesise_answer")
    builder.add_edge("synthesise_answer", "offer_to_save")
    builder.add_edge("offer_to_save", END)

    return builder


def run_query(
    wiki_path: Path,
    project: str,
    question: str,
    llm: Any,
    history: list[dict] | None = None,
    db: Any = None,
    confirm_fn=None,
    thread_id: str | None = None,
) -> QueryState:
    """
    Run the query workflow for a single question.

    Args:
        wiki_path: Root directory of the wiki project.
        project: Wiki project name.
        question: The user's question.
        llm: Injected LLM instance (required).
        history: Prior conversation turns for follow-up context.
        db: Injected DatabaseConnection. None uses index-based retrieval.
        confirm_fn: Callable[[str], bool] for save confirmation.
                    Defaults to always-no (non-interactive).
        thread_id: LangGraph thread ID. Auto-generated if not provided.

    Returns:
        Final QueryState with answer, citations, and updated history.
    """
    thread_id = thread_id or str(uuid.uuid4())
    checkpointer = InMemorySaver()
    graph = build_query_graph().compile(checkpointer=checkpointer)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": thread_id,
            "llm": llm,
            "db": db,
            "confirm_fn": confirm_fn,
        }
    }

    initial_state = QueryState(
        wiki_path=wiki_path,
        project=project,
        question=question,
        history=history or [],
    )

    append_log_md(
        log_path=wiki_path / "log.md",
        event_type="query",
        description=f'"{question[:80]}"',
    )

    result = graph.invoke(initial_state, config)
    return QueryState.model_validate(result)
