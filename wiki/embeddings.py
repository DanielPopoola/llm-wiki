"""
Embedding generation for wiki pages.

Loads nomic-embed-text-v2-moe once on first call (lazy) and exposes
a single generate() function. The model is not loaded at import time
— importing this module is cheap.
"""

from functools import lru_cache

from config import settings


@lru_cache(maxsize=1)
def _load_model():
    """
    Load the embedding model exactly once.

    lru_cache(maxsize=1) ensures subsequent calls return the cached
    instance — no repeated 500MB loads across the process lifetime.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        settings.embedding_model,
        trust_remote_code=True,  # required by nomic-embed-text-v2-moe
    )


def generate_embedding(text: str) -> list[float]:
    """
    Generate a 384-dimensional embedding vector for the given text.

    Args:
        text: The text to embed. For wiki pages, pass
              build_embed_input(title, body) rather than raw page content.

    Returns:
        List of 384 floats representing the semantic vector.
    """
    model = _load_model()
    vector = model.encode(text, convert_to_numpy=True)
    return vector.tolist()


def build_embed_input(title: str, body: str, max_tokens: int = 400) -> str:
    """
    Construct the string to embed for a wiki page.

    Concatenates the title with the first max_tokens words of the body.
    Words are used as a proxy for tokens — close enough for truncation purposes
    without requiring a tokenizer dependency.

    Args:
        title: Page title (e.g. "GTBank Q3 2024 Earnings").
        body: Full page body text (markdown).
        max_tokens: Approximate token budget for the body portion.

    Returns:
        Single string ready to pass to generate_embedding().
    """
    words = body.split()
    truncated_body = " ".join(words[:max_tokens])
    return f"{title}\n\n{truncated_body}"
