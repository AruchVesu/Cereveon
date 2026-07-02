package ai.chesscoach.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Trampoline for the Lichess OAuth redirect
 * (``ai.chesscoach.app://lichess-auth?code=...&state=...``).
 *
 * The system browser fires this VIEW intent when Lichess redirects back
 * after the user authorizes (or denies).  It immediately forwards the
 * redirect URI to [LoginActivity] — which owns the pending PKCE state
 * and performs the backend exchange — and finishes itself, mirroring the
 * AppAuth ``RedirectUriReceiverActivity`` pattern without the dependency.
 *
 * Kept separate from [LoginActivity] so the LAUNCHER activity keeps its
 * default ``standard`` launch mode: `CLEAR_TOP or SINGLE_TOP` delivers
 * the redirect to the existing LoginActivity instance (`onNewIntent`)
 * when the user is still sitting on the login screen behind the browser,
 * and cold-starts one (`onCreate` with intent data) after process death.
 *
 * Hostile-input note: this activity is `exported` (any app can fire the
 * intent), but it carries no logic — it only re-posts `intent.data` to a
 * non-exported activity that validates the OAuth `state` against the
 * locally-persisted pending value before doing anything with the code.
 */
class LichessAuthRedirectActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(
            Intent(this, LoginActivity::class.java)
                .setData(intent?.data)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        )
        finish()
    }
}
