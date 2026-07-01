"""
Oracle DB schema setup.

Run once before first use:
    uv run python scripts/setup_db.py


Tables:
    wiki_pages    — page embeddings and metadata
    wiki_projects — project registry
    wiki_sources  — source ingestion history and duplicate detection

Indexes:
    idx_wiki_embedding — vector index, cosine distance
    idx_wiki_fulltext  — Oracle Text full-text index on title + snippet
"""

import sys
from pathlib import Path

# Add project root to path so imports work when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from infrastructure.db import DatabaseConnection

DDL_STATEMENTS = [
    # -----------------------------------------------------------------------
    # wiki_pages — one row per wiki page
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS wiki_pages (
        id           NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        project      VARCHAR2(255)   NOT NULL,
        page_path    VARCHAR2(1000)  NOT NULL,
        title        VARCHAR2(500),
        page_type    VARCHAR2(50),
        tags         VARCHAR2(1000),
        content_hash VARCHAR2(64),
        snippet      VARCHAR2(4000),
        embedding    VECTOR(384),
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project, page_path)
    )
    """,
    # -----------------------------------------------------------------------
    # wiki_projects — project registry
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS wiki_projects (
        id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name            VARCHAR2(255) UNIQUE NOT NULL,
        wiki_path       VARCHAR2(1000) NOT NULL,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_ingested   TIMESTAMP,
        page_count      NUMBER DEFAULT 0,
        source_count    NUMBER DEFAULT 0
    )
    """,
    # -----------------------------------------------------------------------
    # wiki_sources — ingestion history and duplicate detection
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS wiki_sources (
        id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        project         VARCHAR2(255)   NOT NULL,
        source_path     VARCHAR2(1000)  NOT NULL,
        content_hash    VARCHAR2(64)    NOT NULL,
        title           VARCHAR2(500),
        ingested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status          VARCHAR2(50)    NOT NULL,
        UNIQUE (project, content_hash)
    )
    """,
    # -----------------------------------------------------------------------
    # Vector index — cosine similarity search
    # -----------------------------------------------------------------------
    """
    CREATE VECTOR INDEX IF NOT EXISTS idx_wiki_embedding
        ON wiki_pages (embedding)
        ORGANIZATION NEIGHBOR PARTITIONS
        WITH DISTANCE COSINE
    """,
    # -----------------------------------------------------------------------
    # Full-text index — exact term and name search
    # -----------------------------------------------------------------------
    """
    CREATE INDEX IF NOT EXISTS idx_wiki_fulltext
        ON wiki_pages (snippet)
        INDEXTYPE IS CTXSYS.CONTEXT
        PARAMETERS ('SYNC (ON COMMIT)')
    """,
]


def setup(db: DatabaseConnection) -> None:
    """
    Execute all DDL statements, skipping objects that already exist.

    ORA-00955: name is already used by an existing object
    ORA-29832: cannot drop or create a domain index on a virtual column
    Both are treated as "already done" — safe to ignore.
    """
    import oracledb

    already_exists_codes = {955, 29832}

    for statement in DDL_STATEMENTS:
        sql = statement.strip()
        # Extract a short label for the log line
        label = " ".join(sql.split()[:4])

        try:
            with db.cursor() as cursor:
                cursor.execute(sql)
            print(f"  ✅ {label}")
        except oracledb.DatabaseError as e:
            (error,) = e.args
            if error.code in already_exists_codes:
                print(f"  ⏭️  {label} (already exists)")
            else:
                print(f"  ❌ {label}: {error.message}")
                raise


def main() -> None:
    print("Setting up Oracle DB schema for LLM Wiki...\n")
    db = DatabaseConnection.from_settings(settings)

    try:
        setup(db)
        print("\n✅ Schema setup complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
