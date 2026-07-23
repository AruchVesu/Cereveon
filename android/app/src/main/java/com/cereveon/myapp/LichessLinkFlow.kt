package com.cereveon.myapp

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

/**
 * Starts the "Link Lichess" OAuth flow for a LOGGED-IN player.
 *
 * Linking now requires the user to prove they own the Lichess account
 * (the same PKCE authorization flow as "Sign in with Lichess"), instead
 * of typing a username they may not own.  This mints the PKCE material,
 * persists it, and opens the Lichess consent screen at the DEDICATED
 * link redirect ([LichessOAuth.LINK_REDIRECT_URI]); the redirect lands
 * on [LichessLinkRedirectActivity], which exchanges the code via
 * ``POST /lichess/link``.
 *
 * Kept parallel to (not merged with) [LoginActivity]'s sign-in OAuth so
 * the two flows never cross wires — a link code can never be mistaken
 * for a sign-in.  The pending state lives in its own prefs file.
 */
object LichessLinkFlow {

    /** App-private prefs for the in-flight LINK attempt (separate from
     *  sign-in's ``lichess_oauth`` so the two never collide). */
    const val PREFS = "lichess_link_oauth"
    const val KEY_PENDING_VERIFIER = "pending_code_verifier"
    const val KEY_PENDING_STATE = "pending_state"

    /**
     * Mint fresh PKCE material, persist it with ``commit`` (the browser
     * takes the foreground immediately — an async write racing process
     * death would strand the redirect), and open the consent screen.
     *
     * Returns true if the browser opened; false if no browser is
     * available (the caller may show a message).
     */
    fun start(activity: Activity): Boolean {
        val verifier = LichessOAuth.generateCodeVerifier()
        val state = LichessOAuth.generateState()
        activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_PENDING_VERIFIER, verifier)
            .putString(KEY_PENDING_STATE, state)
            .commit()

        val url = LichessOAuth.buildAuthorizeUrl(
            codeChallenge = LichessOAuth.codeChallengeS256(verifier),
            state = state,
            redirectUri = LichessOAuth.LINK_REDIRECT_URI,
        )
        return try {
            activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            true
        } catch (_: ActivityNotFoundException) {
            clearPending(activity)
            false
        }
    }

    fun clearPending(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_PENDING_VERIFIER)
            .remove(KEY_PENDING_STATE)
            .apply()
    }
}
