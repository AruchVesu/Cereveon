"""SQLAlchemy-backed storage helpers for games, moves, explanations,
repertoire, and bandit weights.

History
-------
Pre-2026-05-09 these helpers used raw ``sqlite3`` against a hardcoded
``data/seca.db`` while the auth tables (``players`` / ``sessions``)
lived in SQLAlchemy and could be backed by Postgres in production via
``DATABASE_URL``.  The two paths drifted into different physical
databases under the production deployment, breaking the
``games.player_id → players.id`` foreign key:

    sqlite3.IntegrityError: FOREIGN KEY constraint failed

at the start of every ``/game/start`` request because Postgres held
the player rows but SQLite held the games table.

The migration (this file + ``llm/seca/storage/models.py``) unifies
ownership: every table is now declared in SQLAlchemy and lives in
whatever database ``DATABASE_URL`` points at — SQLite in dev/tests,
Postgres in production.

Public function signatures are preserved so callers don't need to
change.  Internally, every function obtains a short-lived
``SessionLocal`` session, performs the work in a single transaction,
commits on success, and closes the session.  This mirrors the prior
"open conn / do work / commit / close" shape and keeps each helper
self-contained without forcing the call site to manage a session.
"""

# Two narrowly-scoped pylint disables:
#   - ``redefined-builtin``: the ``Move`` model preserves the legacy SQL
#     column name ``eval`` (matching the raw-sqlite3 schema before the
#     SQLAlchemy migration); shadowing the Python builtin is a known
#     trade-off documented in storage/models.py:Move.
#   - ``invalid-name``: SQL-side identifiers like ``A_json`` keep their
#     UPPER_LETTER variant because the LinUCB sufficient-stats convention
#     uses capital A / b for the matrix / vector, and matching the
#     column name verbatim is clearer than re-spelling them at the
#     function-signature layer.
# pylint: disable=redefined-builtin,invalid-name

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import and_, asc, desc, func, select
from sqlalchemy.orm import Session

from llm.seca.storage.models import (
    BanditWeights,
    Game,
    Repertoire,
)

# -------------------------------------------------
# Session helper
# -------------------------------------------------


def _session() -> Session:
    """Open a short-lived ORM session bound to the project-wide engine.

    Resolved lazily so that test fixtures monkey-patching
    ``llm.seca.auth.router.SessionLocal`` to point at a temp DB
    actually take effect: a module-level ``from ... import SessionLocal``
    would bind the original sessionmaker once at import time and the
    patched value would never be read.

    The import lives inside the function (rather than at module level)
    for the same reason — Python caches the imported name in the
    module's namespace at import time, so a later
    ``monkeypatch.setattr(auth_router, "SessionLocal", new)`` would not
    touch the cached ``repo.SessionLocal`` reference.  Re-resolving
    ``auth_router.SessionLocal`` on every call sidesteps that.
    """
    from llm.seca.auth import router as auth_router

    return auth_router.SessionLocal()


# -------------------------------------------------
# Player
# -------------------------------------------------


def ensure_player(player_id: str) -> None:
    """Insert a placeholder ``players`` row if one doesn't already exist.

    Pre-migration this used ``INSERT OR IGNORE`` against the raw-sqlite
    ``players`` table that ``schema.sql`` created.  Post-migration the
    only ``players`` table is the SQLAlchemy ``Player`` model in
    ``auth/models.py``, which has ``email`` / ``password_hash``
    NOT-NULL columns.

    To avoid corrupting real auth state, ``ensure_player`` only inserts
    a placeholder row when the id doesn't already exist AND no auth
    record will be later overwritten.  The helper is defensive about
    nullable columns: it provides synthetic ``email`` / ``password_hash``
    values that can never collide with a real-user value (the leading
    ``__placeholder__::`` prefix is reserved).

    Production callers reach this helper only via ``/move`` for
    authenticated users — so by the time we get here a real
    ``players`` row already exists for the JWT subject and the
    INSERT path is skipped.  The placeholder logic exists for
    test fixtures that exercise ``create_game(some-fake-id)`` without
    going through registration.
    """
    # Defer the import so the auth.models Base/Player wiring stays
    # internal to repo.py and never imports auth.models from a model
    # consumer that just needs ``Game`` / ``Move`` etc.
    from llm.seca.auth.models import Player

    sess = _session()
    try:
        existing = sess.get(Player, player_id)
        if existing is not None:
            return
        # Placeholder — production callers don't hit this path because
        # the JWT-auth dependency materialises a real Player row before
        # any /move call.
        placeholder = Player(
            id=player_id,
            email=f"__placeholder__::{player_id}",
            password_hash="__placeholder__",
        )
        sess.add(placeholder)
        sess.commit()
    finally:
        sess.close()


