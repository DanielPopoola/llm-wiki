"""
Lint workflow.

A LangGraph state machine that health-checks the wiki for consistency,
broken links, orphans, contradictions, and gaps.

Nodes:
  1. walk_pages          — scan all wiki pages from disk
  2. check_contradictions — compare page pairs sharing entities (LLM)
  3. check_stale_claims  — compare page claims against newer sources (LLM)
  4. find_orphan_pages   — pages with no inbound wikilinks
  5. find_broken_links   — wikilinks that resolve to no existing page
  6. identify_gaps       — concepts mentioned but lacking their own page
  7. suggest_research    — new questions + source types to fill gaps (LLM)
  8. present_findings    — print critical → warnings → suggestions → research
  9. apply_confirmed_fixes — user accepts/rejects each fix (interactive)
  10. append_log          — summary entry to log.md

Interactive by default. auto=True in config skips confirmation.

Runtime dependencies (llm, confirm_fn) passed via RunnableConfig.
State holds only data.
"""

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from pydantic import Field as PydanticField

from wiki.log import append_log_md
from wiki.pages import read_page, resolve_wikilink, write_page
from wiki.prompts import (
    lint_contradiction_prompt,
    lint_stale_claims_prompt,
    lint_suggest_research_prompt,
)

from .utils import get_llm

SEVERITY_ORDER = {"critical": 0, "warning": 1, "suggestion": 2, "research": 3}


@dataclass
class Finding:
    severity: Literal["critical", "warning", "suggestion", "research"]
    finding_type: Literal["contradiction", "stale", "orphan", "broken_link", "gap", "research"]
    description: str
    pages: list[str]  # paths of affected pages
    fix_description: str = ""
    fix: Callable | None = None  # callable that applies the fix if accepted


class LintContradictionResult(BaseModel):
    has_contradictions: bool
    contradictions: list[dict]  # [{existing_claim, conflicting_claim, explanation}]


class StaleClaimsResult(BaseModel):
    has_stale_claims: bool
    stale_claims: list[dict]  # [{page_claim, newer_claim, explanation}]


class ResearchSuggestion(BaseModel):
    questions: list[str]
    source_types: list[str]


def _merge_findings(left: list, right: list) -> list:
    return left + right


class LintState(BaseModel):
    wiki_path: Path
    project: str
    all_pages: list[dict[str, Any]] = PydanticField(default_factory=list)
    findings: Annotated[list[Finding], _merge_findings] = PydanticField(default_factory=list)
    research_questions: list[str] = PydanticField(default_factory=list)
    source_suggestions: list[str] = PydanticField(default_factory=list)


def _get_confirm_fn(config: RunnableConfig) -> Callable[[str], bool]:
    fn = config.get("configurable", {}).get("confirm_fn")
    return fn or (lambda _: False)


def _is_auto(config: RunnableConfig) -> bool:
    return config.get("configurable", {}).get("auto", False)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _all_page_paths(wiki_path: Path) -> list[Path]:
    """Return all markdown pages in content directories (not index/log/schema)."""
    skip = {"index.md", "log.md", "SCHEMA.md"}
    pages = []
    for directory in ("entities", "topics", "summaries"):
        d = wiki_path / directory
        if d.exists():
            pages.extend(p for p in d.glob("*.md") if p.name not in skip)
    return pages


