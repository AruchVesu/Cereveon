"""Link / unlink / import / status operations for the Lichess adapter.

This module is the only place that:

* Inserts rows into ``linked_accounts``.
* Inserts ``source='lichess'`` rows into ``game_events``.
* Mutates ``Player.rating`` / ``Player.confidence`` based on Lichess
  profile data — and only on the first link, never on subsequent
  imports (see ``link_account``).

It explicitly does NOT:

* Run ``SkillUpdater`` against imported games.  Backfilling a player's
  rating from years of historical games would whipsaw the in-app FIDE-
  style Elo model, which is calibrated to the live SECA opponent mix.
* Re-analyse PGNs with the local Stockfish pool during import.  Per
  ``docs/ARCHITECTURE.md`` only local engine output can populate ESV,
  but doing that synchronously for a 100-game backfill would block the
  request for many engine-pool minutes.  Re-analysis happens lazily
  when the user opens a specific game for review.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from typing import Iterator

import chess
import chess.pgn
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.events.models import GameEvent
from llm.seca.lichess import client as lichess_client
from llm.seca.lichess.models import LinkedAccount

logger = logging.getLogger(__name__)

# Naive-UTC epoch math.  ``datetime.utcfromtimestamp(x).timestamp()`` is
# NOT round-trip safe because ``.timestamp()`` on a naive datetime
# reinterprets the value as local time, introducing a wall-clock offset
# equal to ``tzinfo`` on the host.  We use a fixed epoch reference and
# timedelta arithmetic so the conversions are timezone-independent and
# round-trip exactly.
_EPOCH = datetime(1970, 1, 1)


def _ms_to_naive_utc(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms)


def _naive_utc_to_ms(dt: datetime) -> int:
    return int((dt - _EPOCH).total_seconds() * 1000)


PLATFORM_LICHESS = "lichess"

# Default perf-type filter passed to Lichess.  Bullet/UltraBullet are
# noisy for skill calibration; correspondence is too slow to compare
# fairly.  Variants are not part of the standard ESV space.  The user
# can override at the API layer.
DEFAULT_PERF_TYPES = ["blitz", "rapid", "classical"]

# Perf preference order used during first-link calibration: rapid is
# the best single-source proxy for over-the-board strength; blitz is
# the most heavily played; classical fills in for slow-only players.
CALIBRATION_PERF_PREFERENCE = ["rapid", "blitz", "classical"]

# Per-game PGN size cap, mirroring the validator on /game/finish so an
# anomalous Lichess game can't bloat a row.
_MAX_PGN_BYTES = 100_000

# Player defaults — calibration only fires when the player is still at
# these out-of-the-box values, so an active in-app player's rating is
# never silently overwritten by a re-link.
_DEFAULT_RATING = 1200.0
_DEFAULT_CONFIDENCE = 0.5

# Bounds on calibrated rating; defensive against an upstream returning
# garbage perf values.  Matches the bounds on PATCH /auth/me's rating
# field (0 < rating ≤ 4000) but tightened to a chess-realistic window.
_CALIBRATION_RATING_MIN = 600.0
_CALIBRATION_RATING_MAX = 3000.0


class LichessImportError(Exception):
    """Service-layer error distinct from client-layer transport errors."""


class LichessAlreadyLinkedError(LichessImportError):
    """The requested Lichess handle is already linked to another player."""


class LichessNotLinkedError(LichessImportError):
    """Operation requires a linked account but none exists."""


# ---------------------------------------------------------------------------
# Link / unlink
# ---------------------------------------------------------------------------


def link_account(db: DBSession, player: Player, lichess_username: str) -> dict:
    """Attach a Lichess handle to the given player.

    Steps:

    1. Hit ``GET /api/user/{username}`` to confirm the handle exists
       (raises ``LichessUserNotFound`` if not) and to capture the
       canonical lowercase id.
    2. Reject if that canonical id is already linked to a *different*
       player (raises ``LichessAlreadyLinkedError``, surfaced as 409
       by the router).
    3. Delete any existing link this player has on the Lichess
       platform (re-link replaces; we never accumulate stale rows).
    4. Insert the fresh ``linked_accounts`` row.
    5. Run first-link calibration on the Player if (and only if) the
       player is still at default rating/confidence.

    Returns a summary dict for the API response.
    """
    profile = lichess_client.fetch_user_profile(lichess_username)
    canonical_id = str(profile.get("id") or "").strip()
    if not canonical_id:
        raise LichessImportError("profile response missing 'id' field")

    # Step 2: cross-player conflict.
    conflict = (
        db.query(LinkedAccount)
        .filter(
            LinkedAccount.platform == PLATFORM_LICHESS,
            LinkedAccount.external_username == canonical_id,
            LinkedAccount.player_id != player.id,
        )
        .first()
    )
    if conflict is not None:
        raise LichessAlreadyLinkedError(
            f"Lichess account '{canonical_id}' is linked to another player"
        )

    # Step 3: replace any prior link for this player.
    existing = (
        db.query(LinkedAccount)
        .filter(
            LinkedAccount.player_id == player.id,
            LinkedAccount.platform == PLATFORM_LICHESS,
        )
        .first()
    )
    if existing is not None:
        db.delete(existing)
        db.flush()

    # Step 4: insert.
    link = LinkedAccount(
        player_id=player.id,
        platform=PLATFORM_LICHESS,
        external_username=canonical_id,
    )
    db.add(link)
    db.flush()

    # Step 5: first-link calibration.
    calibration = _maybe_calibrate(db, player, profile)

    db.commit()
    db.refresh(player)
    db.refresh(link)

    return {
        "platform": PLATFORM_LICHESS,
        "external_username": canonical_id,
        "linked_at": link.created_at.isoformat() if link.created_at else None,
        "calibration": calibration,
    }


def unlink_account(db: DBSession, player: Player) -> bool:
    """Remove the player's Lichess link.

    Returns ``True`` if a link existed (and was removed), ``False`` if
    the player had no link to begin with.  Imported game_events rows
    are NOT deleted — they remain as history.  Future:
    DELETE /lichess/data could prune them.
    """
    link = (
        db.query(LinkedAccount)
        .filter(
            LinkedAccount.player_id == player.id,
            LinkedAccount.platform == PLATFORM_LICHESS,
        )
        .first()
    )
    if link is None:
        return False
    db.delete(link)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status(db: DBSession, player: Player) -> dict:
    """Return the player's current Lichess link state + import counters."""
    link = (
        db.query(LinkedAccount)
        .filter(
            LinkedAccount.player_id == player.id,
            LinkedAccount.platform == PLATFORM_LICHESS,
        )
        .first()
    )
    if link is None:
        return {"linked": False}

    imported_count = (
        db.query(GameEvent)
        .filter(
            GameEvent.player_id == player.id,
            GameEvent.source == PLATFORM_LICHESS,
        )
        .count()
    )

    return {
        "linked": True,
        "platform": PLATFORM_LICHESS,
        "external_username": link.external_username,
        "linked_at": link.created_at.isoformat() if link.created_at else None,
        "last_imported_at": (link.last_imported_at.isoformat() if link.last_imported_at else None),
        "imported_game_count": int(imported_count),
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_user_games(
    db: DBSession,
    player: Player,
    *,
    max_games: int,
    perf_types: list[str] | None = None,
    rated: bool = True,
) -> dict:
    """Pull the next slice of games from Lichess for the linked player.

    Uses the ``last_imported_at`` watermark on the LinkedAccount for
    incremental fetches: only games created *strictly after* the
    watermark are requested.  On a first import (watermark NULL) the
    full ``max_games`` window is fetched.

    Per-game dedup uses ``(source='lichess', external_game_id)`` so a
    re-import of the same window (e.g. after a partial-fail retry)
    does not duplicate rows.

    Commits per game so that a mid-stream failure preserves progress.
    The watermark advances only after the whole stream completes — a
    failed retry will re-scan the same window, with dedup making the
    re-scan idempotent.
    """
    if max_games <= 0:
        raise ValueError("max_games must be positive")

    link = (
        db.query(LinkedAccount)
        .filter(
            LinkedAccount.player_id == player.id,
            LinkedAccount.platform == PLATFORM_LICHESS,
        )
        .first()
    )
    if link is None:
        raise LichessNotLinkedError("player has no Lichess account linked")

    user_id_lc = link.external_username.lower()

    since_ms: int | None = None
    if link.last_imported_at is not None:
        # Lichess ``since`` is exclusive: games strictly after the ms.
        since_ms = _naive_utc_to_ms(link.last_imported_at)

    games_iter: Iterator[dict] = lichess_client.fetch_user_games(
        link.external_username,
        since_ms=since_ms,
        max_games=max_games,
        rated=rated,
        perf_types=perf_types or DEFAULT_PERF_TYPES,
    )

    # Pre-load already-imported IDs in the requested window so dedup
    # is a single in-memory set lookup per game rather than N round-
    # trips.  For an MVP cap of 100 games this is trivially cheap.
    existing_ids: set[str] = {
        row.external_game_id
        for row in db.query(GameEvent.external_game_id)
        .filter(
            GameEvent.player_id == player.id,
            GameEvent.source == PLATFORM_LICHESS,
            GameEvent.external_game_id.isnot(None),
        )
        .all()
        if row.external_game_id is not None
    }

    inserted = 0
    skipped_duplicate = 0
    skipped_invalid = 0
    newest_created_at_ms: int | None = None

    for game in games_iter:
        external_id = str(game.get("id") or "").strip()
        if not external_id:
            skipped_invalid += 1
            logger.warning("Lichess game missing 'id' — skipping")
            continue
        if external_id in existing_ids:
            skipped_duplicate += 1
            continue

        pgn = game.get("pgn")
        if not isinstance(pgn, str) or not pgn.strip():
            skipped_invalid += 1
            logger.warning("Lichess game %s has no PGN payload — skipping", external_id)
            continue
        if len(pgn) > _MAX_PGN_BYTES:
            skipped_invalid += 1
            logger.warning(
                "Lichess game %s PGN exceeds %d bytes — skipping",
                external_id,
                _MAX_PGN_BYTES,
            )
            continue
        if not _is_parseable_pgn(pgn):
            skipped_invalid += 1
            logger.warning("Lichess game %s PGN unparseable — skipping", external_id)
            continue

        result = _derive_result(game, user_id_lc)
        if result is None:
            # Linked user not present in either side — should be
            # impossible for /api/games/user/{username}, but skip
            # rather than crash if Lichess ever surprises us.
            skipped_invalid += 1
            logger.warning(
                "Lichess game %s does not list %s as a player — skipping",
                external_id,
                user_id_lc,
            )
            continue

        created_at_ms = game.get("createdAt")
        if isinstance(created_at_ms, (int, float)):
            ts = int(created_at_ms)
            if newest_created_at_ms is None or ts > newest_created_at_ms:
                newest_created_at_ms = ts

        row = GameEvent(
            player_id=player.id,
            pgn=pgn,
            result=result,
            # Accuracy / weaknesses are produced by the local engine
            # pool; we leave them at column defaults for imported
            # games.  Lazy re-analysis populates ESV at view time.
            accuracy=None,
            weaknesses_json="{}",
            source=PLATFORM_LICHESS,
            external_game_id=external_id,
        )
        db.add(row)
        db.commit()
        existing_ids.add(external_id)
        inserted += 1

    # Watermark advances only after a clean iteration.  Mid-stream
    # exceptions skip this block; the next retry re-scans and dedup
    # handles repeats.
    if newest_created_at_ms is not None:
        link.last_imported_at = _ms_to_naive_utc(newest_created_at_ms)
        db.add(link)
        db.commit()

    return {
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": skipped_invalid,
        "last_imported_at": (link.last_imported_at.isoformat() if link.last_imported_at else None),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_result(game: dict, user_id_lc: str) -> str | None:
    """Map the Lichess game shape to {'win','loss','draw'} from our user's POV.

    Returns ``None`` if the linked user is not on either side (defensive).
    """
    players = game.get("players") or {}
    white_user = (players.get("white") or {}).get("user") or {}
    black_user = (players.get("black") or {}).get("user") or {}

    white_id = str(white_user.get("id") or "").lower()
    black_id = str(black_user.get("id") or "").lower()

    if user_id_lc == white_id:
        user_color = "white"
    elif user_id_lc == black_id:
        user_color = "black"
    else:
        return None

    winner = game.get("winner")
    if winner is None:
        return "draw"
    if winner not in ("white", "black"):
        # Unknown winner string — treat conservatively as draw rather
        # than guess.
        return "draw"
    return "win" if winner == user_color else "loss"


def _is_parseable_pgn(pgn: str) -> bool:
    """Cheap structural validation — same parser as /game/finish."""
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
    except Exception:  # pylint: disable=broad-except
        return False
    if game is None:
        return False
    if game.errors:
        return False
    if not list(game.mainline_moves()):
        return False
    return True


def _maybe_calibrate(db: DBSession, player: Player, profile: dict) -> dict:
    """Set Player.rating + confidence from Lichess profile, ONCE.

    Calibration fires only when the player is still at default rating
    (1200) and default confidence (0.5).  Any in-app play before the
    first link advances those values and locks calibration out — we do
    not overwrite real SECA rating data with a third-party Elo.
    """
    is_default = (
        abs(player.rating - _DEFAULT_RATING) < 1e-9
        and abs(player.confidence - _DEFAULT_CONFIDENCE) < 1e-9
    )
    if not is_default:
        return {"applied": False, "reason": "player_already_calibrated"}

    perfs = profile.get("perfs") or {}
    chosen_perf: str | None = None
    chosen_data: dict | None = None
    for perf_name in CALIBRATION_PERF_PREFERENCE:
        data = perfs.get(perf_name) or {}
        games = data.get("games") or 0
        rating = data.get("rating")
        if isinstance(games, (int, float)) and games > 0 and isinstance(rating, (int, float)):
            chosen_perf = perf_name
            chosen_data = data
            break

    if chosen_perf is None or chosen_data is None:
        return {"applied": False, "reason": "no_eligible_perf"}

    raw_rating = float(chosen_data["rating"])
    clamped_rating = max(_CALIBRATION_RATING_MIN, min(_CALIBRATION_RATING_MAX, raw_rating))

    provisional = bool(chosen_data.get("prov", False))
    games = int(chosen_data.get("games") or 0)
    confidence = _confidence_from_perf(games=games, provisional=provisional)

    player.rating = clamped_rating
    player.confidence = confidence
    db.add(player)
    db.flush()

    return {
        "applied": True,
        "perf": chosen_perf,
        "rating": clamped_rating,
        "confidence": confidence,
        "games_basis": games,
        "provisional": provisional,
    }


def _confidence_from_perf(*, games: int, provisional: bool) -> float:
    """Map perf-volume to an in-app confidence value in [0, 1].

    Heuristic, not a model output — calibrates the *uncertainty* the
    SECA adaptation layer applies to the seeded rating.  More games +
    non-provisional → higher confidence.
    """
    if provisional:
        return 0.4
    if games >= 200:
        return 0.85
    if games >= 50:
        return 0.7
    return 0.5
