"""
Regression tests for confirmed bugs fixed in this cycle.

Each test is named after the invariant it pins and includes the
original crash/wrong-value description so CI failures are self-explaining.

Bugs covered
------------
BUG-1  reward.py            ZeroDivisionError on empty skill list (test class
                             removed — curriculum/reward.py deleted in the
                             dormant-cluster cleanup, 2026-05-14 PR 7)
BUG-2  spacing.py           next_interval(_, 0.0) returned 0.0 (test classes
                             removed — curriculum/spacing.py deleted in PR 7)
BUG-3  skills/trainer.py    ZeroDivisionError on empty events list (test class
                             removed — skills/trainer.py deleted in PR 7)
BUG-4a bandit.py            LinUCB.select() returned None on empty actions
                             (test class removed — brain/bandit/contextual_bandit.py
                             deleted in PR 7)
BUG-4b bandit.py            np.linalg.inv raised on near-singular A (test class
                             removed alongside BUG-4a in PR 7)
BUG-5  engine_eval.py       cache key collision (test class removed —
                             engine_eval.py deleted in engine-library cleanup,
                             live cache covered by test_fen_move_cache_key.py)
BUG-6  engine_pool.py       stop()/_started race  (test class removed —
                             engine_pool.py deleted, live lifecycle covered by
                             test_engine_pool_crash_recovery.py / test_engine_pool_exhaustion.py)
BUG-7  auth/service.py      token_hash compared with != (timing attack); must use hmac.compare_digest
BUG-8  storage/repo.py      SQLite connections leaked on exception (no try-finally)
BUG-9  adapt.py             random.choice(sorted_moves[:-1]) IndexError (test
                             class removed — seca/adapt.py deleted in PR 7)
BUG-10 global_bandit.py     GlobalLinUCB.select() same three issues (test class
                             removed — brain/bandit/global_bandit.py deleted in PR 7)
BUG-11 meta_bandit.py       LinUCB.select_action() same three issues (test class
                             removed — brain/meta/meta_bandit.py deleted in PR 7)
BUG-12 outcome_tracker.py:130 4 score components divided by 3.0 (test class
                             removed — outcome_tracker.py + /explanation_outcome
                             HTTP surface deleted in PR 22)

Retired test classes for BUG-1/2/3/4a/4b/9/10/11 are not reinstated:
the bug-fix patches landed years ago, the rest of the freeze guard's
keyword scan + brain allowlist now prevents the dormant code from
being re-introduced to the live runtime, and the modules themselves
no longer exist on disk so there is nothing to pin.  Bug-history
entries are retained above for the audit trail.
"""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


# ---------------------------------------------------------------------------
# BUG-1  reward.py           — ZeroDivisionError on empty skill list (removed)
# BUG-2  spacing.py          — next_interval(_, 0.0) returned 0.0   (removed)
# BUG-3  skills/trainer.py   — ZeroDivisionError on empty events    (removed)
# BUG-4a contextual_bandit   — empty-actions returned None          (removed)
# BUG-4b contextual_bandit   — pinv-vs-inv on near-singular A       (removed)
# ---------------------------------------------------------------------------
#
# Test classes retired in the 2026-05-14 dormant-cluster cleanup (PR 7)
# alongside the modules they pinned:
#
#   - ``llm/seca/curriculum/reward.py``                (BUG-1)
#   - ``llm/seca/curriculum/spacing.py``               (BUG-2)
#   - ``llm/seca/skills/trainer.py``                   (BUG-3)
#   - ``llm/seca/brain/bandit/contextual_bandit.py``   (BUG-4a, BUG-4b)
#
# The original bug-fixes landed years ago and the modules sat dormant
# behind the freeze guard until PR 7 removed them.  No live code path
# depended on these modules; the freeze guard's keyword scan +
# ``brain.*`` allowlist now prevents re-introduction to the live
# runtime.  Bug-history entries above retain the audit trail.


