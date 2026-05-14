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
governor}.py``.  The 2026-05-14 dormant-cluster cleanup pass deleted
the remaining test-impact-free dormant code: ``brain/rewards/``,
``brain/meta/{context, meta_coach}.py``, and the two experimental
neural-opponent dirs ``engines/{hmpt, adaptive}/``.  ``meta_coach.py``
in particular carried live ``bandit.update`` / ``bandit.save`` / pickle
keywords — the very patterns the source-scan layer below tripwires on
— so its removal closes a latent crash trigger that would have fired
the moment anything lazy-imported it.

Surviving dormant code on disk after the cleanup pass:

- ``brain/meta/meta_bandit.py`` — paired with BUG-11 regression test.
- ``brain/bandit/{contextual_bandit, global_bandit, online_update}.py`` —
  paired with BUG-4a/4b and BUG-10 regression tests.
- ``brain/world_model/{train_regression.py, world_model.pkl}`` and
  ``brain/data/{build_world_model_dataset.py, world_model_dataset.csv}`` —
  referenced by the standalone diagnostic ``llm/seca/seca_doctor.py``
  via subprocess (different process, never crosses the freeze guard).
- ``learning/{online_learner, causal_engine, causal_impact, credit_assignment,
  performance, pipeline, trainer}.py`` — test-paired or imported only by
  ``test_security_hardening.py``.

All surviving dormant code is unreachable at runtime under
``SAFE_MODE=True`` and trips the guard if imported into a live process
anyway.  Deferred test-paired deletions are tracked for a follow-up
cleanup.  ``BUG-5 / BUG-6`` were retired alongside ``engine_eval.py``
/ ``engine_pool.py`` in the 2026-05-12 engine-library cleanup.

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

3.  Forbidden source patterns.  Anchored regex patterns matched against
    module source text — covers the major training entry points used by
    the dormant ML code (PyTorch, sklearn online learners, custom bandit
    save loops, ``def train(...)`` definitions, ML-receiver
    ``.update(...)`` calls).  Patterns are deliberately specific
    (``\b...\b`` word boundaries, anchored function-definition matches,
    explicit ML-receiver allowlist for ``.update``) so the scan covers
    the full ``llm.*`` tree without false-positive on generic Python
    idioms (``dict.update``, ``set.update``, helper functions named
    ``train_*``).

