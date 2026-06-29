"""
Tests for workflows/lint.py.

Uses committed test fixtures for the contradiction test — a deliberate
pair of pages with conflicting claims, as required by WIKI-009 definition of done.

LLM passed via RunnableConfig. All checks that don't need LLM are tested
without one.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wiki.schema import create_wiki
from workflows.lint import (
    Finding,
    LintContradictionResult,
    LintState,
    ResearchSuggestion,
    StaleClaimsResult,
    apply_confirmed_fixes,
    check_contradictions,
    find_broken_links,
    find_orphan_pages,
    identify_gaps,
    run_lint,
    walk_pages,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki(tmp_path):
    return create_wiki("test-wiki", wikis_dir=tmp_path)


def _make_config(llm=None, auto=False, confirm_fn=None) -> dict:
    return {
        "configurable": {
            "thread_id": "lint-test",
            "llm": llm,
            "auto": auto,
            "confirm_fn": confirm_fn or (lambda _: False),
        }
    }


def _write_page(path: Path, title: str, page_type: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: "{title}"\ntype: {page_type}\ncreated: 2024-01-01\n'
        f"updated: 2024-01-01\ntags: []\nsources: []\n---\n\n{body}"
    )


# ---------------------------------------------------------------------------
# Committed test fixtures — deliberate contradiction
# These exist so the contradiction test has a stable, reviewable fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
def contradiction_wiki(wiki):
    """
    Two entity pages making incompatible claims about GTBank's loan growth.
    Page A says 12%, Page B says 5% — same metric, same period.
    """
    _write_page(
        wiki.path / "entities" / "gtbank.md",
        title="GTBank",
        page_type="entity",
        body="GTBank recorded loan growth of 12% in Q3 2024. See also [[Nigerian Banking Sector]].",
    )
    _write_page(
        wiki.path / "summaries" / "gtbank-q3-analysis.md",
        title="GTBank Q3 Analysis",
        page_type="summary",
        body="GTBank loan growth was only 5% in Q3 2024, below expectations. See also [[Nigerian Banking Sector]].",
    )
    _write_page(
        wiki.path / "entities" / "nigerian-banking-sector.md",
        title="Nigerian Banking Sector",
        page_type="entity",
        body="The Nigerian banking sector showed mixed results in Q3 2024.",
    )
    return wiki


# ---------------------------------------------------------------------------
# Node 1: walk_pages
# ---------------------------------------------------------------------------


def test_walk_pages_loads_all_content_pages(wiki):
    _write_page(wiki.path / "entities" / "gtbank.md", "GTBank", "entity", "Content.")
    _write_page(wiki.path / "topics" / "banking.md", "Banking", "topic", "Content.")

    state = LintState(wiki_path=wiki.path, project="test")
    result = walk_pages(state, _make_config())  # type : ignore

    assert len(result["all_pages"]) == 2


def test_walk_pages_excludes_index_and_log(wiki):
    _write_page(wiki.path / "entities" / "gtbank.md", "GTBank", "entity", "Content.")
    # index.md and log.md are already created by create_wiki

    state = LintState(wiki_path=wiki.path, project="test")
    result = walk_pages(state, _make_config())  # type : ignore

    paths = [p["path"] for p in result["all_pages"]]
    assert not any("index.md" in p or "log.md" in p for p in paths)


def test_walk_pages_extracts_wikilinks(wiki):
    _write_page(
        wiki.path / "entities" / "gtbank.md",
        "GTBank",
        "entity",
        "GTBank is related to [[Segun Agbaje]] and [[Nigerian Banking Sector]].",
    )

    state = LintState(wiki_path=wiki.path, project="test")
    result = walk_pages(state, _make_config())  # type : ignore

    page = result["all_pages"][0]
    assert "Segun Agbaje" in page["wikilinks"]
    assert "Nigerian Banking Sector" in page["wikilinks"]


# ---------------------------------------------------------------------------
# Node 2: check_contradictions (committed fixture)
# ---------------------------------------------------------------------------


def test_check_contradictions_detects_deliberate_conflict(contradiction_wiki):
    """
    Uses committed contradiction fixture: GTBank loan growth 12% vs 5%.
    The LLM is mocked to return a contradiction — the fixture provides
    the realistic page content that would trigger one in production.
    """
    contradiction_result = LintContradictionResult(
        has_contradictions=True,
        contradictions=[
            {
                "existing_claim": "GTBank recorded loan growth of 12% in Q3 2024",
                "conflicting_claim": "GTBank loan growth was only 5% in Q3 2024",
                "explanation": "Two different figures for the same metric and period",
            }
        ],
    )
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = contradiction_result

    state = LintState(wiki_path=contradiction_wiki.path, project="test")
    walk_result = walk_pages(state, _make_config())  # type : ignore
    state = state.model_copy(update=walk_result)

    result = check_contradictions(state, _make_config(llm=llm))  # type : ignore

    assert len(result["findings"]) >= 1
    finding = result["findings"][0]
    assert finding.severity == "critical"
    assert finding.finding_type == "contradiction"
    assert len(finding.pages) == 2


def test_check_contradictions_no_false_positives(wiki):
    """Pages that don't share entity links are never compared."""
    _write_page(wiki.path / "entities" / "gtbank.md", "GTBank", "entity", "GTBank content with [[Segun Agbaje]].")
    _write_page(
        wiki.path / "entities" / "access-bank.md",
        "Access Bank",
        "entity",
        "Access Bank content with [[Herbert Wigwe]].",
    )

    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = LintContradictionResult(
        has_contradictions=False, contradictions=[]
    )

    state = LintState(wiki_path=wiki.path, project="test")
    walk_result = walk_pages(state, _make_config())  # type : ignore
    state = state.model_copy(update=walk_result)

    result = check_contradictions(state, _make_config(llm=llm))  # type : ignore

    # Pages share no entity links so LLM should never be called
    llm.with_structured_output.return_value.invoke.assert_not_called()
    assert result["findings"] == []


