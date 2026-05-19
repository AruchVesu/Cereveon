package ai.chesscoach.app

import kotlinx.serialization.encodeToString
import java.net.HttpURLConnection

// ── Interface ─────────────────────────────────────────────────────────────────

interface GameApiClient {
    suspend fun startGame(playerId: String): ApiResult<GameStartResponse>
    suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse>

    /**
     * Fetch the SECA curriculum recommendation from POST /curriculum/next.
     *
     * Requires Bearer token authentication (uses the configured [tokenProvider]).
     * The server derives the player identity from the JWT — no body is sent
     * (pre-PR-27 this method sent `{"player_id": ...}` which the server
     * silently dropped).  Returns a [CurriculumRecommendation] driven by
     * real per-player history.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so that test
     * fakes implementing only the other methods do not need to override this.
     */
    suspend fun getNextCurriculum(): ApiResult<CurriculumRecommendation> =
        ApiResult.HttpError(501)

    /**
     * GET /game/history.
     *
     * Returns the 20 most recent games for the authenticated player, ordered
     * newest-first. Requires Bearer token authentication.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test fakes
     * do not need to override this method.
     */
    suspend fun getGameHistory(): ApiResult<List<GameHistoryItem>> = ApiResult.HttpError(501)

    /**
     * GET /seca/status — open endpoint, no auth required.
     *
     * Returns SECA runtime safety flags. Called at cold-start to confirm that
     * [SecaStatusDto.safeModeEnabled] is true before sending coaching requests.
     * Logs a warning if the backend reports safe_mode=false.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test fakes
     * do not need to override this method.
     */
    suspend fun getSecaStatus(): ApiResult<SecaStatusDto> = ApiResult.HttpError(501)

    /**
     * GET /player/progress — requires Bearer token authentication.
     *
     * Returns the full progress dashboard snapshot: current world-model state,
     * last 20 games with per-game weaknesses, and HistoricalAnalysisPipeline output.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test fakes
     * do not need to override this method.
     */
    suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> = ApiResult.HttpError(501)

    /**
     * POST /game/{gameId}/checkpoint — store the in-progress board state.
     *
     * Used by the cross-device resume feature: every move on the
     * client triggers a checkpoint so the server knows the latest
     * FEN + UCI history for this game.  When the user later opens
     * the app on a different device (or after a reinstall), they
     * pull this state via [getActiveGame].
     *
     * Bearer auth required.  Returns:
     *   - [ApiResult.Success(Unit)] on HTTP 200 (server returns
     *     {"status":"checkpointed"} but we don't surface the field)
     *   - [ApiResult.HttpError(404)] when the game_id is unknown
     *   - [ApiResult.HttpError(403)] when the game belongs to
     *     another player
     *   - [ApiResult.HttpError(409)] when the game is already
     *     finished (a stale checkpoint after /game/finish)
     *   - Transport variants on failure
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun checkpointGame(
        gameId: String,
        fen: String,
        uciHistory: String,
    ): ApiResult<Unit> = ApiResult.HttpError(501)

    /**
     * GET /game/active — fetch the most recent unfinished game's
     * checkpoint for the authenticated player, or null on 404.
     *
     * Used at cold-start when the local SharedPreferences resume
     * snapshot is missing (fresh install / device swap).  Returning
     * `null` on 404 (rather than HttpError) treats "no resumable
     * game" as the same case as "no checkpoint stored" — both
     * mean "start fresh".
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun getActiveGame(): ApiResult<ActiveGameResponse?> =
        ApiResult.HttpError(501)

    /**
     * GET /repertoire — fetch the player's opening repertoire.
     *
     * Backs the AtriumOpenings screen.  When the player has no saved
     * entries the server returns a canonical 4-entry default list
     * (so a fresh user sees a populated screen); the client doesn't
     * distinguish saved-vs-default here, just renders what comes back.
     *
     * Bearer auth required.  Default implementation returns
     * [ApiResult.HttpError(501)] so test fakes don't have to
     * implement it.
     */
    suspend fun getRepertoire(): ApiResult<List<RepertoireOpeningDto>> =
        ApiResult.HttpError(501)

