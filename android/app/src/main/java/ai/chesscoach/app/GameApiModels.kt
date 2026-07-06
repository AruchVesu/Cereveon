package ai.chesscoach.app

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

/**
 * Typed request/response models for the backend game endpoints.
 *
 * Sprint 4.3.C migrated these off hand-rolled ``org.json.JSONObject``
 * parsing onto kotlinx-serialization.  ``@SerialName`` annotations
 * preserve the snake_case wire format the FastAPI backend emits while
 * keeping the Kotlin properties camelCase.
 *
 * Defaults are intentionally permissive — they mirror what the
 * pre-Sprint-4.3.C ``opt*`` calls used so the deserialiser tolerates
 * older / partial payloads in the same way the old parser did.  The
 * ``ApiJson`` config also flips ``coerceInputValues = true`` so an
 * explicit ``null`` for a non-nullable field falls back to the default
 * rather than throwing.
 */

/**
 * Custom serializer for ``Map<String, String>`` fields that the
 * backend ships as a heterogeneous JSON object (mixed numbers, bools,
 * strings).  The pre-Sprint-4.3.C parser used
 * ``JSONObject.opt(key)?.toString()`` which coerced every value to its
 * string form; this serializer preserves that contract by lifting each
 * primitive into [JsonElement] first, then unwrapping ``JsonPrimitive``s
 * via ``content`` (so ``"win"`` decodes to ``win`` without surrounding
 * quotes) and falling back to ``toString()`` for nested objects /
 * arrays.
 *
 * Used by:
 *  - [CurriculumRecommendation.payload]
 *  - [CoachContentDto.payload]
 */
private object JsonAsStringMapSerializer : KSerializer<Map<String, String>> {
    private val delegate = MapSerializer(String.serializer(), JsonElement.serializer())
    override val descriptor = delegate.descriptor
    override fun serialize(encoder: Encoder, value: Map<String, String>) {
        delegate.serialize(encoder, value.mapValues { (_, v) -> JsonPrimitive(v) })
    }
    override fun deserialize(decoder: Decoder): Map<String, String> =
        delegate.deserialize(decoder).mapValues { (_, v) ->
            if (v is JsonPrimitive) v.content else v.toString()
        }
}

// ── /seca/status ─────────────────────────────────────────────────────────────

/**
 * Response from GET /seca/status.
 *
 * Open endpoint (no auth). Android reads this at cold-start to confirm that
 * the backend is running in SAFE_MODE before sending coaching requests.
 *
 * [safeModeEnabled]  True when SECA bandit/policy training is disabled.
 *
 * The backend previously also returned `bandit_enabled` and `version`.
 * Both were dropped for information-leak reduction: `bandit_enabled` was
 * redundant (`!safeModeEnabled`) and `version` had no client behavioural
 * use.  The Kotlin DTO mirrors the trimmed contract.
 */
@Serializable
data class SecaStatusDto(
    @SerialName("safe_mode") val safeModeEnabled: Boolean = true,
)

// ── /curriculum/next ─────────────────────────────────────────────────────────

/**
 * Training recommendation returned by POST /curriculum/next.
 *
 * Driven by the SECA brain using real per-player history.  This is the
 * authoritative training-recommendation surface; the legacy
 * GET /next-training/{player_id} endpoint + its companion
 * ``TrainingRecommendation`` DTO were retired in PR 26 (2026-05-15).
 *
 * ``difficulty`` is one of ``"easy" | "medium" | "hard"`` — the band
 * string emitted by ``CurriculumPolicy.choose_difficulty()`` on the
 * server.  Earlier revisions declared it as ``Float = 0.5f`` to align
 * with a draft contract that anticipated a 0..1 float, but the live
 * Python implementation has always shipped the band string; with
 * kotlinx-serialization 1.8.1 the type mismatch threw
 * ``JsonDecodingException`` at every call site
 * (``coerceInputValues = true`` covers null-for-non-null and unknown
 * enums but does NOT coerce string-where-number-expected).  The result
 * was that the Training tab silently surfaced "Training unavailable"
 * on every tap.
 *
 * Backend contract: docs/API_CONTRACTS.md §18.
 */
