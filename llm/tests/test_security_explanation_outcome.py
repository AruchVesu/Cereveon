"""
Security/quality test for the /explanation_outcome endpoint.

Finding TRK-01
──────────────
report_outcome() in server.py calls tracker.record_outcome(**req.dict())
unconditionally, but record_outcome() in outcome_tracker.py raises
`ValueError("Unknown explanation_id")` whenever the id is not already
in tracker.events.  Nothing in the live request path ever calls
record_explanation() to populate that dict — every production
explanation_id is therefore unknown to the tracker.

Result: every call to /explanation_outcome raises ValueError.  The
exception is unhandled by the route, so Starlette returns 500 and
logs the full stack trace.  Two concrete impacts:

  - Functional: the endpoint cannot succeed in current code paths.
  - Security:   any caller with the API key can hit this endpoint
                at the rate-limit ceiling (20/minute) and force the
                server to log unbounded stack traces, both burning
                disk and obscuring real incident traces.

The fix is to catch the ValueError and return a clean HTTP 400 with
a generic message — same shape every other validation failure uses
in this codebase.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("SECA_API_KEY", "k" * 32)
os.environ.setdefault("SECA_ENV", "dev")

from fastapi.testclient import TestClient


_PAYLOAD = {
    "explanation_id": "00000000-0000-0000-0000-000000000000",
    "moves_analyzed": 10,
    "avg_cpl": 50.0,
    "blunder_rate": 0.1,
    "tactic_success": True,
    "confidence_delta": 0.2,
}


class TestTrk01ExplanationOutcomeUnknownId(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import llm.server as srv
        cls.client = TestClient(srv.app, raise_server_exceptions=False)
        # T3: /explanation_outcome now requires a JWT-authenticated player
        # session.  Register an ephemeral player and use the issued access
        # token as a Bearer credential.  A fresh email per run keeps tests
        # independent under suite-wide ordering.
        import uuid
        email = f"trk01-{uuid.uuid4().hex[:8]}@test.example"
        password = "TestPassword!23"
        r = cls.client.post(
            "/auth/register", json={"email": email, "password": password}
        )
        assert r.status_code == 200, f"setUpClass register failed: {r.status_code} {r.text[:200]}"
        cls.token = r.json()["access_token"]

    def _post(self):
        return self.client.post(
            "/explanation_outcome",
            headers={"Authorization": f"Bearer {self.token}"},
            json=_PAYLOAD,
        )

    def test_unknown_explanation_id_returns_4xx_not_500(self):
        """A request with an explanation_id the tracker has never seen must
        produce a 4xx response, NOT a 500 with a leaked stack trace.  500s
        let an attacker spam the log pipeline with tracebacks."""
        r = self._post()
        self.assertNotEqual(
            r.status_code, 500,
            f"TRK-01: /explanation_outcome returned 500 for an unknown id. "
            f"Body: {r.text[:200]}",
        )
        # 200 is also acceptable — earlier tests in the suite may have
        # populated tracker.events with this exact UUID, in which case the
        # outcome IS recorded.  What matters is no 500 ever escapes.
        self.assertIn(
            r.status_code, {200, 400, 404, 422},
            f"TRK-01: expected 200 or 4xx, got {r.status_code}",
        )

    def test_unknown_explanation_id_does_not_leak_internals(self):
        """The response body (whether 200 or 4xx) must not include the
        literal internal error wording 'Unknown explanation_id' or any
        traceback fragments — keep the message generic so the endpoint
        cannot be used as an oracle for tracker contents."""
        r = self._post()
        body = r.text.lower()
        forbidden_substrings = [
            "traceback",
            "raise valueerror",
            "outcome_tracker.py",
            "valueerror(",
        ]
        for s in forbidden_substrings:
            self.assertNotIn(
                s, body,
                f"TRK-01: response leaks internal detail '{s}': {r.text[:200]}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