# ---------------------------------------------------------------------------
# Node 4: find_orphan_pages
# ---------------------------------------------------------------------------


def test_find_orphan_pages_flags_unlinked_page(wiki):
    _write_page(
        wiki.path / "entities" / "gtbank.md", "GTBank", "entity", "GTBank content."
    )  # no other page links to this
    _write_page(wiki.path / "topics" / "banking.md", "Banking", "topic", "Banking overview. Links to [[GTBank]].")

    state = LintState(wiki_path=wiki.path, project="test")
    walk_result = walk_pages(state, _make_config())  # type : ignore
    state = state.model_copy(update=walk_result)

    result = find_orphan_pages(state, _make_config())  # type: ignore

    # banking.md is linked by no one → orphan
    # gtbank.md is linked by banking.md → not an orphan
    orphan_paths = [f.pages[0] for f in result["findings"]]
    assert any("banking.md" in p for p in orphan_paths)
    assert not any("gtbank.md" in p for p in orphan_paths)


# ---------------------------------------------------------------------------
# Node 5: find_broken_links
# ---------------------------------------------------------------------------


def test_find_broken_links_detects_missing_target(wiki):
    _write_page(wiki.path / "entities" / "gtbank.md", "GTBank", "entity", "See [[Nonexistent Page]] for details.")

    state = LintState(wiki_path=wiki.path, project="test")
    walk_result = walk_pages(state, _make_config())  # type : ignore
    state = state.model_copy(update=walk_result)

    result = find_broken_links(state, _make_config())  # type: ignore

    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding.severity == "warning"
    assert finding.finding_type == "broken_link"
    assert "Nonexistent Page" in finding.description


def test_find_broken_links_ignores_valid_links(wiki):
    _write_page(wiki.path / "entities" / "gtbank.md", "GTBank", "entity", "See [[Nigerian Banking Sector]].")
    _write_page(wiki.path / "topics" / "nigerian-banking-sector.md", "Nigerian Banking Sector", "topic", "Overview.")

    state = LintState(wiki_path=wiki.path, project="test")
    walk_result = walk_pages(state, _make_config())  # type : ignore
    state = state.model_copy(update=walk_result)

    result = find_broken_links(state, _make_config())  # type: ignore

    assert result["findings"] == []


# ---------------------------------------------------------------------------
# Node 6: identify_gaps
# ---------------------------------------------------------------------------


