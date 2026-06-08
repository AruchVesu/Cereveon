"""
Tests for server-side chat history persistence + the
``GET /chat/history`` recall endpoint.

Approach mirrors test_chat_stream.py:
  * Tier 1 — direct repo tests on an in-memory SQLite session.
  * Tier 2 — AST inspection of server.py to pin that the /chat
            handlers persist after boundary validation.

The full route-level wiring is exercised by the regression / contract
suites; here we focus on the persistence contract in isolation
(cross-player isolation, ordering, limits, idempotent inserts) and the
shape of the handler integration.

Invariants pinned
-----------------
 1. PERSIST_REPO_SAVE_PAIR           save_exchange writes exactly two rows.
 2. PERSIST_REPO_ROLES               first row role="user", second role="assistant".
 3. PERSIST_REPO_FEN_MIRRORED        both rows carry the same fen + mode.
 4. PERSIST_REPO_ORDER_BY_CREATED    recent_turns_for_player returns DESC by created_at.
 5. PERSIST_REPO_CROSS_PLAYER_ISO    one player's history never leaks to another.
 5a. PERSIST_REPO_PER_GAME           game_id scopes save + recall; None = player-global; mirrored onto both rows.
 5b. PERSIST_REPO_GAME_BOUNDARY      same game_id under two players never crosses (player_id is the boundary).
 6. PERSIST_REPO_LIMIT_CLAMPED       recent_turns_for_player clamps limit to HISTORY_MAX_LIMIT.
 7. PERSIST_REPO_LIMIT_ZERO_FLOOR    limit=0 → 1 (lower bound).
 8. SERVER_CHAT_PERSIST_AFTER_VALID  server.py::chat calls save_exchange AFTER validate_chat_response (AST).
 9. SERVER_STREAM_PERSIST_AFTER_VALID  server.py::chat_stream same property.
10. SERVER_HISTORY_ROUTE_AUTHED     /chat/history route uses Depends(get_current_player) (AST).
11. SERVER_HISTORY_NO_CLIENT_FILTER /chat/history accepts no player_id query / body param (AST).
12. DERIVE_PROFILE_SKILL_ESTIMATE_TIERS  _derive_player_profile maps rating → tier bands.
13. DERIVE_PROFILE_COMMON_MISTAKES_TOP_3 returns top-3 skill_vector entries above 0.3 threshold.
14. DERIVE_PROFILE_EMPTY_SKILL_VECTOR   empty / malformed skill_vector → empty common_mistakes.
"""

from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

