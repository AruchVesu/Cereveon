"""
Authorization-layer security tests.

Each test asserts a property the live application MUST hold.  Tests that
fail before the fix is applied prove the vulnerability is real; the same
tests pass after the fix.

Findings covered
────────────────
AUT-01  /next-training/{player_id} accepts arbitrary player_id paths.
        Auth dependency is verify_api_key (shared key).  With the API
        key any caller can probe any player_id and receive the curriculum
        task derived from that player's in-memory skill_state.

AUT-02  llm/seca/player/player_api.py defines unauthenticated routes
        POST /player/update/{player_id} and GET /player/state/{player_id}.
        File is dead code (no current importer), but is a refactor footgun:
        a stray `app.include_router(...)` would expose them immediately.
"""

from __future__ import annotations

import ast
import inspect
import os
import unittest

os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("SECA_ENV", "dev")


# ---------------------------------------------------------------------------
# AUT-01  next-training cross-tenant check
# ---------------------------------------------------------------------------

class TestAut01NextTrainingCrossTenant(unittest.TestCase):
    """next_training() must verify that the path-supplied player_id matches
    the authenticated principal.  Currently it relies only on verify_api_key,
    which is a shared backend secret — any holder of the key can probe any
    player_id and learn that player's rating from player_skill_memory."""

    def setUp(self):
        import llm.server as srv
        self.srv = srv

    def test_next_training_uses_get_current_player(self):
        """The endpoint must include a Depends(get_current_player) parameter
        so the JWT-derived player.id is available for cross-tenant validation."""
        src = inspect.getsource(self.srv.next_training)
        self.assertIn(
            "get_current_player",
            src,
            "AUT-01: next_training() does not depend on get_current_player; "
            "without it there is no JWT-derived player to cross-check the path against.",
        )

    def test_next_training_rejects_mismatched_player_id(self):
        """The endpoint body must contain a check comparing path player_id to
        the authenticated player's id and raising 403 on mismatch."""
        src = inspect.getsource(self.srv.next_training)
        # Look for the comparison pattern: player_id != str(player.id)  OR equivalent
        has_comparison = (
            "player_id != str(player.id)" in src
            or "player_id != player.id" in src
            or "str(player.id) != player_id" in src
        )
        self.assertTrue(
            has_comparison,
            "AUT-01: next_training() does not compare path player_id against the "
            "authenticated player.id — cross-tenant probe is possible.",
        )

    def test_next_training_raises_403_on_mismatch(self):
        """The mismatch path must raise HTTPException(403) — not 401, not 422."""
        src = inspect.getsource(self.srv.next_training)
        self.assertIn(
            "status_code=403",
            src,
            "AUT-01: next_training() does not raise 403 for cross-tenant access.",
        )


# ---------------------------------------------------------------------------
# AUT-02  Dead seca/player/player_api.py is not wired into the live app
# ---------------------------------------------------------------------------

class TestAut02DeadPlayerRouterNotIncluded(unittest.TestCase):
    """The legacy file llm/seca/player/player_api.py defines
    POST /player/update/{player_id} (mutates rating/tilt) and
    GET /player/state/{player_id} (leaks rating/tilt) WITH NO AUTHENTICATION
    AT ALL.  The file is currently dead code — but if any module imports
    its `router`, those routes become live with no controls.

    These tests guarantee:
      (a) server.py never imports the dangerous router
      (b) no other production module imports it either
      (c) the file itself either ships with auth or is removed
    """

    def test_server_py_does_not_import_dangerous_router(self):
        import llm.server as srv
        src = inspect.getsource(srv)
        # The safe top-level llm/player_api.py is fine — only flag the seca path
        self.assertNotIn(
            "from llm.seca.player.player_api",
            src,
            "AUT-02: server.py imports the unauthenticated seca/player/player_api router",
        )
        self.assertNotIn(
            "from .seca.player.player_api",
            src,
            "AUT-02: server.py imports the unauthenticated seca/player/player_api router",
        )

    def test_no_production_module_includes_dead_router(self):
        """Walk the llm/ tree and assert no non-test file imports the dead router."""
        import pathlib
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        llm_dir = repo_root / "llm"

        offenders: list[str] = []
        for path in llm_dir.rglob("*.py"):
            # Skip test files and the dead file itself
            parts = set(path.parts)
            if "tests" in parts or path.name == "player_api.py" and "seca" in parts:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "from llm.seca.player.player_api" in content:
                offenders.append(str(path))
            elif "from seca.player.player_api" in content and "import router" in content:
                offenders.append(str(path))

        self.assertEqual(
            offenders, [],
            f"AUT-02: production modules import the dead unauthenticated router: {offenders}",
        )

    def test_dead_router_has_auth_or_is_quarantined(self):
        """Either the dead file ships with auth dependencies on every route,
        OR it must be quarantined — i.e., its top-level `router` symbol must
        not exist (so an accidental `from … import router` blows up at import
        time rather than silently exposing routes).

        We check for presence of `Depends(get_current_player)` or equivalent
        on the update/state routes.  If absent, require an explicit quarantine
        marker — a top-level `router = None` or a SECURITY-banner module
        docstring that names AUT-02.
        """
        import pathlib
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        dead_file = repo_root / "llm" / "seca" / "player" / "player_api.py"

        if not dead_file.exists():
            return  # File deleted — best possible outcome

        src = dead_file.read_text(encoding="utf-8")
        tree = ast.parse(src)

        update_or_state_funcs = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"update_player", "get_player_state"}
        ]

        has_auth = any(
            "get_current_player" in ast.unparse(fn) or "verify_api_key" in ast.unparse(fn)
            for fn in update_or_state_funcs
        )

        is_quarantined = (
            "AUT-02" in src
            and ("router = None" in src or "raise ImportError" in src or "raise RuntimeError" in src)
        )

        self.assertTrue(
            has_auth or is_quarantined,
            f"AUT-02: {dead_file.name} defines routes that mutate / leak player state "
            f"with NO auth dependency on update_player or get_player_state. "
            f"Either add Depends(get_current_player) to both, delete the file, or "
            f"quarantine the module (set router = None and document AUT-02 in the file).",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
