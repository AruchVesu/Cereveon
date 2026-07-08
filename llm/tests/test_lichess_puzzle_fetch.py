"""Tests for the study-plan Lichess puzzle source.

Two layers:

* ``llm.seca.lichess.client.fetch_puzzle_by_theme`` — the /api/puzzle/next
  wrapper: angle/difficulty allowlisting, HTTP-error translation, the
  pgn -> solver-FEN derivation, and the fail-closed legality guard.
* ``llm.seca.coach.study_plan.lichess_puzzles.fetch_side_matched_variants`` —
  the side filter, difficulty fan-out, dedup, bounded call count, feature
  flag, and best-effort ([]-on-error) contract.

httpx is mocked at ``httpx.Client`` so no live Lichess call is made; the
fetcher tests monkeypatch ``fetch_puzzle_by_theme`` directly.

Pinned invariants
-----------------
PZ_01  happy path derives solver FEN/side/move; solution[0] legal, side correct.
PZ_02  illegal solution[0] in derived position -> LichessParseError (fail closed).
PZ_03  missing/None initialPly -> LichessParseError.
PZ_04  angle not in the allowlist -> ValueError (never touches the URL).
PZ_05  difficulty not in the allowlist -> ValueError.
PZ_06  429 -> LichessRateLimited carrying retry_after.
PZ_07  5xx -> LichessUpstreamError.
PZ_08  malformed JSON body -> LichessParseError.
PZ_09  missing game/puzzle objects -> LichessParseError.
PZ_10  request carries angle + difficulty as query params to /api/puzzle/next.
PZ_11  pgn over the char cap -> LichessParseError.

FV_01  two side-matched puzzles collected (distinct ids, wrong side skipped).
FV_02  feature flag off -> [] with zero fetches.
FV_03  theme with no Lichess slug -> [] with zero fetches.
FV_04  every fetch errors -> [] (no exception escapes), bounded calls.
FV_05  rate-limit stops the batch early, returns what was collected.
FV_06  call count is bounded by STUDY_PLAN_LICHESS_MAX_FETCHES.
FV_07  duplicate ids are de-duplicated.
FV_08  every _THEME_TO_ANGLE value is in the client's angle allowlist.
"""

from __future__ import annotations

import json
import os

import chess
import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.lichess import client as lichess_client
from llm.seca.lichess.client import (
    LichessParseError,
    LichessPuzzle,
    LichessRateLimited,
    LichessUpstreamError,
    fetch_puzzle_by_theme,
)
from llm.seca.coach.study_plan import lichess_puzzles

# ---------------------------------------------------------------------------
# httpx mock — _request_json_bounded streams via httpx.Client(...).stream(...)
# and reads response.iter_bytes().  We stand in for that whole shape.
# ---------------------------------------------------------------------------


class _FakeBytesResponse:
    def __init__(self, status_code: int, body: bytes, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def iter_bytes(self):
        # One chunk is enough — _request_json_bounded sums chunk lengths.
        yield self._body


class _FakeStreamCM:
    def __init__(self, response: _FakeBytesResponse):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, response: _FakeBytesResponse, captured: dict):
        self._response = response
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None, data=None, params=None):
        self._captured["method"] = method
        self._captured["url"] = url
        self._captured["params"] = params or {}
        self._captured["headers"] = headers or {}
        return _FakeStreamCM(self._response)


def _patch_http(monkeypatch, *, status_code=200, body=None, headers=None) -> dict:
    """Monkeypatch httpx.Client so the next puzzle fetch returns ``body``."""
    captured: dict = {}
    if isinstance(body, (dict, list)):
        raw = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    elif isinstance(body, bytes):
        raw = body
    else:
        raw = b""
    response = _FakeBytesResponse(status_code, raw, headers)
    monkeypatch.setattr(
        lichess_client.httpx, "Client", lambda **kw: _FakeClient(response, captured)
    )
    return captured


def _puzzle_body(
    *,
    pgn: str = "e4 e5 Nf3 Nc6 Bb5",
    solution: list[str] | None = None,
    initial_ply: int | None = 4,
    pid: str = "abc12",
    rating: int = 1500,
    themes: list[str] | None = None,
    include_game: bool = True,
    include_puzzle: bool = True,
) -> dict:
    """A /api/puzzle/next body.  Defaults describe a Black-to-move puzzle:
    after 1.e4 e5 2.Nf3 Nc6 3.Bb5 (5 plies, initialPly=4) it is Black's move
    and ``a7a6`` is legal."""
    body: dict = {}
    if include_game:
        body["game"] = {"pgn": pgn}
    if include_puzzle:
        puzzle: dict = {
            "id": pid,
            "rating": rating,
            "solution": solution if solution is not None else ["a7a6", "b5a4"],
            "themes": themes if themes is not None else ["opening", "short"],
        }
        if initial_ply is not None:
            puzzle["initialPly"] = initial_ply
        body["puzzle"] = puzzle
    return body


# ===========================================================================
# Client — fetch_puzzle_by_theme / _parse_puzzle_payload
# ===========================================================================


