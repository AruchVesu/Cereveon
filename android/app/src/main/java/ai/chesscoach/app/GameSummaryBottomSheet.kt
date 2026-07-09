package ai.chesscoach.app

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.encodeToString

/**
 * Bottom sheet shown after a game ends.
 *
 * Displays:
 *  - New rating and confidence progress bar (Gap 6)
 *  - Coach action type badge (Gap 2)
 *  - Coach content title and description (Gap 2)
 *  - Inline training recommendation card from GET /next-training (Gap 3)
 *
 * Arguments are passed via [newInstance]; see [ARG_*] constants.
 */
class GameSummaryBottomSheet : BottomSheetDialogFragment() {

    companion object {
        private const val ARG_RATING          = "rating"
        private const val ARG_CONFIDENCE      = "confidence"
        private const val ARG_ACTION_TYPE     = "action_type"
        private const val ARG_TITLE           = "title"
        private const val ARG_DESCRIPTION     = "description"
        private const val ARG_PLAYER_ID       = "player_id"
        private const val ARG_PAYLOAD_JSON    = "payload_json"
        private const val ARG_LEARNING_STATUS = "learning_status"
        // Atrium hero card — populated by the activity from local
        // game-state, not from the /game/finish response (which does
        // not currently carry result / move-count fields).
        private const val ARG_RESULT          = "result"
        private const val ARG_MOVE_COUNT      = "move_count"
        // Coach decision passed-through for downstream persistence —
        // see ProgressDashboardBottomSheet's "Coach's plan" section.
        private const val ARG_COACH_WEAKNESS  = "coach_weakness"
        private const val ARG_COACH_REASON    = "coach_reason"
        // Phase 3 — biggest_mistake (the mistake-replay payload) serialised
        // as a JSON string so the bundle stays primitive-only.  Optional;
        // absent / blank means the response didn't carry the field.
        private const val ARG_BIGGEST_MISTAKE_JSON = "biggest_mistake_json"

        const val PREFS_NAME  = MainActivity.PREFS_NAME
        const val PREF_RATING = MainActivity.PREF_RATING

        /**
         * Serializer for the ``payload`` map transported through the
         * fragment's [Bundle].  Pinned to ``Map<String, String>`` because
         * the JsonAsStringMapSerializer in [GameApiModels] has already
         * stringified every value by the time it reaches this fragment,
         * so the bundle blob can stay a plain string-map.
         */
        private val PAYLOAD_SERIALIZER =
            MapSerializer(String.serializer(), String.serializer())

        fun newInstance(
            response: GameFinishResponse,
            playerId: String,
            result: GameResult? = null,
            moveCount: Int = 0,
        ): GameSummaryBottomSheet = GameSummaryBottomSheet().apply {
            // Serialise payload map to JSON string for bundle transport
            val payloadJson = ApiJson.encodeToString(
                PAYLOAD_SERIALIZER,
                response.coachContent.payload,
            )
            arguments = Bundle().apply {
                putFloat(ARG_RATING,      response.newRating)
                putFloat(ARG_CONFIDENCE,  response.confidence)
                putString(ARG_ACTION_TYPE, response.coachAction.type)
                putString(ARG_TITLE,       response.coachContent.title)
                putString(ARG_DESCRIPTION, response.coachContent.description)
                putString(ARG_PLAYER_ID,   playerId)
                putString(ARG_PAYLOAD_JSON, payloadJson)
                // Pass coach_action.weakness / coach_action.reason through
                // for the dashboard persistence step in onViewCreated.
                // Not used directly by the in-sheet UI today (the title /
                // description carry the user-visible coach copy), but
                // needed downstream by ProgressDashboardBottomSheet to
                // render the "Coach's plan" section between games.
                response.coachAction.weakness?.let { putString(ARG_COACH_WEAKNESS, it) }
                response.coachAction.reason?.let   { putString(ARG_COACH_REASON, it) }
                response.learningStatus?.let { putString(ARG_LEARNING_STATUS, it) }
                result?.let { putString(ARG_RESULT, it.name) }
                if (moveCount > 0) putInt(ARG_MOVE_COUNT, moveCount)
                // Phase 3 — pass the biggest_mistake DTO through as a
                // JSON string so the bundle stays primitive-only.
                // Decoded back into a [BiggestMistakeDto] in
                // onViewCreated when the card is wired up.  Encoding
                // wrapped defensively: a kotlinx-serialization
                // hiccup must never block the post-game sheet from
                // appearing.  If the encode throws, the bundle just
                // doesn't carry the field and the sheet renders
                // without the replay CTA.
                try {
                    response.biggestMistake?.let {
                        putString(ARG_BIGGEST_MISTAKE_JSON, ApiJson.encodeToString(it))
                    }
                } catch (e: Throwable) {
                    android.util.Log.w(
                        "MISTAKE_REPLAY",
                        "Failed to encode biggest_mistake; sheet will show without replay CTA",
                        e,
                    )
                }
            }
        }

        /**
         * Atrium hero result label — italic display text shown with a
         * cyan halo on the game-end summary card.
         *
         * Maps the game-engine [GameResult] to the design's W/L/D copy:
         *   WHITE_WINS → "Won · 1–0"
         *   BLACK_WINS → "Lost · 0–1"
         *   DRAW       → "Drew · ½–½"
         *
         * The half-symbol uses the canonical Unicode U+00BD; both
         * scoresheet halves separated by an en-dash, per the handoff.
         */
        fun formatHeroResult(result: GameResult?): String = when (result) {
            GameResult.WHITE_WINS -> "Won · 1–0"
            GameResult.BLACK_WINS -> "Lost · 0–1"
            GameResult.DRAW       -> "Drew · ½–½"
            null                  -> "—"
        }

        /**
         * Atrium hero subline — mono-cyan caps row beneath the result
         * label.  Currently shows just the move count; once the
         * /game/finish response carries duration and termination
         * reason we'll extend to "{N} MOVES · {duration} · {reason}"
         * to match the handoff design ("38 MOVES · 27:41 · OPPONENT
         * RESIGNED").
         *
         * Returns null when [moveCount] <= 0 so the activity can hide
         * the row instead of rendering "0 MOVES".
         */
        fun formatHeroSubline(moveCount: Int): String? =
            if (moveCount > 0) "$moveCount Moves" else null

        // ── Pure helper functions — testable without Android framework ────────

        /**
         * Format confidence 0.0–1.0 as a bare percentage ("72%").
         * The Atrium ACCURACY cell carries its own "ACCURACY" kicker
         * underneath, so the value need not include the label.
         */
        fun formatConfidence(confidence: Float): String =
            "%.0f%%".format(confidence * 100f)

        /** Convert confidence 0.0–1.0 to ProgressBar integer (0–100). */
        fun confidenceProgress(confidence: Float): Int =
            (confidence.coerceIn(0f, 1f) * 100f).toInt()

        /**
         * Map a coach action type string to a display badge label.
         * Unknown types fall back to "COACH".
         */
        fun actionBadgeLabel(type: String): String = when (type.uppercase()) {
            "DRILL"       -> "DRILL"
            "PUZZLE"      -> "PUZZLE"
            "REFLECT"     -> "REFLECT"
            "PLAN_UPDATE" -> "PLAN"
            "REST"        -> "REST"
            "CELEBRATE"   -> "CELEBRATE"
            else          -> "COACH"
        }

        /** Format a training topic string as "Topic: Endgame technique". */
        fun formatTopic(topic: String): String =
            "Topic: ${topic.replaceFirstChar { it.uppercase() }.replace('_', ' ')}"

        /**
         * Phase 3 mistake-replay card subline.
         * "Move 14 — find a stronger move (lost 240 cp)."
         */
        fun formatMistakeSummary(moveNumber: Int, evalLossCp: Int): String =
            "Move $moveNumber — find a stronger move (lost $evalLossCp cp)."

        // ``formatFormat`` and ``formatGain`` retired in PR 26 (2026-05-15)
        // alongside the /next-training/{player_id} fallback path that was
        // their sole caller.  ``CurriculumRecommendation`` (the surviving
        // shape from /curriculum/next) uses ``exerciseType`` + ``difficulty``
        // directly, formatted inline at the call site.

        // difficultyProgress(Float) retired with the Float-based difficulty
        // contract in 2026-05-25.  The String-band helpers below moved
        // here from TrainingSessionBottomSheet.Companion when the
        // standalone Lessons surface was removed — the post-game
        // training card is their only remaining caller.

        /**
         * Format a difficulty band string as "Difficulty: Medium".
         *
         * ``CurriculumPolicy.choose_difficulty`` on the server returns one of
         * ``"easy" | "medium" | "hard"``; anything else falls through to the
         * raw string (capitalised) so a future band ("expert", "novice") still
         * renders sensibly without a code change here.
         */
        fun formatDifficulty(difficulty: String): String =
            "Difficulty: ${difficulty.replaceFirstChar { it.uppercase() }}"

        /**
         * Map a difficulty band string to a ProgressBar integer (0–100).
         *
         * The progress bar is a visual cue, not a quantitative scale — easy
         * sits at 30, medium at 60, hard at 85, and any unknown band lands at
         * the 50 midpoint so the bar still renders.
         */
        fun difficultyProgress(difficulty: String): Int = when (difficulty.lowercase()) {
            "easy"   -> 30
            "medium" -> 60
            "hard"   -> 85
            else     -> 50
        }

        /**
         * Map a raw [learningStatus] string to a user-visible indicator label.
         *
         * Currently every status value resolves to "✓ Progress saved".  The
         * server hard-codes `learning_result = {"status": "safe_mode"}` on
         * every `/game/finish` response (see `llm/seca/events/router.py` —
         * the pre-PR-20 `else` branch was unreachable and got retired), so
         * the only status the Android client has ever seen in production is
         * `safe_mode`.  Earlier copy split that case off as
         * "⏸ Tracking paused", which read to users as a transient outage —
         * but their game IS saved (events table), their rating IS updated
         * (Player.rating), their accuracy IS measured (engine recompute),
         * and their coaching profile IS updated (SkillUpdater).  What's
         * actually "paused" is the bandit's online-learning loop, which is
         * permanently off in production by Project Rule 3 / SAFE_MODE — a
         * detail invisible to the user.
         *
         * The when-block scaffold is kept so a future non-safe-mode
         * deployment (research / staging with `SECA_SAFE_MODE=false`) can
         * branch the label without re-introducing the misleading "paused"
         * wording on the prod path.
         */
        fun learningStatusLabel(status: String): String = when (status.lowercase()) {
            "safe_mode" -> "✓ Progress saved"
            else        -> "✓ Progress saved"
        }
    }

