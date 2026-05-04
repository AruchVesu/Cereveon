from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable

import numpy as np

EMBED_DIM = 16
INPUT_DIM = 32
EMBED_KEY_OFFSET = 3
EMBED_SLOT_COUNT = EMBED_DIM
WEAK_START = EMBED_KEY_OFFSET + EMBED_SLOT_COUNT
WEAK_SLOT_COUNT = INPUT_DIM - WEAK_START


def _hash_key(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little") % WEAK_SLOT_COUNT


@dataclass
class PlayerEmbeddingEncoder:
    seed: int = 7

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        self.W1 = rng.normal(scale=0.1, size=(INPUT_DIM, INPUT_DIM)).astype(np.float32)
        self.b1 = rng.normal(scale=0.05, size=(INPUT_DIM,)).astype(np.float32)
        self.W2 = rng.normal(scale=0.1, size=(INPUT_DIM, EMBED_DIM)).astype(np.float32)
        self.b2 = rng.normal(scale=0.05, size=(EMBED_DIM,)).astype(np.float32)

    def build_input(
        self,
        rating: float,
        confidence: float,
        accuracy: float,
        weaknesses: Dict[str, float],
        z_prev: Iterable[float] | None = None,
    ) -> np.ndarray:
        x = np.zeros(INPUT_DIM, dtype=np.float32)
        x[0] = float(rating) / 2000.0
        x[1] = float(confidence)
        x[2] = float(accuracy)

        if z_prev is not None:
            z_arr = np.asarray(list(z_prev), dtype=np.float32)
            if z_arr.size != EMBED_DIM:
                z_arr = np.resize(z_arr, EMBED_DIM)
            x[EMBED_KEY_OFFSET:WEAK_START] = z_arr

        for key, value in weaknesses.items():
            idx = WEAK_START + _hash_key(key)
            x[idx] += float(value)

        return x

    def encode(
        self,
        rating: float,
        confidence: float,
        accuracy: float,
        weaknesses: Dict[str, float],
        z_prev: Iterable[float] | None = None,
    ) -> np.ndarray:
        x = self.build_input(rating, confidence, accuracy, weaknesses, z_prev)
        h = np.tanh(x @ self.W1 + self.b1)
        z = np.tanh(h @ self.W2 + self.b2)
        return z.astype(np.float32)


def zeros_embedding() -> np.ndarray:
    return np.zeros(EMBED_DIM, dtype=np.float32)


def embedding_from_json(value: str | None) -> np.ndarray:
    if not value:
        return zeros_embedding()
    try:
        arr = json.loads(value)
        vec = np.asarray(arr, dtype=np.float32)
    except (json.JSONDecodeError, TypeError, ValueError):
        return zeros_embedding()
    if vec.size != EMBED_DIM:
        vec = np.resize(vec, EMBED_DIM)
    return vec


def embedding_to_json(vec: Iterable[float]) -> str:
    return json.dumps([float(x) for x in vec])
