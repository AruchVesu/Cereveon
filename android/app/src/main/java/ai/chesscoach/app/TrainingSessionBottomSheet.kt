package ai.chesscoach.app

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import com.google.android.material.bottomsheet.BottomSheetDialogFragment

/**
 * Bottom sheet that presents a single SECA curriculum recommendation and lets
 * the user launch a guided training session in [ChatBottomSheet].
 *
 * Arguments are set via [newInstance]; no Android context is required for the
 * pure display helpers — they live in [Companion] and are host-JVM testable.
 */
class TrainingSessionBottomSheet : BottomSheetDialogFragment() {

    companion object {
        private const val ARG_TOPIC         = "arg_topic"
        private const val ARG_EXERCISE_TYPE = "arg_exercise_type"
        private const val ARG_DIFFICULTY    = "arg_difficulty"

        fun newInstance(rec: CurriculumRecommendation): TrainingSessionBottomSheet =
            TrainingSessionBottomSheet().apply {
                arguments = Bundle().apply {
                    putString(ARG_TOPIC, rec.topic)
                    putString(ARG_EXERCISE_TYPE, rec.exerciseType)
                    putString(ARG_DIFFICULTY, rec.difficulty)
                }
            }

        // ── Pure display helpers — testable without Android framework ─────────

        /** Format a topic slug as "Topic: Endgame technique". */
        fun formatTopic(topic: String): String =
            "Topic: ${topic.replace('_', ' ').replaceFirstChar { it.uppercase() }}"

        /** Format an exercise type as "Type: Drill". */
        fun formatExerciseType(exerciseType: String): String =
            "Type: ${exerciseType.replaceFirstChar { it.uppercase() }}"

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
         * Build a seed prompt sent as the opening user turn in [ChatBottomSheet].
         *
         * The prompt is deterministic (pure function of [rec]) so it can be unit-tested
         * and reviewed without running the chat pipeline.
         */
        fun buildSeedPrompt(rec: CurriculumRecommendation): String {
            val topic = rec.topic.replace('_', ' ')
            val type  = rec.exerciseType.replaceFirstChar { it.uppercase() }
            val diff  = rec.difficulty.lowercase()
            return "I want to train on $topic ($type, $diff difficulty). " +
                "Please guide me through this training session."
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_training_session, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val args         = requireArguments()
        val topic        = args.getString(ARG_TOPIC, "")
        val exerciseType = args.getString(ARG_EXERCISE_TYPE, "")
        val difficulty   = args.getString(ARG_DIFFICULTY, "medium")

        view.findViewById<TextView>(R.id.txtTrainingTopic).text        = formatTopic(topic)
        view.findViewById<TextView>(R.id.txtTrainingExerciseType).text = formatExerciseType(exerciseType)
        view.findViewById<TextView>(R.id.txtTrainingDifficultyLabel).text = formatDifficulty(difficulty)
        view.findViewById<ProgressBar>(R.id.progressTrainingDifficulty).progress =
            difficultyProgress(difficulty)

        view.findViewById<Button>(R.id.btnStartTrainingSession).setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            val rec = CurriculumRecommendation(
                topic = topic,
                difficulty = difficulty,
                exerciseType = exerciseType,
            )
            ChatBottomSheet
                .newInstance(
                    fen        = "startpos",
                    seedPrompt = buildSeedPrompt(rec),
                )
                .show(parentFragmentManager, "ChatBottomSheet")
            dismiss()
        }
    }
}