# ---------------------------------------------------------------------------
# BUG-5  engine_eval.py — cache key collision (removed)
# BUG-6  engine_pool.py  — stop() ordering         (removed)
# ---------------------------------------------------------------------------
#
# Both regressions targeted ``llm/engine_eval.py`` and the flat
# ``llm/engine_pool.py``, deleted in the engine-library cleanup
# (2026-05-12) after host_app retirement (PR #111) left them with no
# production callers.  The live ``llm.seca.engines.stockfish.pool`` has
# its own coverage:
#
#   - ``test_fen_move_cache_key.py``        (post-cleanup cache-key sentinel)
#   - ``test_engine_pool_crash_recovery.py`` (post-cleanup lifecycle / stop)
#   - ``test_engine_pool_exhaustion.py``    (acquire-after-stop semantics)
#
# Bug-history entries are kept in the module docstring above for the
# audit trail; the in-file test classes are gone because the modules
# they pinned no longer exist.


# ---------------------------------------------------------------------------
# BUG-7  auth/service.py — token_hash comparison
#
# Originally guarded against a != timing oracle via hmac.compare_digest.
# Removed in PR #66 because the strict check broke JWT rotation
# (AUTH_ROT_01).  Reinstated alongside rotate_session_token in F-07
# (2026-05-11): rotation now ALSO updates session.token_hash, so the
# check is compatible with rotation AND closes the per-token revocation
# gap (a stolen JWT no longer survives until exp).
#
# This class pins both halves of the F-07 contract:
#   - get_player_by_session re-computes sha256(token) + compare_digest
#   - the source uses constant-time comparison (no plain '==' / '!=')
# ---------------------------------------------------------------------------


class TestTokenHashConstantTimeComparison:
    """BUG-7 (post-F-07): get_player_by_session re-computes the hash
    and compares it against session.token_hash using hmac.compare_digest.
    Rotation keeps the hash fresh in router.get_current_player, so the
    check is compatible with the X-Auth-Token rotation feature."""

    def test_service_strict_compares_token_hash_with_constant_time(self):
        """
        Source-level pin: get_player_by_session must hash the inbound
        token and compare it against session.token_hash with
        constant-time hmac.compare_digest.  Removing either side of
        the check reopens the F-07 per-token revocation gap (a stolen
        JWT lives until its 24 h exp).
        """
        import inspect
        from llm.seca.auth import service as svc_module

        source = inspect.getsource(svc_module.AuthService.get_player_by_session)
        assert "hashlib.sha256" in source, (
            "get_player_by_session must recompute sha256(token) so a "
            "previously-rotated JWT no longer validates against the "
            "stored token_hash.  See AUTH_ROT_02 / F-07."
        )
        assert "compare_digest" in source, (
            "get_player_by_session must use hmac.compare_digest for "
            "the hash comparison (constant-time, BUG-7); plain == is a "
            "timing oracle on the token_hash byte distribution."
        )

    def test_service_still_imports_hashlib(self):
        """hashlib is required for both login() (initial hash) and
        get_player_by_session (per-request comparison)."""
        import inspect
        from llm.seca.auth import service as svc_module

        source = inspect.getsource(svc_module)
        assert "import hashlib" in source, (
            "auth/service.py must import hashlib for token_hash "
            "population (login + rotate_session_token) and per-request "
            "comparison (get_player_by_session)."
        )
        assert "import hmac" in source, (
            "auth/service.py must import hmac for the constant-time "
            "compare_digest comparison in get_player_by_session."
        )

    def test_tampered_token_is_rejected(self):
        """F-07: a token whose sha256 does not match session.token_hash
        must be rejected at the service layer, regardless of whether
        the JWT signature was valid upstream.  This is the per-token
        revocation lever: rotation updates the stored hash and the
        old token immediately stops validating."""
        import hashlib
        import uuid
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from llm.seca.auth.models import Base, Player, Session as AuthSession
        from llm.seca.auth.service import AuthService

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine)()

        player = Player(email="t@t.com", password_hash="x", player_embedding="[]")
        db.add(player)
        db.commit()
        db.refresh(player)

        real_token = "valid-token-string"
        token_hash = hashlib.sha256(real_token.encode()).hexdigest()
        session = AuthSession(
            id=str(uuid.uuid4()),
            player_id=player.id,
            token_hash=token_hash,
        )
        db.add(session)
        db.commit()

        svc = AuthService(db)
        # Token whose hash matches the row validates.
        assert svc.get_player_by_session(session.id, real_token) is not None
        # F-07: a different token (hash mismatch) is REJECTED — this is
        # the per-token revocation lever closed by re-introducing the
        # hash check alongside rotate_session_token in router.
        assert svc.get_player_by_session(session.id, "tampered-token") is None
        db.close()