    /** Injected in [newInstance] path; set by [MainActivity] before showing. */
    var gameApiClient: GameApiClient? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_game_summary, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val args = requireArguments()
        val rating          = args.getFloat(ARG_RATING)
        val confidence      = args.getFloat(ARG_CONFIDENCE)
        val actionType      = args.getString(ARG_ACTION_TYPE, "")
        val title           = args.getString(ARG_TITLE, "")
        val description     = args.getString(ARG_DESCRIPTION, "")
        val playerId        = args.getString(ARG_PLAYER_ID, "demo")
        val payloadJsonStr  = args.getString(ARG_PAYLOAD_JSON, "{}")
        val learningStatus  = args.getString(ARG_LEARNING_STATUS)
        val resultName      = args.getString(ARG_RESULT)
        val resultEnum: GameResult? = resultName?.let { runCatching { GameResult.valueOf(it) }.getOrNull() }
        val moveCount       = args.getInt(ARG_MOVE_COUNT, 0)

        // ── Bind views ────────────────────────────────────────────────────────
        // (txtNewRating retired alongside the rest of the user-visible
        // Elo surfaces; ``rating`` is still unpacked below for the
        // PREF_RATING write-back that powers adaptive opponent matching
        // internally.)
        view.findViewById<TextView>(R.id.txtActionBadge).text    = actionBadgeLabel(actionType)
        view.findViewById<TextView>(R.id.txtCoachTitle).text     = title.ifBlank { "Game Over" }
        view.findViewById<TextView>(R.id.txtCoachDescription).text = description

