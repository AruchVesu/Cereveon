package ai.chesscoach.app

/**
 * Schema-version constants for the coaching backend HTTP API.
 *
 * Every outgoing request to the backend carries
 * ``X-API-Version: <COACH_API_VERSION>``.  The server reads the header
 * and rejects mismatched versions with HTTP 400; missing headers are
 * tolerated in Phase 1 (server-side lenient mode) but expected to flip
 * to strict-on-missing once production telemetry confirms reliability.
 *
 * Bump this constant when the request/response shape of any coaching
 * endpoint changes in a way that older client builds would interpret
 * incorrectly.  Additive changes (new optional response fields, new
 * optional request fields with safe defaults) do NOT need a bump —
 * old clients continue to ignore the new fields.
 *
 * Single source of truth, deliberately at the package root rather than
 * per-client, so all four HTTP surfaces (GameApiClient, AuthApiClient,
 * LiveMoveApiClient, EngineEvalApiClient, CoachApiClient) read the same
 * value with no risk of drift.  See `docs/API_CONTRACTS.md` >
 * "API schema versioning" for the migration policy.
 */
const val COACH_API_VERSION: String = "1"

const val COACH_API_VERSION_HEADER: String = "X-API-Version"
