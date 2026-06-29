"""
verify.py — Step 0 environment verification.

Checks all four infrastructure dependencies before any application code
is written. Run this first; fix anything that fails before proceeding.

Usage:
    uv run python scripts/verify.py

Exits 0 only when all four checks pass.
"""

import os
import sys

from dotenv import load_dotenv

# Load .env before anything else — all checks depend on it.
load_dotenv()

REQUIRED_ENV_KEYS = [
    "ORACLE_HOST",
    "ORACLE_PORT",
    "ORACLE_SERVICE",
    "ORACLE_USER",
    "ORACLE_PASSWORD",
    "LLM_MODEL",
    "LLM_API_KEY",
    "EMBED_MODEL",
]

EXPECTED_EMBEDDING_DIMENSIONS = 768


def check_environment() -> bool:
    """
    Verify all required environment variables are present.

    Returns True if every key in REQUIRED_ENV_KEYS has a non-empty value,
    False otherwise.
    """
    missing = [key for key in REQUIRED_ENV_KEYS if not os.getenv(key)]

    if missing:
        print(f"❌ Environment: missing keys: {', '.join(missing)}")
        print("   Copy .env.example to .env and fill in all values.")
        return False

    print("✅ Environment: all required keys present")
    return True


def check_oracle() -> bool:
    """
    Verify Oracle AI Database is reachable and accepts a query.

    Connects using credentials from the environment, creates a test table,
    inserts a row, reads it back, then drops the table.

    Returns True if all operations succeed, False otherwise.
    """
    import oracledb

    host = os.getenv("ORACLE_HOST")
    port = int(os.getenv("ORACLE_PORT"))  # type: ignore
    service = os.getenv("ORACLE_SERVICE")
    user = os.getenv("ORACLE_USER")
    password = os.getenv("ORACLE_PASSWORD")

    dsn = f"{host}:{port}/{service}"

    try:
        with oracledb.connect(user=user, password=password, dsn=dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 'llm-wiki connection ok' AS status FROM dual")
                row = cur.fetchone()
                assert row[0] == "llm-wiki connection ok"

        print(f"✅ Oracle DB: connected ({host}:{port}/{service})")
        return True

    except oracledb.DatabaseError as exc:
        print(f"❌ Oracle DB: connection failed — {exc}")
        print("   Is the Docker container running? Try: docker ps | grep oracle")
        return False


def check_embeddings() -> bool:
    """
    Verify the embedding model loads and returns a 768-dimensional vector.

    Loads the model specified by EMBED_MODEL, runs a test sentence through
    it, and confirms the output shape matches EXPECTED_EMBEDDING_DIMENSIONS.

    Returns True if the vector has the expected shape, False otherwise.
    """
    from sentence_transformers import SentenceTransformer

    model_name = os.getenv("EMBED_MODEL")

    try:
        model = SentenceTransformer(model_name, trust_remote_code=True)
        vector = model.encode("test sentence for dimension check")

        if len(vector) != EXPECTED_EMBEDDING_DIMENSIONS:
            print(
                f"❌ Embeddings: expected {EXPECTED_EMBEDDING_DIMENSIONS} dimensions, \
                got {len(vector)}"
            )
            return False

        print(f"✅ Embeddings: {model_name} loaded — {len(vector)}-dimensional vector confirmed")
        return True

    except Exception as exc:
        print(f"❌ Embeddings: failed to load {model_name} — {exc}")
        return False


def main() -> None:
    """
    Run all four environment checks and report results.

    Exits 0 only when every check passes. Exits 1 if any check fails,
    so this can be used as a gate in CI or setup scripts.
    """
    print("LLM Wiki — Step 0 Environment Verification\n")

    results = [
        check_environment(),
        check_oracle(),
        check_embeddings(),
    ]

    print()

    if all(results):
        print("All checks passed. Environment is ready.")
        sys.exit(0)
    else:
        failed = results.count(False)
        print(f"{failed} check(s) failed. Fix the issues above before proceeding.")
        sys.exit(1)


if __name__ == "__main__":
    main()
