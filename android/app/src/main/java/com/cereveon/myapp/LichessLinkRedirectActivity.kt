package com.cereveon.myapp

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/**
 * Completes the "Link Lichess" OAuth flow (``POST /lichess/link``).
 *
 * The system browser fires ``ai.chesscoach.app://lichess-link?code&state``
 * here after a logged-in user authorizes on Lichess.  This validates the
 * CSRF ``state`` against the value [LichessLinkFlow] persisted when WE
 * started the flow, then forwards the one-time code + verifier to the
 * backend, which performs the Lichess token exchange server-side and
 * links the VERIFIED identity — so the app never sees a Lichess token
 * and a user can only link an account they actually control.
 *
 * Rendered translucent + no UI: the user sees a brief hop back to where
 * they were, then a result toast.
 *
 * Hostile-input note: exported (any app can fire the VIEW intent), but a
 * forged redirect learns nothing — it must carry the ``state`` we
 * persisted, and a redirect with no pending attempt is silently dropped.
 */
class LichessLinkRedirectActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handle(intent?.data)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handle(intent.data)
    }

    private fun handle(data: Uri?) {
        if (data == null ||
            data.scheme != LichessOAuth.REDIRECT_SCHEME ||
            data.host != LichessOAuth.LINK_REDIRECT_HOST
        ) {
            finish()
            return
        }

        val prefs = getSharedPreferences(LichessLinkFlow.PREFS, MODE_PRIVATE)
        val verifier = prefs.getString(LichessLinkFlow.KEY_PENDING_VERIFIER, null)
        val pendingState = prefs.getString(LichessLinkFlow.KEY_PENDING_STATE, null)
        if (verifier == null || pendingState == null) {
            finish() // no pending link attempt — stale re-delivery or forged intent
            return
        }

        // User backed out on the consent screen (?error=access_denied) etc.
        if (data.getQueryParameter("error") != null) {
            LichessLinkFlow.clearPending(this)
            toastAndFinish(getString(R.string.lichess_link_cancelled))
            return
        }

        val code = data.getQueryParameter("code")
        val state = data.getQueryParameter("state")
        if (code.isNullOrEmpty() || state != pendingState) {
            // State mismatch = this redirect is not from the flow WE started.
            LichessLinkFlow.clearPending(this)
            toastAndFinish(getString(R.string.lichess_link_failed))
            return
        }

        LichessLinkFlow.clearPending(this)
        performLink(code, verifier)
    }

    private fun performLink(code: String, codeVerifier: String) {
        val authRepo = AuthRepository(EncryptedTokenStorage(this))
        val token = authRepo.getToken()
        if (token == null) {
            toastAndFinish(getString(R.string.lichess_error_unauthenticated))
            return
        }
        val client: LichessApiClient = HttpLichessApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        lifecycleScope.launch {
            val messageRes = when (val result = client.link(code, codeVerifier, token)) {
                is ApiResult.Success -> R.string.lichess_link_success
                is ApiResult.HttpError ->
                    if (result.code == 401) R.string.lichess_link_failed
                    else R.string.lichess_error_upstream
                is ApiResult.NetworkError -> R.string.lichess_error_network
                ApiResult.Timeout -> R.string.lichess_error_timeout
            }
            toastAndFinish(getString(messageRes))
        }
    }

    private fun toastAndFinish(message: String) {
        // applicationContext: the toast must outlive this finishing activity.
        Toast.makeText(applicationContext, message, Toast.LENGTH_LONG).show()
        finish()
    }
}