# ---------------------------------------------------------------------------
# BUG-8  storage/repo.py — SQLite connections leaked when exception occurs
# ---------------------------------------------------------------------------


class TestRepoConnectionNotLeaked:
    """BUG-8: repo.py must always close SQLite connections, even on error."""

    def test_repo_source_uses_try_finally_for_connections(self):
        """
        Source-level check: every get_conn() call in repo.py must be paired
        with a try-finally block to guarantee conn.close() is called.
        Before the fix, exceptions between get_conn() and conn.close() left
        connections open indefinitely.
        """
        import inspect
        from llm.seca.storage import repo as repo_module

        source = inspect.getsource(repo_module)
        conn_count = source.count("conn = get_conn()")
        finally_count = source.count("finally:")
        assert finally_count >= conn_count, (
            f"repo.py has {conn_count} get_conn() calls but only "
            f"{finally_count} finally: blocks — some connections can leak."
        )

    def test_repo_has_more_finally_blocks_than_get_conn_calls(self):
        """Alias kept for clarity — delegates to the count check."""
        self.test_repo_source_uses_try_finally_for_connections()

    def test_each_function_in_repo_has_try_finally(self):
        """Every connection-acquiring function in repo.py must have try-finally."""
        import inspect
        from llm.seca.storage import repo as repo_module

        for fn_name in ("ensure_player", "create_game", "finish_game"):
            fn = getattr(repo_module, fn_name)
            src = inspect.getsource(fn)
            assert "finally" in src, (
                f"repo.{fn_name} must use try-finally around get_conn() "
                "to guarantee conn.close() is called even on error"
            )


# ---------------------------------------------------------------------------
# BUG-9   adapt.py            — random.choice IndexError on 1-move (removed)
# BUG-10  global_bandit.py    — GlobalLinUCB.select() empty + pinv  (removed)
# BUG-11  meta_bandit.py      — LinUCB.select_action()              (removed)
# ---------------------------------------------------------------------------
#
# Test classes retired in the 2026-05-14 dormant-cluster cleanup (PR 7)
# alongside the modules they pinned:
#
#   - ``llm/seca/adapt.py``                       (BUG-9)
#   - ``llm/seca/brain/bandit/global_bandit.py``  (BUG-10)
#   - ``llm/seca/brain/meta/meta_bandit.py``      (BUG-11)
#
# Same retirement rationale as BUG-1/2/3/4a/4b above: fixes landed
# years ago, modules sat dormant behind the freeze guard, PR 7
# removed them.  Freeze-guard keyword scan + brain allowlist prevents
# re-introduction.  Audit trail in the docstring header.


# ---------------------------------------------------------------------------
# BUG-12  RETIRED in PR 22 (2026-05-15).  The outcome_tracker.py module was
# deleted alongside the /explanation_outcome HTTP surface (no Android caller
# ever emerged; the handler always returned 400 since no callsite ever
# called record_explanation to register an id).  The score-normalisation
# invariant the test pinned is no longer load-bearing because the
# code it pinned no longer exists.
# ---------------------------------------------------------------------------
