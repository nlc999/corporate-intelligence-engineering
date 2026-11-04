"""Optional pretrained MiniLM sentence-embedding adapter.

Weights are not bundled; first use may download a model from Hugging Face.
"""
from __future__ import annotations

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def load_encoder(model_name: str = MODEL_NAME):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("Install the optional ML dependencies: pip install -e '.[ml]'") from exc
    return SentenceTransformer(model_name)


def encode_sentences(encoder, sentences: list[str]):
    if not sentences:
        raise ValueError("Cannot encode an empty set of sentences")
    return encoder.encode(sentences, batch_size=64, show_progress_bar=False)
