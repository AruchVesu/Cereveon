"""SECA SAFE_MODE flag — single source of truth for the adaptive-layer lockdown.

``SAFE_MODE`` gates every ``if not SAFE_MODE:`` branch in the live tree
(the ones in ``events/router.py``, ``skills/trainer.py``, and
``learning/trainer.py`` that lazily import the dormant RL trainers /
counterfactual planner / online learners).  Disabling it would re-enable
the very code paths Project Rule 3 forbids in production.

Resolution
----------
Default ``True``.  May be disabled only by setting the env variable
``SECA_SAFE_MODE`` to one of ``false`` / ``0`` / ``no`` (case-insensitive)
before the process starts.  Any other value — including unset, empty
string, typos — resolves to ``True``: the failure mode of a misconfigured
env var is "stay safe", never "silently unlock".

Production validation
---------------------
The freeze guard validates this invariant at startup
(``llm.seca.safety.freeze._assert_safe_mode_locked``):

- ``SAFE_MODE=True`` (any env): no-op.
- ``SAFE_MODE=False`` with ``SECA_ENV=prod``: hard crash via
  ``sys.exit(1)``.  Production must run with the adaptive layer locked.
- ``SAFE_MODE=False`` with ``SECA_ENV != prod``: allowed for dev testing
  of the dormant code paths, but logged as a warning so the
  configuration cannot be mistaken for normal operation.
"""

import os


def _resolve_safe_mode() -> bool:
    """Resolve SAFE_MODE from SECA_SAFE_MODE; default True for any
    unset / empty / unrecognised value."""
    raw = os.getenv("SECA_SAFE_MODE")
    if raw is None:
        return True
    return raw.strip().lower() not in ("false", "0", "no")


SAFE_MODE: bool = _resolve_safe_mode()


def assert_safe() -> None:
    if not SAFE_MODE:
        raise RuntimeError("Unsafe mode disabled in SAFE SECA v1")