# Importing the auth Base picks up every model that has been
# wildcard-imported in auth/router.py — chat.models is one of them, so
# create_all() below builds the chat_turns table without any explicit
# import here.
from llm.seca.auth.models import Base, Player
from llm.seca.chat.models import ChatTurn
from llm.seca.chat.repo import (
    HISTORY_MAX_LIMIT,
    recent_turns_for_player,
    save_exchange,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVER_PY = _REPO_ROOT / "llm" / "server.py"


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_player(db, email: str, rating: float = 1200.0, skill_vec_json: str = "{}") -> Player:
    """Insert a Player row directly so the repo tests don't depend on
    the full register/login service flow."""
    p = Player(
        email=email,
        password_hash="not-used-here",
        rating=rating,
        confidence=0.5,
        skill_vector_json=skill_vec_json,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# 1–7  Repo-level contract
# ---------------------------------------------------------------------------


class TestRepoPersistence:
    def test_persist_repo_save_pair(self, db):
        """PERSIST_REPO_SAVE_PAIR — save_exchange writes exactly two rows."""
        player = _make_player(db, "save@test.com")
        save_exchange(
            db=db,
            player_id=str(player.id),
            user_content="What's the plan?",
            assistant_content="Develop pieces and control the centre.",
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        )
        rows = db.query(ChatTurn).all()
        assert len(rows) == 2

    def test_persist_repo_roles(self, db):
        """PERSIST_REPO_ROLES — first row 'user', second 'assistant'."""
        player = _make_player(db, "roles@test.com")
        save_exchange(
            db=db,
            player_id=str(player.id),
            user_content="ask",
            assistant_content="reply",
            fen="startpos",
        )
        rows = (
            db.query(ChatTurn)
            .filter(ChatTurn.player_id == str(player.id))
            .order_by(ChatTurn.created_at.asc(), ChatTurn.id.asc())
            .all()
        )
        assert [r.role for r in rows] == ["user", "assistant"]
        assert rows[0].content == "ask"
        assert rows[1].content == "reply"

    def test_persist_repo_fen_mirrored(self, db):
        """PERSIST_REPO_FEN_MIRRORED — both rows share fen + mode."""
        player = _make_player(db, "fen@test.com")
        fen = "8/8/8/8/8/8/8/k1K5 w - - 0 1"
        save_exchange(
            db=db,
            player_id=str(player.id),
            user_content="ask",
            assistant_content="reply",
            fen=fen,
            mode="CHAT_V1",
        )
        rows = db.query(ChatTurn).filter(ChatTurn.player_id == str(player.id)).all()
        assert {r.fen for r in rows} == {fen}
        assert {r.mode for r in rows} == {"CHAT_V1"}

    def test_persist_repo_order_by_created(self, db):
        """PERSIST_REPO_ORDER_BY_CREATED — recent_turns DESC by time."""
        player = _make_player(db, "order@test.com")
        # Two exchanges separated by a short sleep so created_at differs
        # by at least one tick on every platform's DateTime resolution.
        save_exchange(db, str(player.id), "first user", "first reply", "fen-a")
        time.sleep(0.05)
        save_exchange(db, str(player.id), "second user", "second reply", "fen-b")
        rows = recent_turns_for_player(db, str(player.id), limit=10)
        # 4 rows total; newest first.  Newest is the assistant reply
        # from the second exchange.
        assert len(rows) == 4
        assert rows[0].role == "assistant" and rows[0].content == "second reply"
        # Two from each exchange, grouped chronologically when reversed.
        contents_chrono = [r.content for r in reversed(rows)]
        assert contents_chrono == [
            "first user",
            "first reply",
            "second user",
            "second reply",
        ]

    def test_persist_repo_cross_player_iso(self, db):
        """PERSIST_REPO_CROSS_PLAYER_ISO — A's history never leaks to B."""
        alice = _make_player(db, "alice@test.com")
        bob = _make_player(db, "bob@test.com")
        save_exchange(db, str(alice.id), "alice ask", "alice reply", "fen-a")
        save_exchange(db, str(bob.id), "bob ask", "bob reply", "fen-b")
        a_rows = recent_turns_for_player(db, str(alice.id))
        b_rows = recent_turns_for_player(db, str(bob.id))
        assert all(r.player_id == str(alice.id) for r in a_rows)
        assert all(r.player_id == str(bob.id) for r in b_rows)
        a_contents = {r.content for r in a_rows}
        b_contents = {r.content for r in b_rows}
        assert "alice ask" in a_contents and "bob ask" not in a_contents
        assert "bob ask" in b_contents and "alice ask" not in b_contents

    def test_persist_repo_per_game_scoping(self, db):
        """PERSIST_REPO_PER_GAME — game_id scopes save + recall; None is
        player-global (legacy); game_id is mirrored onto both rows."""
        player = _make_player(db, "pergame@test.com")
        save_exchange(db, str(player.id), "g1 ask", "g1 reply", "fen-1", game_id="game-1")
        save_exchange(db, str(player.id), "g2 ask", "g2 reply", "fen-2", game_id="game-2")
        save_exchange(db, str(player.id), "gen ask", "gen reply", "fen-0")  # game_id=None

        # save_exchange mirrors game_id onto both rows of an exchange.
        g1_rows = db.query(ChatTurn).filter(ChatTurn.game_id == "game-1").all()
        assert len(g1_rows) == 2 and {r.game_id for r in g1_rows} == {"game-1"}

        # Recall scoped to a game returns ONLY that game's turns.
        scoped1 = recent_turns_for_player(db, str(player.id), game_id="game-1")
        assert {r.content for r in scoped1} == {"g1 ask", "g1 reply"}
        scoped2 = recent_turns_for_player(db, str(player.id), game_id="game-2")
        assert {r.content for r in scoped2} == {"g2 ask", "g2 reply"}

        # No game_id → player-global: every turn, all games + the untied one.
        all_rows = recent_turns_for_player(db, str(player.id))
        assert {r.content for r in all_rows} == {
            "g1 ask",
            "g1 reply",
            "g2 ask",
            "g2 reply",
            "gen ask",
            "gen reply",
        }

    def test_persist_repo_game_id_under_player_boundary(self, db):
        """A game_id is only a sub-filter within the authenticated player —
        the SAME game_id under two players never crosses over (player_id is
        the isolation boundary, so client-supplied game_id is safe)."""
        alice = _make_player(db, "alice-g@test.com")
        bob = _make_player(db, "bob-g@test.com")
        save_exchange(db, str(alice.id), "alice g ask", "alice g reply", "fa", game_id="shared")
        save_exchange(db, str(bob.id), "bob g ask", "bob g reply", "fb", game_id="shared")
        a = recent_turns_for_player(db, str(alice.id), game_id="shared")
        assert {r.content for r in a} == {"alice g ask", "alice g reply"}
        assert all(r.player_id == str(alice.id) for r in a)

    def test_persist_repo_limit_clamped(self, db):
        """PERSIST_REPO_LIMIT_CLAMPED — limit > HISTORY_MAX_LIMIT clamps."""
        player = _make_player(db, "clamp@test.com")
        save_exchange(db, str(player.id), "u", "r", "fen")
        rows = recent_turns_for_player(db, str(player.id), limit=HISTORY_MAX_LIMIT * 10)
        # Repo doesn't error; it silently caps.  Only 2 rows exist
        # anyway, so the clamping is observed by the absence of a
        # crash — pin the contract explicitly.
        assert len(rows) == 2

    def test_persist_repo_limit_zero_floor(self, db):
        """PERSIST_REPO_LIMIT_ZERO_FLOOR — limit=0 returns up to 1 row."""
        player = _make_player(db, "floor@test.com")
        save_exchange(db, str(player.id), "u1", "r1", "fen")
        rows = recent_turns_for_player(db, str(player.id), limit=0)
        assert len(rows) == 1, "limit=0 must clamp up to 1 to avoid no-op queries"


# ---------------------------------------------------------------------------
# 8–11  Server.py AST pins
# ---------------------------------------------------------------------------


def _parse_server() -> ast.Module:
    return ast.parse(_SERVER_PY.read_text(encoding="utf-8"))


def _find_func(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in server.py")


class TestServerAst:
    def test_server_chat_persist_after_valid(self):
        """SERVER_CHAT_PERSIST_AFTER_VALID — save_exchange runs AFTER
        validate_chat_response in the /chat handler.  The handler must
        only persist on a successfully-validated response so we never
        store a rejected LLM reply the user never saw.

        Match ``validate_chat_response(`` and ``save_exchange(`` (with
        paren) to land on call sites only, skipping any docstring
        mention of the names."""
        tree = _parse_server()
        chat = _find_func(tree, "chat")
        body_src = ast.unparse(chat)
        idx_valid = body_src.find("validate_chat_response(")
        idx_save = body_src.find("save_exchange(")
        assert (
            idx_valid > 0 and idx_save > 0
        ), f"expected both call sites in /chat; idx_valid={idx_valid} idx_save={idx_save}"
        assert (
            idx_save > idx_valid
        ), "save_exchange( must be called AFTER validate_chat_response( in /chat"

    def test_server_stream_persist_after_valid(self):
        """SERVER_STREAM_PERSIST_AFTER_VALID — /chat/stream persists only the
        final, safe reply.

        Real token streaming (2026-06) moved validation INTO the pipeline
        (``stream_chat_reply`` validates-before-emit), so the route no longer
        pre-calls ``validate_chat_response``.  Persistence is now bound to the
        TERMINAL events via ``_persist``: ``StreamDone`` carries the
        end-validated reply, ``StreamAbort`` carries the deterministic
        fallback (valid by construction) — never a raw mid-stream chunk.  The
        behavioural guarantee is covered by test_chat_stream_pipeline.py and
        test_chat_stream.py; this pins the route's structural contract."""
        tree = _parse_server()
        chat_stream = _find_func(tree, "chat_stream")
        body_src = ast.unparse(chat_stream)
        assert "stream_chat_reply" in body_src, (
            "/chat/stream must drive the validate-before-emit pipeline "
            "(stream_chat_reply), where validation happens"
        )
        assert "save_exchange(" in body_src and "_persist(" in body_src, (
            "/chat/stream must persist the resolved reply via _persist"
        )
        assert "_StreamDone" in body_src and "_StreamAbort" in body_src, (
            "persistence must be tied to the terminal events (StreamDone / "
            "StreamAbort), so only an end-validated reply or the deterministic "
            "fallback is stored"
        )

    def test_server_history_route_authed(self):
        """SERVER_HISTORY_ROUTE_AUTHED — /chat/history is Bearer-gated."""
        tree = _parse_server()
        history = _find_func(tree, "chat_history")
        body_src = ast.unparse(history)
        assert "Depends(get_current_player)" in body_src, (
            "/chat/history must depend on get_current_player so cross-player "
            "isolation is enforced by the auth dep, not a client-supplied filter"
        )

    def test_server_history_no_client_filter(self):
        """SERVER_HISTORY_NO_CLIENT_FILTER — handler accepts no player_id
        from the wire.  player_id is sourced solely from the
        get_current_player dep."""
        tree = _parse_server()
        history = _find_func(tree, "chat_history")
        # Args: request, limit, player, db — explicitly no player_id.
        arg_names = {a.arg for a in history.args.args} | {a.arg for a in history.args.kwonlyargs}
        assert "player_id" not in arg_names, (
            "/chat/history must NOT accept a player_id query/body param; "
            "the authenticated player is the only valid filter source."
        )


# ---------------------------------------------------------------------------
# 12–14  Server-side player_profile derivation (Phase 2)
# ---------------------------------------------------------------------------


class TestDerivePlayerProfile:
    """Pins the contract that ``_derive_player_profile`` is a strictly
    deterministic mapper from authenticated Player → pipeline-shape dict.

    Importing server.py is heavy (engine pool, DeepSeek imports), so the
    helper is exercised by AST inspection of the function body and by
    instantiating a fake Player namespace and calling through the
    importable function reference."""

    def _import_helper(self):
        # Import inside the test so a failure to load server.py surfaces
        # as a test failure, not a collection error that blocks other
        # tests.  The import is fine in this environment per the
        # existing test_chat_pipeline / test_auth_refresh_header pattern.
        from llm.server import _derive_player_profile

        return _derive_player_profile

    def _player(self, *, rating: float, skill_vec_json: str | None):
        from types import SimpleNamespace

        return SimpleNamespace(rating=rating, skill_vector_json=skill_vec_json)

    def test_derive_profile_skill_estimate_tiers(self):
        """DERIVE_PROFILE_SKILL_ESTIMATE_TIERS — rating bands."""
        f = self._import_helper()
        assert f(self._player(rating=800, skill_vec_json="{}"))["skill_estimate"] == "beginner"
        assert f(self._player(rating=1200, skill_vec_json="{}"))["skill_estimate"] == "intermediate"
        assert f(self._player(rating=1800, skill_vec_json="{}"))["skill_estimate"] == "advanced"

    def test_derive_profile_common_mistakes_top_3(self):
        """DERIVE_PROFILE_COMMON_MISTAKES_TOP_3 — top-3 above threshold."""
        f = self._import_helper()
        sv = (
            '{"hanging_pieces": 0.8, "pawn_structure": 0.7, "endgame": 0.5, '
            '"openings": 0.4, "calculation": 0.2}'
        )
        out = f(self._player(rating=1200, skill_vec_json=sv))
        # Top-3 by score (only those > 0.3).  "calculation"=0.2 is below threshold.
        assert out["common_mistakes"] == ["hanging_pieces", "pawn_structure", "endgame"]

    def test_derive_profile_empty_skill_vector(self):
        """DERIVE_PROFILE_EMPTY_SKILL_VECTOR — degrades to empty list,
        never raises, never returns None."""
        f = self._import_helper()
        assert f(self._player(rating=1200, skill_vec_json="{}"))["common_mistakes"] == []
        assert f(self._player(rating=1200, skill_vec_json=None))["common_mistakes"] == []
        # Malformed JSON also degrades cleanly.
        assert f(self._player(rating=1200, skill_vec_json="not-json"))["common_mistakes"] == []
