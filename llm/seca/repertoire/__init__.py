"""Player opening repertoire — APIRouter + default repertoire seed.

Split out of ``llm/server.py`` in the Sprint 4.1 server-py-split PR.
The router owns the 5 ``/repertoire`` HTTP endpoints, the
``RepertoireEntryRequest`` / ``DrillResultRequest`` Pydantic bodies, the
``DEFAULT_REPERTOIRE`` seed (mirrored 1-for-1 by
``OpeningsActivity.DEFAULT_REPERTOIRE`` on the Android client), and the
``_validate_eco`` / ``_validate_text_field`` input guards.

Routes live behind ``app.include_router(repertoire_router)`` in
``llm/server.py``; the actual storage layer is ``llm.seca.storage.repo``.
"""
