"""
Tests for wiki/storage.py.

All Oracle interactions are mocked — tests run without a live DB.
Covers WIKI-006 definition of done:
  - upsert_page: insert new, update changed, skip unchanged
  - source_already_ingested: duplicate detection
  - search_pages: vector and full-text paths
  - hash_source node: Oracle path and fallback path
"""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

from wiki import storage
from wiki.storage import (
    PageSearchResult,
    record_source,
    register_project,
    search_pages,
    source_already_ingested,
    upsert_page,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(fetchone_return=None, fetchall_return=None):
    """
    Build a mock DatabaseConnection whose cursor() context manager
    yields a mock cursor with configurable fetch results.
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_return
    cursor.fetchall.return_value = fetchall_return or []

    db = MagicMock()
    db.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    db.cursor.return_value.__exit__ = MagicMock(return_value=False)

    return db, cursor


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# upsert_page
# ---------------------------------------------------------------------------


def test_upsert_page_inserts_new_row():
    db, cursor = _make_db(fetchone_return=None)  # no existing row

    result = upsert_page(
        db=db,
        project="ngx",
        page_path=Path("entities/gtbank.md"),
        title="GTBank",
        page_type="entity",
        tags=["banking"],
        snippet="GTBank Nigerian commercial bank",
        embedding=[0.1] * 768,
    )

    assert result is True
    # INSERT should have been called (not UPDATE)
    sql_calls = [str(c.args[0]).strip() for c in cursor.execute.call_args_list]
    assert any("INSERT" in s for s in sql_calls)


def test_upsert_page_updates_changed_row():
    old_snippet = "old content"
    old_hash = _sha256(old_snippet)
    db, cursor = _make_db(fetchone_return=(old_hash,))  # existing row, different hash

    result = upsert_page(
        db=db,
        project="ngx",
        page_path=Path("entities/gtbank.md"),
        title="GTBank",
        page_type="entity",
        tags=["banking"],
        snippet="new content with different hash",  # different from old_snippet
        embedding=[0.1] * 768,
    )

    assert result is True
    sql_calls = [str(c.args[0]).strip() for c in cursor.execute.call_args_list]
    assert any("UPDATE" in s for s in sql_calls)


def test_upsert_page_skips_unchanged_row():
    snippet = "GTBank Nigerian commercial bank"
    current_hash = _sha256(snippet)
    db, cursor = _make_db(fetchone_return=(current_hash,))  # same hash

    result = upsert_page(
        db=db,
        project="ngx",
        page_path=Path("entities/gtbank.md"),
        title="GTBank",
        page_type="entity",
        tags=["banking"],
        snippet=snippet,
        embedding=[0.1] * 768,
    )

    assert result is False
    # Only the SELECT should have been called — no INSERT or UPDATE
    sql_calls = [str(c.args[0]).strip() for c in cursor.execute.call_args_list]
    assert all("SELECT" in s for s in sql_calls)


# ---------------------------------------------------------------------------
# source_already_ingested
# ---------------------------------------------------------------------------


def test_source_already_ingested_returns_true_when_found():
    db, cursor = _make_db(fetchone_return=(1,))

    result = source_already_ingested(db, project="ngx", content_hash="abc123")

    assert result is True


def test_source_already_ingested_returns_false_when_not_found():
    db, cursor = _make_db(fetchone_return=None)

    result = source_already_ingested(db, project="ngx", content_hash="abc123")

    assert result is False


def test_source_already_ingested_filters_by_project():
    db, cursor = _make_db(fetchone_return=None)

    source_already_ingested(db, project="ngx", content_hash="abc123")

    sql = cursor.execute.call_args.args[0]
    assert "project" in sql.lower()


# ---------------------------------------------------------------------------
# record_source
# ---------------------------------------------------------------------------


def test_record_source_inserts_row():
    db, cursor = _make_db()

    record_source(
        db=db,
        project="ngx",
        source_path=Path("raw/gtbank-q3-2024.md"),
        content_hash="abc123",
        title="GTBank Q3 2024",
        status="completed",
    )

    sql = cursor.execute.call_args.args[0]
    assert "INSERT" in sql


# ---------------------------------------------------------------------------
# search_pages — merged results
# ---------------------------------------------------------------------------


def test_search_pages_merges_vector_and_fulltext():
    """Pages from both searches are returned; duplicates deduplicated."""
    vector_row = ("entities/gtbank.md", "GTBank", "entity", '["banking"]', "snippet", 0.12)
    fulltext_row = ("summaries/gtbank-q3.md", "GTBank Q3", "summary", '["banking"]', "snippet", 85.0)

    db = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [vector_row],  # vector search returns
        [fulltext_row],  # full-text search returns
    ]
    db.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    db.cursor.return_value.__exit__ = MagicMock(return_value=False)

    results = search_pages(
        db=db,
        project="ngx",
        query_embedding=[0.1] * 768,
        query_text="GTBank loan growth",
        top_k=5,
    )

    paths = [r.page_path for r in results]
    assert "entities/gtbank.md" in paths
    assert "summaries/gtbank-q3.md" in paths


def test_search_pages_deduplicates_same_page():
    """A page in both vector and full-text results appears only once."""
    same_row = ("entities/gtbank.md", "GTBank", "entity", '["banking"]', "snippet", 0.1)

    db = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.side_effect = [[same_row], [same_row]]
    db.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    db.cursor.return_value.__exit__ = MagicMock(return_value=False)

    results = search_pages(
        db=db,
        project="ngx",
        query_embedding=[0.1] * 768,
        query_text="GTBank",
        top_k=5,
    )

    assert len([r for r in results if r.page_path == "entities/gtbank.md"]) == 1


def test_search_pages_respects_project_isolation():
    """Search SQL must include project filter."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    db.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    db.cursor.return_value.__exit__ = MagicMock(return_value=False)

    search_pages(
        db=db,
        project="ngx",
        query_embedding=[0.1] * 768,
        query_text="GTBank",
    )

    for c in cursor.execute.call_args_list:
        sql = c.args[0]
        assert "project" in sql.lower(), f"Missing project filter in: {sql}"


# ---------------------------------------------------------------------------
# hash_source node — Oracle path
# ---------------------------------------------------------------------------


def test_hash_source_uses_oracle_when_db_injected(tmp_path):
    from wiki.schema import create_wiki
    from workflows.ingestion import hash_source

    wiki = create_wiki("test", wikis_dir=tmp_path)
    db, cursor = _make_db(fetchone_return=None)  # not already ingested

    state = {
        "wiki_path": wiki.path,
        "source_path": tmp_path / "source.md",
        "project": "test",
        "db": db,
        "source_text": "Some source content",
        "source_hash": "",
        "skip": False,
        "thread_id": "t1",
        "entities": [],
        "concepts": [],
        "key_claims": [],
        "pages_written": [],
    }

    result = hash_source(state)

    assert result["skip"] is False
    # Oracle was consulted — cursor.execute was called
    cursor.execute.assert_called()


def test_hash_source_skips_when_oracle_says_duplicate(tmp_path):
    from wiki.schema import create_wiki
    from workflows.ingestion import hash_source

    wiki = create_wiki("test", wikis_dir=tmp_path)
    db, cursor = _make_db(fetchone_return=(1,))  # already ingested

    state = {
        "wiki_path": wiki.path,
        "source_path": tmp_path / "source.md",
        "project": "test",
        "db": db,
        "source_text": "Some source content",
        "source_hash": "",
        "skip": False,
        "thread_id": "t1",
        "entities": [],
        "concepts": [],
        "key_claims": [],
        "pages_written": [],
    }

    result = hash_source(state)

    assert result["skip"] is True


def test_hash_source_falls_back_to_json_when_no_db(tmp_path):
    from wiki.schema import create_wiki
    from workflows.ingestion import hash_source

    wiki = create_wiki("test", wikis_dir=tmp_path)

    state = {
        "wiki_path": wiki.path,
        "source_path": tmp_path / "source.md",
        "project": "test",
        "db": None,  # no DB injected
        "source_text": "Some source content",
        "source_hash": "",
        "skip": False,
        "thread_id": "t1",
        "entities": [],
        "concepts": [],
        "key_claims": [],
        "pages_written": [],
    }

    result = hash_source(state)

    assert result["skip"] is False
    # Local cache file should have been created
    assert (wiki.path / ".ingested_hashes.json").exists()