def _build_inbound_map(pages: list[dict]) -> dict[str, list[str]]:
    """
    Build a map of page_path → list of page_paths that link TO it.

    Used to detect orphan pages (no inbound links).
    """
    inbound: dict[str, list[str]] = {p["path"]: [] for p in pages}

    for page in pages:
        for link_target in page["wikilinks"]:
            resolved = resolve_wikilink(link_target, Path(page["path"]).parent.parent)
            if resolved and str(resolved) in inbound:
                inbound[str(resolved)].append(page["path"])

    return inbound


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def walk_pages(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 1: Scan all wiki pages from disk into structured dicts.

    Skips pages with malformed frontmatter — they'll show up as broken
    in other checks.
    """
    pages = []

    for path in _all_page_paths(state.wiki_path):
        try:
            page = read_page(path)
        except (ValueError, KeyError):
            continue

        pages.append(
            {
                "path": str(path),
                "title": page.frontmatter.title,
                "type": page.frontmatter.type,
                "body": page.body,
                "wikilinks": page.wikilinks,
                "sources": page.frontmatter.sources,
            }
        )

    return {"all_pages": pages}


def check_contradictions(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 2: Compare page pairs that share entity wikilinks.

    Only compares pages that reference the same entity — this keeps
    the check O(entities × pages_per_entity) rather than O(n²).
    """
    llm = get_llm(config)
    findings = []

    # Group pages by each entity they reference
    entity_to_pages: dict[str, list[dict]] = {}
    for page in state.all_pages:
        for link in page["wikilinks"]:
            entity_to_pages.setdefault(link, []).append(page)

    # Compare pairs within each entity group
    checked_pairs: set[tuple[str, str]] = set()

    for entity, pages in entity_to_pages.items():
        if len(pages) < 2:
            continue

        for i, page_a in enumerate(pages):
            for page_b in pages[i + 1 :]:
                pair = tuple(sorted([page_a["path"], page_b["path"]]))
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)

                result = LintContradictionResult.model_validate(
                    llm.with_structured_output(LintContradictionResult).invoke(
                        lint_contradiction_prompt(
                            page_a_title=page_a["title"],
                            page_a_content=page_a["body"],
                            page_b_title=page_b["title"],
                            page_b_content=page_b["body"],
                        )
                    )
                )

                if not result.has_contradictions:
                    continue

                for c in result.contradictions:
                    findings.append(
                        Finding(
                            severity="critical",
                            finding_type="contradiction",
                            description=(
                                f"Contradiction between [{page_a['title']}] and [{page_b['title']}] "
                                f"regarding {entity}:\n"
                                f"  • {c.get('existing_claim', '')}\n"
                                f"  • {c.get('conflicting_claim', '')}\n"
                                f"  Note: {c.get('explanation', '')}"
                            ),
                            pages=[page_a["path"], page_b["path"]],
                            fix_description="Review both pages and reconcile the conflicting claims manually.",
                        )
                    )

    return {"findings": findings}


