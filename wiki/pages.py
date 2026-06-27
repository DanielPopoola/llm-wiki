"""
Wiki page read/write operations.

Responsible for parsing frontmatter, reading page content, writing pages
to disk, and resolving wikilinks to file paths.

This module knows nothing about directory structure or wiki projects —
it only operates on individual page files given their paths.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]]\]?")

FRONTMATTER_TEMPLATE = """\
---
title: "{title}"
type: {type}
created: {created}
updated: {updated}
tags: {tags}
sources: {sources}
---

"""


@dataclass
class PageFrontmatter:
    title: str
    type: str
    created: str
    updated: str
    tags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class WikiPage:
    path: Path
    frontmatter: PageFrontmatter
    body: str

    @property
    def wikilinks(self) -> list[str]:
        """All [[Link Target]] references found in the page body."""
        return WIKILINK_PATTERN.findall(self.body)


def read_page(path: Path) -> WikiPage:
    """
    Parse a wiki page file into frontmatter and body.

    Args:
        path: Absolute or relative path to the .md file.

    Returns:
        WikiPage with parsed frontmatter and body text.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file has no valid YAML frontmatter block.
    """
    raw = path.read_text(encoding="utf-8")

    if not raw.startswith("---"):
        raise ValueError(f"No frontmatter found in {path}")

    parts = raw.split("---", maxsplit=2)
    if len(parts) < 3:
        raise ValueError(f"Malformed frontmatter in {path}")

    metadata = yaml.safe_load(parts[1])
    body = parts[2].lstrip("\n")

    frontmatter = PageFrontmatter(
        title=metadata.get("title", ""),
        type=metadata.get("type", ""),
        created=str(metadata.get("created", "")),
        updated=str(metadata.get("updated", "")),
        tags=metadata.get("tags") or [],
        sources=metadata.get("sources") or [],
    )

    return WikiPage(path=path, frontmatter=frontmatter, body=body)


def write_page(path: Path, frontmatter: PageFrontmatter, body: str) -> None:
    """
    Write a wiki page to disk with YAML frontmatter.

    Creates parent directories if they don't exist.

    Args:
        path: Destination file path.
        frontmatter: Page metadata to serialise as YAML.
        body: Markdown content to write below the frontmatter.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    header = FRONTMATTER_TEMPLATE.format(
        title=frontmatter.title,
        type=frontmatter.type,
        created=frontmatter.created,
        updated=frontmatter.updated,
        tags=frontmatter.tags,
        sources=frontmatter.sources,
    )

    path.write_text(header + body, encoding="utf-8")


def resolve_wikilink(link_target: str, wiki_path: Path) -> Path | None:
    """
    Resolve a [[Link Target]] to a file path on disk.

    Search order: entities/ first, then topics/, then summaries/.
    Returns None if no matching file exists.

    Args:
        link_target: The text inside [[...]], e.g. "GTBank".
        wiki_path: Root directory of the wiki project.

    Returns:
        Path to the resolved file, or None if not found.
    """
    slug = _slugify(link_target)

    for directory in ("entities", "topics", "summaries"):
        candidate = wiki_path / directory / f"{slug}.md"
        if candidate.exists():
            return candidate

    return None


def make_frontmatter(
    title: str,
    page_type: str,
    tags: list[str] | None = None,
    sources: list[str] | None = None,
) -> PageFrontmatter:
    """
    Convenience constructor for new page frontmatter with today's date.

    Args:
        title: Page title.
        page_type: One of: summary, entity, topic.
        tags: Optional list of tags.
        sources: Optional list of source file references.

    Returns:
        PageFrontmatter with created and updated set to today.
    """
    today = date.today().isoformat()
    return PageFrontmatter(
        title=title,
        type=page_type,
        created=today,
        updated=today,
        tags=tags or [],
        sources=sources or [],
    )


def _slugify(text: str) -> str:
    """Convert a page title to a lowercase hyphen-separated filename."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
