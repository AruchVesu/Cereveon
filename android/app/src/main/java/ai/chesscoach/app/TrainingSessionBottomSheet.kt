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
                    putFloat(ARG_DIFFICULTY, rec.difficulty)
                }
            }

        // ── Pure display helpers — testable without Android framework ─────────

        /** Format a topic slug as "Topic: Endgame technique". */
        fun formatTopic(topic: String): String =
            "Topic: ${topic.replace('_', ' ').replaceFirstChar { it.uppercase() }}"

        /** Format an exercise type as "Type: Drill". */
        fun formatExerciseType(exerciseType: String): String =
            "Type: ${exerciseType.replaceFirstChar { it.uppercase() }}"

        /** Format difficulty 0.0–1.0 as "Difficulty: 70%". */
        fun formatDifficulty(difficulty: Float): String =
            "Difficulty: %.0f%%".format(difficulty.coerceIn(0f, 1f) * 100f)

        /** Convert difficulty 0.0–1.0 to ProgressBar integer (0–100). */
        fun difficultyProgress(difficulty: Float): Int =
            (difficulty.coerceIn(0f, 1f) * 100f).toInt()

        /**
         * Build a seed prompt sent as the opening user turn in [ChatBottomSheet].
         *
         * The prompt is deterministic (pure function of [rec]) so it can be unit-tested
         * and reviewed without running the chat pipeline.
         */
        fun buildSeedPrompt(rec: CurriculumRecommendation): String {
            val topic = rec.topic.replace('_', ' ')
            val type  = rec.exerciseType.replaceFirstChar { it.uppercase() }
            val diff  = "%.0f".format(rec.difficulty.coerceIn(0f, 1f) * 100f)
            return "I want to train on $topic ($type, difficulty $diff%). " +
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
        val difficulty   = args.getFloat(ARG_DIFFICULTY, 0.5f)

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
