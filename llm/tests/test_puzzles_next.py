"""Backend tests for GET /puzzles/next — standalone puzzle-trainer feed.

The endpoint serves the Android Puzzles tab: one practice puzzle per
call, live-fetched from Lichess (``"mix"`` angle, skill-banded
difficulty) with a fallback to the curated study-plan corpus on any
Lichess failure or when the ``PUZZLES_LICHESS_ENABLED`` kill-switch is
off.  The fetch itself is monkeypatched — no live Lichess call runs in
CI.

Pinned invariants
-----------------
PN_01  Lichess happy path: namespaced ``lichess_<id>``, solver FEN /
       move passthrough, source="lichess", rating surfaced.
PN_02  Fetch is called with angle="mix" and the rating-mapped
       difficulty band (beginner→easier, intermediate→normal,
       advanced→harder).
PN_03  LichessClientError → corpus fallback (source="library"), and
       the served corpus puzzle is a legal position + legal move.
PN_04  Unexpected exception from the fetch → corpus fallback, no raise.
PN_05  Kill-switch off → corpus served with ZERO fetch calls.
PN_06  Kill-switch off AND empty corpus → 503 (no puzzle available).
PN_07  Allowlist pins: PUZZLE_ANGLE and every SKILL_TO_DIFFICULTY value
       are accepted by the Lichess client's own allowlists, so the
       router can never assemble a ValueError-raising fetch.
PN_08  Missing Lichess rating (0) → difficulty falls back to the
       player's own band.
PN_09  Corpus pick is skill-banded when the band has entries.
PN_10  /puzzles/next is registered on the server app.
PN_11  Corpus responses carry rating=None.
PN_12  Lichess path carries the FULL solution walk on the wire
       (solution_line_uci), first move == expected_move_uci.
PN_13  Corpus path carries its line when the entry has one, and falls
       back to the single expected move for single-decision entries —
       solution_line_uci is never empty.
"""

from __future__ import annotations

import os

import chess
import pytest
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from fastapi import HTTPException

from llm.seca.coach.study_plan.library import LibraryPuzzle
from llm.seca.lichess.client import (
    LichessPuzzle,
    LichessUpstreamError,
)
from llm.seca.puzzles import router as puzzles_router
from llm.seca.puzzles.router import (
    PUZZLE_ANGLE,
    SKILL_TO_DIFFICULTY,
    next_puzzle,
)
from llm.seca.shared_limiter import limiter


def _fake_request() -> StarletteRequest:
    """Minimal Request satisfying slowapi's isinstance check; the
    limiter is disabled around each call so it is never inspected."""
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/puzzles/next",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


class _FakePlayer:
    """Duck-typed Player — the handler only reads ``rating``."""

    def __init__(self, rating: float = 1500.0):
        self.rating = rating


def _call(player: _FakePlayer):
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        return next_puzzle(request=_fake_request(), player=player)
    finally:
        limiter.enabled = prev_enabled


_LICHESS_PUZZLE = LichessPuzzle(
    id="AbCd1",
    rating=1400,
    themes=("fork", "middlegame"),
    solver_fen="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    solver_move_uci="g1f3",
    side=chess.WHITE,
    solution_line_uci=("g1f3", "b8c6", "f1c4"),
)


_CORPUS = {
    "fork": [
        LibraryPuzzle(
            id="fork_001",
            theme="fork",
            difficulty="beginner",
            fen="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            expected_move_uci="g1f3",
            description="test fork puzzle",
            solution_line_uci=("g1f3", "b8c6", "f1c4"),
        ),
    ],
    "endgame_technique": [
        LibraryPuzzle(
            id="endgame_001",
            theme="endgame_technique",
            difficulty="advanced",
            fen="8/8/8/8/8/4k3/4p3/4K3 b - - 0 1",
            expected_move_uci="e3d3",
            description="test endgame puzzle (single-decision, no line)",
        ),
    ],
}


@pytest.fixture(autouse=True)
def _reset_library_cache(monkeypatch):
    """Serve the rigged corpus and keep the on-disk YAML out of tests."""
    monkeypatch.setattr(puzzles_router, "_library_cache", dict(_CORPUS))
    yield


