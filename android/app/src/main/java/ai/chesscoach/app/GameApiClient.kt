package ai.chesscoach.app

import org.json.JSONObject
import java.net.HttpURLConnection

// ── Interface ─────────────────────────────────────────────────────────────────

interface GameApiClient {
    suspend fun startGame(playerId: String): ApiResult<GameStartResponse>
    suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse>

    /**
     * Fetch the next training recommendation for [playerId] from
     * GET /next-training/{player_id}.
     *
     * Returns [ApiResult.Success] with a [TrainingRecommendation] on HTTP 200;
     * [ApiResult.HttpError] on any non-200 response; [ApiResult.Timeout] when
     * the connect or read deadline is exceeded; [ApiResult.NetworkError]
     * for all other transport failures.
     */
    suspend fun getNextTraining(playerId: String): ApiResult<TrainingRecommendation>

    /**
     * Fetch the SECA curriculum recommendation from POST /curriculum/next.
     *
     * Requires Bearer token authentication (uses the configured [tokenProvider]).
     * Returns a [CurriculumRecommendation] driven by real per-player history
     * — the authoritative training recommendation engine.
     *
     * Schema differs from [getNextTraining]; do not conflate the two responses.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so that test
     * fakes implementing only the other methods do not need to override this.
     */
    suspend fun getNextCurriculum(playerId: String): ApiResult<CurriculumRecommendation> =
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
     * Bearer token from [tokenProvider].  Some endpoints (getNextTraining,
     * getNextCurriculum, getGameHistory, getPlayerProgress) omit the
     * X-Api-Key — set [includeApiKey] = false for those.
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
        body = JSONObject().put("player_id", playerId).toString(),
        onResponse = refreshOnSuccess(),
        parse = { text ->
            val json = JSONObject(text)
            GameStartResponse(gameId = json.opt("game_id")?.toString() ?: "")
        },
    )

    override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> =
        withRetry(maxAttempts = 3) {
            val weaknessesJson = JSONObject()
            req.weaknesses.forEach { (k, v) -> weaknessesJson.put(k, v) }
            val body = JSONObject()
                .put("pgn", req.pgn)
                .put("result", req.result)
                .put("accuracy", req.accuracy)
                .put("weaknesses", weaknessesJson)
                .apply { req.playerId?.let { put("player_id", it) } }
                // game_id ties this finish back to the original /game/start
                // row server-side; omitted-when-null keeps the wire shape
                // compatible with the pre-resume contract.
                .apply { req.gameId?.takeIf { it.isNotBlank() }?.let { put("game_id", it) } }
                .toString()
            http.request(
                path = "/game/finish",
                method = "POST",
                headers = authHeaders(),
                body = body,
                onResponse = refreshOnSuccess(),
                parse = ::parseFinishResponse,
            )
        }

    override suspend fun getNextTraining(playerId: String): ApiResult<TrainingRecommendation> =
        http.request(
            path = "/next-training/$playerId",
            method = "GET",
            headers = mapOf("X-Api-Key" to apiKey),
            parse = ::parseTrainingResponse,
        )

    override suspend fun getNextCurriculum(playerId: String): ApiResult<CurriculumRecommendation> =
        http.request(
            path = "/curriculum/next",
            method = "POST",
            // Bearer-only; X-Api-Key is intentionally omitted here to
            // match the pre-refactor wire shape.
            headers = authHeaders(includeApiKey = false),
            body = JSONObject().put("player_id", playerId).toString(),
            onResponse = refreshOnSuccess(),
            parse = ::parseCurriculumResponse,
        )

    override suspend fun getGameHistory(): ApiResult<List<GameHistoryItem>> = http.request(
        path = "/game/history",
        method = "GET",
        // Bearer-only; pre-refactor wire shape did not send X-Api-Key.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = ::parseHistoryResponse,
    )

    override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> = http.request(
        path = "/player/progress",
        method = "GET",
        // Bearer-only; pre-refactor wire shape did not send X-Api-Key.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = ::parseProgressResponse,
    )

    override suspend fun getSecaStatus(): ApiResult<SecaStatusDto> = http.request(
        path = "/seca/status",
        method = "GET",
        // Open endpoint — no auth headers.
        parse = ::parseSecaStatusResponse,
    )

    override suspend fun checkpointGame(
        gameId: String,
        fen: String,
        uciHistory: String,
    ): ApiResult<Unit> = http.requestNoBody(
        path = "/game/${gameId}/checkpoint",
        method = "POST",
        headers = authHeaders(),
        body = JSONObject().put("fen", fen).put("uci_history", uciHistory).toString(),
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
            parse = { text ->
                if (observedCode == 404) {
                    null
                } else {
                    val root = JSONObject(text)
                    ActiveGameResponse(
                        gameId = root.optString("game_id", ""),
                        currentFen = root.optString("current_fen", ""),
                        currentUciHistory = root.optString("current_uci_history", ""),
                    )
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
        body = JSONObject()
            .put("eco", eco)
            .put("name", name)
            .put("line", line)
            .put("mastery", mastery.toDouble())
            .toString(),
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
        body = JSONObject().put("outcome", outcome.toDouble()).toString(),
        onResponse = refreshOnSuccess(),
        parse = ::parseRepertoireResponse,
    )

    private fun parseRepertoireResponse(body: String): List<RepertoireOpeningDto> {
        val root = JSONObject(body)
        val arr = root.optJSONArray("openings") ?: return emptyList()
        return buildList {
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                add(
                    RepertoireOpeningDto(
                        eco = o.optString("eco", ""),
                        name = o.optString("name", ""),
                        line = o.optString("line", ""),
                        mastery = o.optDouble("mastery", 0.0).toFloat(),
                        isActive = o.optBoolean("is_active", false),
                        ordinal = o.optInt("ordinal", i),
                    ),
                )
            }
        }
    }

    private fun parseTrainingResponse(text: String): TrainingRecommendation {
        val json = JSONObject(text)
        return TrainingRecommendation(
            topic = json.optString("topic", ""),
            difficulty = json.optDouble("difficulty", 0.5).toFloat(),
            format = json.optString("format", ""),
            expectedGain = json.optDouble("expected_gain", 0.0).toFloat(),
        )
    }

    private fun parseCurriculumResponse(text: String): CurriculumRecommendation {
        val json = JSONObject(text)
        val payloadJson = json.optJSONObject("payload") ?: JSONObject()
        val payload = buildMap<String, String> {
            payloadJson.keys().forEach { key -> put(key, payloadJson.opt(key)?.toString() ?: "") }
        }
        return CurriculumRecommendation(
            topic = json.optString("topic", ""),
            difficulty = json.optDouble("difficulty", 0.5).toFloat(),
            exerciseType = json.optString("exercise_type", ""),
            payload = payload,
        )
    }

    private fun parseHistoryResponse(text: String): List<GameHistoryItem> {
        val json = JSONObject(text)
        val arr = json.optJSONArray("games") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val g = arr.getJSONObject(i)
            GameHistoryItem(
                id = g.optString("id", ""),
                result = g.optString("result", ""),
                accuracy = g.optDouble("accuracy", 0.0).toFloat(),
                ratingAfter = if (g.isNull("rating_after")) null
                              else g.optDouble("rating_after").toFloat(),
                createdAt = g.optString("created_at", ""),
            )
        }
    }

    private fun parseSecaStatusResponse(text: String): SecaStatusDto {
        val json = JSONObject(text)
        return SecaStatusDto(
            safeModeEnabled = json.optBoolean("safe_mode", true),
        )
    }

    private fun parseFinishResponse(text: String): GameFinishResponse {
        val json = JSONObject(text)

        val actionJson = json.optJSONObject("coach_action") ?: JSONObject()
        val coachAction =
            CoachActionDto(
                type = actionJson.optString("type", "NONE"),
                weakness = actionJson.optString("weakness").ifEmpty { null },
                reason = actionJson.optString("reason").ifEmpty { null },
            )

        val contentJson = json.optJSONObject("coach_content") ?: JSONObject()
        val payloadJson = contentJson.optJSONObject("payload") ?: JSONObject()
        val payload =
            buildMap<String, String> {
                payloadJson.keys().forEach { key -> put(key, payloadJson.opt(key)?.toString() ?: "") }
            }
        val coachContent =
            CoachContentDto(
                title = contentJson.optString("title", "Keep playing"),
                description = contentJson.optString("description", ""),
                payload = payload,
            )

        // Parse learning.status for P3-B surface
        val learningStatus = json.optJSONObject("learning")?.optString("status")?.ifEmpty { null }

        return GameFinishResponse(
            status = json.optString("status", "stored"),
            newRating = json.optDouble("new_rating", 0.0).toFloat(),
            confidence = json.optDouble("confidence", 0.0).toFloat(),
            coachAction = coachAction,
            coachContent = coachContent,
            learningStatus = learningStatus,
        )
    }

    private fun parseProgressResponse(text: String): PlayerProgressResponse {
        val json = JSONObject(text)

        // current
        val cur = json.optJSONObject("current") ?: JSONObject()
        val svJson = cur.optJSONObject("skill_vector") ?: JSONObject()
        val skillVector = buildMap<String, Float> {
            svJson.keys().forEach { k -> put(k, svJson.optDouble(k, 0.0).toFloat()) }
        }
        val current = ProgressCurrentDto(
            rating           = cur.optDouble("rating", 0.0).toFloat(),
            confidence       = cur.optDouble("confidence", 0.0).toFloat(),
            skillVector      = skillVector,
            tier             = cur.optString("tier", "intermediate"),
            teachingStyle    = cur.optString("teaching_style", "intermediate"),
            opponentElo      = cur.optInt("opponent_elo", 1200),
            explanationDepth = cur.optDouble("explanation_depth", 0.5).toFloat(),
            conceptComplexity = cur.optDouble("concept_complexity", 0.5).toFloat(),
        )

        // history
        val histArr = json.optJSONArray("history") ?: org.json.JSONArray()
        val history = (0 until histArr.length()).map { i ->
            val h = histArr.getJSONObject(i)
            val wJson = h.optJSONObject("weaknesses") ?: JSONObject()
            val weaknesses = buildMap<String, Float> {
                wJson.keys().forEach { k -> put(k, wJson.optDouble(k, 0.0).toFloat()) }
            }
            ProgressHistoryItem(
                gameId          = h.optString("game_id", ""),
                result          = h.optString("result", ""),
                accuracy        = h.optDouble("accuracy", 0.0).toFloat(),
                ratingAfter     = if (h.isNull("rating_after")) null
                                  else h.optDouble("rating_after").toFloat(),
                confidenceAfter = if (h.isNull("confidence_after")) null
                                  else h.optDouble("confidence_after").toFloat(),
                weaknesses      = weaknesses,
                createdAt       = h.optString("created_at", ""),
            )
        }

        // analysis
        val ana = json.optJSONObject("analysis") ?: JSONObject()
        val csJson = ana.optJSONObject("category_scores") ?: JSONObject()
        val categoryScores = buildMap<String, Float> {
            csJson.keys().forEach { k -> put(k, csJson.optDouble(k, 0.0).toFloat()) }
        }
        val prJson = ana.optJSONObject("phase_rates") ?: JSONObject()
        val phaseRates = buildMap<String, Float> {
            prJson.keys().forEach { k -> put(k, prJson.optDouble(k, 0.0).toFloat()) }
        }
        val recsArr = ana.optJSONArray("recommendations") ?: org.json.JSONArray()
        val recommendations = (0 until recsArr.length()).map { i ->
            val r = recsArr.getJSONObject(i)
            ProgressRecommendation(
                category  = r.optString("category", ""),
                priority  = r.optString("priority", "low"),
                rationale = r.optString("rationale", ""),
            )
        }
        val analysis = ProgressAnalysisDto(
            dominantCategory = ana.optString("dominant_category").ifEmpty { null },
            gamesAnalyzed    = ana.optInt("games_analyzed", 0),
            categoryScores   = categoryScores,
            phaseRates       = phaseRates,
            recommendations  = recommendations,
        )

        return PlayerProgressResponse(current = current, history = history, analysis = analysis)
    }
}
