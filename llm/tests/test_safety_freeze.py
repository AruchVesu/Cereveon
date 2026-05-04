"""Unit tests for the SECA safety freeze guard.

Coverage
--------
Three independent layers in seca/safety/freeze.py are exercised:

1.  Brain allowlist — anything under llm.seca.brain.* that is not on the
    explicit allowlist must crash the scan.  This is the strongest defence:
    a renamed-but-equivalent re-introduction of bandit/world-model/training
    code under brain/ would slip past the keyword-only scan but is caught
    here.

2.  Source keyword scan — RL training entry points (optimizer.step,
    loss.backward, .partial_fit(, bandit.save) anywhere in the seca tree
    must crash the scan.  Verified by writing a small fixture .py file
    into a temp dir and loading it as a module.

3.  Runtime invariants — _assert_safe_world_model rejects None and any
    class other than SafeWorldModel; _assert_no_background_tasks rejects
    SECA_ENABLE_ONLINE_LEARNING=1.

The freeze guard's production behaviour is sys.exit(1).  Tests
monkey-patch ``_crash`` to raise a marker exception so the assertion
machinery can observe the rejection without terminating the test runner.
"""

from __future__ import annotations

import os
import sys
import textwrap
import types
import unittest
import importlib.util
import pathlib
import tempfile
from unittest.mock import patch

import llm.seca.safety.freeze as freeze


class _Crash(RuntimeError):
    """Raised by patched _crash so tests can assert without exiting."""


def _raise(reason: str) -> None:
    raise _Crash(reason)


# ---------------------------------------------------------------------------
# Brain-allowlist policy
# ---------------------------------------------------------------------------


class FreezeBrainAllowlistTest(unittest.TestCase):
    """Layer 1: only ALLOWED_BRAIN_MODULES may load under brain/*.

    Other tests in the wider suite (notably test_bug_regressions) import
    dormant brain modules like brain.bandit.contextual_bandit on purpose to
    exercise their internal classes.  Once imported, those modules stay in
    sys.modules and would cause every subsequent _scan_loaded_modules()
    call to crash.  setUp/tearDown therefore *temporarily* remove any
    non-allowlisted brain.* entry from sys.modules so each freeze test
    sees a clean, production-shaped runtime — and restores them afterward
    so we do not affect later tests in the run.
    """

    def setUp(self):
        # Snapshot pre-test sys.modules so additions made during the test
        # can be discarded.
        self._initial_keys = set(sys.modules.keys())
        # Save and temporarily remove non-allowlisted brain.* entries that
        # earlier suite tests may have imported.
        self._stashed: dict[str, types.ModuleType] = {}
        for name in list(sys.modules.keys()):
            if (
                name.startswith("llm.seca.brain.")
                and name not in freeze.ALLOWED_BRAIN_MODULES
            ):
                self._stashed[name] = sys.modules.pop(name)

    def tearDown(self):
        # Discard any module added during the test
        for name in list(sys.modules.keys()):
            if name not in self._initial_keys and name not in self._stashed:
                del sys.modules[name]
        # Restore originally-stashed modules
        for name, mod in self._stashed.items():
            sys.modules[name] = mod

    def _inject_module(self, name: str) -> None:
        """Insert a bare module object at *name* in sys.modules.  No source
        file is attached, so the source-keyword scan will skip it — the
        allowlist check fires first and this is what the test exercises."""
        sys.modules[name] = types.ModuleType(name)

    def test_allowlisted_brain_models_does_not_crash(self):
        """The real llm.seca.brain.models is loaded by auth/router.py at
        runtime; the scan must accept it."""
        import llm.seca.brain.models  # noqa: F401  — populate sys.modules
        with patch.object(freeze, "_crash", _raise):
            freeze._scan_loaded_modules()  # should not raise

    def test_allowlisted_brain_training_models_does_not_crash(self):
        """llm.seca.brain.training.models is loaded by storage/db.py at
        runtime; the scan must accept it."""
        import llm.seca.brain.training.models  # noqa: F401
        with patch.object(freeze, "_crash", _raise):
            freeze._scan_loaded_modules()  # should not raise

    def test_non_allowlisted_brain_bandit_crashes(self):
        """brain.bandit.global_bandit is NOT on the allowlist — must crash."""
        self._inject_module("llm.seca.brain.bandit.global_bandit")
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash) as cm:
                freeze._scan_loaded_modules()
            self.assertIn("brain", str(cm.exception))

    def test_non_allowlisted_brain_world_model_crashes(self):
        """brain.world_model.train_regression — must crash."""
        self._inject_module("llm.seca.brain.world_model.train_regression")
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash):
                freeze._scan_loaded_modules()

    def test_non_allowlisted_brain_meta_crashes(self):
        """brain.meta.* — must crash."""
        self._inject_module("llm.seca.brain.meta.meta_coach")
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash):
                freeze._scan_loaded_modules()

    def test_non_allowlisted_brain_rewards_crashes(self):
        """brain.rewards.* — must crash."""
        self._inject_module("llm.seca.brain.rewards.update_weekly_rewards")
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash):
                freeze._scan_loaded_modules()


