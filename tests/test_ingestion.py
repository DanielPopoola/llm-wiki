"""
Tests for workflows/ingestion.py.

LLM is passed via RunnableConfig — no @patch needed.
Uses tmp_path for filesystem isolation.
"""

import json
from unittest.mock import MagicMock

import pytest

from wiki.schema import create_wiki
from workflows.ingestion import (
    Contradiction,
    ContradictionResult,
    ExtractionResult,
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
    return create_wiki("test-wiki", wikis_dir=tmp_path)


@pytest.fixture
def source_file(tmp_path):
    f = tmp_path / "gtbank-q3-2024.md"
    f.write_text(SAMPLE_SOURCE)
    return f


@pytest.fixture
def base_state(wiki, source_file) -> IngestionState:
    return IngestionState(
        wiki_path=wiki.path,
        source_path=source_file,
        thread_id="test-thread-001",
        project="test",
    )


def _make_config(llm=None, db=None) -> dict:
    return {"configurable": {"thread_id": "test-thread-001", "llm": llm, "db": db}}


def _mock_llm_text(text: str) -> MagicMock:
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content=text)
    return mock


def _mock_llm_structured(result) -> MagicMock:
    """Mock for .with_structured_output(Model).invoke(prompt)."""
    mock = MagicMock()
    mock.with_structured_output.return_value.invoke.return_value = result
    return mock


# ---------------------------------------------------------------------------
# Node 1: read_source
# ---------------------------------------------------------------------------


def test_read_source_loads_text(base_state, source_file):
    result = read_source(base_state, _make_config())  # type: ignore
    assert "GTBank" in result["source_text"]


def test_read_source_copies_to_raw(base_state, wiki, source_file):
    read_source(base_state, _make_config())  # type: ignore
    assert (wiki.path / "raw" / source_file.name).exists()


def test_read_source_does_not_modify_original(base_state, source_file):
    original = source_file.read_text()
    read_source(base_state, _make_config())  # type: ignore
    assert source_file.read_text() == original


# ---------------------------------------------------------------------------
# Node 2: hash_source
# ---------------------------------------------------------------------------


def test_hash_source_not_duplicate(base_state):
    state = base_state.model_copy(update={"source_text": SAMPLE_SOURCE})
    result = hash_source(state, _make_config())  # type: ignore
    assert result["skip"] is False
    assert len(result["source_hash"]) == 64


def test_hash_source_detects_duplicate(base_state, wiki):
    state = base_state.model_copy(update={"source_text": SAMPLE_SOURCE})
    hash_source(state, _make_config())  # type: ignore
    result = hash_source(state, _make_config())  # type: ignore
    assert result["skip"] is True


# ---------------------------------------------------------------------------
# Node 3: extract_entities_and_concepts
# ---------------------------------------------------------------------------


def test_extract_entities_and_concepts(base_state):
    extraction = ExtractionResult(
        entities=["GTBank", "Segun Agbaje", "Nigerian Banking Sector"],
        concepts=["banking", "loan growth", "digital transformation"],
        key_claims=["GTBank recorded loan growth of 12% in Q3 2024"],
    )
    llm = _mock_llm_structured(extraction)
    state = base_state.model_copy(update={"source_text": SAMPLE_SOURCE})

    result = extract_entities_and_concepts(state, _make_config(llm=llm))  # type: ignore

    assert "GTBank" in result["entities"]
    assert len(result["key_claims"]) >= 1


def test_extract_skips_when_flagged(base_state):
    llm = MagicMock()
    state = base_state.model_copy(update={"skip": True})
    extract_entities_and_concepts(state, _make_config(llm=llm))  # type: ignore
    llm.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# Node 4: write_summary_page
# ---------------------------------------------------------------------------


def test_write_summary_page_creates_file(base_state, wiki, source_file):
    llm = _mock_llm_text("## Overview\nGTBank had a strong quarter.\n")
    state = base_state.model_copy(
        update={
            "source_text": SAMPLE_SOURCE,
            "entities": ["GTBank"],
            "concepts": ["banking"],
            "key_claims": ["Loan growth 12%"],
        }
    )

    result = write_summary_page(state, _make_config(llm=llm))  # type: ignore

    summary_path = wiki.path / "summaries" / f"{source_file.stem}.md"
    assert summary_path.exists()
    assert result["pages_written"][0]["is_new"] is True


# ---------------------------------------------------------------------------
# Node 5: update_entity_pages
# ---------------------------------------------------------------------------


