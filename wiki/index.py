"""
Wiki index management.

Owns index.md — the catalogue of every page in the wiki.

Responsibilities:
  - Parse the current index.md into a structured form
  - Reconcile entries against pages actually on disk (removes stale entries)
  - Add entries for newly written pages (one-line description per page)
  - Write the updated index back to disk

index.md format (task-specified):
  # Wiki Index

  ## Entities
  - [[GTBank]] — Nigerian commercial bank; Q3 2024 earnings covered

  ## Topics
  - [[Nigerian Banking Sector]] — overview of NGX banking landscape

  ## Summaries
  - [[GTBank Q3 2024 Earnings]] — ingested 2024-11-15
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

# Maps page type → index section header
SECTION_FOR_TYPE = {
    "entity": "Entities",
    "topic": "Topics",
    "summary": "Summaries",
}

ENTRY_PATTERN = re.compile(r"- \[\[(.+?)\]\] — (.+)")


@dataclass
class IndexEntry:
    title: str
    description: str
    page_type: str  # entity | topic | summary
    page_path: Path


@dataclass
class WikiIndex:
    wiki_path: Path
    entries: list[IndexEntry] = field(default_factory=list)

    @property
    def index_path(self) -> Path:
        return self.wiki_path / "index.md"

    def by_type(self, page_type: str) -> list[IndexEntry]:
        return [e for e in self.entries if e.page_type == page_type]


def read_index(wiki_path: Path) -> WikiIndex:
    """
    Parse index.md into a WikiIndex.

    Unknown or malformed lines are silently skipped — the index is
    rebuilt from disk on every ingestion so stale lines self-correct.
    """
    index = WikiIndex(wiki_path=wiki_path)
    index_path = wiki_path / "index.md"

    if not index_path.exists():
        return index

    current_type: str | None = None
    type_for_section = {v: k for k, v in SECTION_FOR_TYPE.items()}

    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        # Detect section headers — ## Entities, ## Topics, ## Summaries
        if line.startswith("## "):
            section = line[3:]
            current_type = type_for_section.get(section)
            continue

        match = ENTRY_PATTERN.match(line)
        if match and current_type:
            title, description = match.group(1), match.group(2)
            slug = _slugify(title)
            directory = _dir_for_type(current_type)
            page_path = wiki_path / directory / f"{slug}.md"

            index.entries.append(
                IndexEntry(
                    title=title,
                    description=description,
                    page_type=current_type,
                    page_path=page_path,
                )
            )

    return index


def write_index(index: WikiIndex) -> None:
    """
    Serialise a WikiIndex back to index.md.

    Reconciles against disk first — entries whose page_path no longer
    exists are dropped. This handles manually deleted pages automatically.
    """
    # Drop stale entries (page deleted from disk)
    live_entries = [e for e in index.entries if e.page_path.exists()]

    lines = [f"# Wiki Index: {index.wiki_path.name}\n"]

    for page_type, section_header in SECTION_FOR_TYPE.items():
        section_entries = [e for e in live_entries if e.page_type == page_type]
        lines.append(f"\n## {section_header}\n")
        for entry in sorted(section_entries, key=lambda e: e.title):
            lines.append(f"- [[{entry.title}]] — {entry.description}")

    index.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def upsert_entries(index: WikiIndex, new_entries: list[IndexEntry]) -> WikiIndex:
    """
    Add or update entries in the index.

    If an entry with the same title already exists, its description
    is updated. Otherwise the entry is appended.
    """
    existing_by_title = {e.title: i for i, e in enumerate(index.entries)}

    for new_entry in new_entries:
        if new_entry.title in existing_by_title:
            index.entries[existing_by_title[new_entry.title]] = new_entry
        else:
            index.entries.append(new_entry)

    return index


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _dir_for_type(page_type: str) -> str:
    return {"entity": "entities", "topic": "topics", "summary": "summaries"}[page_type]
