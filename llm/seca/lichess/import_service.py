# This module hosts link/unlink/status, both import entrypoints (sync +
# worker), the shared stream driver, and now the disconnect-detection
# glue, so it runs just past the 1000-line soft cap.  It is a split
# candidate (the stream driver is the obvious extract), but carving it
# up is a dedicated refactor out of scope for a feature change — same
# disposition as auth/router.py and events/router.py.
# pylint: disable=too-many-lines
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
* Re-analyse PGNs during the import STREAM.  Per ``docs/ARCHITECTURE.md``
  only local engine output can populate accuracy / weaknesses, and doing
  that inline would multiply per-game latency by the engine budget.

Post-import analysis (2026-07-03)
---------------------------------
After the stream completes, the v2 worker runs a BOUNDED engine pass
(``llm.seca.lichess.analysis_service.analyze_unscored_games``) over the
player's newest unscored
``source='lichess'`` rows: ``compute_accuracy_from_pgn`` — the same
engine-truth recompute /game/finish uses — writes ``accuracy`` +
phase-keyed ``weaknesses_json`` back onto each row so the historical
analysis pipeline (curriculum, weakness charts, progress dashboard)
consumes imported games exactly like in-app ones.  Capped at
``LICHESS_ANALYSIS_MAX_GAMES`` per job; unanalysed backlog is picked up
by subsequent imports.  Earlier docs promised "lazy re-analysis when the
user opens a game for review" — that path was never built; this pass
replaces the plan.  Rating stays untouched (SkillUpdater exclusion above
still holds).
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from typing import Callable, Iterator

import chess
import chess.pgn
from sqlalchemy.orm import Session as DBSession, sessionmaker

from llm.seca.engines.stockfish.pool import StockfishEnginePool
from llm.seca.auth.models import Player
from llm.seca.auth.router import engine
from llm.seca.events.models import GameEvent
from llm.seca.lichess import client as lichess_client
from llm.seca.lichess import get_player_import_lock
from llm.seca.lichess.models import (
    JOB_STATUS_ACTIVE,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    LichessImportJob,
    LinkedAccount,
)
from llm.seca.notifications import service as notifications_service

logger = logging.getLogger(__name__)

# Worker-thread session factory.  Separate from the request-scoped
# ``SessionLocal`` because:
#   1. The thread that runs ``run_import_job`` is detached from the
#      request that created the job — request-scoped sessions are
#      closed by FastAPI's ``Depends(get_db)`` teardown long before.
#   2. ``expire_on_commit=False`` keeps the loaded ``LinkedAccount`` /
#      ``LichessImportJob`` rows usable across N per-game commits.  The
#      default (True) would re-SELECT ``link.last_imported_at`` and
#      ``job.*`` on every attribute access after each commit — turning
#      a 100-game import into ~400 unnecessary round-trips.
_WorkerSession = sessionmaker(bind=engine, expire_on_commit=False)

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


