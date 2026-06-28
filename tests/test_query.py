"""
Tests for workflows/query.py.

LLM, db, and confirm_fn are passed via RunnableConfig — no @patch needed.
Uses tmp_path for filesystem isolation.
"""

from unittest.mock import MagicMock

import pytest

from wiki.schema import create_wiki
from workflows.query import AnswerResult, run_query


@pytest.fixture
def wiki(tmp_path):
    return create_wiki("test-wiki", wikis_dir=tmp_path)


@pytest.fixture
def entity_page(wiki):
    path = wiki.path / "entities" / "gtbank.md"
    path.write_text(
        "---\ntitle: GTBank\ntype: entity\ncreated: 2024-11-15\n"
        "updated: 2024-11-15\ntags: [banking]\nsources: []\n---\n\n"
        "GTBank reported loan growth of 12% in Q3 2024.\n"
        "Non-performing loans declined to 3.2%.\n"
    )
    return path


@pytest.fixture
def index_with_gtbank(wiki, entity_page):
    (wiki.path / "index.md").write_text(
        "# Wiki Index\n\n## Entities\n- [[GTBank]] — Nigerian commercial bank\n\n## Topics\n\n## Summaries\n"
    )
    return wiki


def _answer_result(**kwargs) -> AnswerResult:
    defaults = {
        "answer": "GTBank reported loan growth of 12% in Q3 2024. [GTBank]",
        "citations": ["GTBank"],
        "has_gap": False,
        "format_used": "prose",
    }
    return AnswerResult(**{**defaults, **kwargs})  # type: ignore


def _mock_llm_structured(result) -> MagicMock:
    """Mock for .with_structured_output(Model).invoke(prompt)."""
    mock = MagicMock()
    mock.with_structured_output.return_value.invoke.return_value = result
    return mock


def _mock_llm_both(result: AnswerResult, save_text: str = "## Overview\nAnalysis.\n") -> MagicMock:
    """Mock that handles both structured output and plain invoke calls."""
    mock = MagicMock()
    mock.with_structured_output.return_value.invoke.return_value = result
    mock.invoke.return_value = MagicMock(content=save_text)
    return mock


def test_read_index_retrieves_candidates(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(_answer_result())

    result = run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is GTBank's loan growth?",
        llm=llm,
        db=None,
    )

    assert result.answer != ""
    assert len(result.candidate_pages) >= 1


# ---------------------------------------------------------------------------
# Retrieval — hybrid search path (with DB)
# ---------------------------------------------------------------------------


def test_hybrid_search_uses_db(wiki, entity_page):
    llm = _mock_llm_structured(_answer_result())

    db = MagicMock()
    search_result = MagicMock(page_path=str(entity_page))

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "workflows.query.storage.search_pages", return_value=[search_result]
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "workflows.query.generate_embedding", return_value=[0.1] * 768
        ),
    ):
        result = run_query(
            wiki_path=wiki.path,
            project="test",
            question="What is GTBank's loan growth?",
            llm=llm,
            db=db,
        )

    assert result.answer != ""


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


def test_synthesise_answer_returns_citations(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(_answer_result(citations=["GTBank"]))

    result = run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is GTBank's loan growth?",
        llm=llm,
        db=None,
    )

    assert "GTBank" in result.citations


def test_comparison_question_returns_table_format(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(
        _answer_result(
            answer="| Metric | GTBank | Access Bank |\n|---|---|---|\n| Loan growth | 12% | 8% |",
            format_used="table",
        )
    )

    result = run_query(
        wiki_path=wiki.path,
        project="test",
        question="Compare GTBank and Access Bank loan growth",
        llm=llm,
        db=None,
    )

    assert result.format_used == "table"
    assert "|" in result.answer


# ---------------------------------------------------------------------------
# Gap reporting
# ---------------------------------------------------------------------------


def test_gap_reported_when_wiki_doesnt_cover_question(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(
        _answer_result(
            answer="The wiki does not contain information about Zenith Bank.",
            citations=[],
            has_gap=True,
        )
    )

    result = run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is Zenith Bank's revenue?",
        llm=llm,
        db=None,
    )

    assert result.has_gap is True
    assert result.answer != ""


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------


def test_followup_question_receives_prior_history(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(_answer_result(answer="The NPL ratio is 3.2%. [GTBank]"))

    prior_history = [
        {"role": "user", "content": "What is GTBank's loan growth?"},
        {"role": "assistant", "content": "GTBank reported 12% loan growth."},
    ]

    run_query(
        wiki_path=wiki.path,
        project="test",
        question="What about their NPL ratio?",
        llm=llm,
        history=prior_history,
        db=None,
    )

    call_args = llm.with_structured_output.return_value.invoke.call_args
    prompt_text = call_args.args[0]
    assert "loan growth" in prompt_text


def test_history_accumulates_across_turns(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(_answer_result())

    result = run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is GTBank's loan growth?",
        llm=llm,
        db=None,
    )

    assert any(m["role"] == "user" for m in result.history)
    assert any(m["role"] == "assistant" for m in result.history)


# ---------------------------------------------------------------------------
# Save to wiki
# ---------------------------------------------------------------------------


def test_save_to_wiki_creates_page(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_both(_answer_result(citations=["GTBank"]))

    run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is GTBank's loan growth?",
        llm=llm,
        db=None,
        confirm_fn=lambda _: True,
    )

    topic_pages = list((wiki.path / "topics").glob("*.md"))
    assert len(topic_pages) >= 1


def test_save_to_wiki_logs_entry(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_both(_answer_result(citations=["GTBank"]))

    run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is GTBank's loan growth?",
        llm=llm,
        db=None,
        confirm_fn=lambda _: True,
    )

    assert "SAVED" in (wiki.path / "log.md").read_text()


def test_no_save_when_user_declines(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(_answer_result(citations=["GTBank"]))

    run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is GTBank's loan growth?",
        llm=llm,
        db=None,
        confirm_fn=lambda _: False,
    )

    assert list((wiki.path / "topics").glob("*.md")) == []


def test_no_save_offered_for_gap_answers(wiki, index_with_gtbank, entity_page):
    llm = _mock_llm_structured(_answer_result(has_gap=True, citations=[]))
    save_called = []

    run_query(
        wiki_path=wiki.path,
        project="test",
        question="What is Zenith Bank's revenue?",
        llm=llm,
        db=None,
        confirm_fn=lambda q: save_called.append(q) or False,
    )

    assert len(save_called) == 0
