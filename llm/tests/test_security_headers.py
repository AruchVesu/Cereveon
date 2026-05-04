"""
Security response-header completeness tests.

Findings covered
────────────────
HDR-01  server.py: Content-Security-Policy is not set.
HDR-02  server.py: Permissions-Policy is not set.
HDR-03  host_app.py: NO security headers at all (no HSTS, no X-Frame-Options,
        no X-Content-Type-Options, no CSP, no Permissions-Policy, no
        Referrer-Policy).  host_app.py is the engine-evaluation FastAPI
        sub-server; its responses must carry the same defense-in-depth
        headers as server.py.

All three are defense-in-depth additions for a JSON API.  CSP
`default-src 'none'` ensures that if any unexpected HTML response is ever
returned (for example from a misbehaving error page), no scripts, frames,
or sub-resources can execute.  Permissions-Policy with all features
disabled prevents any browser feature (camera, microphone, geolocation,
payment, USB, etc.) from being activated by response content.

Existing server.py headers (already verified by test_security_hardening.py
SH-12):
  - Strict-Transport-Security
  - X-Content-Type-Options: nosniff
  - X-Frame-Options: DENY
  - Referrer-Policy: strict-origin-when-cross-origin
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("SECA_API_KEY", "k" * 32)
os.environ.setdefault("SECA_ENV", "dev")

from fastapi.testclient import TestClient


class TestSecurityHeadersCompleteness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import llm.server as srv
        cls.client = TestClient(srv.app)

    def _headers(self) -> dict:
        # /health is a public endpoint that returns 200 unauthenticated; its
        # response carries the same global security headers as any other route.
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200, "expected /health to return 200")
        return {k.lower(): v for k, v in r.headers.items()}

    def test_hdr01_content_security_policy_present(self):
        """HDR-01: Content-Security-Policy must be set."""
        headers = self._headers()
        self.assertIn(
            "content-security-policy", headers,
            "HDR-01: Content-Security-Policy header is not set on responses.",
        )

    def test_hdr01_csp_blocks_default_src(self):
        """HDR-01b: the CSP must default-deny script/frame/object sources.
        For a JSON API the most restrictive policy is correct."""
        headers = self._headers()
        csp = headers.get("content-security-policy", "")
        self.assertIn(
            "default-src 'none'", csp,
            f"HDR-01b: CSP must include `default-src 'none'`; got: {csp!r}",
        )
        self.assertIn(
            "frame-ancestors 'none'", csp,
            f"HDR-01b: CSP must include `frame-ancestors 'none'`; got: {csp!r}",
        )

    def test_hdr02_permissions_policy_present(self):
        """HDR-02: Permissions-Policy must be set."""
        headers = self._headers()
        self.assertIn(
            "permissions-policy", headers,
            "HDR-02: Permissions-Policy header is not set on responses.",
        )

    def test_hdr02_permissions_policy_disables_sensitive_features(self):
        """HDR-02b: Permissions-Policy should disable all the high-risk
        browser features by setting their allow-list to the empty list."""
        headers = self._headers()
        pp = headers.get("permissions-policy", "")
        # Each of these features must appear with an empty allow-list `()`
        for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
            self.assertIn(
                f"{feature}=()", pp,
                f"HDR-02b: Permissions-Policy must disable {feature!r}; got: {pp!r}",
            )


class TestHdr03HostAppSecurityHeaders(unittest.TestCase):
    """HDR-03: host_app.py must carry the same defense-in-depth headers
    as server.py.  Currently it carries none."""

    @classmethod
    def setUpClass(cls):
        import llm.host_app as ha
        cls.client = TestClient(ha.app)

    def _headers(self) -> dict:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200, "expected /health to return 200")
        return {k.lower(): v for k, v in r.headers.items()}

    def test_hdr03_hsts_set(self):
        headers = self._headers()
        self.assertIn(
            "strict-transport-security", headers,
            "HDR-03: host_app.py is missing Strict-Transport-Security",
        )

    def test_hdr03_x_content_type_options_set(self):
        headers = self._headers()
        self.assertEqual(
            headers.get("x-content-type-options"), "nosniff",
            "HDR-03: host_app.py is missing X-Content-Type-Options: nosniff",
        )

    def test_hdr03_x_frame_options_deny(self):
        headers = self._headers()
        self.assertEqual(
            headers.get("x-frame-options"), "DENY",
            "HDR-03: host_app.py is missing X-Frame-Options: DENY",
        )

    def test_hdr03_referrer_policy_set(self):
        headers = self._headers()
        self.assertIn(
            "referrer-policy", headers,
            "HDR-03: host_app.py is missing Referrer-Policy",
        )

    def test_hdr03_csp_set_with_default_src_none(self):
        headers = self._headers()
        csp = headers.get("content-security-policy", "")
        self.assertIn(
            "default-src 'none'", csp,
            f"HDR-03: host_app.py CSP missing or weak; got: {csp!r}",
        )

    def test_hdr03_permissions_policy_set(self):
        headers = self._headers()
        self.assertIn(
            "permissions-policy", headers,
            "HDR-03: host_app.py is missing Permissions-Policy",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
