"""
SECA SAFETY FREEZE GUARD
========================
Hard-disables any self-modifying or adaptive learning behaviour at runtime.

The architecture (CLAUDE.md rule 3, docs/ARCHITECTURE.md "Forbidden Changes")
prohibits autonomous reinforcement learning in this system.  This module is
the **runtime tripwire** that enforces the prohibition: if anyone
reintroduces a forbidden keyword, an unsafe world model, an unallowlisted
``brain.*`` module, or flips ``SAFE_MODE`` to ``False`` in production, the
process refuses to start.

``enforce(world_model)`` is called once during FastAPI ``lifespan`` startup
and ``sys.exit(1)``s the process if any unsafe component has been imported,
if the wrong world model is in use, or if online learning has been
explicitly enabled by env var.

Historical context
------------------
Earlier revisions of this codebase carried substantial dormant RL/ML code.
Multiple deletion sweeps progressively removed the most dangerous clusters
(see ``git log llm/seca/`` for the per-commit detail).  The Sprint 2
"delete-only" PR (May 2026) was the largest single sweep, removing
``llm/seca/{optim, models, henm, closed_loop, evolution, opponent, memory,
outcome, realtime, serving, policy, player}/`` outright; reducing
``llm/seca/world_model/`` to ``safe_stub.py`` + ``__init__.py``; pruning
``llm/seca/curriculum/{actions, curriculum_generator, curriculum_types,
lesson_selector, optoimizer, planner, training_tasks, weakness_detector}.py``;
and deleting top-level ``llm/{world_model, bootstrap_skill_dataset,
governor}.py``.  Several test-paired dormant clusters (``brain/{meta,
rewards, world_model, data}/``, ``engines/{hmpt, adaptive}/``, parts of
``learning/``) remain on disk because deleting them requires retiring CI
regression tests (BUG-3..BUG-8 in ``test_bug_regressions.py``); they are
unreachable at runtime under ``SAFE_MODE=True`` and trip the guard if
imported into a live process anyway.  See the audit notes in the Sprint 2
commit message for the full inventory.

Policy
------
Three independent checks (defence in depth):

1.  Brain-tree allowlist.  Anything under ``llm.seca.brain.*`` that is not
    on the explicit allowlist is treated as forbidden — regardless of name
    or contents.  The allowlist is intentionally tiny and contains only the
    SQLAlchemy schema modules required for ORM ``Base.metadata`` to be
    consistent, plus the three observation-only LinUCB helpers used by the
    live coach.  *New* brain modules cannot be silently loaded by appearing
    in ``sys.modules``; they must be deliberately added here.

2.  Forbidden module-name parts.  Substring matches against module names
    used by historical or hypothetical adaptive components.

3.  Forbidden source keywords.  Substring matches against module source
    text — covers the major training entry points used by the dormant ML
    code (PyTorch, sklearn online learners, custom bandit save loops).
    Keywords are deliberately specific to avoid false-positives.

Scan scope
----------
``_scan_loaded_modules`` walks ``llm.seca.*`` only.  The audit's worry
about top-level ``llm/<rl_thing>.py`` shapes (the original
``llm/world_model.py``, ``llm/bootstrap_skill_dataset.py``,
``llm/governor.py`` files) is addressed by their outright deletion in the
Sprint 2 PR rather than by widening the scan — ``FORBIDDEN_KEYWORDS``
contains generic substrings (``train(``, ``.update(``) that false-
positive on legitimate production code outside the SECA tree, so widening
without first tightening the keywords would mask real safety hits with
false alarms.  A future revival of the top-level RL-file shape would
re-trip the audit and warrant a keyword-tightening pass first.
"""

import logging
import os
import sys
import inspect

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

SAFE_WORLD_MODEL_CLASS = "SafeWorldModel"