        // Atrium hero card — italic result label with cyan halo +
        // optional mono subline.  Activity passes resultEnum + moveCount
        // via the bundle; both fall back gracefully when the host
        // doesn't supply them (placeholder "—" / hidden subline).
        view.findViewById<TextView>(R.id.heroResult).text = formatHeroResult(resultEnum)
        val heroSub = view.findViewById<TextView>(R.id.heroSubline)
        formatHeroSubline(moveCount)?.let {
            heroSub.text = it
            heroSub.visibility = View.VISIBLE
        } ?: run {
            heroSub.visibility = View.GONE
        }

        val progressBar = view.findViewById<ProgressBar>(R.id.progressConfidence)
        progressBar.progress = confidenceProgress(confidence)
        view.findViewById<TextView>(R.id.txtConfidenceLabel).text = formatConfidence(confidence)

        // ── P3-B: learning status indicator ───────────────────────────────────
        val txtLearningStatus = view.findViewById<TextView>(R.id.txtLearningStatus)
        if (!learningStatus.isNullOrEmpty()) {
            txtLearningStatus.text = learningStatusLabel(learningStatus)
            txtLearningStatus.visibility = View.VISIBLE
        }

        // ── Phase 3: mistake-replay card ──────────────────────────────────────
        // When the /game/finish response carried a non-null
        // ``biggest_mistake``, surface a "Replay your mistake" CTA above
        // the curriculum training card.  Tap → launches
        // MistakeReplayBottomSheet preloaded with the first-mistake
        // position (the player's first move whose centipawn loss
        // cleared the server-side 150 cp threshold) and the move the
        // user actually played.  Falls back gracefully (card stays
        // gone, sheet doesn't launch) when the JSON arg is missing or
        // malformed — neither path 500s the post-game flow.
        try {
            wireMistakeReplayCard(view, args.getString(ARG_BIGGEST_MISTAKE_JSON))
        } catch (e: Throwable) {
            android.util.Log.w(
                "MISTAKE_REPLAY",
                "wireMistakeReplayCard failed; card will stay hidden",
                e,
            )
        }

