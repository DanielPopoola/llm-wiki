"""
Tests for wiki/pages.py.

Covers frontmatter parsing and wikilink resolution as required by WIKI-002
definition of done. Uses tmp_path (pytest built-in) — no real disk state.
"""

import pytest

from wiki.pages import (
    make_frontmatter,
    read_page,
    resolve_wikilink,
    write_page,
)

# --- Fixtures ---

VALID_PAGE = """\
---
title: "GTBank Q3 2024 Earnings"
type: summary
created: 2024-11-15
updated: 2024-11-15
tags: [banking, earnings]
sources: [raw/gtbank-q3-2024.md]
---

GTBank reported loan growth of 12% in Q3 2024. See also [[GTBank]] and [[Nigerian Banking Sector]].
"""


# --- Frontmatter parsing ---


def test_read_page_parses_frontmatter(tmp_path):
    page_file = tmp_path / "test-page.md"
    page_file.write_text(VALID_PAGE)

    page = read_page(page_file)

    assert page.frontmatter.title == "GTBank Q3 2024 Earnings"
    assert page.frontmatter.type == "summary"
    assert page.frontmatter.created == "2024-11-15"
    assert page.frontmatter.tags == ["banking", "earnings"]
    assert page.frontmatter.sources == ["raw/gtbank-q3-2024.md"]


def test_read_page_parses_body(tmp_path):
    page_file = tmp_path / "test-page.md"
    page_file.write_text(VALID_PAGE)

    page = read_page(page_file)

    assert "loan growth of 12%" in page.body


def test_read_page_raises_on_missing_frontmatter(tmp_path):
    page_file = tmp_path / "no-frontmatter.md"
    page_file.write_text("Just some content with no frontmatter.")

    with pytest.raises(ValueError, match="No frontmatter"):
        read_page(page_file)


def test_read_page_raises_if_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_page(tmp_path / "does-not-exist.md")


def test_write_then_read_roundtrip(tmp_path):
    fm = make_frontmatter("Test Entity", "entity", tags=["test"])
    path = tmp_path / "entities" / "test-entity.md"

    write_page(path, fm, body="This is the entity body.")
    page = read_page(path)

    assert page.frontmatter.title == "Test Entity"
    assert page.frontmatter.type == "entity"
    assert page.frontmatter.tags == ["test"]
    assert "entity body" in page.body


# --- Wikilink extraction ---


def test_wikilinks_extracted_from_body(tmp_path):
    page_file = tmp_path / "test-page.md"
    page_file.write_text(VALID_PAGE)

    page = read_page(page_file)

    assert "GTBank" in page.wikilinks
    assert "Nigerian Banking Sector" in page.wikilinks


# --- Wikilink resolution ---


def test_resolve_wikilink_finds_entity(tmp_path):
    entity_file = tmp_path / "entities" / "gtbank.md"
    entity_file.parent.mkdir(parents=True)
    entity_file.touch()

    resolved = resolve_wikilink("GTBank", wiki_path=tmp_path)

    assert resolved == entity_file


def test_resolve_wikilink_finds_topic_when_no_entity(tmp_path):
    topic_file = tmp_path / "topics" / "nigerian-banking-sector.md"
    topic_file.parent.mkdir(parents=True)
    topic_file.touch()

    resolved = resolve_wikilink("Nigerian Banking Sector", wiki_path=tmp_path)

    assert resolved == topic_file


def test_resolve_wikilink_returns_none_when_missing(tmp_path):
    (tmp_path / "entities").mkdir()
    (tmp_path / "topics").mkdir()
    (tmp_path / "summaries").mkdir()

    resolved = resolve_wikilink("Nonexistent Page", wiki_path=tmp_path)

    assert resolved is None


def test_resolve_wikilink_prefers_entity_over_topic(tmp_path):
    # Same slug exists in both entities/ and topics/ — entities/ wins
    slug = "gtbank"
    for d in ("entities", "topics"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / d / f"{slug}.md").touch()

    resolved = resolve_wikilink("GTBank", wiki_path=tmp_path)

    assert resolved == tmp_path / "entities" / "gtbank.md"