# -------------------------------------------------
# Game lifecycle
# -------------------------------------------------


def get_or_create_auto_game(player_id: str) -> str:
    """Return a stable per-player game ID for non-session move logging.

    Uses a deterministic ``auto-{player_id}`` key so all moves from the
    same authenticated player are grouped under one row until a real
    session system replaces this (see server.py /game/start endpoint).
    """
    ensure_player(player_id)
    game_id = f"auto-{player_id}"

    sess = _session()
    try:
        existing = sess.get(Game, game_id)
        if existing is None:
            sess.add(Game(id=game_id, player_id=player_id))
            sess.commit()
    finally:
        sess.close()
    return game_id


def create_game(player_id: str) -> str:
    """Create a new ``games`` row for the player and return its UUID."""
    ensure_player(player_id)

    game_id = str(uuid.uuid4())
    sess = _session()
    try:
        sess.add(Game(id=game_id, player_id=player_id))
        sess.commit()
    finally:
        sess.close()

    return game_id


def finish_game(game_id: str, result: str) -> None:
    """Mark a game finished, stamping ``finished_at`` to now (UTC)."""
    sess = _session()
    try:
        game = sess.get(Game, game_id)
        if game is None:
            return
        game.result = result
        game.finished_at = datetime.utcnow()
        sess.commit()
    finally:
        sess.close()


# -------------------------------------------------
# In-progress checkpoint (cross-device resume)
# -------------------------------------------------


def checkpoint_game(game_id: str, fen: str, uci_history: str) -> bool:
    """Persist the in-progress state for ``game_id``.  No-ops (returns
    False) when the row is already finished or doesn't exist — sliding
    a checkpoint onto a closed game would create a phantom resume entry
    the user couldn't actually pick up.

    Returns True iff the UPDATE touched a row.
    """
    sess = _session()
    try:
        game = sess.get(Game, game_id)
        if game is None or game.finished_at is not None:
            return False
        game.current_fen = fen
        game.current_uci_history = uci_history
        game.last_checkpoint_at = datetime.utcnow()
        sess.commit()
        return True
    finally:
        sess.close()


def get_active_game(player_id: str) -> dict | None:
    """Return the player's most recent unfinished game with a non-null
    checkpoint, or None if there isn't one.

    Shape (when present):
        {
          "game_id":             str,
          "current_fen":         str,
          "current_uci_history": str,
          "last_checkpoint_at":  datetime,
          "started_at":          datetime,
        }

    Filters:
      - Same player_id.
      - finished_at IS NULL  (unfinished).
      - current_fen IS NOT NULL  (a checkpoint was actually written;
        avoids returning rows for /game/start calls where the user
        never played a single move).

    Order: most-recent last_checkpoint_at first.
    """
    sess = _session()
    try:
        stmt = (
            select(Game)
            .where(
                and_(
                    Game.player_id == player_id,
                    Game.finished_at.is_(None),
                    Game.current_fen.is_not(None),
                )
            )
            .order_by(desc(Game.last_checkpoint_at))
            .limit(1)
        )
        game = sess.execute(stmt).scalar_one_or_none()
        if game is None:
            return None
        return {
            "game_id": game.id,
            "current_fen": game.current_fen,
            "current_uci_history": game.current_uci_history or "",
            "last_checkpoint_at": game.last_checkpoint_at,
            "started_at": game.started_at,
        }
    finally:
        sess.close()


def get_game_owner_status(game_id: str) -> tuple[str | None, datetime | None] | None:
    """Look up ``(player_id, finished_at)`` for ``game_id`` directly.

    Returns ``None`` when the row doesn't exist.  Used by the
    ``/game/{id}/checkpoint`` endpoint to distinguish 404 (no such
    game) from 403 (wrong owner) from 409 (already finished) without
    pulling the full row through ``get_active_game``'s checkpoint
    filters.

    Replaces the inline ``get_conn().execute("SELECT player_id, ...")``
    that lived in ``server.py`` pre-migration; centralising it here
    keeps every direct DB read inside the storage layer.
    """
    sess = _session()
    try:
        game = sess.get(Game, game_id)
        if game is None:
            return None
        return (game.player_id, game.finished_at)
    finally:
        sess.close()


# -------------------------------------------------
# Repertoire (opening study)
# -------------------------------------------------


