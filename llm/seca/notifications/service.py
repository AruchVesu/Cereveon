"""Feed queries, lifecycle mutations, and the two v1 notification
producers (communication & access spec §5, adapted to this codebase).

Producers
---------
* ``notify_games_analyzed`` — fired by the Lichess import worker when a
  job finishes with freshly analyzed games.  Implements the spec §5.4
  batching rule: a second import completing within 60 minutes merges
  into the still-unread notification instead of stacking a new row.
* ``notify_lichess_disconnected`` — fired when an import discovers the
  linked Lichess account is gone (404).  Duplicate-suppressed: at most
  one live (undismissed, unexpired) alert per player.  Resolved — not
  merely aged out — by ``resolve_lichess_disconnected`` when an import
  completes cleanly or the player re-links (spec §5.8: never show a
  Lichess alert the player has already acted on).

Sessions and commits: every mutating function commits, matching the
``import_service`` convention — callers include the import worker's
``_WorkerSession``, where nothing upstream would flush otherwise.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from llm.seca.notifications.models import (
    ACTION_LICHESS_RECONNECT,
    ACTION_OPEN_HISTORY,
    BADGE_PRIORITIES,
    Notification,
    PRIORITY_HIGH,
    PRIORITY_MEDIUM,
    TYPE_GAME_ANALYZED,
    TYPE_SYSTEM_ALERT,
)

logger = logging.getLogger(__name__)

#: Spec §5.4 — imports completing within this window of a still-unread
#: ``game_analyzed`` notification merge into it.
GAME_ANALYZED_MERGE_WINDOW_MINUTES = 60

#: Spec §5.2 expiry for ``game_analyzed`` rows.
GAME_ANALYZED_EXPIRES_DAYS = 30

#: Spec §5.6 — rows older than this are hidden from the feed (they stay
#: in the DB; the filter is read-side only).
FEED_MAX_AGE_DAYS = 30

#: Feed page size cap — the feed is not paginated in v1; the client
#: renders at most this many rows, newest first.
FEED_LIMIT = 50


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


def _visible_filter(query, player_id: str, now: datetime):
    """Feed visibility per spec §5.6: not dismissed, not expired, ≤ 30d."""
    cutoff = now - timedelta(days=FEED_MAX_AGE_DAYS)
    return query.filter(
        Notification.player_id == player_id,
        Notification.dismissed_at.is_(None),
        Notification.created_at >= cutoff,
        or_(Notification.expires_at.is_(None), Notification.expires_at > now),
    )


def unread_badge_count(db: DBSession, player_id: str) -> int:
    """Bell-badge count: unread visible rows at medium+ priority (§5.6)."""
    now = datetime.utcnow()
    return int(
        _visible_filter(db.query(Notification), player_id, now)
        .filter(
            Notification.read_at.is_(None),
            Notification.priority.in_(BADGE_PRIORITIES),
        )
        .count()
    )


def serialize_notification(notification: Notification) -> dict:
    """Wire shape for one feed row (docs/API_CONTRACTS.md §40)."""
    try:
        metadata = json.loads(notification.metadata_json or "{}")
        if not isinstance(metadata, dict):
            metadata = {}
    except (TypeError, ValueError):
        # Defensive: the column is only ever written by this module,
        # but a corrupt row must not 500 the whole feed.
        metadata = {}
    return {
        "id": notification.id,
        "type": notification.type,
        "priority": notification.priority,
        "title": notification.title,
        "body": notification.body,
        "action": notification.action,
        "action_label": notification.action_label,
        "metadata": metadata,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
    }


def get_feed(db: DBSession, player_id: str) -> dict:
    """Full feed response: visible rows newest-first + the badge count."""
    now = datetime.utcnow()
    rows = (
        _visible_filter(db.query(Notification), player_id, now)
        .order_by(Notification.created_at.desc())
        .limit(FEED_LIMIT)
        .all()
    )
    return {
        "notifications": [serialize_notification(row) for row in rows],
        "unread_count": unread_badge_count(db, player_id),
    }


# ---------------------------------------------------------------------------
# Lifecycle mutations
# ---------------------------------------------------------------------------


def mark_read(db: DBSession, notification: Notification) -> None:
    """Idempotent: a second read keeps the original ``read_at``."""
    if notification.read_at is None:
        notification.read_at = datetime.utcnow()
        db.commit()


def mark_all_read(db: DBSession, player_id: str) -> int:
    """Mark every visible unread row read; returns the number marked."""
    now = datetime.utcnow()
    marked = (
        _visible_filter(db.query(Notification), player_id, now)
        .filter(Notification.read_at.is_(None))
        .update({Notification.read_at: now}, synchronize_session=False)
    )
    db.commit()
    return int(marked)


def dismiss(db: DBSession, notification: Notification) -> None:
    """Idempotent soft delete from the feed (row stays for analytics)."""
    if notification.dismissed_at is None:
        notification.dismissed_at = datetime.utcnow()
        db.commit()


# ---------------------------------------------------------------------------
# Producers
# ---------------------------------------------------------------------------


def notify_games_analyzed(db: DBSession, player_id: str, analyzed_count: int) -> None:
    """Create-or-merge the "reviews ready" notification (spec §5.4).

    Merge target: the latest UNREAD ``game_analyzed`` row created within
    the last 60 minutes.  A read row is never merged into — the player
    already saw it, so further games get a fresh notification (spec §5.4
    edge case).  Merging resets ``created_at`` so the merged row sorts
    (and expires) as new.
    """
    if analyzed_count <= 0:
        return

    now = datetime.utcnow()
    window_start = now - timedelta(minutes=GAME_ANALYZED_MERGE_WINDOW_MINUTES)
    existing = (
        db.query(Notification)
        .filter(
            Notification.player_id == player_id,
            Notification.type == TYPE_GAME_ANALYZED,
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
            Notification.created_at >= window_start,
        )
        .order_by(Notification.created_at.desc())
        .first()
    )

    total = analyzed_count
    if existing is not None:
        try:
            prior = json.loads(existing.metadata_json or "{}")
            total += int(prior.get("games_analyzed", 0))
        except (TypeError, ValueError):
            logger.warning(
                "unreadable metadata on notification %s; merge restarts count", existing.id
            )

    title, body = _games_analyzed_copy(total)
    target = (
        existing
        if existing is not None
        else Notification(
            player_id=player_id,
            type=TYPE_GAME_ANALYZED,
            priority=PRIORITY_MEDIUM,
            action=ACTION_OPEN_HISTORY,
            action_label="See games",
        )
    )
    target.title = title
    target.body = body
    target.metadata_json = json.dumps({"games_analyzed": total})
    target.created_at = now
    target.expires_at = now + timedelta(days=GAME_ANALYZED_EXPIRES_DAYS)
    db.add(target)
    db.commit()


def _games_analyzed_copy(total: int) -> tuple[str, str]:
    """Adapted spec §5.3 templates.

    Count-based rather than opponent-named: the import worker knows the
    analyzed COUNT, and threading per-game opponent names through the
    job row would add persistence for one line of copy.  Copy follows
    the client-string conventions: plain coaching voice, no
    engine-of-record wording.
    """
    if total == 1:
        return ("Review ready", "A game from your Lichess history is ready to explore.")
    return (
        f"{total} games reviewed",
        f"{total} games from your Lichess history are ready to explore.",
    )


def notify_lichess_disconnected(db: DBSession, player_id: str, lichess_username: str) -> None:
    """Raise the "Lichess connection lost" alert, at most one live copy.

    Duplicate suppression (spec §5.2 system_alert row): if an
    undismissed alert already exists it is left untouched — including
    its read state, so a player who read the alert yesterday is not
    re-badged every time a retry hits the same 404.
    """
    existing = (
        db.query(Notification)
        .filter(
            Notification.player_id == player_id,
            Notification.type == TYPE_SYSTEM_ALERT,
            Notification.action == ACTION_LICHESS_RECONNECT,
            Notification.dismissed_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        return

    db.add(
        Notification(
            player_id=player_id,
            type=TYPE_SYSTEM_ALERT,
            priority=PRIORITY_HIGH,
            title="Lichess connection lost",
            body=(
                f"We can't reach your Lichess account ({lichess_username}) anymore. "
                "This can happen if the account was closed or renamed. Your games "
                "and reviews stay here — reconnect to keep importing new games."
            ),
            action=ACTION_LICHESS_RECONNECT,
            action_label="Reconnect",
            # expires_at stays NULL: "until resolved" (spec §5.2) — the
            # import service resolves it on proven reconnection.
            metadata_json=json.dumps({"lichess_username": lichess_username}),
        )
    )
    db.commit()


def resolve_lichess_disconnected(db: DBSession, player_id: str) -> int:
    """Dismiss all live Lichess-disconnected alerts for the player.

    Called when connectivity is proven again (clean import, re-link) or
    the player deliberately unlinks — in every case the alert's ask has
    been answered, and spec §5.8 forbids showing it further.  Returns
    the number of rows dismissed.
    """
    dismissed = (
        db.query(Notification)
        .filter(
            Notification.player_id == player_id,
            Notification.type == TYPE_SYSTEM_ALERT,
            Notification.action == ACTION_LICHESS_RECONNECT,
            Notification.dismissed_at.is_(None),
        )
        .update({Notification.dismissed_at: datetime.utcnow()}, synchronize_session=False)
    )
    db.commit()
    return int(dismissed)
