import uuid
from .db import get_conn

# -------------------------------------------------
# Player
# -------------------------------------------------


def ensure_player(player_id: str):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO players (id) VALUES (?)",
            (player_id,),
        )
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------
# Game lifecycle
# -------------------------------------------------


def get_or_create_auto_game(player_id: str) -> str:
    """Return a stable per-player game ID for non-session move logging.

    Uses a deterministic `auto-{player_id}` key so all moves from the same
    authenticated player are grouped under one row until a real session system
    replaces this (see server.py /game/start endpoint).
    """
    ensure_player(player_id)
    game_id = f"auto-{player_id}"
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO games (id, player_id) VALUES (?, ?)",
            (game_id, player_id),
        )
        conn.commit()
    finally:
        conn.close()
    return game_id


def create_game(player_id: str) -> str:
    ensure_player(player_id)

    game_id = str(uuid.uuid4())

    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO games (id, player_id) VALUES (?, ?)",
            (game_id, player_id),
        )
        conn.commit()
    finally:
        conn.close()

    return game_id


def finish_game(game_id: str, result: str):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE games SET result = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result, game_id),
        )
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------
# In-progress checkpoint (cross-device resume)
# -------------------------------------------------


def checkpoint_game(game_id: str, fen: str, uci_history: str) -> bool:
    """Persist the in-progress state for [game_id].  No-ops (returns
    False) when the row is already finished or doesn't exist — sliding
    a checkpoint onto a closed game would create a phantom resume
    entry the user couldn't actually pick up.

    Returns True iff the UPDATE touched a row.
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE games
               SET current_fen = ?,
                   current_uci_history = ?,
                   last_checkpoint_at = CURRENT_TIMESTAMP
             WHERE id = ?
               AND finished_at IS NULL
            """,
            (fen, uci_history, game_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_opening_mastery(player_id: str, eco: str, new_mastery: float) -> bool:
    """Set the mastery of an existing opening row.  Returns True iff
    a row was actually updated.

    Caller is responsible for clamping new_mastery to [0, 1] — this
    helper just writes whatever it's given so the server-side endpoint
    can keep the bounds policy in one place (server.py
    drill_result_endpoint).
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE repertoire
               SET mastery = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE player_id = ? AND eco = ?
            """,
            (float(new_mastery), player_id, eco),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


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
    re-inserting an existing eco updates the row in place — name/
    line/mastery/is_active/ordinal can drift over time without
    creating duplicates.

    When ordinal is None, the new row is appended at the end of
    the player's list (max(ordinal) + 1).
    """
    if ordinal is None:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) FROM repertoire WHERE player_id = ?",
                (player_id,),
            ).fetchone()
            ordinal = (row[0] if row else -1) + 1
        finally:
            conn.close()

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO repertoire
                (player_id, eco, name, line, mastery, is_active, ordinal, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(player_id, eco) DO UPDATE SET
                name = excluded.name,
                line = excluded.line,
                mastery = excluded.mastery,
                is_active = excluded.is_active,
                ordinal = excluded.ordinal,
                updated_at = CURRENT_TIMESTAMP
            """,
            (player_id, eco, name, line, mastery, 1 if is_active else 0, ordinal),
        )
        conn.commit()
    finally:
        conn.close()


def delete_opening(player_id: str, eco: str) -> bool:
    """Remove an opening from the player's repertoire.  Returns True
    iff a row was actually deleted (false for a non-existent eco)."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM repertoire WHERE player_id = ? AND eco = ?",
            (player_id, eco),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_active_opening(player_id: str, eco: str) -> bool:
    """Mark [eco] as the player's active line, demoting any other
    currently-active row to inactive.  Returns True iff a row was
    successfully promoted (false when the eco doesn't exist for
    this player)."""
    conn = get_conn()
    try:
        # Verify the target row exists first — otherwise we'd silently
        # demote the current active row without promoting a replacement.
        existing = conn.execute(
            "SELECT 1 FROM repertoire WHERE player_id = ? AND eco = ?",
            (player_id, eco),
        ).fetchone()
        if existing is None:
            return False

        # Demote everything else, then promote the target.  Two writes
        # in one transaction so the "exactly one active" invariant
        # holds at every observable moment.
        conn.execute(
            "UPDATE repertoire SET is_active = 0, updated_at = CURRENT_TIMESTAMP "
            "WHERE player_id = ? AND eco != ?",
            (player_id, eco),
        )
        conn.execute(
            "UPDATE repertoire SET is_active = 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE player_id = ? AND eco = ?",
            (player_id, eco),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def seed_default_repertoire(player_id: str, defaults: list[dict]) -> int:
    """Insert the canonical default repertoire for a player who has
    nothing stored.  No-op (returns 0) when the player already has
    at least one row.  Returns the number of rows inserted.

    Called by the editing endpoints (POST/DELETE/active) before they
    operate so the user can edit the defaults they see in the GET
    response without an extra "save defaults" step.
    """
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT 1 FROM repertoire WHERE player_id = ? LIMIT 1",
            (player_id,),
        ).fetchone()
        if existing is not None:
            return 0
        for entry in defaults:
            conn.execute(
                """
                INSERT INTO repertoire
                    (player_id, eco, name, line, mastery, is_active, ordinal)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    entry["eco"],
                    entry["name"],
                    entry["line"],
                    entry["mastery"],
                    1 if entry["is_active"] else 0,
                    entry["ordinal"],
                ),
            )
        conn.commit()
        return len(defaults)
    finally:
        conn.close()


