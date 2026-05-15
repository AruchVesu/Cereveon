"""Pin documented constants against their live code values.

Three doc-vs-reality drifts surfaced across the cleanup session
(PRs 6, 10, 11) — each one a documented constant value diverging
silently from the code over time.  The repo had no automated
mechanism that pinned doc claims to code, so every drift survived
merge until a human noticed.  This file is that mechanism.

What each test does
-------------------
1. Imports the live value from production code.
2. Reads the canonical spec doc (``docs/THREAT_MODEL.md``,
   ``docs/SECA.md``, ``docs/API_CONTRACTS.md``,
   ``docs/ARCHITECTURE.md``).
3. Asserts the doc text contains the live value (verbatim or via a
   minimal regex).
4. Failure message names both sides + the resolution path, so a
   contributor who bumps the code value finds the doc location to
   update without spelunking.

What's in scope
---------------
Numeric / string constants that already appear in the canonical
spec docs (THREAT_MODEL / SECA / API_CONTRACTS / ARCHITECTURE).
Drifts in dev-facing docs (``llm/README_DEV.md``) are also worth
fixing — the PR 12 commit landed two such fixes — but pin coverage
is intentionally scoped to the canonical specs to keep the test
surface tight.

What's NOT pinned (yet)
-----------------------
- Rate-limit decorators (``@limiter.limit("30/minute")``) vs the
  documented "30 / min" in the endpoint catalogue.  Possible
  follow-up; less mechanical than scalar constants.
- Prose claims (e.g. "validators run on full output before bytes
  reach the client") — these are properties, not constants;
  enforced by structural tests like INV-04 / INV-06.
- Internal-only constants (e.g. ``_CHAT_RETRY_DELAY_SECONDS = 0.5``)
  that aren't documented anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCS = _REPO_ROOT / "docs"

_THREAT_MODEL = _DOCS / "THREAT_MODEL.md"
_SECA = _DOCS / "SECA.md"
_API_CONTRACTS = _DOCS / "API_CONTRACTS.md"
_ARCHITECTURE = _DOCS / "ARCHITECTURE.md"
_README = _REPO_ROOT / "README.md"


def _assert_doc_pin(
    *,
    doc: Path,
    needle: str,
    code_origin: str,
) -> None:
    """Assert ``needle`` appears verbatim in ``doc``.

    On failure, the message names both sides + a one-line resolution
    path: "either update the doc to match, or update the code."

    ``code_origin`` is a file:line reference like
    ``llm/rag/llm/config.py::MAX_MODE_2_RETRIES`` so the failing
    test makes the round-trip obvious.
    """
    content = doc.read_text(encoding="utf-8")
    assert needle in content, (
        f"\n  DOC DRIFT — pin failed.\n"
        f"    Expected in:  docs/{doc.name}\n"
        f"    Phrase:       {needle!r}\n"
        f"    Code origin:  {code_origin}\n"
        f"    Resolution:   either bump the code value (and the doc "
        f"will match), or update the doc to reflect the current code."
    )


# ---------------------------------------------------------------------------
# THREAT_MODEL.md pins
# ---------------------------------------------------------------------------


class TestThreatModelConstants:
    """Pin numeric claims in ``docs/THREAT_MODEL.md`` against code."""

    def test_max_mode_2_retries(self):
        """§ T1 — prompt-injection defence stack.

        Drifted in real life: PR 11 (2026-05-15) caught the doc at
        ``= 4`` while code had been ``= 2`` indefinitely.
        """
        from llm.rag.llm.config import MAX_MODE_2_RETRIES

        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle=f"MAX_MODE_2_RETRIES = {MAX_MODE_2_RETRIES}",
            code_origin="llm/rag/llm/config.py::MAX_MODE_2_RETRIES",
        )

    def test_engine_pool_size_default(self):
        """§ T3 — engine-pool DoS mitigation."""
        # The default is encoded as a literal in server.py lifespan;
        # extract it via the regex below rather than importing because
        # the call site lives inside a closure.
        from pathlib import Path

        server_src = (_REPO_ROOT / "llm" / "server.py").read_text(encoding="utf-8")
        match = re.search(r'_env_int\("ENGINE_POOL_SIZE",\s*(\d+)\)', server_src)
        assert match, "could not locate ENGINE_POOL_SIZE default in server.py"
        default = int(match.group(1))

        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle=f"`ENGINE_POOL_SIZE` (default {default})",
            code_origin="llm/server.py::_env_int('ENGINE_POOL_SIZE', N)",
        )

    def test_engine_queue_timeout_ms_default(self):
        """§ T3 — engine-pool fast-fail timeout."""
        server_src = (_REPO_ROOT / "llm" / "server.py").read_text(encoding="utf-8")
        match = re.search(r'_env_int\("ENGINE_QUEUE_TIMEOUT_MS",\s*(\d+)\)', server_src)
        assert match, "could not locate ENGINE_QUEUE_TIMEOUT_MS default in server.py"
        default = int(match.group(1))

        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle=f"`ENGINE_QUEUE_TIMEOUT_MS` (default {default} ms)",
            code_origin="llm/server.py::_env_int('ENGINE_QUEUE_TIMEOUT_MS', N)",
        )

    def test_max_movetime_ms_ceiling(self):
        """§ T3 — movetime ceiling on the engine pool."""
        from llm.seca.engines.stockfish.pool import EnginePoolSettings

        # ``max_movetime_ms`` is a dataclass default on EnginePoolSettings.
        defaults = EnginePoolSettings.__dataclass_fields__["max_movetime_ms"].default
        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle=f"`max_movetime_ms = {defaults}`",
            code_origin="llm/seca/engines/stockfish/pool.py::EnginePoolSettings.max_movetime_ms",
        )

    def test_body_size_cap(self):
        """§ Cross-cutting controls — 512 KB body limit."""
        from llm.server import _MAX_BODY_BYTES

        # _MAX_BODY_BYTES = 512 * 1024.  Doc says "512 KB".
        kb = _MAX_BODY_BYTES // 1024
        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle=f"{kb} KB on every endpoint",
            code_origin="llm/server.py::_MAX_BODY_BYTES",
        )

    def test_no_tls_pinning_residual_documented(self):
        """§ T2 — Android-side no-cert-pinning residual.

        Unlike the numeric pins above, this is a **negative-state**
        pin: the property the test enforces is that no
        ``CertificatePinner`` (OkHttp) and no ``<pin-set>`` (NSC)
        exists in ``android/``, AND that the residual is explicitly
        named in ``THREAT_MODEL.md``.  The bidirectional check
        (a) catches the regression where someone prunes the residual
        paragraph from the doc thinking "the Android side is HTTPS,
        we're good" and (b) catches the inverse — someone adds
        ``CertificatePinner`` to ``android/`` without updating the
        threat model.  Both reflect the same drift class the rest of
        this file pins, just with a Boolean reality instead of a
        numeric one.
        """
        android_root = _REPO_ROOT / "android"
        blob_parts: list[str] = []
        for ext in ("*.kt", "*.java", "*.xml"):
            for path in android_root.rglob(ext):
                # Skip build artefacts and IDE caches.
                if any(seg in path.parts for seg in ("build", ".gradle", ".idea")):
                    continue
                blob_parts.append(path.read_text(encoding="utf-8"))
        blob = "\n".join(blob_parts)

        has_pinner = "CertificatePinner" in blob or "<pin-set" in blob
        if has_pinner:
            raise AssertionError(
                "\n  DOC DRIFT — TLS certificate pinning was introduced\n"
                "  in android/ but THREAT_MODEL.md still names\n"
                "  'no TLS certificate pinning' as a § T2 residual.\n"
                "  Resolution: update T2's residual paragraph to\n"
                "  reflect the new posture (which key(s) are pinned,\n"
                "  what the operator procedure is for rotation)."
            )

        # No pinning — the residual paragraph must explicitly name it.
        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle="no TLS certificate pinning",
            code_origin=(
                "android/app/src/main/res/xml/network_security_config.xml "
                "(and absence of CertificatePinner / <pin-set> across android/)"
            ),
        )
        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle="system-store CA compromise is in scope",
            code_origin=(
                "android/app/src/main/res/xml/network_security_config.xml "
                "(and absence of CertificatePinner / <pin-set> across android/)"
            ),
        )


# ---------------------------------------------------------------------------
# SECA.md pins
# ---------------------------------------------------------------------------


class TestSecaDocConstants:
    """Pin numeric claims in ``docs/SECA.md`` against code."""

    def test_divergence_warn_threshold(self):
        """Trust property of the reward signal — divergence telemetry
        fires at ≥ 0.20 server-vs-client accuracy delta."""
        from llm.seca.events.router import _DIVERGENCE_WARN_THRESHOLD

        _assert_doc_pin(
            doc=_SECA,
            needle=f"≥ {_DIVERGENCE_WARN_THRESHOLD}",
            code_origin="llm/seca/events/router.py::_DIVERGENCE_WARN_THRESHOLD",
        )

    def test_recompute_movetime_default(self):
        """Trust property of the reward signal — server-side PGN
        recompute uses 50 ms per move by default."""
        # The default lives in compute_accuracy_from_pgn's signature
        # via _DEFAULT_MOVETIME_MS or a literal; check the actual
        # value used inside _evaluate_cp.
        from llm.seca.analysis import pgn_accuracy as pa_module
        import inspect

        # Extract the default from the function signature.
        signature = inspect.signature(pa_module.compute_accuracy_from_pgn)
        default = signature.parameters["movetime_ms"].default

        _assert_doc_pin(
            doc=_SECA,
            needle=f"default {default} ms per move",
            code_origin="llm/seca/analysis/pgn_accuracy.py::compute_accuracy_from_pgn(movetime_ms=...)",
        )


# ---------------------------------------------------------------------------
# API_CONTRACTS.md pins
# ---------------------------------------------------------------------------


class TestApiContractsConstants:
    """Pin numeric claims in ``docs/API_CONTRACTS.md`` against code."""

    def test_body_size_cap_413(self):
        """Body-size cap surfaces as HTTP 413; doc names it as 512 KB.

        Duplicate of TestThreatModelConstants.test_body_size_cap but
        against a different doc — both must stay in sync."""
        from llm.server import _MAX_BODY_BYTES

        kb = _MAX_BODY_BYTES // 1024
        _assert_doc_pin(
            doc=_API_CONTRACTS,
            needle=f"Request body exceeds {kb} KB",
            code_origin="llm/server.py::_MAX_BODY_BYTES",
        )

    def test_pgn_max_length(self):
        """Game/finish request PGN length cap.

        The doc says "Non-empty, ≤ 100 000 chars".  Extract from the
        Pydantic validator in events/router.py.
        """
        events_src = (
            _REPO_ROOT / "llm" / "seca" / "events" / "router.py"
        ).read_text(encoding="utf-8")
        match = re.search(r"len\(v\)\s*>\s*(\d{3}_\d{3}|\d+)", events_src)
        assert match, "could not locate PGN length cap in events/router.py"
        # Strip underscores so 100_000 parses as int.
        cap = int(match.group(1).replace("_", ""))

        # Doc renders the value as "100 000" (NBSP-style thousands
        # separator, common in the project's docs).  Build a flexible
        # needle that handles either "100000", "100 000", or
        # "100 000".
        formatted_options = [
            f"{cap}",
            f"{cap:,}".replace(",", " "),
            f"{cap:,}".replace(",", " "),
        ]
        content = _API_CONTRACTS.read_text(encoding="utf-8")
        matched = any(opt in content for opt in formatted_options)
        assert matched, (
            f"\n  DOC DRIFT — PGN length cap.\n"
            f"    Code value: {cap}\n"
            f"    Doc:        docs/API_CONTRACTS.md\n"
            f"    Tried:      {formatted_options}\n"
            f"    Resolution: ensure the document mentions the PGN cap "
            f"in a format that includes the digits {cap}."
        )



# ---------------------------------------------------------------------------
# README.md pins
# ---------------------------------------------------------------------------


class TestReadmeConstants:
    """Pin numeric claims in the top-level ``README.md`` against code.

    Some constants are documented in README rather than the docs/
    canonical specs.  This class targets those (e.g. the X-API-Version
    section which lives in README's "API schema versioning" block,
    not in API_CONTRACTS.md despite a stale server.py comment that
    claimed otherwise — fixed in PR 12).
    """

    def test_api_version_constant(self):
        """X-API-Version header value pinned to API_VERSION constant.

        Complements ``test_api_version_header.test_avh_01`` (which
        pins ``API_VERSION == '1'`` in code) by pinning the doc side.
        Note: lives in README.md, NOT in API_CONTRACTS.md despite
        what a stale server.py comment previously claimed.

        Updated in PR 14 (2026-05-15) when the docs moved from
        ``pinned at 1`` to a per-row table — the doc text now reads
        ```| `X-API-Version` | `{API_VERSION}` (currently) | ...```.
        """
        from llm.server import API_VERSION

        _assert_doc_pin(
            doc=_README,
            needle=f"| `X-API-Version` | `{API_VERSION}` (currently) |",
            code_origin="llm/server.py::API_VERSION",
        )

    def test_api_versions_supported_constant(self):
        """``X-API-Versions-Supported`` advertised value pinned to
        ``API_VERSIONS_SUPPORTED`` tuple.  Added PR 14 alongside the
        Phase 2 supported-range advertisement.
        """
        from llm.server import API_VERSIONS_SUPPORTED

        supported_csv = ", ".join(API_VERSIONS_SUPPORTED)
        _assert_doc_pin(
            doc=_README,
            needle=f"| `X-API-Versions-Supported` | `{supported_csv}`",
            code_origin="llm/server.py::API_VERSIONS_SUPPORTED",
        )


# ---------------------------------------------------------------------------
# ARCHITECTURE.md pins
# ---------------------------------------------------------------------------


class TestArchitectureDocConstants:
    """Pin numeric claims in ``docs/ARCHITECTURE.md`` against code."""

    def test_compact_threshold(self):
        """20-turn compaction threshold."""
        from llm.seca.coach.context_compact import COMPACT_THRESHOLD

        _assert_doc_pin(
            doc=_ARCHITECTURE,
            needle=f"`COMPACT_THRESHOLD = {COMPACT_THRESHOLD}`",
            code_origin="llm/seca/coach/context_compact.py::COMPACT_THRESHOLD",
        )

    def test_compact_keep_recent(self):
        """How many most-recent turns the compaction preserves verbatim."""
        from llm.seca.coach.context_compact import COMPACT_KEEP_RECENT

        _assert_doc_pin(
            doc=_ARCHITECTURE,
            needle=f"`COMPACT_KEEP_RECENT = {COMPACT_KEEP_RECENT}`",
            code_origin="llm/seca/coach/context_compact.py::COMPACT_KEEP_RECENT",
        )
