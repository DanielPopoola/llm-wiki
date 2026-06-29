"""
Wiki page storage repository.

All Oracle SQL lives here. Nothing outside this module writes SQL or
touches a cursor directly.

The DatabaseConnection is injected at call time — never constructed here.
This keeps every method independently testable with a mock connection.
"""

import array
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from infrastructure.db import DatabaseConnection


@dataclass
class PageSearchResult:
    page_path: str
    title: str
    page_type: str
    tags: list[str]
    snippet: str
    score: float


def upsert_page(
    db: DatabaseConnection,
    project: str,
    page_path: Path,
    title: str,
    page_type: str,
    tags: list[str],
    snippet: str,
    embedding: list[float],
) -> bool:
    """
    Insert or update a page's embedding and metadata in wiki_pages.

    Uses content hash to detect whether the page actually changed.
    If the hash matches what's already stored, skips the upsert and
    returns False — the embedding is already current.

    Args:
        db: Injected database connection.
        project: Wiki project name (for isolation between projects).
        page_path: Path to the page file on disk.
        title: Page title from frontmatter.
        page_type: One of: entity, topic, summary.
        tags: List of tags from frontmatter.
        snippet: Text that was embedded (title + first 400 tokens).
        embedding: 768-dimensional vector.

    Returns:
        True if the page was upserted, False if unchanged (hash match).
    """
    content_hash = _hash_text(snippet)
    tags_str = json.dumps(tags)
    path_str = str(page_path)

    with db.cursor() as cursor:
        # Check if page exists and hash matches
        cursor.execute(
            """
            SELECT content_hash FROM wiki_pages
            WHERE project = :project AND page_path = :page_path
            """,
            project=project,
            page_path=path_str,
        )
        row = cursor.fetchone()

        if row and row[0] == content_hash:
            return False  # unchanged — skip re-embedding

        embedding_array = array.array("f", embedding)

        if row:
            # Update existing row
            cursor.execute(
                """
                UPDATE wiki_pages SET
                    title = :title,
                    page_type = :page_type,
                    tags = :tags,
                    content_hash = :content_hash,
                    snippet = :snippet,
                    embedding = :embedding,
                    updated_at = :updated_at
                WHERE project = :project AND page_path = :page_path
                """,
                title=title,
                page_type=page_type,
                tags=tags_str,
                content_hash=content_hash,
                snippet=snippet,
                embedding=embedding_array,
                updated_at=datetime.now(timezone.utc),
                project=project,
                page_path=path_str,
            )
        else:
            # Insert new row
            cursor.execute(
                """
                INSERT INTO wiki_pages
                    (project, page_path, title, page_type, tags,
                     content_hash, snippet, embedding, updated_at)
                VALUES
                    (:project, :page_path, :title, :page_type, :tags,
                     :content_hash, :snippet, :embedding, :updated_at)
                """,
                project=project,
                page_path=path_str,
                title=title,
                page_type=page_type,
                tags=tags_str,
                content_hash=content_hash,
                snippet=snippet,
                embedding=embedding_array,
                updated_at=datetime.now(timezone.utc),
            )

    return True


def source_already_ingested(
    db: DatabaseConnection,
    project: str,
    content_hash: str,
) -> bool:
    """
    Check whether a source with this hash has already been ingested.

    Replaces the local JSON cache used in Phase 1.

    Args:
        db: Injected database connection.
        project: Wiki project name.
        content_hash: SHA-256 hash of the source document content.

    Returns:
        True if the source was previously ingested successfully.
    """
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM wiki_sources
            WHERE project = :project
              AND content_hash = :content_hash
              AND status = 'completed'
            """,
            project=project,
            content_hash=content_hash,
        )
        return cursor.fetchone() is not None


def record_source(
    db: DatabaseConnection,
    project: str,
    source_path: Path,
    content_hash: str,
    title: str,
    status: str = "completed",
) -> None:
    """
    Record a source document in wiki_sources after ingestion.

    Args:
        db: Injected database connection.
        project: Wiki project name.
        source_path: Path to the original source file.
        content_hash: SHA-256 hash of the source content.
        title: Inferred title for the source.
        status: One of: completed, failed, rolled_back.
    """
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wiki_sources
                (project, source_path, content_hash, title, ingested_at, status)
            VALUES
                (:project, :source_path, :content_hash, :title, :ingested_at, :status)
            """,
            project=project,
            source_path=str(source_path),
            content_hash=content_hash,
            title=title,
            ingested_at=datetime.now(timezone.utc),
            status=status,
        )


def search_pages(
    db: DatabaseConnection,
    project: str,
    query_embedding: list[float],
    query_text: str,
    top_k: int = 5,
) -> list[PageSearchResult]:
    """
    Hybrid search: vector similarity + full-text, results merged by rank.

    Vector search finds semantically related pages even when words differ.
    Full-text search catches exact names and technical terms.
    Results are union-merged — a page appearing in both gets higher effective rank.

    Args:
        db: Injected database connection.
        project: Wiki project name (search never crosses project boundaries).
        query_embedding: 768-dim vector of the query text.
        query_text: Raw query string for full-text search.
        top_k: Maximum number of results to return.

    Returns:
        List of PageSearchResult ordered by relevance, best first.
    """
    vector_results = _vector_search(db, project, query_embedding, top_k)
    fulltext_results = _fulltext_search(db, project, query_text, top_k)

    return _merge_results(vector_results, fulltext_results, top_k)