Scan scope
----------
``_scan_loaded_modules`` walks ``llm.*`` — every module under the
``llm`` package tree.  Earlier revisions scoped the scan to
``llm.seca.*`` only because ``FORBIDDEN_KEYWORDS`` carried bare
substrings (``train(``, ``.update(``) that false-positive on
legitimate code outside the SECA tree (e.g. ``dict.update(...)``,
``train_session(...)``).  The 2026-05-13 keyword-tightening pass
replaced those substrings with anchored regex patterns
(function-definition match for ``def train(...)``, ML-receiver
allowlist for ``.update(...)``) so the scan can safely cover
top-level ``llm/<rl_thing>.py`` shapes (``llm/world_model.py``,
``llm/governor.py``, etc.) that previously relied on outright file
deletion plus reviewer vigilance.

``llm.tests.*`` is still excluded — test fixtures deliberately
include forbidden patterns to exercise this guard.  ``llm.seca.safety``
is excluded because the guard's own source contains the keyword
strings (and the regex patterns themselves as code).
"""

import logging
import os
import re
import sys
import inspect

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

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

#: ML-context receiver names for the tightened ``.update(`` pattern.
#: ``<receiver>.update(`` only counts as a forbidden online-fit call when
#: the receiver name is one of these — every other ``.update(`` (dict,
#: set, request headers, config, etc.) is a legitimate Python idiom and
#: must not trip the guard.  Keep the list narrow: adding a generic noun
#: like ``state`` would re-introduce false-positives on dict-style state
#: updates throughout the codebase.
_ML_UPDATE_RECEIVERS = (
    "bandit",
    "model",
    "policy",
    "theta",
    "weights",
    "agent",
    "learner",
    "trainer",
    "value_fn",
    "value_function",
    "predictor",
)

#: Anchored regex patterns for the source-keyword scan.  Each entry is
#: a (label, compiled-pattern) pair; the label is used in crash
#: messages.  Patterns use ``\b`` word boundaries and explicit context
#: so the scan can run across the full ``llm.*`` tree without
#: false-positive on legitimate Python idioms.
#:
#: When extending: keep patterns specific to ML training entry points.
#: A new bare-substring entry would re-introduce the false-positive
#: pressure that forced the scan to be SECA-scoped in earlier
#: revisions.
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Custom adaptive controllers — historical class names that flagged
    # the OnlineSECALearner background loop and the planned RL trainers.
    # The classes themselves were deleted in the dormant-RL sweeps; the
    # patterns stay as a re-introduction tripwire so a future contributor
    # cannot quietly resurrect the same shape.
    ("OnlineSECALearner", re.compile(r"\bOnlineSECALearner\b")),
    ("train_rl", re.compile(r"\btrain_rl\b")),
    ("train_value_model", re.compile(r"\btrain_value_model\b")),
    # Bandit persistence / online-update entry points.  ``\b...\b``
    # boundaries prevent matching ``my_bandit_update_helper`` etc.
    # Pinned by
    # ``test_safety_freeze.FreezeKeywordScanTest::test_bandit_save_keyword_blocked``.
    ("bandit.update", re.compile(r"\bbandit\.update\b")),
    ("bandit.save", re.compile(r"\bbandit\.save\b")),
    # ``def train(...)`` — historical SECA trainer entry points all
    # carried a public ``train()`` method.  Anchored to a function
    # *definition* rather than any ``train(...)`` call so chess-domain
    # helpers (``train_session(...)``, ``train_player(...)``) and the
    # ``training_recommendations`` API surface are not caught.  A future
    # ``def train(self, ...):`` slipped into a SECA module trips this.
    ("def train(", re.compile(r"^\s*def\s+train\s*\(", re.MULTILINE)),
    # ML-receiver ``.update(`` — catches fitted-model in-place updates.
    # Restricted to the small set of receiver names defined in
    # ``_ML_UPDATE_RECEIVERS`` so generic ``dict.update(...)`` /
    # ``set.update(...)`` calls do not trip the guard.  The receiver
    # list is intentionally narrow; broaden only after re-auditing the
    # ``llm/*`` tree for new dict-named-state shapes.
    (
        "<ml>.update(",
        re.compile(r"\b(?:" + "|".join(_ML_UPDATE_RECEIVERS) + r")\.update\s*\("),
    ),
    # PyTorch / sklearn training-step keywords.  Canonical signals that
    # a module is doing gradient descent or online fitting — any seca/
    # source that contains them at module load time fails the startup
    # scan.
    ("optimizer.step", re.compile(r"\boptimizer\.step\b")),
    ("loss.backward", re.compile(r"\bloss\.backward\b")),
    ("partial_fit", re.compile(r"\.partial_fit\s*\(")),
    # Neural-policy *definition* keywords.  After the dormant-RL sweeps
    # removed ``seca/engines/hmpt/`` and ``seca/engines/adaptive/``, no
    # live SECA module imports torch.  These patterns ensure a future
    # contributor who tries to reintroduce a neural-policy class — even
    # without ever calling ``optimizer.step`` (e.g., a pure-inference
    # model loaded from a pickled state-dict) — trips the startup scan.
    # Anchored to module-level imports so a torch-mention inside a
    # docstring or comment does not trip.
    ("import torch", re.compile(r"^\s*(?:from|import)\s+torch\b", re.MULTILINE)),
    ("nn.Module", re.compile(r"\bnn\.Module\b")),
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

    Scope: every loaded ``llm.*`` module except the test tree
    (``llm.tests.*``, which carries deliberate forbidden-pattern
    fixtures) and the guard's own source (``llm.seca.safety.*``,
    which contains the keyword strings and regex patterns as code).
    Mock modules — anything whose name contains ``mock`` — are also
    skipped so the test harness's patched ``sys.modules`` entries do
    not trip the guard during unit-test imports.

    The widening from ``llm.seca.*`` to ``llm.*`` was unlocked by the
    2026-05-13 keyword-tightening pass that replaced the bare
    ``train(`` / ``.update(`` substrings with anchored regex
    patterns; see the ``FORBIDDEN_PATTERNS`` block above.
    """
    for name, module in sys.modules.items():
        if module is None:
            continue
        if not name.startswith("llm."):
            continue
        if name.startswith("llm.seca.safety"):
            continue
        if name.startswith("llm.tests"):
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

        # Source pattern scan
        try:
            src = inspect.getsource(module)
        except (OSError, TypeError):
            continue

        for label, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(src):
                _crash(
                    f"Forbidden adaptive code detected in module: {name} "
                    f"(matched pattern: {label!r})"
                )


def _assert_safe_world_model(world_model):
    """Ensure only the canonical SafeWorldModel is used at runtime.

    Uses ``type(...) is SafeWorldModel`` — strict identity, not
    ``isinstance`` — against the imported class object rather than a
    class-name string compare.  Two failure modes are closed:

    1. An imposter class literally named ``SafeWorldModel`` but defined
       outside ``llm.seca.world_model.safe_stub`` would have satisfied
       the previous ``__class__.__name__`` compare; the type-identity
       check rejects it because the class object is a different ``type``.
    2. A subclass of the canonical ``SafeWorldModel`` that overrides
       ``predict_next`` would pass ``isinstance`` but is not the
       canonical class.  Subclasses are the most plausible
       reintroduction vector for rule-3 (no autonomous RL) violations —
       a contributor writes ``class FasterSafeWorldModel(SafeWorldModel)``
       with a mutating ``predict_next`` and the structural shape looks
       innocent.  Type identity rejects this.
    """
    if world_model is None:
        _crash("World model not initialized")

    # Lazy import keeps freeze.py importable in isolation (the safe_stub
    # transitively imports llm.seca.runtime.safe_mode, which is harmless
    # here but the lazy-import pattern matches the rest of this file).
    from llm.seca.world_model.safe_stub import SafeWorldModel

    # pylint: disable=unidiomatic-typecheck
    # Type identity is intentional, not a lint mistake.  ``isinstance``
    # accepts subclasses, which is exactly the rule-3 reintroduction
    # vector this guard exists to close (a contributor writes
    # ``class FasterSafeWorldModel(SafeWorldModel)`` with a mutating
    # ``predict_next`` and the structural shape looks innocent).
    if type(world_model) is not SafeWorldModel:
        cls = world_model.__class__
        _crash(f"Unsafe world model detected: {cls.__module__}.{cls.__qualname__}")


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

    Scope mirrors ``_scan_loaded_modules``: the full ``llm.*`` tree,
    minus ``llm.tests.*`` (deliberate forbidden-pattern fixtures) and
    ``llm.seca.safety.*`` (this guard's own source).  Earlier revisions
    scoped the quick scan to ``llm.seca.*`` only — asymmetric with the
    startup scan, which since 2026-05-13 covers ``llm.*`` so a
    top-level ``llm/<rl_thing>.py`` cannot slip past.  Per-request
    parity is cheap (the brain allowlist + FORBIDDEN_MODULE_PARTS pass
    remain string ops over ``sys.modules``) and closes the gap that a
    future lazy import after startup could otherwise exploit.

    Returns ``None`` when the runtime passes.  Returns a one-line
    operator-readable reason otherwise.
    """
    for name, module in sys.modules.items():
        if module is None:
            continue
        if not name.startswith("llm."):
            continue
        if name.startswith("llm.seca.safety"):
            continue
        if name.startswith("llm.tests"):
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

    from llm.seca.world_model.safe_stub import SafeWorldModel  # noqa: PLC0415

    # Type-identity check (not isinstance) — see _assert_safe_world_model
    # for the rationale.  Subclasses overriding predict_next are exactly
    # the rule-3 reintroduction vector this guard exists to close.
    if type(world_model) is not SafeWorldModel:  # pylint: disable=unidiomatic-typecheck
        cls = world_model.__class__
        return (False, f"unsafe world model class: {cls.__module__}.{cls.__qualname__}")

    reason = _quick_scan_modules()
    if reason is not None:
        return (False, reason)

    return (True, None)