# Modules under llm.seca.brain.* that are SAFE to load — every other path
# under brain/ is forbidden.  Keep this list tiny: schema/registration
# modules required for SQLAlchemy ``Base.metadata``, plus two helpers that
# happen to live under brain/ for historical naming reasons but contain no
# learning logic.
#
# - llm.seca.brain                          : empty package marker
# - llm.seca.brain.models                   : SQLAlchemy tables
#                                             (RatingUpdate, ConfidenceUpdate,
#                                             BanditExperience).  No RL code.
# - llm.seca.brain.training                 : empty package marker
# - llm.seca.brain.training.models          : SQLAlchemy tables
#                                             (TrainingDecision, TrainingOutcome).
#                                             No RL code.
# - llm.seca.brain.bandit                   : empty package marker imported
#                                             transitively by the two helpers
#                                             below.
# - llm.seca.brain.bandit.experience_store  : raw INSERT into bandit_experiences
#                                             table.  Logs context+action+reward
#                                             for *future* offline analysis but
#                                             does no fitting/learning of its own.
#                                             Used live by skills/updater.py.
# - llm.seca.brain.bandit.context_builder   : returns a six-element numpy
#                                             feature vector from rating /
#                                             confidence / accuracy / weaknesses.
#                                             No model, no learning.  Used live
#                                             by skills/updater.py.
#
# All other paths under brain/* (global_bandit, contextual_bandit,
# online_update, world_model.predict, world_model.train_regression,
# rewards/*, meta/*, etc.) are forbidden.
ALLOWED_BRAIN_MODULES = frozenset(
    {
        "llm.seca.brain",
        "llm.seca.brain.models",
        "llm.seca.brain.training",
        "llm.seca.brain.training.models",
        "llm.seca.brain.bandit",
        "llm.seca.brain.bandit.experience_store",
        "llm.seca.brain.bandit.context_builder",
        # Decision-layer LinUCB substrate.  SECA v1 explicitly permits
        # the "lightweight online update of decision-layer components"
        # this module performs (closed-form A ← A+xxᵀ, b ← b+rx — no
        # gradient step, no optimiser state).  See module-level kdoc +
        # docs/SECA.md for the policy boundary.
        "llm.seca.brain.bandit.decision",
    }
)

FORBIDDEN_KEYWORDS = [
    # Custom adaptive controllers — historical names that flagged the
    # OnlineSECALearner background loop and the planned RL trainers.
    # The classes themselves were deleted in the dormant-RL sweeps;
    # the keywords stay as a re-introduction tripwire so a future
    # contributor cannot quietly resurrect the same patterns.
    "OnlineSECALearner",
    "train_rl",
    "train_value_model",
    # Bandit persistence / online-update entry points.  The classes
    # that historically called these (``OnlineSECALearner``, the dormant
    # online_update modules) were deleted in earlier sweeps; the
    # keywords stay as a tripwire so anyone reintroducing a
    # ``bandit.save()`` / ``bandit.update()`` call anywhere in
    # ``llm/seca/`` fails the startup scan.  Pinned by
    # ``test_safety_freeze.FreezeKeywordScanTest::test_bandit_save_keyword_blocked``.
    "bandit.update",
    "bandit.save",
    # Generic in-place training calls.  ``train(`` catches a
    # ``def train(...)`` or ``foo.train(`` invocation anywhere in
    # seca/ — the historical SECA trainers all had a public
    # ``train()`` entry point.  ``.update(`` is a heuristic that
    # would catch a fitted-model in-place update; it has zero
    # false-positive risk in the current tree because the live coach
    # pipelines never call ``.update`` on a learnt model — the
    # observation-only LinUCB head uses closed-form sufficient
    # statistics, not ``.update``.
    "train(",
    ".update(",
    # PyTorch / sklearn training-step keywords.  These are the
    # canonical signals that a module is doing gradient descent or
    # online fitting — any seca/ source that contains them at module
    # load time fails the startup scan.
    "optimizer.step",
    "loss.backward",
    ".partial_fit(",
    # Neural-policy *definition* keywords.  After the fourth deletion
    # sweep removed ``seca/engines/hmpt/`` and ``seca/engines/adaptive/``
    # (PyTorch ``nn.Module`` substrate that nothing imported), no live
    # SECA module imports torch.  These keywords ensure a future
    # contributor who tries to reintroduce a neural-policy class — even
    # without ever calling ``optimizer.step`` (e.g., a pure-inference
    # model loaded from a pickled state-dict) — trips the startup scan.
    # Cross-checked: ``llm/seca/`` contains no ``import torch`` or
    # ``nn.Module`` after the sweep, so these are tripwires only.
    "import torch",
    "nn.Module",
]

FORBIDDEN_MODULE_PARTS = [
    "brain.rl",
    "brain.bandit.online",
]