        // ── P3-A: payload detail section (DRILL / PUZZLE only) ────────────────
        val layoutPayload = view.findViewById<LinearLayout>(R.id.layoutPayload)
        val upperType = actionType.uppercase()
        if (upperType == "DRILL" || upperType == "PUZZLE") {
            try {
                val payload = ApiJson.decodeFromString(
                    PAYLOAD_SERIALIZER,
                    payloadJsonStr ?: "{}",
                )
                if (payload.isNotEmpty()) {
                    payload.forEach { (key, value) ->
                        val tv = TextView(requireContext()).apply {
                            text = "$key: $value"
                            setTextColor(0xFFCCCCCC.toInt())
                            textSize = 12f
                        }
                        layoutPayload.addView(tv)
                    }
                    layoutPayload.visibility = View.VISIBLE
                }
            } catch (_: Exception) { /* malformed JSON — skip silently */ }
        }

        // ── Persist rating + confidence to SharedPreferences (Gap 6 / P3-A) ──
        // Also persist the coach decision so ProgressDashboardBottomSheet
        // can render a "Coach's plan" section between games — without
        // this, the action verdict / weakness / reason / coach copy are
        // visible only during the transient post-game sheet and lost the
        // moment the user dismisses it (the gap the user surfaced
        // 2026-05-16: "I see training focus but we actually need to see
        // full information that the coach provides").
        val coachWeakness = args.getString(ARG_COACH_WEAKNESS)
        val coachReason   = args.getString(ARG_COACH_REASON)
        requireContext()
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putFloat(PREF_RATING, rating)
            .putFloat(MainActivity.PREF_CONFIDENCE, confidence)
            .putString(MainActivity.PREF_LAST_COACH_ACTION_TYPE, actionType)
            .putString(MainActivity.PREF_LAST_COACH_WEAKNESS, coachWeakness ?: "")
            .putString(MainActivity.PREF_LAST_COACH_REASON, coachReason ?: "")
            .putString(MainActivity.PREF_LAST_COACH_TITLE, title)
            .putString(MainActivity.PREF_LAST_COACH_DESCRIPTION, description)
            .apply()