@Serializable
data class CurriculumRecommendation(
    val topic: String = "",
    val difficulty: String = "medium",
    @SerialName("exercise_type") val exerciseType: String = "",
    @Serializable(with = JsonAsStringMapSerializer::class)
    val payload: Map<String, String> = emptyMap(),
)

// CurriculumNextRequest retired in PR 27 (2026-05-15).  POST /curriculum/next
// is body-less now — the server derives the player from the JWT.  Pre-PR-27
// Android sent `{"player_id": ...}` which the server silently dropped
// (wire-noise flagged in the SECA-Android wiring audit § C-1).

// ── /game/history ─────────────────────────────────────────────────────────────

/**
 * Summary of a single completed game returned by GET /game/history.
 *
 * [result]      One of "win", "loss", "draw".
 * [accuracy]    Move accuracy 0.0–1.0 as recorded at the time of /game/finish.
 * [ratingAfter] Player rating after this game; null when no rating update was stored.
 * [createdAt]   ISO-8601 datetime string (e.g. "2026-03-21T14:05:00").
 */
@Serializable
data class GameHistoryItem(
    val id: String = "",
    // The live game id (games.id) this finished game maps to, used to load
    // its coaching chat via GET /chat/history?game_id=.  Null for legacy /
    // imported / pre-game_id rows (no per-game chat thread).  Server: #230.
    @SerialName("game_id") val gameId: String? = null,
    // SAN of the game's final move (e.g. "Qh7#"), for a history-row preview.
    // Null for moveless / legacy rows.  Server: GET /game/history.
    @SerialName("last_move") val lastMove: String? = null,
    // SAN of the winning side's final move, per the PGN Result header.
    // Differs from lastMove when the loser moved last.  Null for draws /
    // ongoing / moveless rows.  Server: GET /game/history.
    @SerialName("winner_move") val winnerMove: String? = null,
    val result: String = "",
    val accuracy: Float = 0f,
    @SerialName("rating_after") val ratingAfter: Float? = null,
    @SerialName("created_at") val createdAt: String = "",
    // Provenance: "lichess" for imported games, "app" for in-app games
    // (the server normalises legacy NULL-source rows to "app").  Drives
    // the history screen's source filter + the "LICHESS" row badge.
    // Defaults to "app" so a payload from a server predating the field
    // decodes as an in-app game rather than throwing.
    val source: String = "app",
)

/**
 * Wire shape for GET /game/{event_id}/positions — game-replay data.
 *
 * [positions] N+1 FENs: index 0 is the start, index i is the board AFTER ply i
 *             (positions.last() is the final position).
 * [moves]     N SANs: moves[i] produced positions[i+1] (for move-list labels).
 */
@Serializable
data class GamePositionsResponse(
    val positions: List<String> = emptyList(),
    val moves: List<String> = emptyList(),
    // Which side the player was on ("white" / "black"), for replay board
    // orientation.  Null for in-app games (always white) and legacy rows —
    // the review renders null as white (no flip).
    @SerialName("player_color") val playerColor: String? = null,
)

/**
 * Wire shape for GET /game/history.  The backend wraps the array in a
 * ``{"games": [...]}`` envelope; this wrapper lets the client decode
 * the envelope and the caller surface receives the bare list.
 */
@Serializable
internal data class GameHistoryResponse(
    val games: List<GameHistoryItem> = emptyList(),
)

// ── /player/progress ─────────────────────────────────────────────────────────

/**
 * Current player world-model snapshot from GET /player/progress.
 *
 * [rating]           Current Elo-style rating.
 * [confidence]       Confidence estimate [0.0–1.0].
 * [skillVector]      Aggregated weakness scores per skill category.
 * [tier]             Human-readable skill tier: "beginner" | "intermediate" | "advanced".
 * [teachingStyle]    Coach verbosity: "simple" | "intermediate" | "advanced".
 * [opponentElo]      Effective opponent strength the engine currently plays at.
 * [explanationDepth] Normalised pedagogy depth [0.0–1.0].
 * [conceptComplexity] Normalised concept complexity [0.0–1.0].
 */
