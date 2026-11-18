"""
Embedding generation and cosine similarity calculation for RAG retrieval.
Designed for 8 GB RAM Mac: lightweight, persistent, with zero external vector daemon required.
"""

import hashlib
import json
import logging
import math
import re
from typing import List, Union
import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 256
_TRANSFORMER_MODEL = None


def _get_transformer_model():
    """Lazy-load sentence transformer model if available."""
    global _TRANSFORMER_MODEL
    if _TRANSFORMER_MODEL is not False and _TRANSFORMER_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            _TRANSFORMER_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Loaded sentence-transformers all-MiniLM-L6-v2 model for RAG.")
        except Exception as e:
            logger.info(f"SentenceTransformer not available ({e}); using lightweight dense feature embeddings.")
            _TRANSFORMER_MODEL = False
    return _TRANSFORMER_MODEL if _TRANSFORMER_MODEL is not False else None


def _lightweight_embed(text: str, dim: int = EMBEDDING_DIM) -> List[float]:
    """
    Fast, deterministic dense semantic vector using subword and token n-gram hashing.
    L2-normalized so dot product directly yields cosine similarity.
    Completely self-contained, requiring zero external downloads or GPU/RAM overhead.
    """
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = clean.split()
    if not tokens:
        return [0.0] * dim

    vec = np.zeros(dim, dtype=np.float32)

    # Word unigrams and bigrams
    for i, token in enumerate(tokens):
        # Unigram hash
        h1 = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[h1] += 1.0

        # Subword character 3-grams
        if len(token) >= 3:
            for j in range(len(token) - 2):
                tri = token[j:j+3]
                h_tri = int(hashlib.md5(tri.encode("utf-8")).hexdigest(), 16) % dim
                vec[h_tri] += 0.4

        # Word bigram
        if i + 1 < len(tokens):
            bigram = f"{token}_{tokens[i+1]}"
            h2 = int(hashlib.md5(bigram.encode("utf-8")).hexdigest(), 16) % dim
            vec[h2] += 0.8

    # L2 normalize
    norm = np.linalg.norm(vec)
    if norm > 1e-9:
        vec = vec / norm

    return [float(x) for x in vec]


def embed_text(text: str) -> List[float]:
    """Generate dense embedding vector for a single text string."""
    model = _get_transformer_model()
    if model is not None:
        try:
            emb = model.encode(text, normalize_embeddings=True)
            return [float(x) for x in emb]
        except Exception as e:
            logger.warning(f"Transformer embedding failed: {e}. Falling back to lightweight engine.")

    return _lightweight_embed(text)


def embed_batch(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts."""
    model = _get_transformer_model()
    if model is not None:
        try:
            embs = model.encode(texts, normalize_embeddings=True)
            return [[float(x) for x in e] for e in embs]
        except Exception as e:
            logger.warning(f"Batch transformer embedding failed: {e}. Falling back to lightweight engine.")

    return [_lightweight_embed(t) for t in texts]


def cosine_similarity(v1: Union[List[float], np.ndarray], v2: Union[List[float], np.ndarray]) -> float:
    """Calculate cosine similarity between two vectors in [-1.0, 1.0]."""
    if not v1 or not v2:
        return 0.0

    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)

    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0

    return float(np.dot(a, b) / (norm_a * norm_b))

