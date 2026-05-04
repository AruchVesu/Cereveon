from __future__ import annotations

import hashlib

try:
    from . import position_input as _position_input
except ImportError:
    import position_input as _position_input


def _limit_suffix(movetime_ms: int | None = None, nodes: int | None = None) -> str:
    if nodes is not None:
        return f"nodes:{max(1, int(nodes))}"
    if movetime_ms is not None:
        return f"movetime:{max(1, int(movetime_ms))}"
    return "default"


def eval_cache_key(
    *,
    fen: str | None = None,
    moves: list[str] | None = None,
    movetime_ms: int | None = None,
    nodes: int | None = None,
) -> str:
    position_fen, _, _ = _position_input.normalize_position(fen=fen, moves=moves)
    digest = hashlib.sha256(position_fen.encode("utf-8")).hexdigest()[:12]
    return f"cc:eval:{digest}:{_limit_suffix(movetime_ms=movetime_ms, nodes=nodes)}"