@Serializable
data class ProgressCurrentDto(
    val rating: Float = 0f,
    val confidence: Float = 0f,
    @SerialName("skill_vector") val skillVector: Map<String, Float> = emptyMap(),
    val tier: String = "intermediate",
    @SerialName("teaching_style") val teachingStyle: String = "intermediate",
    @SerialName("opponent_elo") val opponentElo: Int = 1200,
    @SerialName("explanation_depth") val explanationDepth: Float = 0.5f,
    @SerialName("concept_complexity") val conceptComplexity: Float = 0.5f,
)

/**
 * Single game entry in the progress history from GET /player/progress.
 *
 * [weaknesses] per-phase mistake rates: keys "opening", "middlegame", "endgame".
 */
@Serializable
data class ProgressHistoryItem(
    @SerialName("game_id") val gameId: String = "",
    val result: String = "",
    val accuracy: Float = 0f,
    @SerialName("rating_after") val ratingAfter: Float? = null,
    @SerialName("confidence_after") val confidenceAfter: Float? = null,
    val weaknesses: Map<String, Float> = emptyMap(),
    @SerialName("created_at") val createdAt: String = "",
)

/** One training recommendation in the analysis block. */
@Serializable
data class ProgressRecommendation(
    val category: String = "",
    val priority: String = "low",   // "high" | "medium" | "low"
    val rationale: String = "",
)

/**
 * Analysis block in GET /player/progress — output of HistoricalAnalysisPipeline.
 *
 * [categoryScores]  Category → score [0.0–1.0].
 * [phaseRates]      Phase → mistake rate [0.0–1.0].
 */
@Serializable
data class ProgressAnalysisDto(
    @SerialName("dominant_category") val dominantCategory: String? = null,
    @SerialName("games_analyzed") val gamesAnalyzed: Int = 0,
    @SerialName("category_scores") val categoryScores: Map<String, Float> = emptyMap(),
    @SerialName("phase_rates") val phaseRates: Map<String, Float> = emptyMap(),
    val recommendations: List<ProgressRecommendation> = emptyList(),
)

/** Full response from GET /player/progress. */
@Serializable
data class PlayerProgressResponse(
    val current: ProgressCurrentDto = ProgressCurrentDto(),
    val history: List<ProgressHistoryItem> = emptyList(),
    val analysis: ProgressAnalysisDto = ProgressAnalysisDto(),
)

// ── /game/start ──────────────────────────────────────────────────────────────

/** Request body for POST /game/start. */
@Serializable
data class GameStartRequest(
    @SerialName("player_id") val playerId: String,
)

/** Response from POST /game/start. */
@Serializable
data class GameStartResponse(
    @SerialName("game_id") val gameId: String = "",
)

/**
 * Response from GET /game/active — the most recent unfinished
 * game's checkpoint for the authenticated player.  Null is returned
 * by the client when the server responds 404 (= "no resumable game").
 */
@Serializable
data class ActiveGameResponse(
    @SerialName("game_id") val gameId: String = "",
    @SerialName("current_fen") val currentFen: String = "",
    /** Comma-separated UCI moves; empty when no moves were made yet. */
    @SerialName("current_uci_history") val currentUciHistory: String = "",
)

/**
 * Body for POST /game/{gameId}/checkpoint.  The path carries the game
 * id; the body just snapshots the current FEN and UCI history.
 */
@Serializable
data class CheckpointRequest(
    val fen: String,
    @SerialName("uci_history") val uciHistory: String,
)

/**
 * One opening in the player's repertoire — wire shape of GET /repertoire.
 * Mirrors OpeningsActivity.OpeningEntry but lives in the API layer so
 * the client/UI conversion is explicit at the activity boundary.
 */
@Serializable
data class RepertoireOpeningDto(
    val eco: String = "",
    val name: String = "",
    val line: String = "",
    val mastery: Float = 0f,
    @SerialName("is_active") val isActive: Boolean = false,
    val ordinal: Int = 0,
)

/**
 * Wire shape for /repertoire endpoints.  The backend wraps the array
 * in ``{"openings": [...]}``; this wrapper lets the client decode the
 * envelope and the caller surface receives the bare list.
 */
