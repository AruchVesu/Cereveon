"""
SECA SAFETY FREEZE GUARD
========================
Hard-disables any self-modifying or adaptive learning behaviour at runtime.

The architecture (CLAUDE.md rule 3, docs/ARCHITECTURE.md "Forbidden Changes")
prohibits autonomous reinforcement learning in this system.  RL/ML research
code remains in the tree (under llm/seca/brain/, llm/seca/henm/,
llm/seca/closed_loop/, llm/seca/optim/, llm/seca/models/, etc.) for future
study, but MUST NOT be loaded into the live runtime.

This module is the single runtime-enforcement layer for that policy.
``enforce(world_model)`` is called once during FastAPI ``lifespan`` startup
and ``sys.exit(1)``s the process if any unsafe component has been imported,
or if the wrong world model is in use, or if online learning has been
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
ALLOWED_BRAIN_MODULES = frozenset({
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
})

FORBIDDEN_KEYWORDS = [
    # Custom adaptive controllers
    "OnlineSECALearner",
    "train_rl",
    "train_value_model",
    # Bandit training entry points
    "bandit.update",
    "bandit.save",
    # Generic in-place training calls (catches train(...) helpers)
    "train(",
    ".update(",
    # PyTorch training step (catches gradient updates anywhere in seca)
    "optimizer.step",
    "loss.backward",
    # sklearn online learners
    ".partial_fit(",
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

    The bool lives in ``seca.runtime.safe_mode`` and gates the dormant
    adaptive code paths in ``events/router.py``, ``skills/trainer.py``,
    and ``learning/trainer.py``.  Those imports are lazy — they happen
    inside ``if not SAFE_MODE:`` blocks at request time, NOT at module
    import — so the startup ``_scan_loaded_modules`` pass cannot see
    them.  This guard exists to catch the underlying flag flip itself
    rather than waiting for the first request to import a forbidden
    module.

    Resolution:
    - ``SAFE_MODE=True`` (default, any env): no-op.
    - ``SAFE_MODE=False`` with ``SECA_ENV=prod``: hard crash.
    - ``SAFE_MODE=False`` with ``SECA_ENV != prod``: warning, no crash —
      developers may legitimately need to exercise the dormant paths
      under test, but the warning makes the configuration audible.
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