# ------------------------------------------------------------------
# Guards
# ------------------------------------------------------------------


def _scan_loaded_modules():
    """Scan already imported modules for forbidden adaptive components.

    Scope is ``llm.seca.*`` only.  Widening to ``llm.*`` was attempted but
    reverted: ``FORBIDDEN_KEYWORDS`` contains generic substrings
    (``train(``, ``.update(``) chosen to catch the historical SECA
    trainers' entry points, and those false-positive on legitimate
    production modules outside the SECA tree (e.g.
    ``llm.elite_engine_service.update_*`` cache helpers).  The audit's
    specific worry — the top-level ``llm/world_model.py`` and
    ``llm/bootstrap_skill_dataset.py`` files sitting outside the scan —
    is addressed by deleting those files in the Sprint 2 PR.  A future
    revival of that shape would re-trip the audit and warrant a
    keyword-tightening pass before re-widening the scan.
    """
    for name, module in sys.modules.items():
        if module is None:
            continue
        if not name.startswith("llm.seca"):
            continue
        if name.startswith("llm.seca.safety"):
            continue
        if "mock" in name:
            continue

        # Brain allowlist — strictest check.  Any brain.* module that is not
        # on the allowlist is forbidden, even if its source contains no
        # keyword matches.
        if name == "llm.seca.brain" or name.startswith("llm.seca.brain."):
            if name not in ALLOWED_BRAIN_MODULES:
                _crash(f"Forbidden brain module loaded: {name}")

        # Forbidden module-name parts (fallback for non-brain paths)
        for bad in FORBIDDEN_MODULE_PARTS:
            if bad in name:
                _crash(f"Forbidden adaptive module loaded: {name}")

        # Source keyword scan
        try:
            src = inspect.getsource(module)
        except (OSError, TypeError):
            continue

        for kw in FORBIDDEN_KEYWORDS:
            if kw in src:
                _crash(f"Forbidden adaptive code detected in module: {name}")


def _assert_safe_world_model(world_model):
    """Ensure only SafeWorldModel is used at runtime."""
    if world_model is None:
        _crash("World model not initialized")

    cls_name = world_model.__class__.__name__
    if cls_name != SAFE_WORLD_MODEL_CLASS:
        _crash(f"Unsafe world model detected: {cls_name}")


def _assert_no_background_tasks():
    """Detect accidental async learner loops via env flags."""
    if os.getenv("SECA_ENABLE_ONLINE_LEARNING") == "1":
        _crash("Online learning explicitly enabled via env")


def _assert_safe_mode_locked():
    """Validate that ``SAFE_MODE`` is True at startup, or that the
    process is in a non-prod environment where a False value is a
    deliberate dev opt-in.

    The bool lives in ``seca.runtime.safe_mode``.  Historically it
    gated dormant ``if not SAFE_MODE:`` blocks in ``events/router.py``,
    ``skills/trainer.py``, and ``learning/trainer.py``; those blocks
    and the modules they imported were deleted in the dormant-RL
    sweep.  The flag and this guard are retained as a re-introduction
    tripwire: a future feature that wants to gate adaptive behaviour
    on ``SAFE_MODE`` will trip this check in production unless the
    operator explicitly opts in (which the freeze guard refuses for
    ``SECA_ENV=prod``).

    Resolution:
    - ``SAFE_MODE=True`` (default, any env): no-op.
    - ``SAFE_MODE=False`` with ``SECA_ENV=prod``: hard crash.
    - ``SAFE_MODE=False`` with ``SECA_ENV != prod``: warning, no crash —
      developers can exercise hypothetical adaptive paths under test,
      but the warning makes the configuration audible.
    """
    from llm.seca.runtime.safe_mode import SAFE_MODE

    if SAFE_MODE:
        return

    env = os.getenv("SECA_ENV", "dev").strip().lower()
    if env == "prod":
        _crash(
            f"SAFE_MODE is False with SECA_ENV={env!r}.  Production "
            f"must run with the SECA adaptive layer locked.  Disable "
            f"SECA_SAFE_MODE only in non-prod environments."
        )

    logger.warning(
        "SAFE_MODE is False (SECA_ENV=%s).  The dormant adaptive code "
        "paths gated by `if not SAFE_MODE:` are now LIVE.  This must "
        "never happen in production.",
        env,
    )