class TestFetchPuzzleByTheme:
    def test_happy_path_derives_solver_position(self, monkeypatch):
        """PZ_01 — replaying the pgn lands on the solver position where
        solution[0] is legal, and the side to move is the solver's side."""
        _patch_http(monkeypatch, body=_puzzle_body())
        puzzle = fetch_puzzle_by_theme("fork", difficulty="normal")

        assert isinstance(puzzle, LichessPuzzle)
        assert puzzle.id == "abc12"
        assert puzzle.rating == 1500
        assert "opening" in puzzle.themes
        assert puzzle.solver_move_uci == "a7a6"
        assert puzzle.side == chess.BLACK
        board = chess.Board(puzzle.solver_fen)
        assert board.turn == chess.BLACK
        assert chess.Move.from_uci("a7a6") in board.legal_moves

    def test_illegal_solution_move_fails_closed(self, monkeypatch):
        """PZ_02 — solution[0] not legal in the derived position is rejected
        rather than shipped as a broken puzzle."""
        _patch_http(monkeypatch, body=_puzzle_body(solution=["e2e4"]))  # e-pawn already moved
        with pytest.raises(LichessParseError, match="not legal"):
            fetch_puzzle_by_theme("fork")

    def test_missing_initial_ply_rejected(self, monkeypatch):
        """PZ_03 — a missing/None initialPly is a malformed body."""
        _patch_http(monkeypatch, body=_puzzle_body(initial_ply=None))
        with pytest.raises(LichessParseError, match="initialPly"):
            fetch_puzzle_by_theme("fork")

    def test_unknown_angle_rejected_preflight(self):
        """PZ_04 — a non-allowlisted angle never reaches the URL."""
        with pytest.raises(ValueError, match="angle"):
            fetch_puzzle_by_theme("definitely_not_a_theme")

    def test_unknown_difficulty_rejected_preflight(self):
        """PZ_05 — a non-allowlisted difficulty never reaches the URL."""
        with pytest.raises(ValueError, match="difficulty"):
            fetch_puzzle_by_theme("fork", difficulty="impossible")

    def test_rate_limited(self, monkeypatch):
        """PZ_06 — 429 maps to LichessRateLimited with retry_after parsed."""
        _patch_http(monkeypatch, status_code=429, body={}, headers={"Retry-After": "17"})
        with pytest.raises(LichessRateLimited) as exc:
            fetch_puzzle_by_theme("fork")
        assert exc.value.retry_after == 17

    def test_upstream_5xx(self, monkeypatch):
        """PZ_07 — 5xx maps to LichessUpstreamError."""
        _patch_http(monkeypatch, status_code=503, body={})
        with pytest.raises(LichessUpstreamError):
            fetch_puzzle_by_theme("fork")

    def test_malformed_json_body(self, monkeypatch):
        """PZ_08 — a non-JSON body surfaces as LichessParseError."""
        _patch_http(monkeypatch, body=b"this is not json")
        with pytest.raises(LichessParseError):
            fetch_puzzle_by_theme("fork")

    def test_missing_game_or_puzzle(self, monkeypatch):
        """PZ_09 — a body without the game/puzzle objects is malformed."""
        _patch_http(monkeypatch, body=_puzzle_body(include_game=False))
        with pytest.raises(LichessParseError, match="game/puzzle"):
            fetch_puzzle_by_theme("fork")

    def test_request_carries_angle_and_difficulty(self, monkeypatch):
        """PZ_10 — the query string carries angle + difficulty and hits
        /api/puzzle/next."""
        captured = _patch_http(monkeypatch, body=_puzzle_body())
        fetch_puzzle_by_theme("pin", difficulty="harder")
        assert captured["url"].endswith("/api/puzzle/next")
        assert captured["params"] == {"angle": "pin", "difficulty": "harder"}

    def test_pgn_over_cap_rejected(self, monkeypatch):
        """PZ_11 — an oversized pgn is rejected before python-chess replays it."""
        huge_pgn = "e4 e5 " * 3000  # > _MAX_PUZZLE_PGN_CHARS (8192)
        _patch_http(monkeypatch, body=_puzzle_body(pgn=huge_pgn))
        with pytest.raises(LichessParseError, match="pgn exceeds cap"):
            fetch_puzzle_by_theme("fork")


# ===========================================================================
# Fetcher — fetch_side_matched_variants
# ===========================================================================


def _lp(pid: str, side: chess.Color, rating: int = 1500) -> LichessPuzzle:
    return LichessPuzzle(
        id=pid,
        rating=rating,
        themes=("fork",),
        solver_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        solver_move_uci="e2e4",
        side=side,
    )


class _QueuedFetch:
    """Fake fetch_puzzle_by_theme returning a queued sequence.  Each entry is
    a LichessPuzzle to return or an Exception to raise; past the end, the last
    entry repeats."""

    def __init__(self, items: list):
        self.items = items
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, angle, *, difficulty=None):
        self.calls.append((angle, difficulty))
        idx = len(self.calls) - 1
        item = self.items[idx] if idx < len(self.items) else self.items[-1]
        if isinstance(item, Exception):
            raise item
        return item


