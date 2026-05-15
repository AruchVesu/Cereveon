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
# AUT-01  RETIRED in PR 26 (2026-05-15) alongside the /next-training/{player_id}
# HTTP endpoint.  The cross-tenant-probe class of attack the test pinned
# (path player_id != authenticated principal) defended a handler that no
# longer exists.  /curriculum/next, the surviving training-recommendation
# surface, derives the player_id from get_current_player only — there is
# no path parameter to spoof.
# ---------------------------------------------------------------------------


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
        # The top-level llm/player_api.py was retired in PR 22 (2026-05-15).
        # This guard remains for the SECA-prefixed variant that never lived
        # in the live tree — protecting against a future reintroduction
        # that would mount an unauthenticated ``/player/...`` router.
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
