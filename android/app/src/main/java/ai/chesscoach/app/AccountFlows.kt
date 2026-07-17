package ai.chesscoach.app

import android.app.AlertDialog
import android.content.Context
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
            routeToLogin(activity)
        }
    }

    /**
     * "Delete account" flow (GDPR Art. 17; DELETE /auth/me — contract
     * §41): an explicit "Are you sure" confirmation gates the
     * irreversible call, same product rule as the Reset-game dialog.
     * The tap that opened this dialog must never be destructive on its
     * own — SettingsDeleteAccountSourcePinTest pins that the network
     * call is reachable only from the positive button.
     *
     * Outcome handling (the asymmetry with [performLogout] is the
     * point — logout clears local state unconditionally, deletion only
     * on proof):
     *  - Success → wipe local user state + token, route to
     *    [LoginActivity].
     *  - 401 → the session is already dead, so the deletion did NOT
     *    run under this token; route to login WITHOUT claiming
     *    deletion (standard expired-session posture).
     *  - Any other failure → the account still exists server-side;
     *    keep the user signed in, keep local state, and say so.
     */
    fun confirmAndDeleteAccount(
        activity: AppCompatActivity,
        authRepo: AuthRepository,
        authApiClient: AuthApiClient,
    ) {
        if (activity.isFinishing || activity.isDestroyed) return
        AlertDialog.Builder(activity)
            .setTitle(R.string.delete_account_confirm_title)
            .setMessage(R.string.delete_account_confirm_message)
            .setPositiveButton(R.string.delete_account_confirm_positive) { _, _ ->
                performAccountDeletion(activity, authRepo, authApiClient)
            }
            .setNegativeButton(R.string.delete_account_confirm_negative, null)
            .show()
    }

    private fun performAccountDeletion(
        activity: AppCompatActivity,
        authRepo: AuthRepository,
        authApiClient: AuthApiClient,
    ) {
        val token = authRepo.getToken()
        if (token == null) {
            // No credential at all — nothing to delete under; the login
            // screen is the only sensible destination.
            routeToLogin(activity)
            return
        }
        activity.lifecycleScope.launch {
            when (val result = authApiClient.deleteAccount(token)) {
                is ApiResult.Success -> {
                    clearLocalUserState(activity)
                    authRepo.clearToken()
                    Toast.makeText(activity, "Account deleted.", Toast.LENGTH_LONG).show()
                    routeToLogin(activity)
                }
                is ApiResult.HttpError -> {
                    if (result.code == 401) {
                        authRepo.clearToken()
                        Toast.makeText(
                            activity,
                            "Session expired — sign in and try again.",
                            Toast.LENGTH_LONG,
                        ).show()
                        routeToLogin(activity)
                    } else {
                        Toast.makeText(
                            activity,
                            "Couldn't delete the account. Please try again.",
                            Toast.LENGTH_LONG,
                        ).show()
                    }
                }
                else ->
                    Toast.makeText(
                        activity,
                        "Network error. Account not deleted — please try again.",
                        Toast.LENGTH_LONG,
                    ).show()
            }
        }
    }

    /**
     * Wipe the shared user-state prefs (board checkpoint, rating cache,
     * pending finish PGN, plan cache, ...) after a CONFIRMED server-side
     * erasure.  The appearance mode survives: it is a device preference,
     * not account data, and clearing it here would flip the palette and
     * recreate the host activity underneath the login routing.
     */
    private fun clearLocalUserState(activity: AppCompatActivity) {
        val prefs = activity.getSharedPreferences(
            SettingsBottomSheet.PREFS_NAME,
            Context.MODE_PRIVATE,
        )
        val appearance = SettingsBottomSheet.readAppearanceMode(activity)
        prefs.edit()
            .clear()
            .putString(SettingsBottomSheet.PREF_APPEARANCE_MODE, appearance)
            .apply()
    }

    /**
     * Hard route to [LoginActivity] with NEW_TASK|CLEAR_TASK so
     * back-navigation can never re-enter an authenticated surface.
     * Shared tail of [performLogout] and the deletion outcomes above.
     */
    private fun routeToLogin(activity: AppCompatActivity) {
        activity.startActivity(
            Intent(activity, LoginActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        activity.finish()
    }
}