    /**
     * POST /repertoire — add or upsert one opening.
     *
     * Server returns the full updated list so callers can re-render
     * in one round-trip.  When the player has no saved entries yet,
     * the server materialises the canonical defaults first then
     * appends the new line — UX continuity with the GET defaults.
     */
    suspend fun addOpening(
        eco: String,
        name: String,
        line: String,
        mastery: Float = 0.0f,
    ): ApiResult<List<RepertoireOpeningDto>> = ApiResult.HttpError(501)

    /**
     * DELETE /repertoire/{eco} — remove an opening from the
     * player's repertoire.  404 means "already gone" from the
     * caller's perspective — the client should still refresh the
     * list either way.
     */
    suspend fun deleteOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        ApiResult.HttpError(501)

    /**
     * POST /repertoire/{eco}/active — promote one opening to active,
     * demoting any other active line atomically.  Returns the full
     * updated list.
     */
    suspend fun setActiveOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        ApiResult.HttpError(501)

    /**
     * POST /repertoire/{eco}/drill-result — apply one drill outcome
     * to the named opening's mastery.  [outcome] is the user's
     * self-rated score in [0.0, 1.0]; the server applies an EMA
     * step toward it.  Returns the full updated list.
     */
    suspend fun recordDrillResult(
        eco: String,
        outcome: Float,
    ): ApiResult<List<RepertoireOpeningDto>> = ApiResult.HttpError(501)

