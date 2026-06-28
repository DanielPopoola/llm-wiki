"""
Tests for wiki/index.py and wiki/log.py.
"""

import json
import re
import subprocess

import pytest

from wiki.index import IndexEntry, WikiIndex, read_index, upsert_entries, write_index
from wiki.log import (
    append_log_md,
    find_incomplete_ingestions,
    log_completed,
    log_started,
    log_wrote,
    rollback_ingestion,
)
from wiki.schema import create_wiki

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki(tmp_path):
    return create_wiki("test-wiki", wikis_dir=tmp_path)


@pytest.fixture
def entity_page(wiki):
    path = wiki.path / "entities" / "gtbank.md"
    path.write_text(
        "---\ntitle: GTBank\ntype: entity\ncreated: 2024-11-15\n"
        "updated: 2024-11-15\ntags: [banking]\nsources: []\n---\n\n"
        "GTBank is a Nigerian commercial bank.\n"
    )
    return path


@pytest.fixture
def summary_page(wiki):
    path = wiki.path / "summaries" / "gtbank-q3-2024.md"
    path.write_text(
        "---\ntitle: GTBank Q3 2024 Earnings\ntype: summary\ncreated: 2024-11-15\n"
        "updated: 2024-11-15\ntags: [banking]\nsources: [raw/gtbank-q3-2024.md]\n---\n\n"
        "GTBank reported strong Q3 results.\n"
    )
    return path


# ---------------------------------------------------------------------------
# wiki/index.py — read_index
# ---------------------------------------------------------------------------


def test_read_index_returns_empty_for_blank_index(wiki):
    index = read_index(wiki.path)
    assert index.entries == []


def test_read_index_parses_entries(wiki):
    (wiki.path / "index.md").write_text(
        "# Wiki Index\n\n"
        "## Entities\n"
        "- [[GTBank]] — Nigerian commercial bank covering Q3 2024\n\n"
        "## Topics\n\n"
        "## Summaries\n"
        "- [[GTBank Q3 2024 Earnings]] — ingested 2024-11-15\n"
    )

    index = read_index(wiki.path)

    assert len(index.entries) == 2
    entity = next(e for e in index.entries if e.page_type == "entity")
    assert entity.title == "GTBank"
    assert "Nigerian commercial bank" in entity.description


# ---------------------------------------------------------------------------
# wiki/index.py — write_index / upsert_entries
# ---------------------------------------------------------------------------


def test_write_index_creates_correct_sections(wiki, entity_page):
    index = WikiIndex(wiki_path=wiki.path)
    index.entries.append(
        IndexEntry(
            title="GTBank",
            description="Nigerian commercial bank",
            page_type="entity",
            page_path=entity_page,
        )
    )

    write_index(index)

    content = (wiki.path / "index.md").read_text()
    assert "## Entities" in content
    assert "[[GTBank]]" in content
    assert "Nigerian commercial bank" in content


def test_write_index_drops_stale_entries(wiki):
    # Entry points to a page that doesn't exist on disk
    index = WikiIndex(wiki_path=wiki.path)
    index.entries.append(
        IndexEntry(
            title="Deleted Page",
            description="this page was deleted",
            page_type="entity",
            page_path=wiki.path / "entities" / "deleted-page.md",
        )
    )

    write_index(index)

    content = (wiki.path / "index.md").read_text()
    assert "Deleted Page" not in content


def test_upsert_entries_adds_new_entry(wiki, entity_page):
    index = WikiIndex(wiki_path=wiki.path)
    new_entry = IndexEntry(
        title="GTBank",
        description="Nigerian commercial bank",
        page_type="entity",
        page_path=entity_page,
    )

    updated = upsert_entries(index, [new_entry])

    assert len(updated.entries) == 1
    assert updated.entries[0].title == "GTBank"


def test_upsert_entries_updates_existing_description(wiki, entity_page):
    index = WikiIndex(wiki_path=wiki.path)
    index.entries.append(
        IndexEntry(
            title="GTBank",
            description="old description",
            page_type="entity",
            page_path=entity_page,
        )
    )

    updated = upsert_entries(
        index,
        [
            IndexEntry(
                title="GTBank",
                description="updated description",
                page_type="entity",
                page_path=entity_page,
            )
        ],
    )

    assert len(updated.entries) == 1
    assert updated.entries[0].description == "updated description"