def _vector_search(
    db: DatabaseConnection,
    project: str,
    query_embedding: list[float],
    top_k: int,
) -> list[PageSearchResult]:
    """Cosine similarity search using the vector index."""
    with db.cursor() as cursor:
        embedding_var = array.array("f", query_embedding)

        cursor.execute(
            """
            SELECT page_path, title, page_type, tags, snippet,
                   VECTOR_DISTANCE(embedding, :query_vec, COSINE) AS score
            FROM wiki_pages
            WHERE project = :project
            ORDER BY score ASC
            FETCH FIRST :top_k ROWS ONLY
            """,
            query_vec=embedding_var,
            project=project,
            top_k=top_k,
        )

        return [
            PageSearchResult(
                page_path=row[0],
                title=row[1],
                page_type=row[2],
                tags=json.loads(row[3]) if row[3] else [],
                snippet=row[4],
                score=float(row[5]),
            )
            for row in cursor.fetchall()
        ]


def _fulltext_search(
    db: DatabaseConnection,
    project: str,
    query_text: str,
    top_k: int,
) -> list[PageSearchResult]:
    """Oracle Text full-text search using the CONTEXT index."""
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT page_path, title, page_type, tags, snippet,
                   SCORE(1) AS score
            FROM wiki_pages
            WHERE project = :project
              AND CONTAINS(snippet, :query_text, 1) > 0
            ORDER BY score DESC
            FETCH FIRST :top_k ROWS ONLY
            """,
            project=project,
            query_text=query_text,
            top_k=top_k,
        )

        return [
            PageSearchResult(
                page_path=row[0],
                title=row[1],
                page_type=row[2],
                tags=json.loads(row[3]) if row[3] else [],
                snippet=row[4],
                score=float(row[5]),
            )
            for row in cursor.fetchall()
        ]


def _merge_results(
    vector_results: list[PageSearchResult],
    fulltext_results: list[PageSearchResult],
    top_k: int,
) -> list[PageSearchResult]:
    """
    Merge vector and full-text results by path, deduplicating.

    Pages appearing in both lists are ranked higher — they matched
    both semantic meaning and exact terms.
    """
    seen: dict[str, PageSearchResult] = {}

    # Full-text results first — exact matches are high-confidence
    for result in fulltext_results:
        seen[result.page_path] = result

    # Vector results fill in semantic matches not caught by full-text
    for result in vector_results:
        if result.page_path not in seen:
            seen[result.page_path] = result

    return list(seen.values())[:top_k]


def register_project(
    db: DatabaseConnection,
    name: str,
    wiki_path: Path,
) -> None:
    """
    Insert a project into wiki_projects if it doesn't already exist.

    Called when a new wiki is created via `llm-wiki new`.
    """
    with db.cursor() as cursor:
        cursor.execute(
            """
            MERGE INTO wiki_projects dest
            USING (SELECT :name AS name FROM DUAL) src
            ON (dest.name = src.name)
            WHEN NOT MATCHED THEN
                INSERT (name, wiki_path, created_at, page_count, source_count)
                VALUES (:name, :wiki_path, :created_at, 0, 0)
            """,
            name=name,
            wiki_path=str(wiki_path),
            created_at=datetime.now(timezone.utc),
        )


def update_project_stats(
    db: DatabaseConnection,
    name: str,
    page_count: int,
    source_count_delta: int = 1,
) -> None:
    """Update page count and last ingestion timestamp for a project."""
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE wiki_projects SET
                page_count = :page_count,
                source_count = source_count + :source_count_delta,
                last_ingested = :last_ingested
            WHERE name = :name
            """,
            page_count=page_count,
            source_count_delta=source_count_delta,
            last_ingested=datetime.now(timezone.utc),
            name=name,
        )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class ProjectInfo:
    name: str
    wiki_path: str
    created_at: str
    last_ingested: str | None
    page_count: int
    source_count: int


def get_project(db: DatabaseConnection, name: str) -> ProjectInfo | None:
    """
    Fetch a single project's metadata from wiki_projects.

    Returns None if the project doesn't exist — callers decide how to handle.
    """
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT name, wiki_path, created_at, last_ingested,
                   page_count, source_count
            FROM wiki_projects
            WHERE name = :name
            """,
            name=name,
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return ProjectInfo(
        name=row[0],
        wiki_path=row[1],
        created_at=str(row[2]),
        last_ingested=str(row[3]) if row[3] else None,
        page_count=row[4] or 0,
        source_count=row[5] or 0,
    )


def list_projects(db: DatabaseConnection) -> list[ProjectInfo]:
    """
    Return all projects from wiki_projects ordered by creation date.

    Used by `llm-wiki list` to show project metadata alongside disk info.
    """
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT name, wiki_path, created_at, last_ingested,
                   page_count, source_count
            FROM wiki_projects
            ORDER BY created_at ASC
            """
        )
        rows = cursor.fetchall()

    return [
        ProjectInfo(
            name=row[0],
            wiki_path=row[1],
            created_at=str(row[2]),
            last_ingested=str(row[3]) if row[3] else None,
            page_count=row[4] or 0,
            source_count=row[5] or 0,
        )
        for row in rows
    ]


def select_project(db: DatabaseConnection, name: str) -> ProjectInfo:
    """
    Validate a project exists in Oracle before any operation.

    Called internally by CLI commands after reading the selected project
    from local config. Fails fast with a clear error if not found.

    Raises:
        ValueError: If the project doesn't exist in wiki_projects.
    """
    project = get_project(db, name)
    if project is None:
        raise ValueError(f"Project '{name}' not found in database.\nRun: llm-wiki new {name}")
    return project
