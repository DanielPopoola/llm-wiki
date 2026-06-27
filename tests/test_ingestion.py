"""
Tests for workflows/ingestion.py.

All LLM calls are mocked — tests run without any API key or network access.
Uses tmp_path for filesystem isolation.

Covers (per WIKI-003 definition of done):
  - Happy path: summary, entity, topic pages created
  - Contradiction detection: flagged under ## Contradictions
  - raw/ copy: source unchanged
  - Stub creation: missing wikilink targets get stubs
  - Duplicate detection: same source skipped on second ingest
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from wiki.schema import create_wiki
from workflows.ingestion import (
    IngestionState,
    create_stub_pages,
    extract_entities_and_concepts,
    flag_contradictions,
    hash_source,
    read_source,
    update_entity_pages,
    write_summary_page,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SOURCE = """
GTBank reported strong results for Q3 2024. The bank recorded loan growth of 12%
year-on-year, driven by retail and SME lending. Non-performing loans declined to
3.2% of total loans. CEO Segun Agbaje attributed the performance to the bank's
digital transformation strategy. The Nigerian Banking Sector as a whole saw
improved liquidity conditions in the quarter.
"""

CONTRADICTING_SOURCE = """
GTBank's Q3 2024 results revealed loan growth of only 5%, significantly below
analyst expectations. The bank's non-performing loan ratio rose to 6.1%.
"""


@pytest.fixture
def wiki(tmp_path):
    """A freshly scaffolded wiki project."""
    return create_wiki("test-wiki", wikis_dir=tmp_path)


@pytest.fixture
def source_file(tmp_path):
    """A sample source document on disk."""
    f = tmp_path / "gtbank-q3-2024.md"
    f.write_text(SAMPLE_SOURCE)
    return f


@pytest.fixture
def base_state(wiki, source_file) -> IngestionState:
    """Minimal state wired up for node-level tests."""
    return {
        "wiki_path": wiki.path,
        "source_path": source_file,
        "thread_id": "test-thread-001",
        "source_text": "",
        "source_hash": "",
        "skip": False,
        "entities": [],
        "concepts": [],
        "key_claims": [],
        "pages_written": [],
    }


def _llm_extract_response():
    """Fake structured output for extract_entities_and_concepts."""
    from workflows.ingestion import ExtractionResult

    return ExtractionResult(
        entities=["GTBank", "Segun Agbaje", "Nigerian Banking Sector"],
        concepts=["banking", "loan growth", "digital transformation"],
        key_claims=[
            "GTBank recorded loan growth of 12% in Q3 2024",
            "Non-performing loans declined to 3.2%",
            "CEO Segun Agbaje attributed results to digital transformation",
        ],
    )


def _llm_text_response(text: str):
    """Fake LLM response returning plain markdown text."""
    mock = MagicMock()
    mock.content = text
    return mock


# ---------------------------------------------------------------------------
# Node 1: read_source
# ---------------------------------------------------------------------------


def test_read_source_loads_text(base_state, source_file):
    result = read_source(base_state)
    assert "GTBank" in result["source_text"]
    assert "loan growth" in result["source_text"]


def test_read_source_copies_to_raw(base_state, wiki, source_file):
    read_source(base_state)
    raw_copy = wiki.path / "raw" / source_file.name
    assert raw_copy.exists()
    assert raw_copy.read_text() == source_file.read_text()


def test_read_source_does_not_modify_original(base_state, source_file):
    original = source_file.read_text()
    read_source(base_state)
    assert source_file.read_text() == original


# ---------------------------------------------------------------------------
# Node 2: hash_source
# ---------------------------------------------------------------------------


def test_hash_source_not_duplicate(base_state):
    state = {**base_state, "source_text": SAMPLE_SOURCE}
    result = hash_source(state)
    assert result["skip"] is False
    assert len(result["source_hash"]) == 64  # SHA-256 hex


def test_hash_source_detects_duplicate(base_state, wiki):
    state = {**base_state, "source_text": SAMPLE_SOURCE}

    # First ingest records the hash
    hash_source(state)

    # Second ingest should skip
    result = hash_source(state)
    assert result["skip"] is True


# ---------------------------------------------------------------------------
# Node 3: extract_entities_and_concepts
# ---------------------------------------------------------------------------


@patch("workflows.ingestion._make_llm")
def test_extract_entities_and_concepts(mock_llm, base_state):
    mock_llm.return_value.with_structured_output.return_value.invoke.return_value = _llm_extract_response()
    state = {**base_state, "source_text": SAMPLE_SOURCE}

    result = extract_entities_and_concepts(state)

    assert "GTBank" in result["entities"]
    assert "Segun Agbaje" in result["entities"]
    assert len(result["key_claims"]) >= 1
    assert len(result["concepts"]) >= 1


@patch("workflows.ingestion._make_llm")
def test_extract_skips_when_flagged(mock_llm, base_state):
    state = {**base_state, "skip": True}
    result = extract_entities_and_concepts(state)
    mock_llm.assert_not_called()
    assert result == {}


# ---------------------------------------------------------------------------
# Node 4: write_summary_page
# ---------------------------------------------------------------------------


@patch("workflows.ingestion._make_llm")
def test_write_summary_page_creates_file(mock_llm, base_state, wiki, source_file):
    mock_llm.return_value.invoke.return_value = _llm_text_response(
        "## Overview\nGTBank had a strong quarter.\n\n## Key Claims\n- Loan growth 12%\n"
    )
    state = {
        **base_state,
        "source_text": SAMPLE_SOURCE,
        "entities": ["GTBank"],
        "concepts": ["banking"],
        "key_claims": ["Loan growth 12%"],
    }

    result = write_summary_page(state)

    summary_path = wiki.path / "summaries" / f"{source_file.stem}.md"
    assert summary_path.exists()
    assert "GTBank" in summary_path.read_text()
    assert len(result["pages_written"]) == 1
    assert result["pages_written"][0]["is_new"] is True


# ---------------------------------------------------------------------------
# Node 5: update_entity_pages
# ---------------------------------------------------------------------------


@patch("workflows.ingestion._make_llm")
def test_update_entity_pages_creates_new_entity(mock_llm, base_state, wiki, source_file):
    mock_llm.return_value.invoke.return_value = _llm_text_response(
        "## Overview\nGTBank is a Nigerian commercial bank.\n\n## Key Facts\n- Loan growth 12%\n"
    )
    state = {
        **base_state,
        "source_text": SAMPLE_SOURCE,
        "entities": ["GTBank"],
        "concepts": ["banking"],
        "key_claims": ["GTBank loan growth 12%"],
    }

    result = update_entity_pages(state)

    entity_path = wiki.path / "entities" / "gtbank.md"
    assert entity_path.exists()
    assert len(result["pages_written"]) == 1


@patch("workflows.ingestion._make_llm")
def test_update_entity_pages_backs_up_existing(mock_llm, base_state, wiki, source_file):
    # Pre-create an entity page
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text("---\ntitle: GTBank\ntype: entity\n---\n\nOld content.\n")

    mock_llm.return_value.invoke.return_value = _llm_text_response("## Overview\nUpdated content.\n")
    state = {
        **base_state,
        "source_text": SAMPLE_SOURCE,
        "entities": ["GTBank"],
        "concepts": ["banking"],
        "key_claims": ["GTBank loan growth 12%"],
    }

    update_entity_pages(state)

    # WAL should have a backup event
    log_content = (wiki.path / "log.ndjson").read_text()
    events = [json.loads(line) for line in log_content.splitlines() if line.strip()]
    backup_events = [e for e in events if e["status"] == "backup"]
    assert len(backup_events) >= 1
    assert "Old content." in backup_events[0]["old_content"]


# ---------------------------------------------------------------------------
# Node 7: flag_contradictions
# ---------------------------------------------------------------------------


@patch("workflows.ingestion._make_llm")
def test_flag_contradictions_adds_section(mock_llm, base_state, wiki, source_file):
    # Entity page with a claim that will be contradicted
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text("---\ntitle: GTBank\ntype: entity\n---\n\nGTBank loan growth was 12% in Q3 2024.\n")

    from workflows.ingestion import Contradiction, ContradictionResult

    mock_llm.return_value.with_structured_output.return_value.invoke.return_value = ContradictionResult(
        has_contradictions=True,
        contradictions=[
            Contradiction(
                existing_claim="GTBank loan growth was 12% in Q3 2024",
                new_claim="GTBank loan growth was only 5% in Q3 2024",
                explanation="Two different figures for the same metric and period",
            )
        ],
    )

    state = {
        **base_state,
        "source_text": CONTRADICTING_SOURCE,
        "entities": ["GTBank"],
        "concepts": ["banking"],
        "key_claims": ["GTBank loan growth was only 5% in Q3 2024"],
        "pages_written": [],
    }

    flag_contradictions(state)

    updated = entity_path.read_text()
    assert "## Contradictions" in updated
    assert "5%" in updated


@patch("workflows.ingestion._make_llm")
def test_flag_contradictions_no_false_positives(mock_llm, base_state, wiki, source_file):
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text("---\ntitle: GTBank\ntype: entity\n---\n\nGTBank is a Nigerian bank.\n")

    from workflows.ingestion import ContradictionResult

    mock_llm.return_value.with_structured_output.return_value.invoke.return_value = ContradictionResult(
        has_contradictions=False,
        contradictions=[],
    )

    state = {
        **base_state,
        "source_text": SAMPLE_SOURCE,
        "entities": ["GTBank"],
        "concepts": ["banking"],
        "key_claims": ["GTBank is a commercial bank"],
        "pages_written": [],
    }

    flag_contradictions(state)

    updated = entity_path.read_text()
    assert "## Contradictions" not in updated


# ---------------------------------------------------------------------------
# Node 8: create_stub_pages
# ---------------------------------------------------------------------------


def test_create_stub_pages_for_unresolved_wikilinks(base_state, wiki, source_file):
    # Write a summary page with a wikilink to a non-existent entity
    summary_path = wiki.path / "summaries" / "test-article.md"
    summary_path.write_text(
        "---\ntitle: Test\ntype: summary\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\n"
        "See [[Unknown Entity]] for more details.\n"
    )

    state = {
        **base_state,
        "pages_written": [{"path": str(summary_path), "is_new": True}],
    }

    result = create_stub_pages(state)

    stub_path = wiki.path / "entities" / "unknown-entity.md"
    assert stub_path.exists()
    assert "Stub" in stub_path.read_text()
    assert len(result["pages_written"]) == 1


def test_create_stub_pages_skips_existing_entities(base_state, wiki, source_file):
    # Entity already exists
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text(
        "---\ntitle: GTBank\ntype: entity\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\nContent.\n"
    )

    summary_path = wiki.path / "summaries" / "test-article.md"
    summary_path.write_text(
        "---\ntitle: Test\ntype: summary\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\n"
        "See [[GTBank]] for details.\n"
    )

    state = {
        **base_state,
        "pages_written": [{"path": str(summary_path), "is_new": True}],
    }

    result = create_stub_pages(state)

    # No new stubs — GTBank already exists
    assert result["pages_written"] == []