def test_index_reflects_two_ingestions(wiki, entity_page, summary_page):
    """Shared entity page shows updated description after second ingestion."""
    index = WikiIndex(wiki_path=wiki.path)

    # First ingestion
    index = upsert_entries(
        index,
        [
            IndexEntry(
                title="GTBank",
                description="first description",
                page_type="entity",
                page_path=entity_page,
            )
        ],
    )
    write_index(index)

    # Second ingestion updates the same entry
    index = read_index(wiki.path)
    index = upsert_entries(
        index,
        [
            IndexEntry(
                title="GTBank",
                description="updated after Q4 2024 earnings",
                page_type="entity",
                page_path=entity_page,
            )
        ],
    )
    write_index(index)

    final = read_index(wiki.path)
    print("final ouptut:", final)
    print("index.md contents:", (wiki.path / "index.md").read_text())
    print("final.entries:", final.entries)
    gtbank = next(e for e in final.entries if e.title == "GTBank")
    assert gtbank.description == "updated after Q4 2024 earnings"
    # Only one entry — no duplicates
    assert len([e for e in final.entries if e.title == "GTBank"]) == 1


# ---------------------------------------------------------------------------
# wiki/log.py — log.md format
# ---------------------------------------------------------------------------


def test_append_log_md_correct_format(wiki):
    append_log_md(wiki.path / "log.md", "ingest", "COMPLETED | gtbank-q3-2024.md")

    content = (wiki.path / "log.md").read_text()
    # Must match ## [YYYY-MM-DD] type | Description
    assert re.search(r"## \[\d{4}-\d{2}-\d{2}\] ingest \| COMPLETED", content)


def test_append_log_md_grep_parseable(wiki, tmp_path):
    """Verify grep "^## [" log.md returns clean output — task acceptance criterion."""
    log_path = wiki.path / "log.md"

    append_log_md(log_path, "ingest", "COMPLETED | source-one.md")
    append_log_md(log_path, "query", "What is GTBank's loan growth?")
    append_log_md(log_path, "ingest", "COMPLETED | source-two.md")

    result = subprocess.run(
        ["grep", "^## \\[", str(log_path)],
        capture_output=True,
        text=True,
    )

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 3
    assert all(l.startswith("## [") for l in lines)


def test_log_md_entries_are_chronological(wiki):
    log_path = wiki.path / "log.md"

    append_log_md(log_path, "ingest", "COMPLETED | first.md")
    append_log_md(log_path, "ingest", "COMPLETED | second.md")

    content = log_path.read_text()
    first_pos = content.index("first.md")
    second_pos = content.index("second.md")
    assert first_pos < second_pos


# ---------------------------------------------------------------------------
# wiki/log.py — log.ndjson WAL
# ---------------------------------------------------------------------------


def test_log_started_and_completed_bracket_ingestion(wiki):
    log_path = wiki.path / "log.ndjson"
    thread_id = "test-thread-001"

    log_started(log_path, thread_id, source="gtbank.md")
    log_completed(log_path, thread_id, source="gtbank.md", pages_written=5)

    events = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    statuses = [e["status"] for e in events]

    assert "started" in statuses
    assert "completed" in statuses


def test_find_incomplete_ingestions_detects_missing_completed(wiki):
    log_path = wiki.path / "log.ndjson"
    thread_id = "crashed-thread"

    log_started(log_path, thread_id, source="gtbank.md")
    log_wrote(log_path, thread_id, wiki.path / "entities" / "gtbank.md", is_new=True)
    # No log_completed — simulates a crash

    incomplete = find_incomplete_ingestions(log_path)
    assert thread_id in incomplete


def test_find_incomplete_ingestions_ignores_completed(wiki):
    log_path = wiki.path / "log.ndjson"
    thread_id = "clean-thread"

    log_started(log_path, thread_id, source="gtbank.md")
    log_completed(log_path, thread_id, source="gtbank.md", pages_written=3)

    incomplete = find_incomplete_ingestions(log_path)
    assert thread_id not in incomplete


def test_rollback_deletes_new_pages(wiki):
    log_path = wiki.path / "log.ndjson"
    thread_id = "rollback-thread"
    new_page = wiki.path / "entities" / "new-entity.md"
    new_page.write_text("content")

    log_started(log_path, thread_id, source="gtbank.md")
    log_wrote(log_path, thread_id, new_page, is_new=True)

    rollback_ingestion(log_path, thread_id)

    assert not new_page.exists()