@Serializable
internal data class RepertoireListResponse(
    val openings: List<RepertoireOpeningDto> = emptyList(),
)

/** Body for POST /repertoire (add opening). */
@Serializable
data class AddOpeningRequest(
    val eco: String,
    val name: String,
    val line: String,
    val mastery: Float,
)

/** Body for POST /repertoire/{eco}/drill-result. */
@Serializable
data class DrillResultRequest(
    val outcome: Float,
)

// ── /game/finish ─────────────────────────────────────────────────────────────

@Serializable
data class GameFinishRequest(
    val pgn: String,
    val result: String, // "win" | "loss" | "draw"
    val accuracy: Float, // 0..1
    val weaknesses: Map<String, Float> = emptyMap(),
    @SerialName("player_id") val playerId: String? = null,
    /**
     * Optional game_id captured from the corresponding /game/start
     * response.  When forwarded, the backend marks the matching `games`
     * row complete (result + finished_at columns) instead of leaving it
     * orphaned in NULL purgatory.  Null is accepted by the server for
     * backwards-compat with older clients that didn't track the id.
     *
     * The Resume flow reuses the same id across the original session
     * and the resumed-with-the-same-position one — that's how a
     * resumed game finishes against exactly one games row server-side.
     */
    @SerialName("game_id") val gameId: String? = null,
)

@Serializable
data class CoachActionDto(
    val type: String = "NONE",
    val weakness: String? = null,
    val reason: String? = null,
)

@Serializable
data class CoachContentDto(
    val title: String = "Keep playing",
    val description: String = "",
    @Serializable(with = JsonAsStringMapSerializer::class)
    val payload: Map<String, String> = emptyMap(),
)

/**
 * Nested ``learning`` block in the /game/finish response.
 *
 * Surfaced flat through [GameFinishResponse.learningStatus] so callers
 * keep a single-field accessor while the wire shape stays a nested
 * object.  An empty string from the backend collapses to null at the
 * accessor (pre-migration parity).
 */
@Serializable
data class LearningStatusDto(
    val status: String? = null,
)

/**
 * The first move in the game whose centipawn loss cleared the
 * server-side mistake threshold (150 cp).
 *
 * Surfaced on the /game/finish response (Phase 3) so the Android
 * client can show a "Replay your mistake" CTA on the post-game sheet
 * and launch ``MistakeReplayBottomSheet`` with the position + the
 * move the player originally played.  Server-side selection policy
 * is "first above threshold" rather than "largest loss" so the user
 * learns the originating mistake before its downstream cascade.
 *
 * Always present on the wire, but ``null`` when (a) the engine
 * recompute fell back to client values, or (b) no move clears the
 * server-side ``MIN_MISTAKE_LOSS_CP`` threshold (150 cp).  The
 * client just hides the CTA in those cases.
 *
 * The DTO is named ``BiggestMistakeDto`` and decodes the
 * ``biggest_mistake`` wire field for backward compatibility with
 * PR #192's original "biggest loss" picker; the selection semantics
 * flipped to "first above threshold" without breaking the wire
 * contract.  See ``docs/API_CONTRACTS.md`` §3.
 */
@Serializable
data class BiggestMistakeDto(
    /** FEN of the position the player was looking at, BEFORE the bad move. */
    val fen: String = "",
    /** UCI of the move the player actually played at that position. */
    @SerialName("played_move") val playedMove: String = "",
    /** 1-indexed Nth player half-move.  Used in the replay sheet header copy. */
    @SerialName("move_number") val moveNumber: Int = 0,
    /** Centipawn loss this single move cost the player. Always >= 150 when populated. */
    @SerialName("eval_loss_cp") val evalLossCp: Int = 0,
    /**
     * Opaque identifier to forward to POST /training/solve as
     * ``source_ref`` on a verified-correct replay.  The server
     * constructs it as ``event_<event_id>:move_<n>`` so the
     * ``(player, source_type, source_ref)`` dedup triple stays
     * stable across retries.
     */
    @SerialName("source_ref") val sourceRef: String = "",
)