def link_account(
    db: DBSession,
    player: Player,
    lichess_username: str,
    *,
    profile: dict | None = None,
    claim_from_other_player: bool = False,
) -> dict:
    """Attach a Lichess handle to the given player.

    Steps:

    1. Hit ``GET /api/user/{username}`` to confirm the handle exists
       (raises ``LichessUserNotFound`` if not) and to capture the
       canonical lowercase id.  Skipped when the caller supplies a
       pre-fetched ``profile`` — the OAuth sign-in path
       (``POST /auth/lichess``) already holds the ``GET /api/account``
       response, which is a superset of the public-profile shape, and
       its identity is verified rather than self-asserted.
    2. Handle a *different* player already owning that canonical id:
       - Default (``claim_from_other_player=False`` — the self-asserted
         ``POST /lichess/link`` path): reject with
         ``LichessAlreadyLinkedError`` (409).  A user who merely typed a
         username must not be able to steal another account's link.
       - ``claim_from_other_player=True`` (the OAuth sign-in path, where
         the caller PROVED ownership via the authorization-code
         exchange): take the link over — delete the other player's link
         row.  Verified ownership overrides a self-asserted claim, and
         it's the only claim that can (an attacker would have to actually
         control the Lichess account to reach this path).  The other
         account's imported ``game_events`` are left as history; only its
         ``linked_accounts`` row is removed.  Fixes the same-human /
         two-Cereveon-logins case where the handle was manually linked on
         one account and OAuth sign-in on the other could never link.
    3. Delete any existing link this player has on the Lichess
       platform (re-link replaces; we never accumulate stale rows).
    4. Insert the fresh ``linked_accounts`` row.
    5. Run first-link calibration on the Player if (and only if) the
       player is still at default rating/confidence.

    Returns a summary dict for the API response.
    """
    if profile is None:
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
        if not claim_from_other_player:
            raise LichessAlreadyLinkedError(
                f"Lichess account '{canonical_id}' is linked to another player"
            )
        # Verified OAuth ownership claims the handle from the other
        # account's self-asserted link.  Cancel that account's in-flight
        # import jobs first (same ordering as unlink_account) so no
        # worker is left running against a link that's about to vanish.
        db.query(LichessImportJob).filter(
            LichessImportJob.player_id == conflict.player_id,
            LichessImportJob.status.in_(JOB_STATUS_ACTIVE),
        ).update(
            {
                LichessImportJob.status: JOB_STATUS_FAILED,
                LichessImportJob.error_message: "lichess handle claimed by verified owner",
            },
            synchronize_session=False,
        )
        db.delete(conflict)
        db.flush()

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

    # Linking (or re-linking) IS the reconnect action: any outstanding
    # "Lichess connection lost" alert has been answered, and spec §5.8
    # forbids continuing to show it.  The fresh row above starts with
    # ``disconnected_at`` NULL, so link state and alert state stay in
    # step.
    notifications_service.resolve_lichess_disconnected(db, str(player.id))

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

    Any in-flight v2 import jobs for this player are marked ``failed``
    before the link row is deleted.  The worker picks up the status
    change via ``db.refresh(job, ['status'])`` between games and exits
    cleanly without advancing the watermark.  We update jobs BEFORE
    deleting the link so the worker never sees a half-detached state
    (link gone but job still ``running``).
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

    db.query(LichessImportJob).filter(
        LichessImportJob.player_id == player.id,
        LichessImportJob.status.in_(JOB_STATUS_ACTIVE),
    ).update(
        {
            LichessImportJob.status: JOB_STATUS_FAILED,
            LichessImportJob.error_message: "link removed during import",
        },
        synchronize_session=False,
    )
    db.delete(link)
    db.commit()

    # A deliberate unlink answers the "reconnect" ask just as surely as
    # reconnecting does — dropping the alert here keeps a detached
    # player's feed from nagging about an account they chose to remove.
    notifications_service.resolve_lichess_disconnected(db, str(player.id))
    return True


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _heal_registered_lichess_link(db: DBSession, player: Player) -> LinkedAccount | None:
    """Recreate the import-link row for a Lichess-REGISTERED player missing it.

    A player who signed up via "Sign in with Lichess" (``lichess_user_id``
    set) IS the OAuth-verified owner of that handle, so their game-import
    ``LinkedAccount`` row should always exist.  The first-sign-in auto-link
    (``auth/router._ensure_lichess_link``) that creates it is best-effort,
    though — it can fail transiently, accounts created before that feature
    never got a row, and a user who stays signed in via JWT refresh never
    re-hits ``/auth/lichess`` to retry it.  Any of those leaves
    ``GET /lichess/status`` reporting ``linked: False`` for a bona-fide
    Lichess account.

    This repairs the row lazily on status read, keyed on the verified
    ``lichess_user_id``.  A minimal ``{"id": ...}`` profile is passed so
    there is NO Lichess network call and NO rating calibration in the
    status path — only the row (and, if the handle was self-asserted on a
    different account, the same verified-owner claim the sign-in path
    would perform).

    Best-effort: any failure rolls back and returns ``None`` so status
    simply falls through to its existing not-linked answer — never a 500.
    """
    lichess_id = getattr(player, "lichess_user_id", None)
    if not lichess_id:
        # Password/email account with no Lichess identity — nothing to heal.
        return None
    player_id = player.id
    try:
        link_account(
            db,
            player,
            str(lichess_id),
            profile={"id": str(lichess_id)},
            claim_from_other_player=True,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        db.rollback()
        logger.warning("lichess status self-heal failed for player %s", player_id, exc_info=True)
        return None
    return (
        db.query(LinkedAccount)
        .filter(
            LinkedAccount.player_id == player_id,
            LinkedAccount.platform == PLATFORM_LICHESS,
        )
        .first()
    )


def get_status(db: DBSession, player: Player) -> dict:
    """Return the player's current Lichess link state + import counters.

    ``active_import_job_id`` is non-null iff a v2 import is in flight for
    this player (status ``queued`` or ``running``); the Android client
    uses it to resume the in-progress UI after a sheet dismiss / reopen.
    Field is omitted from the not-linked response so that shape stays
    minimal — the client side conditionally reads it.
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
        # Recover a missing import-link for an OAuth-registered player
        # before answering "not linked" — see the helper's docstring.
        link = _heal_registered_lichess_link(db, player)
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

    active_job_id = (
        db.query(LichessImportJob.id)
        .filter(
            LichessImportJob.player_id == player.id,
            LichessImportJob.status.in_(JOB_STATUS_ACTIVE),
        )
        .scalar()
    )

    return {
        "linked": True,
        "platform": PLATFORM_LICHESS,
        "external_username": link.external_username,
        "linked_at": link.created_at.isoformat() if link.created_at else None,
        "last_imported_at": (link.last_imported_at.isoformat() if link.last_imported_at else None),
        "imported_game_count": int(imported_count),
        "active_import_job_id": active_job_id,
        # Reconnect flow: True once an import 404'd on the account
        # (closed/renamed) and no clean stream has been seen since.
        # The client renders the reconnect state from this.  Additive
        # field — the deployed Android client ignores unknown keys.
        "disconnected": link.disconnected_at is not None,
        "disconnected_at": (link.disconnected_at.isoformat() if link.disconnected_at else None),
    }


def serialize_job(job: LichessImportJob) -> dict:
    """Render a ``LichessImportJob`` row as the v2 response body.

    Shared by ``POST /lichess/import`` (202 response) and
    ``GET /lichess/import/job/{job_id}`` (200 response).  Field set is
    stable across both so the Android client can decode the same type.
    """
    return {
        "job_id": job.id,
        "status": job.status,
        "inserted": int(job.inserted),
        "skipped_duplicate": int(job.skipped_duplicate),
        "skipped_invalid": int(job.skipped_invalid),
        # Post-stream engine-analysis progress (docs/API_CONTRACTS.md §31).
        # ``or 0`` guards rows created before the column's ADD COLUMN
        # migration backfilled a default.  Additive field: the deployed
        # Android client ignores unknown keys.
        "analyzed": int(job.analyzed or 0),
        "target_max_games": int(job.target_max_games),
        "last_imported_at_ms": job.last_imported_at_ms,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def cleanup_stale_import_jobs_on_startup() -> int:
    """Mark any ``queued`` / ``running`` jobs as failed on process boot.

    A prior process exit (crash, SIGTERM, container restart) cannot
    cleanly wind down a worker thread that's blocked on
    ``httpx.iter_lines``; the row is left ``running`` in the DB even
    though no thread is attached.  Without this janitor:

    * ``start_import_job`` coalesces forever onto the orphan row.
    * ``GET /lichess/status.active_import_job_id`` keeps pointing at it,
      and the Android client polls a job that will never advance.

    Idempotent and cheap.  Runs once from FastAPI lifespan startup,
    after ``init_schema`` and before the first request hits the route.
    Returns the count of rows swept (mostly for observability / tests).
    """
    db = _WorkerSession()
    try:
        affected = (
            db.query(LichessImportJob)
            .filter(LichessImportJob.status.in_(JOB_STATUS_ACTIVE))
            .update(
                {
                    LichessImportJob.status: JOB_STATUS_FAILED,
                    LichessImportJob.error_message: "abandoned by server restart",
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if affected:
            logger.info("Lichess import janitor swept %d stale job(s) to failed", affected)
        return int(affected)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _mark_link_disconnected(db: DBSession, player_id: str) -> None:
    """Persist the lost-access state + raise the reconnect alert.

    Called when an import stream 404s on the linked account — the only
    signal this architecture has for "Lichess access is gone" (there is
    no stored OAuth token whose refresh could fail; see the model
    comment on ``disconnected_at``).  First 404 stamps the time; repeat
    404s keep the original stamp.  The alert producer is duplicate-
    suppressed on its side, so this function is safe to call on every
    failing retry.

    Refetches the link itself (callers arrive here after a rollback,
    holding possibly-expired instances): a concurrent ``unlink_account``
    may have removed the row, in which case there is nothing to mark
    and no one to alert.
    """
    link = (
        db.query(LinkedAccount)
        .filter(
            LinkedAccount.player_id == player_id,
            LinkedAccount.platform == PLATFORM_LICHESS,
        )
        .first()
    )
    if link is None:
        return
    if link.disconnected_at is None:
        link.disconnected_at = datetime.utcnow()
        db.add(link)
        db.commit()
    notifications_service.notify_lichess_disconnected(
        db, str(link.player_id), link.external_username
    )


def import_user_games(
    db: DBSession,
    player: Player,
    *,
    max_games: int,
    perf_types: list[str] | None = None,
    rated: bool = True,
) -> dict:
    """Pull the next slice of games from Lichess for the linked player (v1 sync).

    Used by the legacy ``X-API-Version: 1`` path on ``POST /lichess/import``.
    The v2 path uses ``start_import_job`` + ``run_import_job`` instead.

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

    try:
        return _run_import_stream(
            db,
            link,
            player,
            max_games=max_games,
            rated=rated,
            perf_types=perf_types,
        )
    except lichess_client.LichessUserNotFound:
        # The linked account is gone (closed/renamed).  Record the
        # broken link + raise the reconnect alert, then let the router
        # translate the exception to its existing 404 — the sync caller
        # still sees the failure it always saw.
        db.rollback()
        _mark_link_disconnected(db, str(player.id))
        raise


def start_import_job(
    db: DBSession,
    player: Player,
    *,
    max_games: int,
    dispatch: Callable[[str], object] | None = None,
) -> LichessImportJob:
    """Create-or-coalesce an import job for the player (v2 async).

    Critical section is per-player: two concurrent ``POST /lichess/import``
    calls from the same player serialize on ``get_player_import_lock`` so
    they cannot both pass the "any active job?" SELECT and both insert.
    On Postgres the partial unique index added in ``init_schema`` is the
    second line of defense; on SQLite the lock alone carries the property.

    Holds the lock around the SELECT + INSERT + commit only — NOT across
    the actual Lichess stream, which is the worker's job.  This keeps
    concurrent imports for *different* players fully parallel.

    ``rated`` is intentionally NOT a parameter here: this function only
    creates the job row.  The caller's ``dispatch`` closure carries it to
    the worker, so persisting it on the row would just duplicate state.

    Worker dispatch (2026-07-03): callers pass ``dispatch`` — invoked
    with the job id, inside the lock, for FRESHLY-CREATED rows only.
    Earlier callers keyed their own ``executor.submit`` on
    ``status == 'queued'``, which double-submits when a job created by an
    earlier call gets coalesced back while still awaiting executor pickup
    (two workers then race on per-game commits; the loser's
    IntegrityError flips the job to failed mid-stream).  Only this
    function knows fresh-vs-coalesced, so the dispatch decision lives
    here.  Coalesced returns never dispatch.

    Returns the active job row — either one freshly inserted by this
    call or the one a concurrent caller raced past.
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

    lock = get_player_import_lock(player.id)
    with lock:
        existing = (
            db.query(LichessImportJob)
            .filter(
                LichessImportJob.player_id == player.id,
                LichessImportJob.status.in_(JOB_STATUS_ACTIVE),
            )
            .first()
        )
        if existing is not None:
            # Pick up any counter advances the worker has committed.
            db.refresh(existing)
            return existing

        job = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_QUEUED,
            target_max_games=max_games,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        if dispatch is not None:
            dispatch(job.id)
        return job


def run_import_job(
    job_id: str,
    *,
    max_games: int,
    rated: bool = True,
    engine_pool: StockfishEnginePool | None = None,
) -> None:
    """Worker entrypoint — runs in the thread pool, not under a request.

    Opens its own ``_WorkerSession`` (``expire_on_commit=False``) so the
    request-scoped session of the POST that started us has long since
    closed by the time we run.

    Idempotency: if the job is already terminal at pickup time
    (``unlink_account`` raced ahead, or the startup janitor marked it
    failed after a restart) the worker returns without touching the
    row.  This prevents clobbering ``error_message`` with a generic
    "linked account not found" when the real cause was the unlink.
    """
    db = _WorkerSession()
    try:
        job = db.get(LichessImportJob, job_id)
        if job is None:
            logger.error("Lichess import job %s not found in worker; aborting", job_id)
            return
        if job.status not in JOB_STATUS_ACTIVE:
            logger.info(
                "Lichess import job %s already terminal (%s); worker skipping",
                job_id,
                job.status,
            )
            return

        player = db.get(Player, job.player_id)
        link = (
            db.query(LinkedAccount)
            .filter(
                LinkedAccount.player_id == job.player_id,
                LinkedAccount.platform == PLATFORM_LICHESS,
            )
            .first()
        )
        if player is None or link is None:
            job.status = JOB_STATUS_FAILED
            job.error_message = "linked account not found at worker start"
            db.commit()
            return

        job.status = JOB_STATUS_RUNNING
        db.commit()

        try:
            _run_import_stream(
                db,
                link,
                player,
                max_games=max_games,
                rated=rated,
                job=job,
            )
        except Exception as exc:  # pylint: disable=broad-except
            # Postgres ``InFailedSqlTransaction`` will cascade through
            # subsequent statements on this session unless we roll back
            # first — see ``feedback_raw_ddl_dialect_drift``.
            db.rollback()
            job_refetch = db.get(LichessImportJob, job_id)
            if job_refetch is not None and job_refetch.status == JOB_STATUS_RUNNING:
                # Only overwrite a still-running job: a concurrent
                # ``unlink_account`` may have already marked it failed
                # with a more specific reason.
                job_refetch.status = JOB_STATUS_FAILED
                job_refetch.error_message = str(exc)[:500]
                db.commit()
            if isinstance(exc, lichess_client.LichessUserNotFound):
                # The linked account is gone (closed/renamed) — the
                # reconnect-flow trigger, not a transient upstream
                # error.  The helper refetches the link post-rollback;
                # a concurrent unlink leaves nothing to mark and no one
                # to alert.
                _mark_link_disconnected(db, str(job.player_id))
            logger.warning("Lichess import job %s failed: %s", job_id, exc)
            return

        # Stream completed.  Run the bounded post-import engine analysis
        # while the job is still ``running`` — the Android client treats
        # every non-terminal status as in-progress, so the longer-lived
        # job needs no client change.  ``engine_pool`` is None when the
        # server booted without a pool (tests, degraded deploys); the
        # import still succeeds and the rows stay unscored for a later
        # job that does have a pool.  Lazy import: analysis_service is a
        # sibling leaf module and importing it here (not at module level)
        # keeps the pair cycle-free in both load orders.
        db.refresh(job, ["status"])
        if job.status == JOB_STATUS_RUNNING and engine_pool is not None:
            from llm.seca.lichess import analysis_service

            try:
                analysis_service.analyze_unscored_games(db, player, engine_pool, job=job)
            except Exception:  # pylint: disable=broad-exception-caught
                # Analysis is enrichment — a surprise failure must not
                # fail an import whose games already landed.  Rollback so
                # a poisoned transaction can't break the promotion below.
                db.rollback()
                logger.exception("Lichess analysis pass crashed for job %s", job_id)

        # Promote to succeeded ONLY if the row is still ``running`` —
        # cancellation may have flipped it to ``failed`` mid-stream or
        # mid-analysis and we must not clobber that.
        db.refresh(job, ["status"])
        if job.status == JOB_STATUS_RUNNING:
            job.status = JOB_STATUS_SUCCEEDED
            db.commit()
            # Feed entry for the async paths (sign-in auto-import,
            # backfill, v2 sheet import): "N games reviewed", batched
            # per spec §5.4 by the producer.  Only on a job that this
            # worker promoted — a cancelled job's partial work never
            # notifies.  The v1 sync path deliberately has no feed
            # entry: its caller blocks on the response and renders the
            # summary directly.
            analyzed_count = int(job.analyzed or 0)
            if analyzed_count > 0:
                try:
                    notifications_service.notify_games_analyzed(
                        db, str(job.player_id), analyzed_count
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    # The feed is enrichment — its failure must not
                    # flip an import whose games already landed back
                    # to failed (same stance as the analysis pass).
                    db.rollback()
                    logger.exception("games-analyzed notification failed for job %s", job_id)
    finally:
        db.close()


def _run_import_stream(
    db: DBSession,
    link: LinkedAccount,
    player: Player,
    *,
    max_games: int,
    rated: bool,
    perf_types: list[str] | None = None,
    job: LichessImportJob | None = None,
) -> dict:
    """Drive the Lichess NDJSON stream into ``game_events`` rows.

    Shared between the v1 sync entrypoint (``import_user_games`` — ``job``
    is None) and the v2 worker (``run_import_job`` — ``job`` is the
    persistent progress row).  When ``job`` is provided, per-game counter
    updates are committed to the job row and the loop respects external
    cancellation via ``job.status`` (set by ``unlink_account``).

    Watermark advance is identical in both modes: only on clean stream
    completion, never on cancellation or mid-stream exception.
    """
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
        # Non-None because ``result`` (which gates the skip above) is
        # derived from the same white/black match.
        player_color = _derive_player_color(game, user_id_lc)

        created_at_ms = game.get("createdAt")
        if isinstance(created_at_ms, (int, float)):
            ts = int(created_at_ms)
            if newest_created_at_ms is None or ts > newest_created_at_ms:
                newest_created_at_ms = ts

        row = GameEvent(
            player_id=player.id,
            pgn=pgn,
            result=result,
            # Unscored marker consumed by the post-stream engine pass
            # (analysis_service.analyze_unscored_games).  NOTE the ORM
            # quirk: SQLAlchemy 2.x fires the column's Python-side
            # default (0.0) even for this EXPLICIT None kwarg, so the
            # row persists accuracy=0.0, not NULL — the analysis
            # service's unscored predicate matches both forms, and
            # AN_00 in test_lichess_analysis.py pins this producer
            # behaviour so a "simplification" here can't silently
            # detach the pass from its input set.
            accuracy=None,
            weaknesses_json="{}",
            source=PLATFORM_LICHESS,
            external_game_id=external_id,
            player_color=player_color,
        )
        db.add(row)
        db.commit()
        existing_ids.add(external_id)
        inserted += 1

        # v2 job mode: mirror counters into the job row + check for
        # external cancellation (e.g. ``unlink_account``).
        if job is not None:
            job.inserted = inserted
            job.skipped_duplicate = skipped_duplicate
            job.skipped_invalid = skipped_invalid
            job.last_imported_at_ms = newest_created_at_ms
            db.commit()
            db.refresh(job, ["status"])
            if job.status not in JOB_STATUS_ACTIVE:
                # Cancellation observed — leave the row's terminal
                # status + error_message intact, do NOT advance
                # watermark.  Already-inserted game rows are retained
                # (per-game commits).
                logger.info(
                    "Lichess import job %s cancelled mid-stream (status=%s)",
                    job.id,
                    job.status,
                )
                return {
                    "inserted": inserted,
                    "skipped_duplicate": skipped_duplicate,
                    "skipped_invalid": skipped_invalid,
                    "last_imported_at": (
                        link.last_imported_at.isoformat() if link.last_imported_at else None
                    ),
                }

    # Watermark advances only after a clean iteration.  Mid-stream
    # exceptions skip this block; the next retry re-scans and dedup
    # handles repeats.
    if newest_created_at_ms is not None:
        link.last_imported_at = _ms_to_naive_utc(newest_created_at_ms)
        db.add(link)
        db.commit()

    # A clean stream — even an empty one — proves the account is
    # reachable again: clear any recorded disconnection and retire the
    # reconnect alert (spec §5.8).  Shared by the sync + worker paths,
    # same as the watermark logic above.  The mid-stream cancellation
    # return deliberately skips this: a cancelled job proves nothing
    # either way, so the recorded state stands.
    if link.disconnected_at is not None:
        link.disconnected_at = None
        db.add(link)
        db.commit()
        notifications_service.resolve_lichess_disconnected(db, str(link.player_id))

    return {
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": skipped_invalid,
        "last_imported_at": (link.last_imported_at.isoformat() if link.last_imported_at else None),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_player_color(game: dict, user_id_lc: str) -> str | None:
    """Return the side our linked user played — ``'white'`` / ``'black'`` —
    or ``None`` if the user isn't listed on either side (defensive).

    Drives replay board orientation: a game the user played as Black must
    render flipped so their pieces sit at the bottom.
    """
    players = game.get("players") or {}
    white_id = str(((players.get("white") or {}).get("user") or {}).get("id") or "").lower()
    black_id = str(((players.get("black") or {}).get("user") or {}).get("id") or "").lower()

    if user_id_lc == white_id:
        return "white"
    if user_id_lc == black_id:
        return "black"
    return None


def _derive_result(game: dict, user_id_lc: str) -> str | None:
    """Map the Lichess game shape to {'win','loss','draw'} from our user's POV.

    Returns ``None`` if the linked user is not on either side (defensive).
    """
    user_color = _derive_player_color(game, user_id_lc)
    if user_color is None:
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
