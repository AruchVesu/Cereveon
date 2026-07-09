"""Deterministic usage-metering service for the freemium entitlements layer.

Subtask 2 of the freemium-tiers plan.  Pure service layer: no routes, no
LLM calls, no schema changes — just the plan/limit table and the three
metering primitives the route layer (next subtasks) composes:

- ``check(db, player, metric)``   — read-only pre-check ("would the next
  unit be over the limit?").  Used by the /chat 402 gate.
- ``record(db, player, metric)``  — atomically increment a pure counter
  after the metered work SUCCEEDED (so a 5xx never consumes quota).
- ``admit(db, player, metric, subject)`` — distinct-subject admission for
  per-game metering: the FIRST N distinct game_ids of the period get the
  LLM-coached path, and every later move of an admitted game stays
  admitted; games over the limit degrade consistently.
- ``set_plan(db, player, plan)``  — plan flip for the billing endpoint.

Activation
----------
Everything is dormant until ``SECA_ENTITLEMENTS_ENFORCED`` resolves true
(``resolve_enforced``, read at CALL time so tests can monkeypatch the
env and a misconfigured process fails to the current production
behaviour: no metering).  Polarity is the OPPOSITE of ``SECA_SAFE_MODE``
on purpose: safe-mode's failure posture is "stay locked", the
entitlements failure posture is "stay free" — an unset / empty / typo'd
value must never start rejecting paying traffic.

Failure posture
---------------
Metering is a monetisation signal, never a safety gate, so every DB
failure inside ``check`` / ``record`` / ``admit`` rolls back and FAILS
OPEN (allow, don't degrade) with a warning — coaching availability wins
over quota precision.  ``set_plan`` is the one exception: the billing
endpoint must observe persistence failures, so it re-raises after
rollback.

Concurrency
-----------
``record`` increments via a single ``UPDATE ... SET count = count + 1``
(no read-modify-write lost updates); first-use insert races resolve via
``uq_usage_counter_scope`` + ``IntegrityError`` retry.  ``admit`` may
over-admit by one game when two DIFFERENT game_ids race the last slot —
acceptable for a soft product limit and documented here rather than
solved with locking.

Project Rule 3 note: this module is deterministic bookkeeping (closed
vocabulary in, counter rows out).  It defines no ``def train`` and calls
no ML-receiver ``.update`` — see ``llm/seca/safety/freeze.py``; the test
file runs the freeze patterns over this source to pin that.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.entitlements.models import UsageCounter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

PLAN_FREE = "free"
PLAN_PRO = "pro"
KNOWN_PLANS = (PLAN_FREE, PLAN_PRO)

METRIC_COACHED_GAME = "coached_game"
METRIC_CHAT_TURN = "chat_turn"
METRIC_IMPORT_ANALYSIS = "import_analysis"

_DAILY = "daily"
_MONTHLY = "monthly"

#: Over-limit behaviour kinds.  ``degrade`` = request proceeds on the
#: deterministic (zero-LLM-cost) path; ``block`` = request is refused
#: with the documented payment-required contract.
BEHAVIOR_DEGRADE = "degrade"
BEHAVIOR_BLOCK = "block"


@dataclass(frozen=True)
class Limit:
    """One plan/metric entitlement: threshold, calendar bucket, behaviour."""

    limit: int
    granularity: str  # _DAILY | _MONTHLY
    behavior: str  # BEHAVIOR_DEGRADE | BEHAVIOR_BLOCK


#: The product spec, verbatim.  Free showcases the coach (one fully
#: LLM-coached game + three chat questions a day); the daily game is
#: HARD-blocked at /game/start (client shows the paywall), and the
#: degrade behaviour below is defense-in-depth for clients that bypass
#: the start gate.  Pro plays UNLIMITED games ("Unlimited adaptive
#: games" on the paywall is literal): the /game/start gate never blocks
#: pro; past the daily coached-game cap the hints degrade to the
#: deterministic coach, which costs zero LLM tokens — so the caps below
#: are the pro TOKEN ceiling, not a play ceiling.  Post-2×-price
#: worst case ≈ €2/mo per pathological subscriber vs ~€7 net revenue
#: (see the unit-economics memory / PaywallActivity pricing KDoc).
LIMITS: dict[str, dict[str, Limit]] = {
    PLAN_FREE: {
        METRIC_COACHED_GAME: Limit(1, _DAILY, BEHAVIOR_DEGRADE),
        METRIC_CHAT_TURN: Limit(3, _DAILY, BEHAVIOR_BLOCK),
        METRIC_IMPORT_ANALYSIS: Limit(3, _MONTHLY, BEHAVIOR_BLOCK),
    },
    PLAN_PRO: {
        METRIC_COACHED_GAME: Limit(10, _DAILY, BEHAVIOR_DEGRADE),
        # 30/day (was 100): far above honest use (heavy testing runs
        # ~3/day) but it halves the pathological token ceiling — chat
        # is the priciest per-unit surface.
        METRIC_CHAT_TURN: Limit(30, _DAILY, BEHAVIOR_BLOCK),
        METRIC_IMPORT_ANALYSIS: Limit(50, _MONTHLY, BEHAVIOR_BLOCK),
    },
}


@dataclass(frozen=True)
class Decision:
    """Outcome of a metering question.

    ``allowed=False`` only ever comes from BLOCK-behaviour metrics (the
    caller returns the 402 contract).  ``degrade=True`` only ever comes
    from DEGRADE-behaviour metrics (the caller switches to the
    deterministic coach).  When enforcement is off the limit/used/
    remaining fields are ``None`` — "not metered", distinct from
    "metered with headroom".
    """

    allowed: bool
    degrade: bool
    plan: str
    metric: str
    limit: int | None = None
    used: int | None = None
    remaining: int | None = None


# ---------------------------------------------------------------------------
# Activation + small helpers
# ---------------------------------------------------------------------------


def resolve_enforced() -> bool:
    """Whether entitlement limits are live.  Default False (dormant).

    Only an explicit ``true`` / ``1`` / ``yes`` (case-insensitive)
    enables enforcement; unset, empty, or unrecognised values stay
    dormant.  Read at call time — cheap, and it keeps tests free of
    module-reload gymnastics.
    """
    raw = os.getenv("SECA_ENTITLEMENTS_ENFORCED")
    if raw is None:
        return False
    return raw.strip().lower() in ("true", "1", "yes")


def _plan_for(player: Player) -> str:
    """The player's plan, normalised to the closed vocabulary.

    Unknown values (bad manual edit, future plan name reaching an old
    deploy) fall back to ``free`` with a warning — the schema default
    guarantees non-NULL, so this is belt-and-braces.
    """
    plan = getattr(player, "plan", None) or PLAN_FREE
    if plan not in LIMITS:
        logger.warning("Unknown plan %r for player %s; treating as free", plan, player.id)
        return PLAN_FREE
    return plan


def _period_key(granularity: str, now: datetime) -> str:
    """UTC calendar bucket — must match the format documented on
    ``UsageCounter.period_key``."""
    return now.strftime("%Y-%m-%d") if granularity == _DAILY else now.strftime("%Y-%m")


def _not_metered(plan: str, metric: str) -> Decision:
    return Decision(allowed=True, degrade=False, plan=plan, metric=metric)


# ---------------------------------------------------------------------------
# Metering primitives
# ---------------------------------------------------------------------------


def check(
    db: DBSession,
    player: Player,
    metric: str,
    *,
    now: datetime | None = None,
) -> Decision:
    """Read-only: would the NEXT unit of ``metric`` be within the limit?

    For pure counters ``used`` is the counter row's count; for the
    marker metric (``coached_game``) it is the number of distinct
    admitted subjects this period.  Never writes.
    """
    plan = _plan_for(player)
    if not resolve_enforced():
        return _not_metered(plan, metric)
    cfg = LIMITS[plan].get(metric)
    if cfg is None:
        logger.warning("No limit configured for plan=%s metric=%s; allowing", plan, metric)
        return _not_metered(plan, metric)

    # Captured before any rollback: rollback() expires ORM instances, so
    # touching player.id afterwards would itself hit the (failing) DB.
    pid = player.id
    period = _period_key(cfg.granularity, now or datetime.utcnow())
    try:
        query = db.query(UsageCounter).filter(
            UsageCounter.player_id == pid,
            UsageCounter.metric == metric,
            UsageCounter.period_key == period,
        )
        if metric == METRIC_COACHED_GAME:
            used = query.filter(UsageCounter.subject != "").count()
        else:
            row = query.filter(UsageCounter.subject == "").one_or_none()
            used = int(row.count) if row is not None else 0
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Entitlements check failed for %s/%s; failing open", metric, pid)
        return _not_metered(plan, metric)

    over = used >= cfg.limit
    return Decision(
        allowed=not (over and cfg.behavior == BEHAVIOR_BLOCK),
        degrade=over and cfg.behavior == BEHAVIOR_DEGRADE,
        plan=plan,
        metric=metric,
        limit=cfg.limit,
        used=used,
        remaining=max(0, cfg.limit - used),
    )


def record(
    db: DBSession,
    player: Player,
    metric: str,
    *,
    now: datetime | None = None,
) -> None:
    """Consume one unit of a pure-counter metric.

    Call AFTER the metered work succeeded (2xx) so failures never eat
    quota.  Atomic ``count = count + 1`` UPDATE (no lost updates);
    first-use insert races resolve via the unique constraint.  Failures
    roll back and are swallowed with a warning — see the module
    docstring's failure posture.
    """
    if not resolve_enforced():
        return
    cfg = LIMITS[_plan_for(player)].get(metric)
    if cfg is None:
        return
    pid = player.id  # pre-rollback capture, same reasoning as check()
    period = _period_key(cfg.granularity, now or datetime.utcnow())

    def _increment() -> int:
        return (
            db.query(UsageCounter)
            .filter(
                UsageCounter.player_id == pid,
                UsageCounter.metric == metric,
                UsageCounter.period_key == period,
                UsageCounter.subject == "",
            )
            .update({"count": UsageCounter.count + 1}, synchronize_session=False)
        )

    try:
        if _increment() == 0:
            try:
                db.add(UsageCounter(player_id=pid, metric=metric, period_key=period, count=1))
                db.commit()
                return
            except IntegrityError:
                # Concurrent first use created the row between our UPDATE
                # and INSERT — retry the increment on the winner's row.
                db.rollback()
                _increment()
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Entitlements record failed for %s/%s; usage not counted", metric, pid)


def admit(
    db: DBSession,
    player: Player,
    metric: str,
    subject: str | None,
    *,
    now: datetime | None = None,
) -> Decision:
    """Distinct-subject admission (the ``coached_game`` path).

    A period admits up to ``limit`` distinct subjects; each admission is
    a marker row, so re-asking for an admitted subject is idempotent and
    an over-limit subject degrades consistently for its whole game (no
    marker is written for it).  A missing subject fails OPEN — clients
    that don't send a game_id yet must never be punished.
    """
    plan = _plan_for(player)
    if not resolve_enforced():
        return _not_metered(plan, metric)
    cfg = LIMITS[plan].get(metric)
    if cfg is None:
        return _not_metered(plan, metric)
    if not subject:
        logger.debug("admit(%s) without subject; failing open (pre-game_id client)", metric)
        return _not_metered(plan, metric)

    pid = player.id  # pre-rollback capture, same reasoning as check()
    period = _period_key(cfg.granularity, now or datetime.utcnow())
    try:
        scope = db.query(UsageCounter).filter(
            UsageCounter.player_id == pid,
            UsageCounter.metric == metric,
            UsageCounter.period_key == period,
        )
        already = scope.filter(UsageCounter.subject == subject).one_or_none()
        used = scope.filter(UsageCounter.subject != "").count()
        if already is not None:
            return Decision(
                allowed=True, degrade=False, plan=plan, metric=metric,
                limit=cfg.limit, used=used, remaining=max(0, cfg.limit - used),
            )

        if used < cfg.limit:
            try:
                db.add(
                    UsageCounter(
                        player_id=pid,
                        metric=metric,
                        period_key=period,
                        subject=subject,
                        count=1,
                    )
                )
                db.commit()
            except IntegrityError:
                # Concurrent admit of the SAME subject — it's admitted.
                db.rollback()
            return Decision(
                allowed=True, degrade=False, plan=plan, metric=metric,
                limit=cfg.limit, used=used + 1, remaining=max(0, cfg.limit - used - 1),
            )
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Entitlements admit failed for %s/%s; failing open", metric, pid)
        return _not_metered(plan, metric)

    return Decision(
        allowed=not (cfg.behavior == BEHAVIOR_BLOCK),
        degrade=cfg.behavior == BEHAVIOR_DEGRADE,
        plan=plan,
        metric=metric,
        limit=cfg.limit,
        used=used,
        remaining=0,
    )


def set_plan(db: DBSession, player: Player, plan: str) -> None:
    """Flip the player's subscription plan (billing endpoint only).

    Validates against the closed vocabulary and re-raises persistence
    failures after rollback — the caller must not report success for a
    flip that didn't land.
    """
    if plan not in KNOWN_PLANS:
        raise ValueError(f"Unknown plan {plan!r}; expected one of {KNOWN_PLANS}")
    try:
        player.plan = plan
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
