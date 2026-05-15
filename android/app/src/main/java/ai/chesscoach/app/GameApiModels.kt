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
 * Backend contract: docs/API_CONTRACTS.md §18.
 */
@Serializable
data class CurriculumRecommendation(
    val topic: String = "",
    val difficulty: Float = 0.5f,
    @SerialName("exercise_type") val exerciseType: String = "",
    @Serializable(with = JsonAsStringMapSerializer::class)
    val payload: Map<String, String> = emptyMap(),
)

/** Request body for POST /curriculum/next. */
@Serializable
data class CurriculumNextRequest(
    @SerialName("player_id") val playerId: String,
)

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
    val result: String = "",
    val accuracy: Float = 0f,
    @SerialName("rating_after") val ratingAfter: Float? = null,
    @SerialName("created_at") val createdAt: String = "",
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
) {
    /**
     * Status string from the `learning` object in the /game/finish response
     * (e.g. "stored", "updated").  Null when the backend omitted the field
     * or sent an empty string.
     */
    val learningStatus: String?
        get() = learning?.status?.takeIf { it.isNotEmpty() }
}