@pytest.fixture()
def lichess_on(monkeypatch):
    monkeypatch.setenv("PUZZLES_LICHESS_ENABLED", "1")


@pytest.fixture()
def lichess_off(monkeypatch):
    monkeypatch.setenv("PUZZLES_LICHESS_ENABLED", "0")


def _patch_fetch(monkeypatch, result=None, error: Exception | None = None) -> dict:
    """Replace the client's fetch_puzzle_by_theme; record call args.

    Patched at the CLIENT module because the router lazy-imports the
    function inside the handler body on every call.
    """
    calls: dict = {"count": 0}

    def _fake_fetch(angle_slug, *, difficulty=None):
        calls["count"] += 1
        calls["angle"] = angle_slug
        calls["difficulty"] = difficulty
        if error is not None:
            raise error
        return result

    import llm.seca.lichess.client as lichess_client

    monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", _fake_fetch)
    return calls


# ---------------------------------------------------------------------------
# Lichess happy path
# ---------------------------------------------------------------------------


class TestLichessPath:
    def test_pn01_happy_path_serves_lichess_puzzle(self, monkeypatch, lichess_on):
        _patch_fetch(monkeypatch, result=_LICHESS_PUZZLE)
        resp = _call(_FakePlayer(rating=1500.0))
        assert resp.puzzle_id == "lichess_AbCd1"
        assert resp.fen == _LICHESS_PUZZLE.solver_fen
        assert resp.expected_move_uci == "g1f3"
        assert resp.source == "lichess"
        assert resp.theme == "mix"
        assert resp.rating == 1400
        # 1400 sits in the intermediate band of skill_hint_for_rating.
        assert resp.difficulty == "intermediate"

    @pytest.mark.parametrize(
        ("rating", "expected_difficulty"),
        [
            (800.0, "easier"),
            (1500.0, "normal"),
            (2100.0, "harder"),
        ],
    )
    def test_pn02_fetch_uses_mix_angle_and_skill_band(
        self, monkeypatch, lichess_on, rating, expected_difficulty
    ):
        calls = _patch_fetch(monkeypatch, result=_LICHESS_PUZZLE)
        _call(_FakePlayer(rating=rating))
        assert calls["count"] == 1
        assert calls["angle"] == "mix"
        assert calls["difficulty"] == expected_difficulty

    def test_pn08_missing_lichess_rating_falls_back_to_player_band(self, monkeypatch, lichess_on):
        unrated = LichessPuzzle(
            id="NoRat",
            rating=0,
            themes=(),
            solver_fen=_LICHESS_PUZZLE.solver_fen,
            solver_move_uci="g1f3",
            side=chess.WHITE,
        )
        _patch_fetch(monkeypatch, result=unrated)
        resp = _call(_FakePlayer(rating=2000.0))
        assert resp.difficulty == "advanced"
        assert resp.rating is None

    def test_pn12_lichess_path_carries_solution_line(self, monkeypatch, lichess_on):
        """PN_12 — the trainer walks multi-move puzzles: the full Lichess
        line rides the wire and starts with the expected move."""
        _patch_fetch(monkeypatch, result=_LICHESS_PUZZLE)
        resp = _call(_FakePlayer(rating=1500.0))
        assert resp.solution_line_uci == ["g1f3", "b8c6", "f1c4"]
        assert resp.solution_line_uci[0] == resp.expected_move_uci

    def test_pn12b_lichess_missing_line_falls_back_to_single_move(
        self, monkeypatch, lichess_on
    ):
        """PN_12 (degraded) — a LichessPuzzle without a captured line
        (defensive; the client always fills it) still serves a non-empty
        single-move walk."""
        bare = LichessPuzzle(
            id="NoLine",
            rating=1400,
            themes=(),
            solver_fen=_LICHESS_PUZZLE.solver_fen,
            solver_move_uci="g1f3",
            side=chess.WHITE,
        )
        _patch_fetch(monkeypatch, result=bare)
        resp = _call(_FakePlayer(rating=1500.0))
        assert resp.solution_line_uci == ["g1f3"]


# ---------------------------------------------------------------------------
# Corpus fallback
# ---------------------------------------------------------------------------