# ---------------------------------------------------------------------------
# Source-keyword policy
# ---------------------------------------------------------------------------


class FreezeKeywordScanTest(unittest.TestCase):
    """Layer 2: forbidden source keywords anywhere in seca/* must crash.

    Same hermetic-runtime requirement as FreezeBrainAllowlistTest: prior
    suite tests may have left non-allowlisted brain.* modules in
    sys.modules; those would crash the scan with the wrong reason before
    our fixture's keyword-bearing module is ever inspected.  setUp/
    tearDown stash and restore them so each test sees a production-shaped
    starting state.
    """

    def setUp(self):
        self._initial_keys = set(sys.modules.keys())
        self._stashed: dict[str, types.ModuleType] = {}
        for name in list(sys.modules.keys()):
            if (
                name.startswith("llm.seca.brain.")
                and name not in freeze.ALLOWED_BRAIN_MODULES
            ):
                self._stashed[name] = sys.modules.pop(name)
        # Use a temp directory so the fixture file is removed on teardown
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = pathlib.Path(self._tmp.name)

    def tearDown(self):
        for name in list(sys.modules.keys()):
            if name not in self._initial_keys and name not in self._stashed:
                del sys.modules[name]
        for name, mod in self._stashed.items():
            sys.modules[name] = mod
        self._tmp.cleanup()

    def _load_fixture(self, fake_module_name: str, source: str) -> None:
        """Write *source* to a temp file and import it under *fake_module_name*
        so inspect.getsource() can recover the body during the scan."""
        path = self._tmp_path / f"{fake_module_name.replace('.', '_')}.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(fake_module_name, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[fake_module_name] = mod
        spec.loader.exec_module(mod)

    def test_optimizer_step_keyword_blocked(self):
        """PyTorch gradient step in any seca module must trigger crash."""
        self._load_fixture(
            "llm.seca.henm.fake_train",
            textwrap.dedent("""
                def step(optimizer, loss):
                    loss.backward()
                    optimizer.step()
            """),
        )
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash) as cm:
                freeze._scan_loaded_modules()
            self.assertIn("Forbidden", str(cm.exception))

    def test_partial_fit_keyword_blocked(self):
        """sklearn online-learner entry point must trigger crash."""
        self._load_fixture(
            "llm.seca.learning.fake_online",
            textwrap.dedent("""
                def go(model, X, y):
                    model.partial_fit(X, y)
            """),
        )
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash):
                freeze._scan_loaded_modules()

    def test_bandit_save_keyword_blocked(self):
        """Bandit persistence call must trigger crash even outside brain/."""
        self._load_fixture(
            "llm.seca.outcome.fake_bandit_persist",
            "def go(bandit):\n    bandit.save()\n",
        )
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash):
                freeze._scan_loaded_modules()


# ---------------------------------------------------------------------------
# Runtime invariants
# ---------------------------------------------------------------------------


class FreezeWorldModelTest(unittest.TestCase):
    """Layer 3a: only SafeWorldModel may serve as the runtime world model."""

    def test_safe_world_model_accepted(self):
        from llm.seca.world_model.safe_stub import SafeWorldModel
        with patch.object(freeze, "_crash", _raise):
            freeze._assert_safe_world_model(SafeWorldModel())  # no raise

    def test_none_world_model_rejected(self):
        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash):
                freeze._assert_safe_world_model(None)

    def test_unsafe_world_model_class_rejected(self):
        class UnsafeWorldModel:  # name != SafeWorldModel
            pass

        with patch.object(freeze, "_crash", _raise):
            with self.assertRaises(_Crash) as cm:
                freeze._assert_safe_world_model(UnsafeWorldModel())
            self.assertIn("Unsafe", str(cm.exception))


