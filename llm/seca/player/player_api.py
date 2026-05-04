"""
QUARANTINED — DO NOT IMPORT THIS MODULE'S `router`.

This file used to expose three FastAPI routes under prefix /player:
    POST /player/create
    POST /player/update/{player_id}
    GET  /player/state/{player_id}

None of them performed any authentication or cross-tenant authorisation.
update_player and get_player_state took a path parameter as the sole
identifier — anyone reaching the endpoint could mutate or read any
player's rating and tilt.

The routes were never wired into the live application (server.py imports
the safe top-level llm/player_api.py instead), but the file was kept
around as a footgun: a single `app.include_router(...)` line in any
future refactor would have exposed all three routes with no controls.

Tracked as AUT-02 in llm/tests/test_security_authz.py.

This module is now quarantined.  The `router` symbol is `None` so any
attempt to `app.include_router(router)` raises AttributeError at startup
rather than silently re-publishing the unauthenticated routes.  If a
properly-authenticated player API is ever needed, build it from scratch
on top of get_current_player — do not resurrect this file.
"""

# Sentinel that fails fast if someone tries to wire this back into the app.
router = None
"""Intentionally None — see module docstring (AUT-02)."""