def check_stale_claims(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 3: Compare entity pages against their source summaries.

    If a summary page is newer than the entity page's last update,
    the entity page may have stale claims.
    """
    llm = get_llm(config)
    findings = []

    summaries = {p["path"]: p for p in state.all_pages if p["type"] == "summary"}
    entities = [p for p in state.all_pages if p["type"] == "entity"]

    for entity_page in entities:
        # Find summaries that reference this entity via wikilink
        entity_title = entity_page["title"]
        relevant_summaries = [s for s in summaries.values() if entity_title in s["wikilinks"]]

        for summary in relevant_summaries:
            result = StaleClaimsResult.model_validate(
                llm.with_structured_output(StaleClaimsResult).invoke(
                    lint_stale_claims_prompt(
                        page_title=entity_page["title"],
                        page_content=entity_page["body"],
                        newer_source_content=summary["body"],
                        newer_source_title=summary["title"],
                    )
                )
            )

            if not result.has_stale_claims:
                continue

            for claim in result.stale_claims:
                findings.append(
                    Finding(
                        severity="critical",
                        finding_type="stale",
                        description=(
                            f"Stale claim in [{entity_page['title']}] "
                            f"superseded by [{summary['title']}]:\n"
                            f"  • Old: {claim.get('page_claim', '')}\n"
                            f"  • New: {claim.get('newer_claim', '')}"
                        ),
                        pages=[entity_page["path"], summary["path"]],
                        fix_description=f"Update [{entity_page['title']}] with the newer claim.",
                    )
                )

    return {"findings": findings}


def find_orphan_pages(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 4: Find pages with no inbound wikilinks from other pages.

    Orphans are isolated — no other page links to them. They may be
    stubs that were never integrated or pages that lost their connections.
    """
    findings = []
    inbound_map = _build_inbound_map(state.all_pages)

    for page in state.all_pages:
        inbound = inbound_map.get(page["path"], [])
        if len(inbound) == 0:
            findings.append(
                Finding(
                    severity="warning",
                    finding_type="orphan",
                    description=f"Orphan page: [{page['title']}] has no inbound links from other pages.",
                    pages=[page["path"]],
                    fix_description=f"Link to [{page['title']}] from a related entity or topic page.",
                )
            )

    return {"findings": findings}


def find_broken_links(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 5: Find wikilinks that don't resolve to any existing page.

    A broken link means [[Target]] appears in a page body but no file
    exists for that target in entities/, topics/, or summaries/.
    """
    findings = []

    for page in state.all_pages:
        for link_target in page["wikilinks"]:
            resolved = resolve_wikilink(link_target, state.wiki_path)
            if resolved is None:
                findings.append(
                    Finding(
                        severity="warning",
                        finding_type="broken_link",
                        description=(
                            f"Broken link in [{page['title']}]: [[{link_target}]] does not resolve to any page."
                        ),
                        pages=[page["path"]],
                        fix_description=f"Create a stub page for [[{link_target}]] or remove the link.",
                        fix=lambda p=page["path"], t=link_target: _create_stub(state.wiki_path, t),
                    )
                )

    return {"findings": findings}


def identify_gaps(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 6: Find concepts mentioned across multiple pages but lacking their own page.

    A gap is a wikilink target that appears in 2+ pages but has no
    dedicated page — it's discussed but never properly documented.
    """
    findings = []

    # Count how many pages reference each link target
    link_counts: dict[str, list[str]] = {}
    for page in state.all_pages:
        for link in page["wikilinks"]:
            link_counts.setdefault(link, []).append(page["path"])

    for link_target, referencing_pages in link_counts.items():
        if len(referencing_pages) < 2:
            continue

        resolved = resolve_wikilink(link_target, state.wiki_path)
        if resolved is not None:
            continue  # page exists — not a gap

        findings.append(
            Finding(
                severity="suggestion",
                finding_type="gap",
                description=(
                    f"[[{link_target}]] is referenced in {len(referencing_pages)} pages but has no dedicated page."
                ),
                pages=referencing_pages,
                fix_description=f"Create a dedicated page for [[{link_target}]].",
                fix=lambda t=link_target: _create_stub(state.wiki_path, t),
            )
        )

    return {"findings": findings}


def suggest_research(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 7: Generate research questions and source suggestions from gaps and orphans.

    Turns the lint pass from a bug-finding exercise into a research
    planning tool. Output is suggestions only — never auto-applied.
    """
    llm = get_llm(config)

    gaps = [f.description for f in state.findings if f.finding_type == "gap"]
    orphans = [f.pages[0] for f in state.findings if f.finding_type == "orphan"]

    if not gaps and not orphans:
        return {"research_questions": [], "source_suggestions": []}

    result = ResearchSuggestion.model_validate(
        llm.with_structured_output(ResearchSuggestion).invoke(
            lint_suggest_research_prompt(
                gaps=gaps,
                orphans=orphans,
                wiki_name=state.wiki_path.name,
            )
        )
    )

    return {
        "research_questions": result.questions,
        "source_suggestions": result.source_types,
    }


def present_findings(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 8: Print all findings sorted by severity.

    Critical → Warnings → Suggestions → Research
    No user input here — that's apply_confirmed_fixes.
    """
    sorted_findings = sorted(
        state.findings,
        key=lambda f: SEVERITY_ORDER.get(f.severity, 99),
    )

    if not sorted_findings and not state.research_questions:
        print("\n✅ Wiki is clean — no issues found.\n")
        return {}

    print(f"\n{'─' * 60}")
    print(f"  Lint Results: {state.wiki_path.name}")
    print(f"{'─' * 60}\n")

    current_severity = None
    for i, finding in enumerate(sorted_findings):
        if finding.severity != current_severity:
            current_severity = finding.severity
            label = {
                "critical": "🔴 Critical",
                "warning": "🟡 Warnings",
                "suggestion": "🔵 Suggestions",
                "research": "🔍 Research",
            }.get(finding.severity, finding.severity.title())
            print(f"\n{label}")
            print("─" * 40)

        print(f"\n[{i + 1}] {finding.description}")
        if finding.fix_description:
            print(f"    Fix: {finding.fix_description}")

    if state.research_questions:
        print("\n🔍 Research Questions")
        print("─" * 40)
        for q in state.research_questions:
            print(f"  • {q}")

    if state.source_suggestions:
        print("\n📚 Sources to Look For")
        print("─" * 40)
        for s in state.source_suggestions:
            print(f"  • {s}")

    print(f"\n{'─' * 60}\n")
    return {}


def apply_confirmed_fixes(state: LintState, config: RunnableConfig) -> dict:
    """
    Node 9: Let the user accept or reject each fixable finding.

    Auto mode (config auto=True): applies all fixes without confirmation.
    Interactive mode (default): presents each fix and waits for confirm_fn.

    Findings without a fix callable are reported but skipped.
    """
    auto = _is_auto(config)
    confirm_fn = _get_confirm_fn(config)

    fixable = [f for f in state.findings if f.fix is not None]
    if not fixable:
        return {}

    applied = 0
    rejected = 0

    for finding in fixable:
        if auto:
            should_apply = True
        else:
            prompt = f"\nApply fix: {finding.fix_description}\nAccept? [y/N] "
            should_apply = confirm_fn(prompt)

        if should_apply:
            try:
                finding.fix() if isinstance(finding.fix, Callable) else None
                applied += 1
                print(f"  ✅ Applied: {finding.fix_description}")
            except Exception as e:
                print(f"  ❌ Fix failed: {e}")
        else:
            rejected += 1
            print(f"  ⏭️  Skipped: {finding.fix_description}")

    print(f"\n  {applied} fixes applied, {rejected} skipped.\n")
    return {}


def append_log(state: LintState, config: RunnableConfig) -> dict:
    """Node 10: Append a lint summary entry to log.md."""
    critical = sum(1 for f in state.findings if f.severity == "critical")
    warnings = sum(1 for f in state.findings if f.severity == "warning")
    suggestions = sum(1 for f in state.findings if f.severity == "suggestion")

    append_log_md(
        log_path=state.wiki_path / "log.md",
        event_type="lint",
        description=(f"COMPLETED | {critical} critical, {warnings} warnings, {suggestions} suggestions"),
    )
    return {}


def _create_stub(wiki_path: Path, link_target: str) -> None:
    """Create a minimal stub page for a missing wikilink target."""
    from wiki.pages import make_frontmatter

    slug = _slugify(link_target)
    stub_path = wiki_path / "entities" / f"{slug}.md"

    if stub_path.exists():
        return

    fm = make_frontmatter(title=link_target, page_type="entity")
    body = f"# {link_target}\n\n> **Stub** — auto-created by lint. Add content when a source covers this entity.\n"
    write_page(stub_path, fm, body)


def build_lint_graph() -> StateGraph:
    builder = StateGraph(state_schema=LintState)

    builder.add_node("walk_pages", walk_pages)
    builder.add_node("check_contradictions", check_contradictions)
    builder.add_node("check_stale_claims", check_stale_claims)
    builder.add_node("find_orphan_pages", find_orphan_pages)
    builder.add_node("find_broken_links", find_broken_links)
    builder.add_node("identify_gaps", identify_gaps)
    builder.add_node("suggest_research", suggest_research)
    builder.add_node("present_findings", present_findings)
    builder.add_node("apply_confirmed_fixes", apply_confirmed_fixes)
    builder.add_node("append_log", append_log)

    builder.add_edge(START, "walk_pages")
    builder.add_edge("walk_pages", "check_contradictions")
    builder.add_edge("check_contradictions", "check_stale_claims")
    builder.add_edge("check_stale_claims", "find_orphan_pages")
    builder.add_edge("find_orphan_pages", "find_broken_links")
    builder.add_edge("find_broken_links", "identify_gaps")
    builder.add_edge("identify_gaps", "suggest_research")
    builder.add_edge("suggest_research", "present_findings")
    builder.add_edge("present_findings", "apply_confirmed_fixes")
    builder.add_edge("apply_confirmed_fixes", "append_log")
    builder.add_edge("append_log", END)

    return builder


def run_lint(
    wiki_path: Path,
    project: str,
    llm: Any,
    auto: bool = False,
    confirm_fn: Callable[[str], bool] | None = None,
    thread_id: str | None = None,
) -> LintState:
    """
    Run the lint workflow against a wiki project.

    Args:
        wiki_path: Root directory of the wiki project.
        project: Wiki project name.
        llm: Injected LLM instance.
        auto: If True, apply all fixes without confirmation.
        confirm_fn: Callable[[str], bool] for interactive confirmation.
                    Defaults to always-no.
        thread_id: LangGraph thread ID. Auto-generated if not provided.

    Returns:
        Final LintState with all findings populated.
    """
    thread_id = thread_id or str(uuid.uuid4())
    checkpointer = InMemorySaver()
    graph = build_lint_graph().compile(checkpointer=checkpointer)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": thread_id,
            "llm": llm,
            "auto": auto,
            "confirm_fn": confirm_fn or (lambda _: False),
        }
    }

    initial_state = LintState(
        wiki_path=wiki_path,
        project=project,
    )

    result = graph.invoke(initial_state, config)
    return LintState.model_validate(result)
