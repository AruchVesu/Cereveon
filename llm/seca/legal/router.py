"""Public legal pages — the privacy policy Google Play requires as a
live URL in the store listing and Data safety form.

Served as a static, self-contained HTML file (no auth, no DB, no
external assets).  The file is read once at import so a missing/renamed
file fails loudly at startup rather than 500-ing the first visitor.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

_PRIVACY_HTML = (Path(__file__).parent / "privacy_policy.html").read_text(encoding="utf-8")

router = APIRouter(tags=["legal"])


@router.get("/privacy", response_class=HTMLResponse)
@router.get("/privacy-policy", response_class=HTMLResponse)
def privacy_policy() -> HTMLResponse:
    """The published privacy policy.  ``/privacy`` is canonical;
    ``/privacy-policy`` is an alias so either URL works in the store
    listing.  Cacheable — the content is static between deploys."""
    return HTMLResponse(
        _PRIVACY_HTML,
        headers={"Cache-Control": "public, max-age=3600"},
    )
