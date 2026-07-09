package ai.chesscoach.app

import android.app.AlertDialog
import android.content.Intent
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/**
 * Account-section flows shared by every host of [SettingsBottomSheet]
 * — MainActivity's drawer entry and HomeActivity's avatar entry.
 *
 * Extracted verbatim from MainActivity when the Settings sheet gained
 * its second host: password validation, error copy, and the logout
 * routing are user-visible contracts that must not drift between the
 * two hosts.
 */
object AccountFlows {

    /**
     * Change-password dialog: two password fields, client-side
     * validation (non-blank, new ≥ 8 chars — mirrors the server rule),
     * then POST via [AuthApiClient.changePassword] with a toast for
     * each outcome.  Cancel is a no-op.
     */
    fun showChangePasswordDialog(
        activity: AppCompatActivity,
        authRepo: AuthRepository,
        authApiClient: AuthApiClient,
    ) {
        if (activity.isFinishing || activity.isDestroyed) return
        val layout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(64, 32, 64, 16)
        }
        val etCurrent = EditText(activity).apply {
            hint = "Current password"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val etNew = EditText(activity).apply {
            hint = "New password (min 8 characters)"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        layout.addView(etCurrent)
        layout.addView(etNew)

        AlertDialog.Builder(activity)
            .setTitle("Change Password")
            .setView(layout)
            .setPositiveButton("Save") { _, _ ->
                val current = etCurrent.text.toString()
                val new = etNew.text.toString()
                if (current.isBlank() || new.isBlank()) {
                    Toast.makeText(activity, "Fields must not be empty.", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                if (new.length < 8) {
                    Toast.makeText(activity, "New password must be at least 8 characters.", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                val token = authRepo.getToken() ?: return@setPositiveButton
                activity.lifecycleScope.launch {
                    when (authApiClient.changePassword(current, new, token)) {
                        is ApiResult.Success ->
                            Toast.makeText(activity, "Password updated.", Toast.LENGTH_SHORT).show()
                        is ApiResult.HttpError ->
                            Toast.makeText(activity, "Incorrect current password.", Toast.LENGTH_SHORT).show()
                        else ->
                            Toast.makeText(activity, "Network error. Please try again.", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Best-effort server logout, local token clear, then a hard route
     * to [LoginActivity] with NEW_TASK|CLEAR_TASK so back-navigation
     * can never re-enter an authenticated surface.
     */
    fun performLogout(
        activity: AppCompatActivity,
        authRepo: AuthRepository,
        authApiClient: AuthApiClient,
    ) {
        val token = authRepo.getToken()
        activity.lifecycleScope.launch {
            if (token != null) {
                authApiClient.logout(token)   // best-effort; ignore result
            }
            authRepo.clearToken()
            activity.startActivity(
                Intent(activity, LoginActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            activity.finish()
        }
    }
}