class TestCorpusFallback:
    def test_pn03_lichess_error_falls_back_to_corpus(self, monkeypatch, lichess_on):
        _patch_fetch(monkeypatch, error=LichessUpstreamError("upstream 502"))
        resp = _call(_FakePlayer(rating=1500.0))
        assert resp.source == "library"
        assert resp.puzzle_id in {"fork_001", "endgame_001"}
        # Served corpus puzzle must be playable: FEN parses, move legal.
        board = chess.Board(resp.fen)
        assert chess.Move.from_uci(resp.expected_move_uci) in board.legal_moves

    def test_pn04_unexpected_error_falls_back_to_corpus(self, monkeypatch, lichess_on):
        _patch_fetch(monkeypatch, error=RuntimeError("boom"))
        resp = _call(_FakePlayer(rating=1500.0))
        assert resp.source == "library"

    def test_pn05_kill_switch_serves_corpus_without_fetch(self, monkeypatch, lichess_off):
        calls = _patch_fetch(monkeypatch, result=_LICHESS_PUZZLE)
        resp = _call(_FakePlayer(rating=1500.0))
        assert calls["count"] == 0
        assert resp.source == "library"

    def test_pn06_empty_corpus_and_lichess_off_returns_503(self, monkeypatch, lichess_off):
        monkeypatch.setattr(puzzles_router, "_library_cache", {})
        with pytest.raises(HTTPException) as excinfo:
            _call(_FakePlayer(rating=1500.0))
        assert excinfo.value.status_code == 503

    def test_pn09_corpus_pick_prefers_player_band(self, monkeypatch, lichess_off):
        # Beginner rating (800) → only fork_001 sits in the beginner band.
        resp = _call(_FakePlayer(rating=800.0))
        assert resp.puzzle_id == "fork_001"
        assert resp.difficulty == "beginner"
        assert resp.theme == "fork"

    def test_pn11_corpus_response_has_no_rating(self, monkeypatch, lichess_off):
        resp = _call(_FakePlayer(rating=800.0))
        assert resp.rating is None

    def test_pn13_corpus_line_on_wire_with_single_move_fallback(
        self, monkeypatch, lichess_off
    ):
        """PN_13 — a corpus entry WITH a line serves it; a single-decision
        entry serves its one expected move.  solution_line_uci is never
        empty on this endpoint."""
        # Beginner band → fork_001 (has a 3-ply line).
        resp = _call(_FakePlayer(rating=800.0))
        assert resp.puzzle_id == "fork_001"
        assert resp.solution_line_uci == ["g1f3", "b8c6", "f1c4"]
        # Advanced band → endgame_001 (no line → single-move walk).
        resp2 = _call(_FakePlayer(rating=2100.0))
        assert resp2.puzzle_id == "endgame_001"
        assert resp2.solution_line_uci == ["e3d3"]


# ---------------------------------------------------------------------------
# Allowlist pins + wiring
# ---------------------------------------------------------------------------


class TestPins:
    def test_pn07_angle_and_difficulties_are_client_allowlisted(self):
        """The router must never assemble a fetch the client rejects.

        ``fetch_puzzle_by_theme`` raises ``ValueError`` (programming
        error, no fallback semantics) for a non-allowlisted angle or
        difficulty — pin the router's constants against the client's
        allowlists so drift on either side fails here first.
        """
        from llm.seca.lichess.client import (
            _PUZZLE_ANGLE_ALLOWED,
            _PUZZLE_DIFFICULTY_ALLOWED,
        )

        assert PUZZLE_ANGLE in _PUZZLE_ANGLE_ALLOWED
        assert set(SKILL_TO_DIFFICULTY.values()) <= _PUZZLE_DIFFICULTY_ALLOWED
        # Every skill band skill_hint_for_rating can emit has a mapping.
        assert set(SKILL_TO_DIFFICULTY.keys()) == {"beginner", "intermediate", "advanced"}

    def test_pn10_route_registered_on_server(self):
        from llm import server as server_module

        paths = [getattr(r, "path", None) for r in server_module.app.routes]
        assert "/puzzles/next" in paths, "server.app must expose GET /puzzles/next"
