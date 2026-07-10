package ai.chesscoach.app

import android.app.AlertDialog
import android.text.InputFilter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/**
 * "Send feedback" dialog form, opened from the game drawer's
 * btnSendFeedback.
 *
 * Same construction idiom as [AccountFlows.showChangePasswordDialog]
 * (programmatic AlertDialog + EditText, toast per outcome, Cancel is a
 * no-op) so the two drawer-adjacent form flows read identically.  The
 * message is POSTed to /feedback (docs/API_CONTRACTS.md §38) with the
 * app version attached; the server stores it for the operator and
 * never feeds it back into coaching.
 */
object FeedbackFlows {

    /**
     * Client-side mirror of the server's MAX_FEEDBACK_MESSAGE_LEN so an
     * over-long paste is truncated at input time instead of bouncing
     * off the server's 422.
     */
    const val MAX_FEEDBACK_MESSAGE_LEN = 2000

    fun showSendFeedbackDialog(
        activity: AppCompatActivity,
        feedbackApiClient: FeedbackApiClient,
    ) {
        if (activity.isFinishing || activity.isDestroyed) return
        val layout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(64, 32, 64, 16)
        }
        val etMessage = EditText(activity).apply {
            hint = activity.getString(R.string.feedback_input_hint)
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 4
            gravity = android.view.Gravity.TOP or android.view.Gravity.START
            filters = arrayOf(InputFilter.LengthFilter(MAX_FEEDBACK_MESSAGE_LEN))
        }
        layout.addView(etMessage)

        AlertDialog.Builder(activity)
            .setTitle(R.string.feedback_dialog_title)
            .setView(layout)
            .setPositiveButton(R.string.feedback_send) { _, _ ->
                val message = etMessage.text.toString().trim()
                if (message.isBlank()) {
                    Toast.makeText(
                        activity,
                        R.string.feedback_empty_error,
                        Toast.LENGTH_SHORT,
                    ).show()
                    return@setPositiveButton
                }
                activity.lifecycleScope.launch {
                    when (
                        feedbackApiClient.submitFeedback(
                            message = message,
                            appVersion = BuildConfig.VERSION_NAME,
                        )
                    ) {
                        is ApiResult.Success ->
                            Toast.makeText(
                                activity,
                                R.string.feedback_sent,
                                Toast.LENGTH_SHORT,
                            ).show()
                        else ->
                            Toast.makeText(
                                activity,
                                R.string.feedback_send_failed,
                                Toast.LENGTH_SHORT,
                            ).show()
                    }
                }
            }
            .setNegativeButton(R.string.feedback_cancel, null)
            .show()
    }
}
