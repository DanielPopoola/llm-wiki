"""
Wiki schema management.

Responsible for scaffolding a new wiki's directory structure and writing
SCHEMA.md — the conventions file the agent reads to stay consistent.

A directory is a valid wiki if it contains SCHEMA.md. This is the canonical
marker used by list_wikis() to distinguish wikis from arbitrary folders.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

WIKI_DIRS = ["summaries", "entities", "topics", "raw"]

SCHEMA_TEMPLATE = """\
# Wiki Schema: {name}

## Directories

- `summaries/` — one page per ingested source document
- `entities/`  — pages for people, companies, and organisations
- `topics/`    — overview pages connecting related concepts
- `raw/`       — original source files, never modified by the agent

## Frontmatter Fields

Every page (except index.md and log.md) must carry this YAML frontmatter:

```yaml
---
title: ""
type: summary | entity | topic
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: []
sources: []
---
```

## Cross-Reference Convention

Pages reference each other using wikilinks: [[Entity Name]]

The agent resolves a wikilink [[Foo Bar]] to the file at entities/foo-bar.md
or topics/foo-bar.md, searching entities/ first.

## Naming Convention

File names are lowercase, hyphen-separated versions of the page title.
Example: "GTBank Q3 2024 Earnings" → summaries/gtbank-q3-2024-earnings.md

## Created

{created}
"""

INDEX_TEMPLATE = """\
# Wiki Index: {name}

## Entities

## Topics

## Summaries
"""

LOG_TEMPLATE = """\
# Wiki Log: {name}

<!-- Format: ## [YYYY-MM-DD] type | Description -->
<!-- Grep-parseable: grep "^## \\[" log.md | tail -5 -->
"""


@dataclass
class WikiSchema:
    name: str
    path: Path

    @property
    def is_valid(self) -> bool:
        return (self.path / "SCHEMA.md").exists()

    @property
    def page_counts(self) -> dict[str, int]:
        """Count of markdown pages per content directory."""
        return {d: len(list((self.path / d).glob("*.md"))) for d in WIKI_DIRS if (self.path / d).exists()}


def create_wiki(name: str, wikis_dir: Path) -> WikiSchema:
    """
    Scaffold a new wiki project on disk.

    Creates the directory structure, SCHEMA.md, index.md, log.md,
    and an empty log.ndjson for crash recovery.

    Args:
        name: The wiki project name (used as directory name).
        wikis_dir: Parent directory where all wikis live.

    Returns:
        WikiSchema for the newly created wiki.

    Raises:
        FileExistsError: If a wiki with this name already exists.
    """
    wiki_path = wikis_dir / name

    if wiki_path.exists():
        raise FileExistsError(f"Wiki {name} already exists at {wiki_path}")

    for d in WIKI_DIRS:
        (wiki_path / d).mkdir(parents=True)

    (wiki_path / "SCHEMA.md").write_text(SCHEMA_TEMPLATE.format(name=name, create=date.today().isoformat()))

    (wiki_path / "index.md").write_text(INDEX_TEMPLATE.format(name=name))
    (wiki_path / "log.md").write_text(LOG_TEMPLATE.format(name=name))

    (wiki_path / "log.ndjson").touch()

    return WikiSchema(name=name, path=wiki_path)


def list_wikis(wikis_dir: Path) -> list[WikiSchema]:
    """
    Return all valid wiki projects found in wikis_dir.

    A directory is a valid wiki if it contains SCHEMA.md.

    Args:
        wikis_dir: Parent directory where all wikis live.

    Returns:
        List of WikiSchema objects, one per valid wiki found.
    """
    if not wikis_dir.exists():
        return []

    return [
        WikiSchema(name=d.name, path=d)
        for d in sorted(wikis_dir.iterdir())
        if d.is_dir() and (d / "SCHEMA.md").exists()
    ]


def inspect_wiki(name: str, wikis_dir: Path) -> WikiSchema:
    """
    Load a WikiSchema for an existing wiki.

    Args:
        name: The wiki project name.
        wikis_dir: Parent directory where all wikis live.

    Returns:
        WikiSchema with page counts populated.

    Raises:
        FileNotFoundError: If no valid wiki with this name exists.
    """
    wiki = WikiSchema(name=name, path=wikis_dir / name)

    if not wiki.is_valid:
        raise FileNotFoundError(f"No valid wiki '{name}' found in {wikis_dir}")

    return wiki
