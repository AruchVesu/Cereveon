"""Shared pytest fixtures for backend tests.

Schema setup
------------
SQLAlchemy DDL was moved out of ``llm/seca/auth/router.py`` module import
time and into ``init_schema()`` (commit moves DDL to lifespan).  Production
calls ``init_schema()`` once in the FastAPI lifespan startup hook.

Most existing tests instantiate ``TestClient(server.app)`` *without* a
``with`` block, which does NOT trigger Starlette's lifespan.  Those tests
previously got their schema as a side-effect of importing
``llm.seca.auth.router``; once DDL is no longer at import time, they need
the schema set up another way.

The session-scoped autouse fixture below calls ``init_schema()`` once
before any test in the backend suite runs, restoring the
schema-already-exists invariant the old import-time DDL provided.  Tests
that DO use ``with TestClient(app):`` get the schema from lifespan; the
fixture's prior call is a no-op (``create_all`` is idempotent).
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _backend_schema_init() -> None:
    """Create the SQLAlchemy schema once for the test session."""
    from llm.seca.auth.router import init_schema

    init_schema()