@Serializable
data class GameFinishResponse(
    val status: String = "stored",
    @SerialName("new_rating") val newRating: Float = 0f,
    val confidence: Float = 0f,
    @SerialName("coach_action") val coachAction: CoachActionDto = CoachActionDto(),
    @SerialName("coach_content") val coachContent: CoachContentDto = CoachContentDto(),
    /**
     * Backing for [learningStatus].  Deserialised from the nested
     * ``{"learning": {"status": ...}}`` block of the /game/finish
     * response; callers read the flat [learningStatus] accessor below.
     */
    val learning: LearningStatusDto? = null,
    /**
     * Phase 3 mistake-replay payload.  ``null`` (the default) means
     * either no mistake worth replaying or the accuracy recompute
     * fell back to client values; the Android client hides the
     * "Replay your mistake" CTA in both cases.
     */
    @SerialName("biggest_mistake") val biggestMistake: BiggestMistakeDto? = null,
) {
    /**
     * Status string from the `learning` object in the /game/finish response
     * (e.g. "stored", "updated").  Null when the backend omitted the field
     * or sent an empty string.
     */
    val learningStatus: String?
        get() = learning?.status?.takeIf { it.isNotEmpty() }
}


// ── /training/verify-replay ──────────────────────────────────────────────────


/**
 * Request body for POST /training/verify-replay.
 *
 * The Android replay sheet sends this after the user submits a move
 * on the embedded ChessBoardView.  The server runs Stockfish, checks
 * whether the move is within 30 cp of the engine's best, and returns
 * the verdict.  Only on ``isCorrect=true`` does the client follow up
 * with POST /training/solve to actually credit XP.
 */
@Serializable
data class VerifyReplayRequest(
    val fen: String,
    @SerialName("move_uci") val moveUci: String,
)


/**
 * Response from POST /training/verify-replay.
 *
 * [isCorrect] = true means the user's move gave up at most 30 cp vs
 * the engine's best move; false means "try again, this one was too
 * loose".  [engineBestUci] is always populated so the UI can offer a
 * "Show me the engine's move" peek without a second round-trip.
 * [evalLossCp] is signed (player POV) — positive when the user's
 * move was worse than the engine's.
 */
@Serializable
data class VerifyReplayResponse(
    @SerialName("is_correct") val isCorrect: Boolean = false,
    @SerialName("engine_best_uci") val engineBestUci: String = "",
    @SerialName("eval_loss_cp") val evalLossCp: Int = 0,
)


// ── /training/solve ──────────────────────────────────────────────────────────


/**
 * Request body for POST /training/solve (Phase 2 endpoint).
 *
 * The Android replay sheet posts this on a verified-correct attempt
 * with ``sourceType = "mistake_replay"`` and ``sourceRef`` copied
 * from [BiggestMistakeDto.sourceRef] so the server-side
 * ``(player, source_type, source_ref)`` dedup triple stays stable.
 */
@Serializable
data class TrainingSolveRequest(
    @SerialName("source_type") val sourceType: String,
    @SerialName("source_ref") val sourceRef: String? = null,
)


/**
 * Response from POST /training/solve.
 *
 * [xpAwarded] = 0 indicates a dedup hit (the client retried the same
 * solve) — the client should NOT toast "+10 XP" in that case, but
 * may still update [trainingXp] in PREF_TRAINING_XP since the server
 * value is authoritative.
 */
@Serializable
data class TrainingSolveResponse(
    @SerialName("xp_awarded") val xpAwarded: Int = 0,
    @SerialName("training_xp") val trainingXp: Int = 0,
    @SerialName("completed_at") val completedAt: String = "",
)


// ── /coach/plan/today ────────────────────────────────────────────────────────


/**
 * Today's due puzzle from the per-mistake study plan.
 *
 * Nullable inside [CoachPlanResponse] because a plan can be active
 * but have no puzzle currently due (e.g. day-0 has been completed
 * and day-3 isn't due for another two days).  Android renders
 * "Next drill in N days" copy in that case.
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §34.
 */
