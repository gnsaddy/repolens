"""Embedding providers. `hash` is a local, deterministic, offline identifier-aware embedder."""

import hashlib
import math
import re
import time
from functools import lru_cache

import httpx

from ..config import get_settings

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def stem(word: str) -> str:
    """Very light suffix stripping so 'reserving', 'reserved' and 'reserve' meet. Added as an extra token."""
    w = word
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    """Identifier-aware tokens: splits snake_case and camelCase, keeps the original identifier too."""
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        low = raw.lower()
        out.append(low)
        if len(low) >= 5 and low.isalpha() and (st := stem(low)) != low:
            out.append(st)
        parts = [p.lower() for piece in raw.split("_") for p in _CAMEL_RE.findall(piece)]
        if len(parts) > 1:
            out.extend(parts)
    return out


@lru_cache(maxsize=262_144)
def _slot(feature: str, dim: int) -> tuple[int, float]:
    """Which of the `dim` positions a feature lands on, and with which sign. Cached: words and word pairs repeat."""
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
    return h % dim, 1.0 if (h >> 63) & 1 else -1.0


def hash_embed(text: str, dim: int) -> list[float]:
    """The same vector as always (recipe `local-hash-v1`), computed about twice as fast."""
    vec = [0.0] * dim
    toks = tokenize(text)
    for i, t in enumerate(toks):
        k, sign = _slot(t, dim)
        vec[k] += sign
        if i + 1 < len(toks):
            k, sign = _slot(t + " " + toks[i + 1], dim)
            vec[k] += 0.5 * sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class Embedder:
    def __init__(self):
        s = get_settings()
        self.provider = s.embedding_provider
        self.dim = s.embedding_dim
        self.model = s.embedding_model if self.provider != "hash" else "local-hash-v1"
        self.last_usage: dict = {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        started = time.time()
        if self.provider == "hash":
            vecs = [hash_embed(t, self.dim) for t in texts]
            self.last_usage = {
                "input_tokens": sum(len(tokenize(t)) for t in texts),
                "latency_ms": int((time.time() - started) * 1000),
                "usage_source": "local",
            }
            return vecs
        s = get_settings()
        out: list[list[float]] = []
        tokens = 0
        for i in range(0, len(texts), 64):
            batch = [t[:8000] for t in texts[i : i + 64]]
            r = httpx.post(
                f"{s.embedding_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {s.embedding_api_key}"}
                if s.embedding_api_key
                else {},  # local servers need no key
                json={
                    "model": s.embedding_model,
                    "input": batch,
                    **({"dimensions": self.dim} if s.embedding_send_dimensions else {}),
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            out.extend(d["embedding"] for d in data["data"])
            tokens += data.get("usage", {}).get("prompt_tokens", 0)
        self.last_usage = {
            "input_tokens": tokens,
            "latency_ms": int((time.time() - started) * 1000),
            "usage_source": "provider_reported",
        }
        return out
