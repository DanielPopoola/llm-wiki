"""
Log management for LLM Wiki.

Two distinct log files, two distinct purposes:

log.ndjson — internal write-ahead log (WAL) for crash recovery.
  Append-only NDJSON. One event per line. Written before/after every
  filesystem side effect during ingestion. Not for human consumption.

  Event types:
    started     — ingestion began (written before graph runs)
    backup      — old content saved BEFORE modifying an existing page
    wrote       — page written to disk (new or modified)
    completed   — ingestion finished successfully
    rolled_back — failed ingestion cleaned up

log.md — human-readable chronological operations record (task-specified).
  Format: ## [YYYY-MM-DD] type | Description
  Grep-parseable: grep "^## [" log.md | tail -5

The LangGraph checkpointer tracks which nodes completed. The WAL tracks
which files changed so we can restore the filesystem on rollback.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path


def _append_ndjson(log_path: Path, event: dict) -> None:
    """Append a single JSON event to log.ndjson."""
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def log_started(log_path: Path, thread_id: str, source: str) -> None:
    _append_ndjson(
        log_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "ingest",
            "status": "started",
            "thread_id": thread_id,
            "source": source,
        },
    )


def log_backup(log_path: Path, thread_id: str, page_path: Path, old_content: str) -> None:
    _append_ndjson(
        log_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "ingest",
            "status": "backup",
            "thread_id": thread_id,
            "path": str(page_path),
            "old_content": old_content,
        },
    )


def log_wrote(log_path: Path, thread_id: str, page_path: Path, is_new: bool) -> None:
    _append_ndjson(
        log_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "ingest",
            "status": "wrote",
            "thread_id": thread_id,
            "path": str(page_path),
            "is_new": is_new,
        },
    )


def log_completed(log_path: Path, thread_id: str, source: str, pages_written: int) -> None:
    _append_ndjson(
        log_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "ingest",
            "status": "completed",
            "thread_id": thread_id,
            "source": source,
            "pages_written": pages_written,
        },
    )


def log_rolled_back(log_path: Path, thread_id: str, source: str) -> None:
    _append_ndjson(
        log_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "ingest",
            "status": "rolled_back",
            "thread_id": thread_id,
            "source": source,
        },
    )


def append_log_md(log_path: Path, event_type: str, description: str) -> None:
    today = date.today().isoformat()
    entry = f"\n## [{today}] {event_type} | {description}"

    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


def find_incomplete_ingestions(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []

    events = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    started = {e["thread_id"] for e in events if e["status"] == "started"}
    completed = {e["thread_id"] for e in events if e["status"] == "completed"}
    rolled_back = {e["thread_id"] for e in events if e["status"] == "rolled_back"}

    return list(started - completed - rolled_back)


def rollback_ingestion(log_path: Path, thread_id: str) -> None:
    events = [
        json.loads(line)
        for line in log_path.read_text().splitlines()
        if line.strip() and json.loads(line).get("thread_id") == thread_id
    ]

    backups: dict[str, str] = {e["path"]: e["old_content"] for e in events if e["status"] == "backup"}

    for e in events:
        if e["status"] != "wrote":
            continue

        page_path = Path(e["path"])

        if e["is_new"]:
            page_path.unlink(missing_ok=True)
        elif e["path"] in backups:
            page_path.write_text(backups[e["path"]], encoding="utf-8")

    log_rolled_back(log_path, thread_id, source="unknown")
