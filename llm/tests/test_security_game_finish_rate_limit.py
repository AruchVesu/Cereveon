"""
Security test for /game/finish rate-limit gap.

Finding RPL-01
──────────────
POST /game/finish has NO @limiter.limit decorator while every other
state-mutating route in the application is rate-limited:

    /auth/register             5/minute
    /auth/login               10/minute
    /auth/change-password      5/minute
    /move                     30/minute
    /live/move                30/minute
    /analyze                  30/minute
    /game/start               20/minute
    /explain                  30/minute
    /explanation_outcome      20/minute
    /chat, /chat/stream       10/minute
    /adaptation/mode          30/minute
    /next-training/{id}       (no decorator, but JWT-bound + cross-tenant)

/game/finish writes a GameEvent, runs SkillUpdater (which mutates rating
and confidence), and creates RatingUpdate / ConfidenceUpdate rows.  Without
a rate limit, an authenticated player can replay the same PGN — or post
a stream of crafted PGNs — at arbitrary throughput, inflating their
stored rating by megabytes worth of side-effects per second.

Compounding factors:
  - There is no PGN deduplication; replay of the same PGN with the same
    weaknesses produces N rating updates where N is request count.
  - SkillUpdater swallows its own exceptions, so a bad inputs path still
    proceeds to RatingUpdate / ConfidenceUpdate inserts.
  - The endpoint also kicks the post-game coach pipeline and the
    counterfactual planner (when SAFE_MODE is off), each of which is
    expensive enough that a flood is also a CPU vector.

Fix: add @limiter.limit("10/minute") to /game/finish, matching the
chat-endpoint cadence, which is appropriate for "finish a game" which
realistically happens at most a few times per hour for a real human.
"""

from __future__ import annotations

import ast
import inspect
import unittest


class TestRpl01GameFinishRateLimit(unittest.TestCase):
    """RPL-01: /game/finish must carry @limiter.limit, the same as every
    other state-mutating route in the application."""

    def setUp(self):
        from llm.seca.events import router as events_router_mod
        self.module = events_router_mod
        self.src = inspect.getsource(events_router_mod)

    def test_finish_game_has_limiter_decorator(self):
        """AST inspection: the finish_game function must have a @limiter.limit
        decorator above its @router.post("/finish") decorator."""
        tree = ast.parse(self.src)
        finish_fn = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "finish_game":
                finish_fn = node
                break

        self.assertIsNotNone(finish_fn, "RPL-01: finish_game() not found in events router")

        decorator_sources = [ast.unparse(d) for d in finish_fn.decorator_list]
        has_limiter = any("limiter.limit" in d for d in decorator_sources)
        self.assertTrue(
            has_limiter,
            f"RPL-01: finish_game() has no @limiter.limit decorator. "
            f"Decorators found: {decorator_sources}.  Without rate limiting, "
            f"a malicious client can replay /game/finish unbounded and inflate "
            f"their rating, since GameEvent / RatingUpdate / ConfidenceUpdate "
            f"are written on every call with no dedup.",
        )

    def test_finish_game_takes_request_parameter(self):
        """slowapi requires a `request: Request` parameter to apply the
        per-IP rate-limit key.  finish_game already has this for the
        existing skill pipeline; assert it survives the fix."""
        tree = ast.parse(self.src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "finish_game":
                arg_names = [a.arg for a in node.args.args]
                self.assertIn(
                    "request", arg_names,
                    "RPL-01: finish_game() needs a `request: Request` parameter "
                    "for slowapi to apply the rate-limit key.",
                )
                return
        self.fail("finish_game() not found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
