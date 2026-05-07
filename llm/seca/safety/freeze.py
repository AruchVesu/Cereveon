"""
SECA SAFETY FREEZE GUARD
========================
Hard-disables any self-modifying or adaptive learning behaviour at runtime.

The architecture (CLAUDE.md rule 3, docs/ARCHITECTURE.md "Forbidden Changes")
prohibits autonomous reinforcement learning in this system.  Five deletion
sweeps removed every dormant RL/ML tree from the live codebase:

  - First sweep (May 2026): ``llm/seca/{closed_loop, evolution, optim,
    opponent, policy, henm, outcome, world, coaching}/``, ``llm/seca/brain/{meta,
    rewards, world_model, data}/``, ``llm/seca/learning/{trainer, causal_*,
    online_learner, performance, pipeline}.py``, plus the
    ``if not SAFE_MODE:`` blocks in ``events/router.py``.
  - Second sweep (May 2026): orphaned modules with no live importers that
    had been missed by the first sweep —
    ``llm/seca/{adapt, db, models}.py`` and ``llm/seca/{api, data, models,
    ratings, realtime, serving, skill}/``.
  - Third sweep (May 2026): ``llm/seca/memory/`` (six dialogue-memory
    modules with only internal cross-imports) and ``llm/seca/player/``
    (eight files including the AUT-02 quarantine ``player_api.py``;
    deleting the file is a stronger invariant than the prior "do not
    import" warning since the footgun no longer exists).
  - Fourth sweep (May 2026): the abandoned RL-curriculum substrate in
    ``llm/seca/curriculum/`` —
    ``{actions, curriculum_generator, curriculum_types, lesson_selector,
    optoimizer, planner, training_tasks, weakness_detector}.py``.
    The cluster was an alternative curriculum design built on the
    deleted ``llm.seca.player.player_model`` (so several files were
    already broken at import time after the third sweep); the live
    curriculum lives in ``router → generator → models/policy/types``
    + ``scheduler → priority_model + task_selector``.
  - Fifth sweep (May 2026): the neural-policy substrate inside
    ``llm/seca/engines/`` —
    ``llm/seca/engines/hmpt/{config, dataset, infer, model, train,
    v1encoder}.py`` (a 6-file PyTorch ``nn.Module`` cluster) plus
    ``llm/seca/engines/adaptive/{__init__, aoci, controller}.py``
    (its only consumer, itself with zero external importers).  Also
    deleted: ``data/models/hmpt/v1encoder.py`` (byte-identical
    duplicate at repo root), ``llm/data/skill_dataset.npz`` (1.28 MB
    RL training data), and orphaned data-prep scripts
    ``llm/bootstrap_skill_dataset.py`` + ``scripts/build_skill_dataset.py``
    (the latter even imported deleted modules — broken since sweep one).
    After this sweep, no live SECA module imports torch; the
    ``import torch`` and ``nn.Module`` keywords were added to
    ``FORBIDDEN_KEYWORDS`` as re-introduction tripwires.

This guard now serves as a re-introduction tripwire: if anyone reintroduces
a forbidden keyword, an unsafe world model, an unallowlisted ``brain.*``
module, or flips ``SAFE_MODE`` to ``False`` in production, the process
refuses to start.

``enforce(world_model)`` is called once during FastAPI ``lifespan`` startup
and ``sys.exit(1)``s the process if any unsafe component has been imported,
if the wrong world model is in use, or if online learning has been
explicitly enabled by env var.

Policy
------
Three independent checks (defence in depth):

1.  Brain-tree allowlist.  Anything under ``llm.seca.brain.*`` that is not on
    the explicit allowlist is treated as forbidden — regardless of name or
    contents.  The allowlist is intentionally tiny and contains only the
    SQLAlchemy schema modules required for ORM ``Base.metadata`` to be
    consistent.  *New* brain modules cannot be silently loaded by appearing
    in ``sys.modules``; they must be deliberately added here.

2.  Forbidden module-name parts.  Substring matches against module names
    used by historical or hypothetical adaptive components elsewhere in the
    seca tree.  This is a fallback for code paths that may move outside
    ``brain/`` in future refactors.

3.  Forbidden source keywords.  Substring matches against module source
    text — covers the major training entry points used by the dormant ML
    code (PyTorch, sklearn online learners, custom bandit save loops).
    Keywords are deliberately specific to avoid false-positives on
    non-RL modules; the brain allowlist provides the broad coverage.
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
    """Scan already imported modules for forbidden adaptive components."""
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