def update_opening_mastery(player_id: str, eco: str, new_mastery: float) -> bool:
    """Set the mastery of an existing opening row.  Returns True iff a
    row was actually updated.

    Caller is responsible for clamping new_mastery to [0, 1] — this
    helper just writes whatever it's given so the server-side endpoint
    can keep the bounds policy in one place (server.py
    drill_result_endpoint).
    """
    sess = _session()
    try:
        stmt = select(Repertoire).where(
            and_(Repertoire.player_id == player_id, Repertoire.eco == eco)
        )
        row = sess.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        row.mastery = float(new_mastery)
        row.updated_at = datetime.utcnow()
        sess.commit()
        return True
    finally:
        sess.close()


def upsert_opening(
    player_id: str,
    eco: str,
    name: str,
    line: str,
    mastery: float = 0.0,
    is_active: bool = False,
    ordinal: int | None = None,
) -> None:
    """Insert or update an opening line for the player.

    Conflict resolution: the (player_id, eco) UNIQUE constraint means
    re-inserting an existing eco updates the row in place — name /
    line / mastery / is_active / ordinal can drift over time without
    creating duplicates.

    When ordinal is None, the new row is appended at the end of the
    player's list (max(ordinal) + 1).
    """
    sess = _session()
    try:
        if ordinal is None:
            current_max = sess.execute(
                select(func.coalesce(func.max(Repertoire.ordinal), -1)).where(
                    Repertoire.player_id == player_id
                )
            ).scalar_one()
            ordinal = int(current_max) + 1

        stmt = select(Repertoire).where(
            and_(Repertoire.player_id == player_id, Repertoire.eco == eco)
        )
        existing = sess.execute(stmt).scalar_one_or_none()
        now = datetime.utcnow()
        if existing is None:
            sess.add(
                Repertoire(
                    player_id=player_id,
                    eco=eco,
                    name=name,
                    line=line,
                    mastery=float(mastery),
                    is_active=1 if is_active else 0,
                    ordinal=int(ordinal),
                    updated_at=now,
                )
            )
        else:
            existing.name = name
            existing.line = line
            existing.mastery = float(mastery)
            existing.is_active = 1 if is_active else 0
            existing.ordinal = int(ordinal)
            existing.updated_at = now
        sess.commit()
    finally:
        sess.close()


def delete_opening(player_id: str, eco: str) -> bool:
    """Remove an opening from the player's repertoire.  Returns True iff
    a row was actually deleted (false for a non-existent eco)."""
    sess = _session()
    try:
        stmt = select(Repertoire).where(
            and_(Repertoire.player_id == player_id, Repertoire.eco == eco)
        )
        row = sess.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        sess.delete(row)
        sess.commit()
        return True
    finally:
        sess.close()


def set_active_opening(player_id: str, eco: str) -> bool:
    """Mark ``eco`` as the player's active line, demoting any other
    currently-active row to inactive.  Returns True iff a row was
    successfully promoted (false when the eco doesn't exist for this
    player).

    Two writes in one transaction so the "exactly one active"
    invariant holds at every observable moment.
    """
    sess = _session()
    try:
        stmt = select(Repertoire).where(
            and_(Repertoire.player_id == player_id, Repertoire.eco == eco)
        )
        target = sess.execute(stmt).scalar_one_or_none()
        if target is None:
            return False

        now = datetime.utcnow()
        # Demote everything else.
        others_stmt = select(Repertoire).where(
            and_(Repertoire.player_id == player_id, Repertoire.eco != eco)
        )
        for other in sess.execute(others_stmt).scalars():
            other.is_active = 0
            other.updated_at = now

        # Promote the target.
        target.is_active = 1
        target.updated_at = now
        sess.commit()
        return True
    finally:
        sess.close()


def seed_default_repertoire(player_id: str, defaults: Iterable[dict]) -> int:
    """Insert the canonical default repertoire for a player who has
    nothing stored.  No-op (returns 0) when the player already has
    at least one row.  Returns the number of rows inserted.

    Called by the editing endpoints (POST/DELETE/active) before they
    operate so the user can edit the defaults they see in the GET
    response without an extra "save defaults" step.
    """
    defaults = list(defaults)
    sess = _session()
    try:
        existing = sess.execute(
            select(Repertoire.id).where(Repertoire.player_id == player_id).limit(1)
        ).first()
        if existing is not None:
            return 0
        for entry in defaults:
            sess.add(
                Repertoire(
                    player_id=player_id,
                    eco=entry["eco"],
                    name=entry["name"],
                    line=entry["line"],
                    mastery=float(entry["mastery"]),
                    is_active=1 if entry["is_active"] else 0,
                    ordinal=int(entry["ordinal"]),
                )
            )
        sess.commit()
        return len(defaults)
    finally:
        sess.close()