@Serializable
data class TodayPuzzleDto(
    /** One of 0, 3, 7 — which step of the spaced-repetition program. */
    @SerialName("day_offset") val dayOffset: Int = 0,
    /** Position the puzzle drops the user into. */
    val fen: String = "",
    /** Engine's preferred move at [fen] (UCI), used by the verifier. */
    @SerialName("expected_move_uci") val expectedMoveUci: String = "",
    /** ``"original"`` for day-0 (the player's actual mistake) or
     *  ``"library"`` for theme-matched corpus variants. */
    @SerialName("source_type") val sourceType: String = "",
    /** ISO-8601 UTC timestamp; invariant: ``due_at <= now()`` when this
     *  object is non-null. */
    @SerialName("due_at") val dueAt: String = "",
)


/**
 * One day-slot in the week-overview schedule (`days[]`).
 *
 * Powers the week-overview screen: each day is [completed] (done),
 * [isDue] (available to start now), or neither (locked behind its
 * [dueAt]).  Unlike [TodayPuzzleDto] this carries no FEN / expected
 * move — the playable position comes from [CoachPlanResponse.todayPuzzle].
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §34.
 */
@Serializable
data class PlanDayDto(
    /** One of 0, 3, 7. */
    @SerialName("day_offset") val dayOffset: Int = 0,
    /** ISO-8601 UTC timestamp of when this day unlocks. */
    @SerialName("due_at") val dueAt: String = "",
    /** True once the day's puzzle has been solved. */
    val completed: Boolean = false,
    /** True when available now — ``due_at <= now()`` AND not completed. */
    @SerialName("is_due") val isDue: Boolean = false,
    /** ``"original"`` (the player's actual mistake) or ``"library"``. */
    @SerialName("source_type") val sourceType: String = "",
)


/**
 * Top-level shape of ``GET /coach/plan/today`` when the player has an
 * active per-mistake study plan.  Also the response of
 * ``POST /coach/plan/puzzle/complete``.
 *
 * The endpoint also returns JSON ``null`` (HTTP 200) when no active
 * plan exists; the Android client decodes that to a Kotlin ``null``
 * via the [GameApiClient.getCoachPlanToday] parse step.
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §34 / §35.
 */
@Serializable
data class CoachPlanResponse(
    @SerialName("plan_id") val planId: String = "",
    /** One of [llm.seca.coach.study_plan.verdict.THEME_VOCABULARY]
     *  on the server side; the Android client treats it as opaque
     *  and renders it via [formatTheme] for display. */
    val theme: String = "generic",
    /** LLM-written ≤ 60-word retrospective.  Empty string when the
     *  LLM was unreachable or failed validators — TodaysDrillCard
     *  hides the verdict line in that case. */
    val verdict: String = "",
    /** The aggregate dominant weakness the week is built around — one of
     *  opening_preparation / tactical_vision / positional_play /
     *  endgame_technique, or ``null`` for legacy plans / too little
     *  history.  Rendered as the week's focus in the overview screen. */
    @SerialName("anchor_category") val anchorCategory: String? = null,
    /** Plan lifecycle: ``"active"`` while in progress, ``"completed"``
     *  once every day is solved.  GET returns only active plans; the
     *  completion endpoint returns the freshly-completed plan so the
     *  client can show the week-complete state. */
    val status: String = "active",
    /** Always 3 today; surfaced for "Day N of M" rendering. */
    @SerialName("total_days") val totalDays: Int = 3,
    /** ``null`` when no puzzle's ``due_at`` has elapsed yet (e.g.
     *  day-0 solved, day-3 not yet due). */
    @SerialName("today_puzzle") val todayPuzzle: TodayPuzzleDto? = null,
    /** The full week schedule, ordered by day_offset (always
     *  [totalDays] entries).  Empty only when decoding an older
     *  server response that predates the field. */
    val days: List<PlanDayDto> = emptyList(),
)


/**
 * Body for ``POST /coach/plan/puzzle/complete`` — mark one day's puzzle
 * solved and advance the plan.  Sent after a verified-correct solve
 * (verify-replay → training/solve).
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §35.
 */
@Serializable
data class CompletePuzzleRequest(
    @SerialName("plan_id") val planId: String,
    @SerialName("day_offset") val dayOffset: Int,
)