class TestFetchSideMatchedVariants:
    def test_two_side_matched_collected(self, monkeypatch):
        """FV_01 — keeps only the requested side, distinct ids, up to two."""
        monkeypatch.setenv("STUDY_PLAN_LICHESS_ENABLED", "1")
        fake = _QueuedFetch(
            [_lp("b1", chess.BLACK), _lp("w1", chess.WHITE), _lp("b2", chess.BLACK)]
        )
        monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", fake)

        out = lichess_puzzles.fetch_side_matched_variants(
            theme="fork", side_to_move=chess.BLACK, skill_hint="intermediate"
        )
        assert [p.id for p in out] == ["lichess_b1", "lichess_b2"]
        assert all(chess.Board(p.fen).turn == chess.BLACK for p in out)
        assert len(fake.calls) == 3  # stopped as soon as two matched

    def test_disabled_flag_short_circuits(self, monkeypatch):
        """FV_02 — flag off returns [] and never calls the client."""
        monkeypatch.setenv("STUDY_PLAN_LICHESS_ENABLED", "0")
        fake = _QueuedFetch([_lp("b1", chess.BLACK)])
        monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", fake)

        out = lichess_puzzles.fetch_side_matched_variants(
            theme="fork", side_to_move=chess.BLACK, skill_hint="intermediate"
        )
        assert out == []
        assert fake.calls == []

    def test_unmapped_theme_short_circuits(self, monkeypatch):
        """FV_03 — a theme with no Lichess slug returns [] without a fetch."""
        monkeypatch.setenv("STUDY_PLAN_LICHESS_ENABLED", "1")
        fake = _QueuedFetch([_lp("b1", chess.BLACK)])
        monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", fake)

        out = lichess_puzzles.fetch_side_matched_variants(
            theme="queen_safety", side_to_move=chess.BLACK, skill_hint="intermediate"
        )
        assert out == []
        assert fake.calls == []

    def test_all_errors_returns_empty(self, monkeypatch):
        """FV_04 — every call failing yields [] (no exception escapes)."""
        monkeypatch.setenv("STUDY_PLAN_LICHESS_ENABLED", "1")
        monkeypatch.setenv("STUDY_PLAN_LICHESS_MAX_FETCHES", "5")
        fake = _QueuedFetch([LichessParseError("bad")])
        monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", fake)

        out = lichess_puzzles.fetch_side_matched_variants(
            theme="fork", side_to_move=chess.BLACK, skill_hint="intermediate"
        )
        assert out == []
        assert len(fake.calls) == 5

    def test_rate_limit_stops_early(self, monkeypatch):
        """FV_05 — a 429 backs off for the rest of the plan, returning what
        was already collected."""
        monkeypatch.setenv("STUDY_PLAN_LICHESS_ENABLED", "1")
        fake = _QueuedFetch([_lp("b1", chess.BLACK), LichessRateLimited("slow down")])
        monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", fake)

        out = lichess_puzzles.fetch_side_matched_variants(
            theme="fork", side_to_move=chess.BLACK, skill_hint="intermediate"
        )
        assert [p.id for p in out] == ["lichess_b1"]
        assert len(fake.calls) == 2  # stopped at the rate-limit

    def test_call_count_is_bounded(self, monkeypatch):
        """FV_06 — never exceeds STUDY_PLAN_LICHESS_MAX_FETCHES even when no
        side match is ever found."""
        monkeypatch.setenv("STUDY_PLAN_LICHESS_ENABLED", "1")
        monkeypatch.setenv("STUDY_PLAN_LICHESS_MAX_FETCHES", "2")
        fake = _QueuedFetch([_lp("w1", chess.WHITE)])  # never the requested side
        monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", fake)

        out = lichess_puzzles.fetch_side_matched_variants(
            theme="fork", side_to_move=chess.BLACK, skill_hint="intermediate"
        )
        assert out == []
        assert len(fake.calls) == 2

    def test_duplicate_ids_deduped(self, monkeypatch):
        """FV_07 — the same puzzle id returned twice counts once."""
        monkeypatch.setenv("STUDY_PLAN_LICHESS_ENABLED", "1")
        fake = _QueuedFetch(
            [_lp("b1", chess.BLACK), _lp("b1", chess.BLACK), _lp("b2", chess.BLACK)]
        )
        monkeypatch.setattr(lichess_client, "fetch_puzzle_by_theme", fake)

        out = lichess_puzzles.fetch_side_matched_variants(
            theme="fork", side_to_move=chess.BLACK, skill_hint="intermediate"
        )
        assert [p.id for p in out] == ["lichess_b1", "lichess_b2"]


class TestThemeAngleMapping:
    def test_every_mapped_angle_is_allowlisted(self):
        """FV_08 — a theme->angle value the client rejects would silently
        break every fetch for that theme; pin the two in sync."""
        for theme, angle in lichess_puzzles._THEME_TO_ANGLE.items():
            assert angle in lichess_client._PUZZLE_ANGLE_ALLOWED, (theme, angle)