        // ── Fetch training recommendation from /curriculum/next ────────────────
        // PR 26 (2026-05-15) retired the /next-training/{player_id} fallback;
        // it was a placeholder with hardcoded "demo weaknesses".  When
        // /curriculum/next fails (auth, network, server outage), the UI
        // surfaces the empty training-card state.
        // R.id.trainingCard is declared as <ai.chesscoach.app.AtriumCardView> in
        // bottom_sheet_game_summary.xml (Atrium design-system migration).  An
        // earlier version of this file cast to LinearLayout and crashed the
        // post-game summary with ClassCastException (caught on-device
        // 2026-05-15) — the user got bounced back to HomeActivity because
        // BottomSheetDialogFragment died before it could render.
        val trainingCard  = view.findViewById<AtriumCardView>(R.id.trainingCard)
        val trainingEmpty = view.findViewById<TextView>(R.id.txtTrainingEmpty)
        val client = gameApiClient
        if (client != null) {
            lifecycleScope.launch {
                val curriculumResult = client.getNextCurriculum()
                if (curriculumResult is ApiResult.Success) {
                    val rec = curriculumResult.data

                    // Persist for the MainActivity chip so the recommendation
                    // survives sheet dismissal.  PREF_CURRICULUM_DIFFICULTY is
                    // intentionally NOT written here — the band string never
                    // flowed into a read site (the drawer chip only renders
                    // topic + exerciseType), so the prior putFloat call was
                    // dead.  Writing a String now would collide with old
                    // installs that already have a Float at the same key
                    // (ClassCastException on read).  The key constant survives
                    // in MainActivity.Companion so the cache-key test passes
                    // unchanged.
                    requireContext()
                        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                        .edit()
                        .putString(MainActivity.PREF_CURRICULUM_TOPIC, rec.topic)
                        .putString(MainActivity.PREF_CURRICULUM_EXERCISE_TYPE, rec.exerciseType)
                        .apply()

                    view.findViewById<TextView>(R.id.txtTrainingTopic).text  = formatTopic(rec.topic)
                    view.findViewById<TextView>(R.id.txtTrainingFormat).text =
                        "Format: ${rec.exerciseType.replaceFirstChar { it.uppercase() }}"
                    // /curriculum/next has no expected_gain; show difficulty
                    // band instead.  Source of truth is the label text — the
                    // ProgressBar is a visual cue derived from a fixed
                    // band → percent map (easy=30 / medium=60 / hard=85).
                    view.findViewById<TextView>(R.id.txtTrainingGain).text =
                        formatDifficulty(rec.difficulty)
                    view.findViewById<ProgressBar>(R.id.progressDifficulty).progress =
                        difficultyProgress(rec.difficulty)
                    trainingCard.visibility  = View.VISIBLE
                    trainingEmpty.visibility = View.GONE
                } else {
                    trainingCard.visibility  = View.GONE
                    trainingEmpty.visibility = View.VISIBLE
                }
            }
        }

        // ── Start training button ─────────────────────────────────────────────
        view.findViewById<Button>(R.id.btnStartTraining).setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            // Open ChatBottomSheet with a training seed prompt
            val fen = "startpos"
            ChatBottomSheet
                .newInstance(fen, null, null, 0)
                .show(parentFragmentManager, "ChatBottomSheet")
            dismiss()
        }
    }

    /**
     * Show the mistake-replay card iff [biggestMistakeJson] decodes to
     * a usable [BiggestMistakeDto].  Card stays gone (the default
     * layout state) on null / blank / malformed input — the rest of
     * the sheet renders normally either way.
     */
    private fun wireMistakeReplayCard(view: View, biggestMistakeJson: String?) {
        val card = view.findViewById<AtriumCardView>(R.id.mistakeReplayCard)
        if (biggestMistakeJson.isNullOrBlank()) {
            card.visibility = View.GONE
            return
        }
        val mistake = try {
            ApiJson.decodeFromString<BiggestMistakeDto>(biggestMistakeJson)
        } catch (_: Exception) {
            // Malformed payload — the activity should never see it
            // (the JSON came from this same process's encode), but be
            // defensive so a wire-shape drift can't 500 the sheet.
            card.visibility = View.GONE
            return
        }
        // FEN is the load-bearing field; a missing FEN means the
        // detector returned None and the wire field shouldn't have
        // been set.  Treat blank FEN as "no mistake" to mirror the
        // server-side semantics.
        if (mistake.fen.isBlank()) {
            card.visibility = View.GONE
            return
        }

        view.findViewById<TextView>(R.id.txtMistakeReplaySummary).text =
            formatMistakeSummary(mistake.moveNumber, mistake.evalLossCp)
        card.visibility = View.VISIBLE
        view.findViewById<Button>(R.id.btnReplayMistake).setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            val sheet = MistakeReplayBottomSheet.newInstance(mistake)
            sheet.gameApiClient = gameApiClient
            sheet.show(parentFragmentManager, "MistakeReplayBottomSheet")
            dismiss()
        }
    }
}