    /**
     * POST /training/verify-replay — Phase 3 mistake-replay verifier.
     *
     * Sends the position FEN the user was looking at + the move
     * they're proposing as a fix; the server runs Stockfish and
     * returns whether the move is within 30 cp of the engine's best.
     * The Android replay sheet calls this BEFORE calling
     * [submitTrainingSolve] so an unverified move never moves the
     * XP counter.
     *
     * Bearer auth required.  Returns:
     *   - 200 + [VerifyReplayResponse] — engine ran; ``isCorrect``
     *     reflects the verdict.  Note: a wrong move is NOT an error,
     *     it's a successful response with ``isCorrect=false``.
     *   - 400 — malformed FEN, illegal move, or over-long input.
     *   - 503 — engine pool unavailable (boot-time failure or
     *     queue timeout).  Client should show a soft retry message.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun verifyReplayMove(
        fen: String,
        moveUci: String,
    ): ApiResult<VerifyReplayResponse> = ApiResult.HttpError(501)

    /**
     * POST /training/solve — Phase 2 verified-solve persistence.
     *
     * Bumps [Player.training_xp] by 10 (server-side constant) and
     * inserts a TrainingCompletion row.  Idempotent on
     * ``(player, source_type, source_ref)``: a retry returns
     * ``xpAwarded=0`` plus the original completion's timestamp.
     *
     * Bearer auth required.  Returns:
     *   - 200 + [TrainingSolveResponse] — XP credit recorded.
     *   - 400 — invalid source_type or over-long source_ref.
     *   - 429 — rate limit exceeded.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun submitTrainingSolve(
        sourceType: String,
        sourceRef: String?,
    ): ApiResult<TrainingSolveResponse> = ApiResult.HttpError(501)
}

// ── HTTP implementation ───────────────────────────────────────────────────────

class HttpGameApiClient(
    val baseUrl: String,
    val apiKey: String,
    val connectTimeoutMs: Int = 8_000,
    val readTimeoutMs: Int = 30_000,
    val tokenProvider: (() -> String?)? = null,
    /**
     * Optional sink for the X-Auth-Token refresh header — see
     * [TokenRefresh].  Every successful authenticated response
     * (game/start, game/finish, etc.) hands the freshly-minted JWT
     * here so the activity's AuthRepository can rotate the stored
     * token transparently.  Without this, the user's JWT would
     * expire after 24 h despite continuous gameplay.
     */
    val tokenSink: ((String) -> Unit)? = null,
) : GameApiClient {

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    /**
     * Header set used by most game endpoints: X-Api-Key plus optional
     * Bearer token from [tokenProvider].  Some endpoints (getNextCurriculum,
     * getGameHistory, getPlayerProgress) omit the X-Api-Key — set
     * [includeApiKey] = false for those.
     */
    private fun authHeaders(includeApiKey: Boolean = true): Map<String, String> = buildMap {
        if (includeApiKey) put("X-Api-Key", apiKey)
        tokenProvider?.invoke()?.let { token -> put("Authorization", "Bearer $token") }
    }

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> = http.request(
        path = "/game/start",
        method = "POST",
        headers = authHeaders(),
        // T3: /game/start now requires a JWT-authenticated session.
        // The server derives player_id from the token; the body
        // player_id is accepted for back-compat but ignored.
        body = ApiJson.encodeToString(GameStartRequest(playerId = playerId)),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameStartResponse>(body) },
    )

    override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> =
        withRetry(maxAttempts = 3) {
            // ``encodeDefaults = false`` on [ApiJson] strips null
            // player_id / game_id from the wire payload so the server
            // sees the same shape as the prior hand-rolled builder.
            // game_id ties this finish back to the original /game/start
            // row server-side; an empty-but-not-null id is normalised
            // away to keep parity with the pre-resume contract.
            val normalised = req.copy(gameId = req.gameId?.takeIf { it.isNotBlank() })
            http.request(
                path = "/game/finish",
                method = "POST",
                headers = authHeaders(),
                body = ApiJson.encodeToString(normalised),
                onResponse = refreshOnSuccess(),
                parse = { body -> ApiJson.decodeFromString<GameFinishResponse>(body) },
            )
        }

    override suspend fun getNextCurriculum(): ApiResult<CurriculumRecommendation> =
        http.request(
            path = "/curriculum/next",
            method = "POST",
            // Bearer-only; X-Api-Key is intentionally omitted here.
            // No body — pre-PR-27 this sent `{"player_id": ...}` which the
            // server silently ignored (it derives the player from the JWT).
            headers = authHeaders(includeApiKey = false),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<CurriculumRecommendation>(body) },
        )

    override suspend fun getGameHistory(): ApiResult<List<GameHistoryItem>> = http.request(
        path = "/game/history",
        method = "GET",
        // Bearer-only; pre-refactor wire shape did not send X-Api-Key.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameHistoryResponse>(body).games },
    )

    override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> = http.request(
        path = "/player/progress",
        method = "GET",
        // Bearer-only; pre-refactor wire shape did not send X-Api-Key.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<PlayerProgressResponse>(body) },
    )

    override suspend fun getSecaStatus(): ApiResult<SecaStatusDto> = http.request(
        path = "/seca/status",
        method = "GET",
        // Open endpoint — no auth headers.
        parse = { body -> ApiJson.decodeFromString<SecaStatusDto>(body) },
    )

    override suspend fun checkpointGame(
        gameId: String,
        fen: String,
        uciHistory: String,
    ): ApiResult<Unit> = http.requestNoBody(
        path = "/game/${gameId}/checkpoint",
        method = "POST",
        headers = authHeaders(),
        body = ApiJson.encodeToString(CheckpointRequest(fen = fen, uciHistory = uciHistory)),
        onResponse = refreshOnSuccess(),
    )

    override suspend fun getActiveGame(): ApiResult<ActiveGameResponse?> {
        // Special case: 404 is the documented "no resumable game" signal —
        // a normal absence-of-data response, not an error.  We treat both
        // 200 and 404 as success; the parser distinguishes them by the
        // response code captured in [observedCode] from the onResponse
        // hook.  FastAPI emits a non-empty ``{"detail": ...}`` body on
        // 404 so we cannot rely on body-blankness to discriminate.
        var observedCode = 0
        return http.request<ActiveGameResponse?>(
            path = "/game/active",
            method = "GET",
            headers = authHeaders(),
            successCodes = setOf(200, 404),
            onResponse = { conn ->
                observedCode = conn.responseCode
                consumeRefreshedToken(conn, tokenSink)
            },
            parse = { body ->
                if (observedCode == 404) {
                    null
                } else {
                    ApiJson.decodeFromString<ActiveGameResponse>(body)
                }
            },
        )
    }

    override suspend fun getRepertoire(): ApiResult<List<RepertoireOpeningDto>> = http.request(
        path = "/repertoire",
        method = "GET",
        headers = authHeaders(),
        onResponse = refreshOnSuccess(),
        parse = ::parseRepertoireResponse,
    )

    override suspend fun addOpening(
        eco: String,
        name: String,
        line: String,
        mastery: Float,
    ): ApiResult<List<RepertoireOpeningDto>> = http.request(
        path = "/repertoire",
        method = "POST",
        headers = authHeaders(),
        body = ApiJson.encodeToString(
            AddOpeningRequest(eco = eco, name = name, line = line, mastery = mastery)
        ),
        onResponse = refreshOnSuccess(),
        parse = ::parseRepertoireResponse,
    )

    override suspend fun deleteOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        http.request(
            path = "/repertoire/$eco",
            method = "DELETE",
            headers = authHeaders(),
            onResponse = refreshOnSuccess(),
            parse = ::parseRepertoireResponse,
        )

    override suspend fun setActiveOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        http.request(
            path = "/repertoire/$eco/active",
            method = "POST",
            headers = authHeaders(),
            // Empty body — the eco from the path is the only input;
            // the endpoint takes no JSON.
            body = "{}",
            onResponse = refreshOnSuccess(),
            parse = ::parseRepertoireResponse,
        )

    override suspend fun recordDrillResult(
        eco: String,
        outcome: Float,
    ): ApiResult<List<RepertoireOpeningDto>> = http.request(
        path = "/repertoire/$eco/drill-result",
        method = "POST",
        headers = authHeaders(),
        body = ApiJson.encodeToString(DrillResultRequest(outcome = outcome)),
        onResponse = refreshOnSuccess(),
        parse = ::parseRepertoireResponse,
    )

    override suspend fun verifyReplayMove(
        fen: String,
        moveUci: String,
    ): ApiResult<VerifyReplayResponse> = http.request(
        path = "/training/verify-replay",
        method = "POST",
        // Bearer-only — /training/* never carries X-Api-Key.  The
        // server identifies the player via JWT; the FEN + move come
        // from the replay sheet's state.
        headers = authHeaders(includeApiKey = false),
        body = ApiJson.encodeToString(VerifyReplayRequest(fen = fen, moveUci = moveUci)),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<VerifyReplayResponse>(body) },
    )

    override suspend fun submitTrainingSolve(
        sourceType: String,
        sourceRef: String?,
    ): ApiResult<TrainingSolveResponse> = http.request(
        path = "/training/solve",
        method = "POST",
        headers = authHeaders(includeApiKey = false),
        // ``encodeDefaults = false`` on ApiJson strips a null
        // sourceRef from the wire payload so the server's NULL-distinct
        // dedup branch fires correctly (a serialised ``"source_ref": null``
        // would still be a present-but-null key and is treated the
        // same on the FastAPI side, but stripping it keeps the wire
        // shape minimal and matches the request schema example in
        // docs/API_CONTRACTS.md §32).
        body = ApiJson.encodeToString(
            TrainingSolveRequest(sourceType = sourceType, sourceRef = sourceRef)
        ),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<TrainingSolveResponse>(body) },
    )

    /**
     * Decode the ``{"openings": [...]}`` envelope to the bare list
     * the [GameApiClient] surface returns.  Shared by every /repertoire
     * verb (GET, POST, DELETE, /active, /drill-result) — all five emit
     * the same wrapper shape so callers always receive the full
     * post-mutation list.
     */
    private fun parseRepertoireResponse(body: String): List<RepertoireOpeningDto> =
        ApiJson.decodeFromString<RepertoireListResponse>(body).openings
}