def _crash(reason: str):
    """Immediate hard stop."""
    logger.critical("=" * 60)
    logger.critical("SECA SAFETY FREEZE TRIGGERED")
    logger.critical("Reason: %s", reason)
    logger.critical("Runtime is NOT SAFE. Shutting down.")
    logger.critical("=" * 60)
    sys.exit(1)


# ------------------------------------------------------------------
# Public entrypoint
# ------------------------------------------------------------------


def enforce(world_model):
    """
    Call once during FastAPI startup.

    Example:
        from llm.seca.safety.freeze import enforce
        enforce(world_model)
    """
    _assert_safe_world_model(world_model)
    _assert_safe_mode_locked()
    _assert_no_background_tasks()
    _scan_loaded_modules()

    logger.info("SECA SAFETY FREEZE: runtime verified SAFE")


# ------------------------------------------------------------------
# Request-time verifier
# ------------------------------------------------------------------


def _quick_scan_modules() -> str | None:
    """Fast variant of ``_scan_loaded_modules`` for the request path.

    Skips the source-keyword regex pass (megabytes of regex on a busy
    Python process — fine once at startup, prohibitive per request).
    The brain allowlist and forbidden module-name parts still run; they
    are O(N) over ``sys.modules`` with cheap string ops and catch the
    structural reintroduction cases we care about.

    Returns ``None`` when the runtime passes.  Returns a one-line
    operator-readable reason otherwise.
    """
    for name, module in sys.modules.items():
        if module is None:
            continue
        if not name.startswith("llm.seca"):
            continue
        if name.startswith("llm.seca.safety"):
            continue
        if "mock" in name:
            continue

        if name == "llm.seca.brain" or name.startswith("llm.seca.brain."):
            if name not in ALLOWED_BRAIN_MODULES:
                return f"forbidden brain module loaded: {name}"

        for bad in FORBIDDEN_MODULE_PARTS:
            if bad in name:
                return f"forbidden module loaded: {name}"

    return None


def verify_runtime_safety(world_model) -> tuple[bool, str | None]:
    """Re-run the freeze guard's structural checks against the **current**
    runtime and return the result instead of ``sys.exit``-ing.

    This is the per-request counterpart to :func:`enforce`.  The startup
    enforcer is a hard process-level gate; this verifier is what the
    ``GET /seca/status`` route uses to answer the Android client's
    "is the backend currently safe?" question.

    Why a separate function
    -----------------------
    Calling ``enforce`` from a request handler would ``sys.exit(1)`` the
    whole server on the first drift.  That's the right policy at startup
    (refuse to serve traffic) but the wrong shape per-request: we want
    to surface ``safe_mode: false`` to the caller so they can stop
    coaching, log the reason for operators, and let the process keep
    serving non-coaching traffic until the operator intervenes.

    Why a *quick* scan
    ------------------
    The source-keyword pass that ``enforce`` runs is too expensive to
    repeat per request.  It stays at startup as the deep
    code-fingerprinting check; this verifier covers the brain allowlist,
    forbidden module-name parts, the ``SAFE_MODE`` flag, the world
    model class, and the ``SECA_ENABLE_ONLINE_LEARNING`` env opt-in.

    Returns
    -------
    Tuple ``(ok, reason)``:

    - ``(True, None)``  every check passed.
    - ``(False, "<reason>")``  one check failed; ``reason`` is a
      single line suitable for the operator log.  The string never
      contains untrusted user input — only module names, env names,
      and class names from the runtime itself — so it is safe to log
      verbatim and acceptable to surface to operators in
      ``/seca/status`` if the response shape is later widened.
    """
    from llm.seca.runtime.safe_mode import SAFE_MODE  # local import — avoids cycle

    if not SAFE_MODE:
        return (False, "SAFE_MODE flag is False")

    if os.getenv("SECA_ENABLE_ONLINE_LEARNING") == "1":
        return (False, "SECA_ENABLE_ONLINE_LEARNING=1 set in environment")

    if world_model is None:
        return (False, "world_model not initialised (lifespan startup did not complete)")

    cls_name = world_model.__class__.__name__
    if cls_name != SAFE_WORLD_MODEL_CLASS:
        return (False, f"unsafe world model class: {cls_name}")

    reason = _quick_scan_modules()
    if reason is not None:
        return (False, reason)

    return (True, None)
