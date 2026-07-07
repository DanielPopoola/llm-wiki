from functools import lru_cache

from config import settings

from .utils import traceable


@lru_cache(maxsize=1)
def _load_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        settings.embedding_model,
        backend="onnx",
    )


@traceable(name="embeddings.generate_embedding")
def generate_embedding(text: str) -> list[float]:
    model = _load_model()
    vector = model.encode(text, convert_to_numpy=True)
    return vector.tolist()


def build_embed_input(title: str, body: str, max_tokens: int = 400) -> str:
    words = body.split()
    truncated_body = " ".join(words[:max_tokens])
    return f"{title}\n\n{truncated_body}"


def preload_model() -> None:
    _load_model()