def test_update_entity_pages_creates_new_entity(base_state, wiki):
    llm = _mock_llm_text("## Overview\nGTBank is a Nigerian commercial bank.\n")
    state = base_state.model_copy(
        update={
            "source_text": SAMPLE_SOURCE,
            "entities": ["GTBank"],
            "concepts": ["banking"],
            "key_claims": ["GTBank loan growth 12%"],
        }
    )

    result = update_entity_pages(state, _make_config(llm=llm))  # type: ignore

    assert (wiki.path / "entities" / "gtbank.md").exists()
    assert len(result["pages_written"]) == 1


def test_update_entity_pages_backs_up_existing(base_state, wiki):
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text(
        "---\ntitle: GTBank\ntype: entity\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\nOld content.\n"
    )

    llm = _mock_llm_text("## Overview\nUpdated content.\n")
    state = base_state.model_copy(
        update={
            "source_text": SAMPLE_SOURCE,
            "entities": ["GTBank"],
            "concepts": ["banking"],
            "key_claims": ["GTBank loan growth 12%"],
        }
    )

    update_entity_pages(state, _make_config(llm=llm))  # type: ignore

    events = [json.loads(l) for l in (wiki.path / "log.ndjson").read_text().splitlines() if l.strip()]
    backups = [e for e in events if e["status"] == "backup"]
    assert len(backups) >= 1
    assert "Old content." in backups[0]["old_content"]


# ---------------------------------------------------------------------------
# Node 7: flag_contradictions
# ---------------------------------------------------------------------------


def test_flag_contradictions_adds_section(base_state, wiki):
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text(
        "---\ntitle: GTBank\ntype: entity\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\n"
        "GTBank loan growth was 12% in Q3 2024.\n"
    )

    contradiction = ContradictionResult(
        has_contradictions=True,
        contradictions=[
            Contradiction(
                existing_claim="GTBank loan growth was 12% in Q3 2024",
                new_claim="GTBank loan growth was only 5% in Q3 2024",
                explanation="Two different figures for the same metric and period",
            )
        ],
    )
    llm = _mock_llm_structured(contradiction)
    state = base_state.model_copy(
        update={
            "source_text": CONTRADICTING_SOURCE,
            "entities": ["GTBank"],
            "concepts": ["banking"],
            "key_claims": ["GTBank loan growth was only 5% in Q3 2024"],
        }
    )

    flag_contradictions(state, _make_config(llm=llm))  # type: ignore

    updated = entity_path.read_text()
    assert "## Contradictions" in updated
    assert "5%" in updated


def test_flag_contradictions_no_false_positives(base_state, wiki):
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text(
        "---\ntitle: GTBank\ntype: entity\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\n"
        "GTBank is a Nigerian bank.\n"
    )

    llm = _mock_llm_structured(ContradictionResult(has_contradictions=False, contradictions=[]))
    state = base_state.model_copy(
        update={
            "source_text": SAMPLE_SOURCE,
            "entities": ["GTBank"],
            "concepts": ["banking"],
            "key_claims": ["GTBank is a commercial bank"],
        }
    )

    flag_contradictions(state, _make_config(llm=llm))  # type: ignore

    assert "## Contradictions" not in entity_path.read_text()


# ---------------------------------------------------------------------------
# Node 8: create_stub_pages
# ---------------------------------------------------------------------------


def test_create_stub_pages_for_unresolved_wikilinks(base_state, wiki):
    summary_path = wiki.path / "summaries" / "test-article.md"
    summary_path.write_text(
        "---\ntitle: Test\ntype: summary\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\n"
        "See [[Unknown Entity]] for more details.\n"
    )
    state = base_state.model_copy(
        update={
            "pages_written": [{"path": str(summary_path), "is_new": True}],
        }
    )

    result = create_stub_pages(state, _make_config())  # type: ignore

    assert (wiki.path / "entities" / "unknown-entity.md").exists()
    assert len(result["pages_written"]) == 1


def test_create_stub_pages_skips_existing_entities(base_state, wiki):
    entity_path = wiki.path / "entities" / "gtbank.md"
    entity_path.write_text(
        "---\ntitle: GTBank\ntype: entity\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\nContent.\n"
    )
    summary_path = wiki.path / "summaries" / "test-article.md"
    summary_path.write_text(
        "---\ntitle: Test\ntype: summary\ncreated: 2024-01-01\nupdated: 2024-01-01\ntags: []\nsources: []\n---\n\n"
        "See [[GTBank]] for details.\n"
    )
    state = base_state.model_copy(
        update={
            "pages_written": [{"path": str(summary_path), "is_new": True}],
        }
    )

    result = create_stub_pages(state, _make_config())  # type: ignore
    assert result["pages_written"] == []
