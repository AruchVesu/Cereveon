package ai.chesscoach.app

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
data class SecaStatusDto(
    val safeModeEnabled: Boolean,
)

// ── /curriculum/next ─────────────────────────────────────────────────────────

/**
 * Training recommendation returned by POST /curriculum/next.
 *
 * Driven by the SECA brain using real per-player history — more accurate than
 * [TrainingRecommendation] from /next-training which uses hardcoded demo weights.
 *
 * **Schema note:** field names differ from [TrainingRecommendation]:
 *  - [exerciseType] (not `format`) — exercise type string
 *  - [payload] (not `expectedGain`) — type-specific parameters dict
 *
 * Clients MUST NOT conflate this type with [TrainingRecommendation].
 *
 * Backend contract: docs/API_CONTRACTS.md §2 (schema conflict note).
 */
data class CurriculumRecommendation(
    val topic: String,
    val difficulty: Float,
    val exerciseType: String,
    val payload: Map<String, String> = emptyMap(),
)

// ── /next-training/{player_id} ───────────────────────────────────────────────

/**
 * Training recommendation returned by GET /next-training/{player_id}.
 *
 * Schema matches the backend contract documented in docs/API_CONTRACTS.md §2.
 * Fields correspond 1-to-1 with the JSON keys: topic, difficulty, format,
 * expected_gain.
 *
 * [topic]       Training topic (e.g. "tactics", "endgame", "general_play").
 * [difficulty]  Difficulty in the range 0.0–1.0.
 * [format]      Training format ("puzzle", "drill", "game", "explanation").
 * [expectedGain] Estimated rating gain from completing the recommended task.
 */
data class TrainingRecommendation(
    val topic: String,
    val difficulty: Float,
    val format: String,
    val expectedGain: Float,
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
data class GameHistoryItem(
    val id: String,
    val result: String,
    val accuracy: Float,
    val ratingAfter: Float?,
    val createdAt: String,
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
data class ProgressCurrentDto(
    val rating: Float,
    val confidence: Float,
    val skillVector: Map<String, Float>,
    val tier: String,
    val teachingStyle: String,
    val opponentElo: Int,
    val explanationDepth: Float,
    val conceptComplexity: Float,
)

/**
 * Single game entry in the progress history from GET /player/progress.
 *
 * [weaknesses] per-phase mistake rates: keys "opening", "middlegame", "endgame".
 */
data class ProgressHistoryItem(
    val gameId: String,
    val result: String,
    val accuracy: Float,
    val ratingAfter: Float?,
    val confidenceAfter: Float?,
    val weaknesses: Map<String, Float>,
    val createdAt: String,
)

/** One training recommendation in the analysis block. */
data class ProgressRecommendation(
    val category: String,
    val priority: String,   // "high" | "medium" | "low"
    val rationale: String,
)

/**
 * Analysis block in GET /player/progress — output of HistoricalAnalysisPipeline.
 *
 * [categoryScores]  Category → score [0.0–1.0].
 * [phaseRates]      Phase → mistake rate [0.0–1.0].
 */
data class ProgressAnalysisDto(
    val dominantCategory: String?,
    val gamesAnalyzed: Int,
    val categoryScores: Map<String, Float>,
    val phaseRates: Map<String, Float>,
    val recommendations: List<ProgressRecommendation>,
)

/** Full response from GET /player/progress. */
data class PlayerProgressResponse(
    val current: ProgressCurrentDto,
    val history: List<ProgressHistoryItem>,
    val analysis: ProgressAnalysisDto,
)

// ── /game/start ──────────────────────────────────────────────────────────────

data class GameStartRequest(val playerId: String)

data class GameStartResponse(val gameId: String)

/**
 * Response from GET /game/active — the most recent unfinished
 * game's checkpoint for the authenticated player.  Null is returned
 * by the client when the server responds 404 (= "no resumable game").
 */
data class ActiveGameResponse(
    val gameId: String,
    val currentFen: String,
    /** Comma-separated UCI moves; empty when no moves were made yet. */
    val currentUciHistory: String,
)

/**
 * One opening in the player's repertoire — wire shape of GET /repertoire.
 * Mirrors OpeningsActivity.OpeningEntry but lives in the API layer so
 * the client/UI conversion is explicit at the activity boundary.
 */
data class RepertoireOpeningDto(
    val eco: String,
    val name: String,
    val line: String,
    val mastery: Float,
    val isActive: Boolean,
    val ordinal: Int,
)

// ── /game/finish ─────────────────────────────────────────────────────────────

data class GameFinishRequest(
    val pgn: String,
    val result: String, // "win" | "loss" | "draw"
    val accuracy: Float, // 0..1
    val weaknesses: Map<String, Float> = emptyMap(),
    val playerId: String? = null,
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
    val gameId: String? = null,
)

data class CoachActionDto(
    val type: String,
    val weakness: String?,
    val reason: String?,
)

data class CoachContentDto(
    val title: String,
    val description: String,
    val payload: Map<String, String> = emptyMap(),
)

data class GameFinishResponse(
    val status: String,
    val newRating: Float,
    val confidence: Float,
    val coachAction: CoachActionDto,
    val coachContent: CoachContentDto,
    /**
     * Status string from the `learning` object in the /game/finish response
     * (e.g. "stored", "updated").  Null when the backend omitted the field.
     */
    val learningStatus: String? = null,
)