def load_bandit_weights(player_id: str, action: str) -> dict | None:
    """Read a single (player, action) row from `bandit_weights`.

    Returns None when the player+action pair has never been recorded
    — caller (decision module) initialises a fresh identity-A,
    zero-b in that case.

    Schema:
        {n_features: int, A_json: str, b_json: str, alpha: float}
    A and b are returned as JSON-encoded strings; the decision
    module deserialises them via numpy.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT n_features, A_json, b_json, alpha
              FROM bandit_weights
             WHERE player_id = ? AND action = ?
            """,
            (player_id, action),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "n_features": int(row[0]),
        "A_json": row[1],
        "b_json": row[2],
        "alpha": float(row[3]),
    }


def save_bandit_weights(
    player_id: str,
    action: str,
    n_features: int,
    A_json: str,
    b_json: str,
    alpha: float,
) -> None:
    """Upsert one (player, action) row into `bandit_weights`.

    The UNIQUE(player_id, action) constraint means re-saving an
    existing pair updates the existing row; the row count grows
    only with new actions or new players.
    """
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO bandit_weights
                (player_id, action, n_features, A_json, b_json, alpha, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(player_id, action) DO UPDATE SET
                n_features = excluded.n_features,
                A_json = excluded.A_json,
                b_json = excluded.b_json,
                alpha = excluded.alpha,
                updated_at = CURRENT_TIMESTAMP
            """,
            (player_id, action, n_features, A_json, b_json, alpha),
        )
        conn.commit()
    finally:
        conn.close()


def list_repertoire(player_id: str) -> list[dict]:
    """Return the player's opening repertoire ordered by `ordinal`,
    or an empty list when nothing is stored.

    Caller (server.py /repertoire) is responsible for substituting
    the canonical defaults when the list is empty — the repo doesn't
    invent rows for an unknown player.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT eco, name, line, mastery, is_active, ordinal
              FROM repertoire
             WHERE player_id = ?
             ORDER BY ordinal ASC, id ASC
            """,
            (player_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "eco": r[0],
            "name": r[1],
            "line": r[2],
            "mastery": float(r[3]),
            "is_active": bool(r[4]),
            "ordinal": int(r[5]),
        }
        for r in rows
    ]


def get_active_game(player_id: str) -> dict | None:
    """Return the player's most recent unfinished game with a
    non-null checkpoint, or None if there isn't one.

    Shape (when present):
        {
          "game_id":             str,
          "current_fen":         str,
          "current_uci_history": str,
          "last_checkpoint_at":  str (ISO timestamp),
          "started_at":          str (ISO timestamp),
        }

    Filters:
      - Same player_id.
      - finished_at IS NULL  (unfinished).
      - current_fen IS NOT NULL  (a checkpoint was actually written;
        avoids returning rows for /game/start calls where the user
        never played a single move).

    Order: most-recent last_checkpoint_at first, so a multi-game
    history returns the user's last active session, not an ancient
    one.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT id,
                   current_fen,
                   current_uci_history,
                   last_checkpoint_at,
                   started_at
              FROM games
             WHERE player_id = ?
               AND finished_at IS NULL
               AND current_fen IS NOT NULL
             ORDER BY last_checkpoint_at DESC
             LIMIT 1
            """,
            (player_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return {
        "game_id": row[0],
        "current_fen": row[1],
        "current_uci_history": row[2] or "",
        "last_checkpoint_at": row[3],
        "started_at": row[4],
    }


# -------------------------------------------------
# Moves
# -------------------------------------------------


def log_move(game_id: str, ply: int, fen: str, uci: str, san: str, eval: float | None):
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO moves (game_id, ply, fen, uci, san, eval)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (game_id, ply, fen, uci, san, eval),
        )
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------
# Explanations
# -------------------------------------------------


def log_explanation(
    game_id: str,
    ply: int,
    explanation_type: str,
    confidence: float,
):
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO explanations (game_id, ply, explanation_type, confidence)
            VALUES (?, ?, ?, ?)
            """,
            (game_id, ply, explanation_type, confidence),
        )
        conn.commit()
        explanation_id = cur.lastrowid
    finally:
        conn.close()

    return explanation_id


def update_learning_score(explanation_id: int, score: float):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE explanations SET learning_score = ? WHERE id = ?",
            (score, explanation_id),
        )
        conn.commit()
    finally:
        conn.close()