def test_identify_gaps_flags_concept_without_own_page(wiki):
    # Two pages both reference [[Digital Transformation]] but no page exists for it
    _write_page(
        wiki.path / "entities" / "gtbank.md",
        "GTBank",
        "entity",
        "GTBank's [[Digital Transformation]] strategy drove growth.",
    )
    _write_page(
        wiki.path / "summaries" / "gtbank-q3.md",
        "GTBank Q3",
        "summary",
        "[[Digital Transformation]] was key to GTBank's results.",
    )

    state = LintState(wiki_path=wiki.path, project="test")
    walk_result = walk_pages(state, _make_config())  # type : ignore
    state = state.model_copy(update=walk_result)

    result = identify_gaps(state, _make_config())  # type: ignore

    assert len(result["findings"]) >= 1
    finding = next(f for f in result["findings"] if "Digital Transformation" in f.description)
    assert finding.severity == "suggestion"
    assert finding.finding_type == "gap"
    assert len(finding.pages) == 2


def test_identify_gaps_ignores_single_reference(wiki):
    """A concept referenced only once isn't a gap — might just be context."""
    _write_page(
        wiki.path / "entities" / "gtbank.md", "GTBank", "entity", "GTBank uses [[some obscure concept]] occasionally."
    )

    state = LintState(wiki_path=wiki.path, project="test")
    walk_result = walk_pages(state, _make_config())  # type : ignore
    state = state.model_copy(update=walk_result)

    result = identify_gaps(state, _make_config())  # type: ignore

    gap_findings = [f for f in result["findings"] if f.finding_type == "gap"]
    assert gap_findings == []


# ---------------------------------------------------------------------------
# Node 9: apply_confirmed_fixes
# ---------------------------------------------------------------------------


def test_apply_confirmed_fixes_calls_fix_when_accepted(wiki):
    fix_called = []
    finding = Finding(
        severity="warning",
        finding_type="broken_link",
        description="Broken link test",
        pages=[],
        fix_description="Create stub page",
        fix=lambda: fix_called.append(True),
    )

    state = LintState(wiki_path=wiki.path, project="test")
    state = state.model_copy(update={"findings": [finding]})

    apply_confirmed_fixes(state, _make_config(confirm_fn=lambda _: True))  # type: ignore

    assert len(fix_called) == 1


def test_apply_confirmed_fixes_skips_fix_when_rejected(wiki):
    fix_called = []
    finding = Finding(
        severity="warning",
        finding_type="broken_link",
        description="Broken link test",
        pages=[],
        fix_description="Create stub page",
        fix=lambda: fix_called.append(True),
    )

    state = LintState(wiki_path=wiki.path, project="test")
    state = state.model_copy(update={"findings": [finding]})

    apply_confirmed_fixes(state, _make_config(confirm_fn=lambda _: False))  # type: ignore

    assert len(fix_called) == 0


def test_apply_confirmed_fixes_auto_mode_applies_all(wiki):
    fix_called = []
    findings = [
        Finding(
            severity="warning",
            finding_type="broken_link",
            description=f"Finding {i}",
            pages=[],
            fix_description="Fix",
            fix=lambda: fix_called.append(True),
        )
        for i in range(3)
    ]

    state = LintState(wiki_path=wiki.path, project="test")
    state = state.model_copy(update={"findings": findings})

    apply_confirmed_fixes(state, _make_config(auto=True))  # type: ignore

    assert len(fix_called) == 3


def test_apply_confirmed_fixes_skips_findings_without_fix(wiki):
    """Findings with no fix callable are reported but never cause errors."""
    finding = Finding(
        severity="critical",
        finding_type="contradiction",
        description="Manual review needed",
        pages=["a.md", "b.md"],
        fix_description="Review manually",
        fix=None,
    )

    state = LintState(wiki_path=wiki.path, project="test")
    state = state.model_copy(update={"findings": [finding]})

    # Should not raise even with confirm_fn=always True
    apply_confirmed_fixes(state, _make_config(confirm_fn=lambda _: True, auto=True))  # type: ignore


# ---------------------------------------------------------------------------
# Log entry
# ---------------------------------------------------------------------------


def test_lint_appends_to_log(wiki):
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.side_effect = [
        LintContradictionResult(has_contradictions=False, contradictions=[]),
        StaleClaimsResult(has_stale_claims=False, stale_claims=[]),
        ResearchSuggestion(questions=[], source_types=[]),
    ]

    run_lint(wiki_path=wiki.path, project="test", llm=llm)

    log_content = (wiki.path / "log.md").read_text()
    assert "lint" in log_content
    assert "COMPLETED" in log_content