class FreezeBackgroundTasksTest(unittest.TestCase):
    """Layer 3b: SECA_ENABLE_ONLINE_LEARNING=1 must be rejected."""

    def test_online_learning_env_var_rejected(self):
        with patch.dict(os.environ, {"SECA_ENABLE_ONLINE_LEARNING": "1"}):
            with patch.object(freeze, "_crash", _raise):
                with self.assertRaises(_Crash):
                    freeze._assert_no_background_tasks()

    def test_online_learning_env_var_unset_passes(self):
        env = {k: v for k, v in os.environ.items() if k != "SECA_ENABLE_ONLINE_LEARNING"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(freeze, "_crash", _raise):
                freeze._assert_no_background_tasks()  # no raise


class FreezeSafeModeLockTest(unittest.TestCase):
    """Layer 3c: SAFE_MODE must be True at startup, with the prod/dev
    branching the audit promised.

    The inner ``from llm.seca.runtime.safe_mode import SAFE_MODE`` inside
    ``_assert_safe_mode_locked`` resolves against the live module
    object, so ``patch.object(safe_mode_module, "SAFE_MODE", ...)``
    works for these tests without needing to reload the module.
    """

    def test_safe_mode_true_passes_in_any_env(self):
        """Default safe_mode=True is a no-op, regardless of SECA_ENV."""
        from llm.seca.runtime import safe_mode as safe_mode_module

        for env_value in ("prod", "dev", "staging", ""):
            with patch.object(safe_mode_module, "SAFE_MODE", True):
                with patch.dict(os.environ, {"SECA_ENV": env_value}):
                    with patch.object(freeze, "_crash", _raise):
                        freeze._assert_safe_mode_locked()  # no raise

    def test_safe_mode_false_in_prod_crashes(self):
        """SAFE_MODE=False with SECA_ENV=prod is a hard production stop."""
        from llm.seca.runtime import safe_mode as safe_mode_module

        with patch.object(safe_mode_module, "SAFE_MODE", False):
            with patch.dict(os.environ, {"SECA_ENV": "prod"}):
                with patch.object(freeze, "_crash", _raise):
                    with self.assertRaises(_Crash) as cm:
                        freeze._assert_safe_mode_locked()
                    self.assertIn("SAFE_MODE", str(cm.exception))
                    self.assertIn("prod", str(cm.exception))

    def test_safe_mode_false_in_prod_case_insensitive(self):
        """SECA_ENV value is normalised — 'PROD' / 'Prod' must trip the
        production crash too, since deployment configs vary."""
        from llm.seca.runtime import safe_mode as safe_mode_module

        for env_value in ("PROD", "Prod", "  prod  "):
            with patch.object(safe_mode_module, "SAFE_MODE", False):
                with patch.dict(os.environ, {"SECA_ENV": env_value}):
                    with patch.object(freeze, "_crash", _raise):
                        with self.assertRaises(_Crash):
                            freeze._assert_safe_mode_locked()

    def test_safe_mode_false_in_dev_warns_no_crash(self):
        """SAFE_MODE=False with SECA_ENV=dev is allowed (with a logged
        warning) so dev tests of the dormant code paths still work."""
        from llm.seca.runtime import safe_mode as safe_mode_module

        with patch.object(safe_mode_module, "SAFE_MODE", False):
            with patch.dict(os.environ, {"SECA_ENV": "dev"}):
                with patch.object(freeze, "_crash", _raise):
                    with self.assertLogs(freeze.logger, level="WARNING") as cm:
                        freeze._assert_safe_mode_locked()
                    self.assertTrue(
                        any("SAFE_MODE is False" in line for line in cm.output),
                        f"expected a SAFE_MODE=False warning, got {cm.output!r}",
                    )

    def test_safe_mode_false_with_unset_env_treated_as_dev(self):
        """SECA_ENV unset must default to dev — no crash, just a warning.
        (The startup-time SECA_API_KEY check in server.py is the
        authoritative guard against running unconfigured in prod.)"""
        from llm.seca.runtime import safe_mode as safe_mode_module

        env = {k: v for k, v in os.environ.items() if k != "SECA_ENV"}
        with patch.object(safe_mode_module, "SAFE_MODE", False):
            with patch.dict(os.environ, env, clear=True):
                with patch.object(freeze, "_crash", _raise):
                    with self.assertLogs(freeze.logger, level="WARNING"):
                        freeze._assert_safe_mode_locked()  # no raise

    def test_resolve_safe_mode_default_true(self):
        """Module-level resolution: no env var → True."""
        from llm.seca.runtime import safe_mode as safe_mode_module

        env = {k: v for k, v in os.environ.items() if k != "SECA_SAFE_MODE"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(safe_mode_module._resolve_safe_mode())

    def test_resolve_safe_mode_false_values(self):
        """Module-level resolution: 'false' / '0' / 'no' (any case) → False."""
        from llm.seca.runtime import safe_mode as safe_mode_module

        for value in ("false", "FALSE", "False", "0", "no", "NO", "  false  "):
            with patch.dict(os.environ, {"SECA_SAFE_MODE": value}):
                self.assertFalse(
                    safe_mode_module._resolve_safe_mode(),
                    f"value {value!r} must resolve to False",
                )

    def test_resolve_safe_mode_unrecognised_defaults_to_true(self):
        """Module-level resolution: typos / empty / anything-else → True
        (failure mode of a misconfigured env var is 'stay safe')."""
        from llm.seca.runtime import safe_mode as safe_mode_module

        for value in ("", "true", "1", "yes", "falce", "off", "disabled"):
            with patch.dict(os.environ, {"SECA_SAFE_MODE": value}):
                self.assertTrue(
                    safe_mode_module._resolve_safe_mode(),
                    f"unrecognised value {value!r} must default to True",
                )


# ---------------------------------------------------------------------------
# End-to-end lifespan integration
# ---------------------------------------------------------------------------


class FreezeIntegrationTest(unittest.TestCase):
    """End-to-end: the real FastAPI lifespan path must enforce the freeze.

    The unit tests above monkey-patch ``_crash`` to raise an exception so
    assertions can observe rejection without sys.exit.  This class
    instead exercises the full production code path
    (server.lifespan → init_db → init_auth_schema → SafeWorldModel →
    enforce → _scan_loaded_modules → _crash → sys.exit(1)) using
    FastAPI's real ``TestClient`` and confirms ``SystemExit`` propagates.

    Without this test, a future refactor that, say, swallowed the freeze
    guard's exit inside lifespan's ``except Exception:`` block would
    silently disable the rule-3 enforcement and the unit tests would
    still all pass.
    """

    _FAKE_FORBIDDEN = "llm.seca.brain.bandit._integration_test_only_fake"

    def setUp(self):
        # Hermetic: stash any non-allowlisted brain.* state so the
        # integration scenario starts from a production-shaped sys.modules.
        self._stashed: dict[str, types.ModuleType] = {}
        for name in list(sys.modules.keys()):
            if (
                name.startswith("llm.seca.brain.")
                and name not in freeze.ALLOWED_BRAIN_MODULES
            ):
                self._stashed[name] = sys.modules.pop(name)
        sys.modules.pop(self._FAKE_FORBIDDEN, None)

    def tearDown(self):
        sys.modules.pop(self._FAKE_FORBIDDEN, None)
        for name, mod in self._stashed.items():
            sys.modules[name] = mod

    def test_clean_lifespan_succeeds(self):
        """With no forbidden brain modules pre-loaded, the production
        lifespan (init_db → init_auth_schema → enforce) must complete
        without crashing, and basic routes must serve."""
        from fastapi.testclient import TestClient
        from llm.server import app

        with TestClient(app) as client:
            r = client.get("/health")
            self.assertEqual(r.status_code, 200)

    def test_lifespan_crashes_with_forbidden_brain_module(self):
        """If a non-allowlisted brain.* module is in sys.modules when
        FastAPI lifespan starts, ``enforce(world_model)`` must call
        ``sys.exit(1)`` — and SystemExit must propagate out of lifespan
        rather than being swallowed by the surrounding ``except Exception``
        block (SystemExit is a BaseException, not Exception, so the
        production code is correct by construction; this test pins that
        property).

        We invoke the lifespan async context manager directly via
        ``asyncio.run`` rather than through ``TestClient(app)``.  The
        TestClient path uses anyio's blocking portal which runs the
        startup coroutine inside a TaskGroup; SystemExit propagating
        out of that task is wrapped in a BaseExceptionGroup that lives
        on the task object, while the main thread receives a plain
        CancelledError instead.  The direct asyncio.run path preserves
        the SystemExit shape that production process exit relies on.
        """
        import asyncio
        from llm.server import lifespan as server_lifespan, app

        async def _trigger_startup() -> None:
            async with server_lifespan(app):
                # If startup completes, the freeze guard failed silently.
                self.fail(
                    "lifespan startup unexpectedly succeeded with forbidden "
                    "brain module pre-loaded — freeze guard regression."
                )

        sys.modules[self._FAKE_FORBIDDEN] = types.ModuleType(self._FAKE_FORBIDDEN)
        with self.assertRaises(SystemExit) as cm:
            asyncio.run(_trigger_startup())
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
