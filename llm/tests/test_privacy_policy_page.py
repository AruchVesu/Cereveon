"""
Backend tests for the public privacy-policy page (GET /privacy) — the
live URL Google Play requires in the store listing + Data safety form.

The load-bearing test is PP_NO_INTERNAL_MARKERS: it structurally
guarantees the internal working draft (docs/PRIVACY_POLICY.md, which
carries compliance-gap tracking + a claim-to-code evidence map) can
never be published — the served page must contain no placeholder /
draft / annex markers.

Pinned invariants
-----------------
 1. PP_SERVES_HTML          GET /privacy → 200 text/html, no auth.
 2. PP_ALIAS               GET /privacy-policy → 200 (store-listing alias).
 3. PP_HAS_REQUIRED_PARTS  controller, contact email, rights, the deletion
                           URL, effective date, and the DeepSeek transfer
                           are all present.
 4. PP_NO_INTERNAL_MARKERS the served HTML contains no unresolved
                           placeholder / draft / annex / internal markers.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm.seca.legal.router import router as legal_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(legal_router)
    return TestClient(app)


def test_privacy_page_serves_html():
    """PP_SERVES_HTML."""
    r = _client().get("/privacy")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Privacy Policy" in r.text


def test_privacy_policy_alias():
    """PP_ALIAS."""
    r = _client().get("/privacy-policy")
    assert r.status_code == 200
    assert "Privacy Policy" in r.text


def test_privacy_page_has_required_parts():
    """PP_HAS_REQUIRED_PARTS."""
    body = _client().get("/privacy").text
    assert "data controller" in body.lower()
    assert "privacy@cereveon.com" in body
    assert "cereveon.com/delete-account" in body  # the account-deletion right
    assert "download my data" in body.lower()  # the access/portability right
    assert re.search(r"[Ee]ffective", body)  # dated
    assert "DeepSeek" in body  # the one material international transfer disclosed


def test_privacy_page_has_no_internal_markers():
    """PP_NO_INTERNAL_MARKERS: the internal draft's placeholder / gap /
    annex markers must never reach the public page."""
    body = _client().get("/privacy").text
    forbidden = [
        "[",  # any [PLACEHOLDER] / [CONFIRM ...] / [Annex ...]
        "]",
        "Annex",
        "DRAFT",
        "MOCK",
        "TODO",
        "CONFIRM",
        "Counsel",
        "GAP —",
        "REMOVE",
        "placeholder",
    ]
    hits = [marker for marker in forbidden if marker in body]
    assert not hits, (
        f"The published privacy page contains internal-draft markers {hits}. "
        "Edit llm/seca/legal/privacy_policy.html — never publish the "
        "docs/PRIVACY_POLICY.md working draft."
    )
