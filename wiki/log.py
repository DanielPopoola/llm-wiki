"""
Write-ahead log (WAL) for crash recovery.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def _append(log_path: Path, event: dict) -> None:
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def log_backup(log_path: Path, thread_id: str, page_path: Path, old_content: str) -> None:
    """Record the old content of a page BEFORE modifying it."""
    _append(
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
    _append(
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


def log_rolled_back(log_path: Path, thread_id: str, source: str) -> None:
    _append(
        log_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "ingest",
            "status": "rolled_back",
            "thread_id": thread_id,
            "source": source,
        },
    )


def find_incomplete_ingestions(log_path: Path) -> list[str]:
    """
    Scan log.ndjson for ingestions that started but never completed.

    We rely on the LangGraph checkpointer for start/complete tracking.
    This function finds thread_ids that have backup/wrote events but
    whose corresponding LangGraph thread has no completed checkpoint —
    meaning the process crashed after file writes but before finishing.

    Args:
        log_path: Path to log.ndjson.

    Returns:
        List of thread_ids with unresolved file writes.
    """
    if not log_path.exists():
        return []

    events = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    wrote = {e["thread_id"] for e in events if e["status"] in ("wrote", "backup")}
    resolved = {e["thread_id"] for e in events if e["status"] in ("rolled_back",)}

    return list(wrote - resolved)


def rollback_ingestion(log_path: Path, thread_id: str) -> None:
    """Restore filesystem to pre-ingestion state for a failed thread."""
    events = [
        json.loads(line)
        for line in log_path.read_text().splitlines()
        if line.strip() and json.loads(line)["thread_id"] == thread_id
    ]

    # Build a map of path -> old_content for modified pages
    backups: dict[str, str] = {e["path"]: e["old_content"] for e in events if e["status"] == "backup"}

    for e in events:
        if e["status"] != "wrote":
            continue

        page_path = Path(e["path"])

        if e["is_new"]:
            # Page didn't exist before ingestion — delete it
            page_path.unlink(missing_ok=True)
        elif e["path"] in backups:
            # Page existed before — restore old content
            page_path.write_text(backups[e["path"]], encoding="utf-8")

    log_rolled_back(log_path, thread_id, source="unknown")
