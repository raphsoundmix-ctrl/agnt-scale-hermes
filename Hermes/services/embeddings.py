"""AGNT SCALE — local self-hosted embeddings (fastembed, ONNX/CPU).

bge-small-en-v1.5 (384-dim, English). No API key, no torch, and the memory text
never leaves the server. Lazy singleton; the sync encode is wrapped in
asyncio.to_thread so it doesn't block the event loop.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Optional

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
DIM = int(os.environ.get("EMBED_DIM", "384"))
_CACHE_DIR = os.environ.get("FASTEMBED_CACHE_PATH", "/home/app/.fastembed_cache")

_model = None
_lock = threading.Lock()


def _get():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding  # heavy import, deferred
                _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=_CACHE_DIR)
    return _model


def embed(text: str) -> list[float]:
    text = (text or "").strip()
    if not text:
        return []
    vec = next(iter(_get().embed([text])))
    return [float(x) for x in vec]


async def aembed(text: str) -> list[float]:
    return await asyncio.to_thread(embed, text)


def to_pgvector(vec: list[float]) -> Optional[str]:
    """pgvector accepts the text form '[0.1,0.2,...]'. None → SQL NULL."""
    if not vec:
        return None
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
