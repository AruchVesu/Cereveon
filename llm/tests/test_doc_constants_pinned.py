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

    def test_symmetric_jwt_residual_documented(self):
        """§ T2 — symmetric-JWT residual.

        Bidirectional pin (same shape as
        ``test_no_tls_pinning_residual_documented``): asserts
        ``tokens.py::ALGORITHM == "HS256"`` matches the doc's claim
        about HS256, and asserts the residual paragraph names the
        consequence ("forge a JWT for any `player_id`") + the
        secret-disclosure framing.

        If a future PR migrates the algorithm to RS256 / ES256 / etc.,
        this test fails with a pointer that the residual paragraph
        must be rewritten — the asymmetric posture has a different
        consequence (public key in api container, private key
        isolated in a secret manager) that the doc must reflect
        before the test passes again.
        """
        from llm.seca.auth.tokens import ALGORITHM

        if ALGORITHM != "HS256":
            raise AssertionError(
                f"\n  DOC DRIFT — JWT algorithm changed.\n"
                f"    tokens.py::ALGORITHM:  {ALGORITHM}\n"
                f"    THREAT_MODEL.md § T2:  still names 'HS256' as a residual\n"
                f"    Resolution: rewrite the symmetric-JWT residual\n"
                f"    paragraph in T2 to reflect the new (likely asymmetric)\n"
                f"    posture — what's signed, what's verified, where the\n"
                f"    private key lives, what the rotation procedure is."
            )

        # HS256 — the residual paragraph must explicitly name the
        # algorithm, the symmetric property, and the consequence.
        # Each needle is a phrase that appears on a single line in
        # the doc (substring matching doesn't cross line wraps).
        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle=f"`ALGORITHM = \"{ALGORITHM}\"`",
            code_origin="llm/seca/auth/tokens.py::ALGORITHM",
        )
        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle="both the signer and the verifier",
            code_origin="llm/seca/auth/tokens.py (symmetric: same SECRET_KEY signs + verifies)",
        )
        _assert_doc_pin(
            doc=_THREAT_MODEL,
            needle="forge a JWT for any `player_id`",
            code_origin="llm/seca/auth/tokens.py (consequence of symmetric HS256 + env-readable SECRET_KEY)",
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


class TestSecaLayersTableCoverage:
    """Pin SECA.md's live-layers table against the actual ``seca/`` filesystem.

    Drift class caught: a new ``seca/<dir>/`` lands in code with at
    least one external importer in ``llm/``, but the live-layers table
    in ``docs/SECA.md`` never gets updated to describe it.  The next
    reviewer trying to map endpoints to layers reads an inaccurate
    table.  Closes the same class of doc-vs-reality drift as the rest
    of this file, with a Boolean reality (subpackage exists + has
    external importers) instead of a numeric constant.

    The ``KNOWN_LIVE_SUBPACKAGES`` list below is this test's source
    of truth.  When adding a new live ``seca/`` layer, the contributor
    updates the list AND adds a row to SECA.md's live-layers table.
    When retiring a layer, remove from the list (the doc edit happens
    naturally as part of the retirement PR).
    """

    KNOWN_LIVE_SUBPACKAGES = (
        # Original 14 layers (pre-PR-20):
        "auth",
        "events",
        "storage",
        "skills",
        "adaptation",
        "coach",
        "analytics",
        "analysis",
        "brain",
        "learning",
        "inference",
        "engines",
        "safety",
        "runtime",
        # Added 2026-05-15 in PR 20 — verified live by external-importer grep:
        "chat",  # imported by chat_pipeline.py, test_chat_persistence.py
        "curriculum",  # imported by auth/router.py + test_curriculum_next_contract.py
        "explainer",  # imported by inference/pipeline.py + coach Mode-2 pipelines
        "performance",  # imported by analysis/performance_builder.py + game_analyzer.py
        "repertoire",  # mounts /repertoire router from server.py
        "world_model",  # imported by server.py:80 + safety/freeze.py for SafeWorldModel
    )

    # Subpackages on disk that are intentionally NOT in the live-layers
    # table because they have zero external importers in ``llm/`` (as of
    # PR 20).  Documented here so a contributor reading the test sees the
    # known dormant cluster without spelunking.
    KNOWN_DORMANT_SUBPACKAGES = (
        "data",  # feature_builder / pgn_loader / timeline_builder — zero importers
        "ratings",  # elo.py — zero importers (skill update uses its own ratings math)
    )

    def test_every_known_live_subpackage_has_a_doc_row(self):
        """Each KNOWN_LIVE_SUBPACKAGES entry must appear as a row
        in SECA.md's live-layers table, identified by the
        ``| **<name>**`` pattern (markdown table row + bold layer
        identifier).  Allows for trailing annotations like
        ``(allowlisted)`` after the bold name, so the test doesn't
        rot when a layer's doc framing changes.
        """
        doc_text = _SECA.read_text(encoding="utf-8")
        missing = [
            name
            for name in self.KNOWN_LIVE_SUBPACKAGES
            if f"| **{name}**" not in doc_text
        ]
        assert not missing, (
            f"\n  DOC DRIFT — SECA.md live-layers table missing live subpackage row(s):\n"
            f"    {missing}\n"
            f"    Resolution: add a `| **<name>** | seca/<name>/ | <responsibility> |`\n"
            f"    row in docs/SECA.md, then re-run.  If the subpackage is no\n"
            f"    longer live (zero external importers in llm/), remove its name\n"
            f"    from KNOWN_LIVE_SUBPACKAGES and add it to KNOWN_DORMANT_SUBPACKAGES."
        )

    def test_every_known_live_subpackage_exists_on_disk(self):
        """Catches the inverse drift: KNOWN_LIVE_SUBPACKAGES says a
        layer is live, but the directory was retired without updating
        the list.  The doc-pin test above would still pass (the row
        is in the doc), but the test below fails loudly so the list
        + doc + filesystem stay in sync.
        """
        seca_root = _REPO_ROOT / "llm" / "seca"
        gone = [
            name
            for name in self.KNOWN_LIVE_SUBPACKAGES
            if not (seca_root / name).is_dir()
        ]
        assert not gone, (
            f"\n  KNOWN_LIVE_SUBPACKAGES references missing dir(s): {gone}\n"
            f"  Resolution: remove from KNOWN_LIVE_SUBPACKAGES and update\n"
            f"  SECA.md (likely by removing the corresponding row, or moving\n"
            f"  it to the 'What's dormant on disk and why' section)."
        )


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


class TestApiContractsAndroidCoverage:
    """Source-pin: every endpoint the Android client calls must appear
    as a section heading in ``docs/API_CONTRACTS.md``.

    Drift class caught: a new server route lands and Android starts
    calling it, but the contract doc is never updated.  The next
    integrator looking at API_CONTRACTS.md reads an inaccurate
    inventory and ships a wrong client.  Closes the same drift class
    as PR #157's ``TestSecaLayersTableCoverage`` (live-layers table),
    but for the Android-facing API surface.

    The ``KNOWN_ANDROID_ENDPOINTS`` mapping below is the source of
    truth.  When adding a new endpoint that Android will call,
    update this map AND add a section to API_CONTRACTS.md.  When
    retiring an endpoint, remove from the map (the doc edit happens
    naturally as part of the retirement PR).
    """

    # Each entry: path → (method, expected heading fragment).
    # The heading fragment is the part after ``## N. `` in
    # API_CONTRACTS.md so the test is robust to section renumbering.
    KNOWN_ANDROID_ENDPOINTS = {
        # Auth
        "/auth/register": "`POST /auth/register`",
        "/auth/login": "`POST /auth/login`",
        "/auth/logout": "`POST /auth/logout`",
        "/auth/me": "`GET /auth/me` / `PATCH /auth/me`",
        "/auth/change-password": "`POST /auth/change-password`",
        # Game lifecycle
        "/game/start": "`POST /game/start`",
        "/game/finish": "`POST /game/finish`",
        "/game/{game_id}/checkpoint": "`POST /game/{game_id}/checkpoint`",
        "/game/active": "`GET /game/active`",
        "/game/history": "`GET /game/history`",
        "/game/coach-feedback": "`POST /game/coach-feedback`",
        # Training + analytics
        "/next-training/{player_id}": "`GET /next-training/{player_id}`",
        "/curriculum/next": "`POST /curriculum/next`",
        "/player/progress": "`GET /player/progress`",
        # SECA / engine
        "/seca/status": "`GET /seca/status`",
        "/engine/eval": "`POST /engine/eval`",
        "/live/move": "`POST /live/move`",
        # Chat
        "/chat": "`POST /chat`",
        "/chat/stream": "`POST /chat/stream`",
        "/chat/history": "`GET /chat/history`",
        # Repertoire
        "/repertoire": "`GET /repertoire`",
        "/repertoire (POST)": "`POST /repertoire`",
        "/repertoire/{eco}": "`DELETE /repertoire/{eco}`",
        "/repertoire/{eco}/active": "`POST /repertoire/{eco}/active`",
        "/repertoire/{eco}/drill-result": "`POST /repertoire/{eco}/drill-result`",
    }

    def test_every_android_endpoint_has_a_doc_section(self):
        """Each KNOWN_ANDROID_ENDPOINTS value must appear as a section
        heading in API_CONTRACTS.md, identified by ``## N. <fragment>``.
        """
        doc_text = _API_CONTRACTS.read_text(encoding="utf-8")
        missing = [
            (path, fragment)
            for path, fragment in self.KNOWN_ANDROID_ENDPOINTS.items()
            if fragment not in doc_text
        ]
        assert not missing, (
            "\n  DOC DRIFT — API_CONTRACTS.md is missing section(s) for:\n"
            + "\n".join(f"    {path} → expected heading fragment {frag!r}" for path, frag in missing)
            + "\n  Resolution: add a `## N. <method+path>` section in "
            "docs/API_CONTRACTS.md with the canonical request/response "
            "shape, then re-run.  If the endpoint is no longer called "
            "from Android, remove it from KNOWN_ANDROID_ENDPOINTS in "
            "this test."
        )

    def test_known_endpoints_actually_referenced_by_android(self):
        """Catches the inverse drift: KNOWN_ANDROID_ENDPOINTS lists an
        endpoint, but no Android source file references the path.

        Implementation: greps the Android source tree for the path's
        URL prefix (the part before any ``{var}`` placeholder).
        A stale entry in the doc-pin list would otherwise let an
        Android-side retirement go uncaught — the next API_CONTRACTS.md
        editor would maintain a section nobody calls.
        """
        android_src = _REPO_ROOT / "android" / "app" / "src" / "main"
        if not android_src.exists():
            import pytest

            pytest.skip("android/app/src/main not present in this checkout")

        kt_blob_parts: list[str] = []
        for py in android_src.rglob("*.kt"):
            try:
                kt_blob_parts.append(py.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
        blob = "\n".join(kt_blob_parts)

        # For each declared path, take the literal URL prefix up to the
        # first ``{`` placeholder.  Android emits placeholders as
        # ``$variable`` or ``${expr}`` interpolations, so a substring
        # match on the literal prefix is sufficient evidence the path
        # is in use.
        unreferenced: list[str] = []
        for path in self.KNOWN_ANDROID_ENDPOINTS:
            # Strip the (POST) disambiguator if present.
            url = path.split(" ", 1)[0]
            literal_prefix = url.split("{", 1)[0].rstrip("/")
            # Bare prefix is dangerous (e.g. ``/repertoire`` would match
            # ``/repertoire/{eco}``), so require the prefix appear as
            # a closed string in a Kotlin literal: surrounded by `"`
            # or terminated by `/{` / `/$`.
            if (
                f'"{literal_prefix}"' not in blob
                and f"{literal_prefix}/$" not in blob
                and f'"{literal_prefix}/' not in blob
            ):
                unreferenced.append(path)
        assert not unreferenced, (
            f"\n  KNOWN_ANDROID_ENDPOINTS references path(s) with no Android caller:\n"
            f"    {unreferenced}\n"
            f"  Resolution: remove from KNOWN_ANDROID_ENDPOINTS and consider\n"
            f"  retiring the corresponding API_CONTRACTS.md section if no\n"
            f"  other client uses the route."
        )


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