def list_repertoire(player_id: str) -> list[dict]:
    """Return the player's opening repertoire ordered by ``ordinal``,
    or an empty list when nothing is stored.

    Caller (server.py /repertoire) is responsible for substituting the
    canonical defaults when the list is empty — the repo doesn't invent
    rows for an unknown player.
    """
    sess = _session()
    try:
        stmt = (
            select(Repertoire)
            .where(Repertoire.player_id == player_id)
            .order_by(asc(Repertoire.ordinal), asc(Repertoire.id))
        )
        rows = sess.execute(stmt).scalars().all()
    finally:
        sess.close()
    return [
        {
            "eco": r.eco,
            "name": r.name,
            "line": r.line,
            "mastery": float(r.mastery),
            "is_active": bool(r.is_active),
            "ordinal": int(r.ordinal),
        }
        for r in rows
    ]


# -------------------------------------------------
# Bandit weights (LinUCB sufficient statistics)
# -------------------------------------------------


def load_bandit_weights(player_id: str, action: str) -> dict | None:
    """Read a single (player, action) row from ``bandit_weights``.

    Returns None when the player+action pair has never been recorded
    — caller (decision module) initialises a fresh identity-A, zero-b
    in that case.

    Schema:
        {n_features: int, A_json: str, b_json: str, alpha: float}
    A and b are returned as JSON-encoded strings; the decision module
    deserialises them via numpy.
    """
    sess = _session()
    try:
        stmt = select(BanditWeights).where(
            and_(BanditWeights.player_id == player_id, BanditWeights.action == action)
        )
        row = sess.execute(stmt).scalar_one_or_none()
    finally:
        sess.close()
    if row is None:
        return None
    return {
        "n_features": int(row.n_features),
        "A_json": row.A_json,
        "b_json": row.b_json,
        "alpha": float(row.alpha),
    }


def save_bandit_weights(
    player_id: str,
    action: str,
    n_features: int,
    A_json: str,
    b_json: str,
    alpha: float,
) -> None:
    """Upsert one (player, action) row into ``bandit_weights``.

    The UNIQUE(player_id, action) constraint means re-saving an existing
    pair updates the existing row; the row count grows only with new
    actions or new players.
    """
    sess = _session()
    try:
        stmt = select(BanditWeights).where(
            and_(BanditWeights.player_id == player_id, BanditWeights.action == action)
        )
        existing = sess.execute(stmt).scalar_one_or_none()
        now = datetime.utcnow()
        if existing is None:
            sess.add(
                BanditWeights(
                    player_id=player_id,
                    action=action,
                    n_features=int(n_features),
                    A_json=A_json,
                    b_json=b_json,
                    alpha=float(alpha),
                    updated_at=now,
                )
            )
        else:
            existing.n_features = int(n_features)
            existing.A_json = A_json
            existing.b_json = b_json
            existing.alpha = float(alpha)
            existing.updated_at = now
        sess.commit()
    finally:
        sess.close()


def reset_bandit_weights(player_id: str, action: str | None = None) -> None:
    """Wipe the player's bandit state.  When ``action`` is given, only
    that (player, action) row is deleted; when ``None``, every action
    for the player is cleared.

    Provided so ``llm/seca/brain/bandit/decision.reset_player`` can stop
    reaching directly into the storage layer's connection.  Caller path:
    diagnostic helper + the /seca-doctor command-line tool.
    """
    sess = _session()
    try:
        if action is None:
            stmt: Any = select(BanditWeights).where(BanditWeights.player_id == player_id)
        else:
            stmt = select(BanditWeights).where(
                and_(
                    BanditWeights.player_id == player_id,
                    BanditWeights.action == action,
                )
            )
        for row in sess.execute(stmt).scalars():
            sess.delete(row)
        sess.commit()
    finally:
        sess.close()


# -------------------------------------------------
# Moves — RETIRED in PR 24 (2026-05-15).  ``log_move`` had no live
# callers anywhere in the repo after the /move HTTP endpoint was
# retired in PR 23.  Function deleted; the ``Move`` SQLAlchemy class
# remains in ``models.py`` so the ``moves`` table on existing
# production databases isn't disturbed — schema retirement is a
# separate migration concern (same handling as ``Explanation``).
# -------------------------------------------------


# -------------------------------------------------
# Explanations — RETIRED in PR 22 (2026-05-15).  ``log_explanation`` /
# ``update_learning_score`` had no live callers anywhere in the repo
# after ``/explanation_outcome`` retirement (no production path ever
# called ``log_explanation`` to register an id, so every call to the
# HTTP endpoint already returned 400).  Functions deleted; the
# ``Explanation`` SQLAlchemy class remains in ``models.py`` so the
# table on existing production databases isn't disturbed — schema
# retirement is a separate migration concern.
