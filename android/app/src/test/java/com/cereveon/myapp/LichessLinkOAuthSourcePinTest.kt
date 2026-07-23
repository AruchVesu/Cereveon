package com.cereveon.myapp

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Source-pins for the account-link OAuth flow (2026-07-17 security fix):
 * linking a Lichess account now requires the SAME ownership proof as
 * "Sign in with Lichess" (an authorization-code + PKCE round-trip),
 * replacing the old self-asserted `{username}` body that let a logged-in
 * email user link any handle they didn't own.
 *
 * The load-bearing wiring lives in Android-framework code that host-JVM
 * tests can't instantiate — [LichessLinkRedirectActivity] reads
 * `android.net.Uri` / `SharedPreferences` / `Toast`, and [LichessLinkFlow]
 * opens the system browser.  These pins read the source directly, the
 * same drift-guard idiom as [GamePanelActionsSourcePinTest].  Each
 * failure names the invariant that regressed.
 *
 * Pinned invariants
 * -----------------
 *  1. REDIRECT_VALIDATES_STATE      the redirect activity rejects any code
 *                                   whose `state` != the value we persisted
 *                                   (CSRF / mix-up defence).
 *  2. REDIRECT_FORWARDS_CODE_VERIFIER  it forwards the one-time code + the
 *                                   persisted verifier to LichessApiClient.link.
 *  3. REDIRECT_NEVER_EXCHANGES_TOKEN   the app never performs the Lichess
 *                                   token exchange itself (server-side only) —
 *                                   it must not touch an access_token.
 *  4. FLOW_TARGETS_LINK_REDIRECT    LichessLinkFlow opens the consent screen at
 *                                   the DEDICATED link redirect, not sign-in's.
 *  5. FLOW_COMMITS_PENDING_STATE    the pending verifier + state are persisted
 *                                   with commit() before the browser launches.
 *  6. MANIFEST_REGISTERS_REDIRECT   AndroidManifest exports
 *                                   LichessLinkRedirectActivity on host
 *                                   `lichess-link`.
 *  7. VIEWMODEL_HAS_NO_USERNAME_LINK  the insecure username-link path is gone
 *                                   from the ViewModel and can't quietly return.
 *  8. REQUEST_MODEL_CARRIES_VERIFIER  the wire model serialises `code_verifier`.
 */
class LichessLinkOAuthSourcePinTest {

    private val redirectPath =
        "src/main/java/com/cereveon/myapp/LichessLinkRedirectActivity.kt"
    private val flowPath = "src/main/java/com/cereveon/myapp/LichessLinkFlow.kt"
    private val viewModelPath =
        "src/main/java/com/cereveon/myapp/LichessConnectViewModel.kt"
    private val modelsPath = "src/main/java/com/cereveon/myapp/LichessApiModels.kt"
    private val manifestPath = "src/main/AndroidManifest.xml"

    @Test
    fun `REDIRECT_VALIDATES_STATE - redirect rejects a mismatched CSRF state`() {
        val src = File(redirectPath).readText()
        assertTrue(
            "LichessLinkRedirectActivity must compare the returned state against " +
                "the persisted pending state (CSRF defence) — a forged redirect " +
                "with the wrong state must be rejected.",
            src.contains("state != pendingState"),
        )
    }

    @Test
    fun `REDIRECT_FORWARDS_CODE_VERIFIER - code and persisted verifier go to the client`() {
        val src = File(redirectPath).readText()
        assertTrue(
            "The redirect activity must read the verifier persisted by " +
                "LichessLinkFlow (KEY_PENDING_VERIFIER) — the PKCE proof.",
            src.contains("LichessLinkFlow.KEY_PENDING_VERIFIER"),
        )
        assertTrue(
            "The redirect activity must forward the one-time code + verifier to " +
                "LichessApiClient.link (server-side exchange).  Found no " +
                "client.link(code, codeVerifier, token) call.",
            Regex("""client\.link\(\s*code\s*,\s*codeVerifier\s*,\s*token\s*\)""")
                .containsMatchIn(src),
        )
    }

    @Test
    fun `REDIRECT_NEVER_EXCHANGES_TOKEN - the app performs no Lichess token exchange`() {
        val src = File(redirectPath).readText()
        // The whole point of routing the code through POST /lichess/link is
        // that the DeepSeek/Lichess token exchange happens server-side; the
        // app must never see (or handle) a Lichess access token.
        assertFalse(
            "The link redirect activity must NOT exchange the authorization " +
                "code itself — that is a server-side responsibility.  A reference " +
                "to access_token here means a Lichess token is reaching the device.",
            src.contains("access_token"),
        )
        assertFalse(
            "The link redirect activity must NOT call the Lichess token endpoint " +
                "directly (exchangeAuthorizationCode is server-side only).",
            src.contains("exchangeAuthorizationCode"),
        )
    }

    @Test
    fun `FLOW_TARGETS_LINK_REDIRECT - link flow uses the dedicated link redirect`() {
        val src = File(flowPath).readText()
        assertTrue(
            "LichessLinkFlow must build the authorize URL with " +
                "LichessOAuth.LINK_REDIRECT_URI so the code routes to " +
                "LichessLinkRedirectActivity, never the sign-in handler.",
            Regex("""redirectUri\s*=\s*LichessOAuth\.LINK_REDIRECT_URI""")
                .containsMatchIn(src),
        )
        assertTrue(
            "LichessLinkFlow must open the consent screen via " +
                "LichessOAuth.buildAuthorizeUrl.",
            src.contains("LichessOAuth.buildAuthorizeUrl("),
        )
    }

    @Test
    fun `FLOW_COMMITS_PENDING_STATE - verifier and state are committed before the browser opens`() {
        val src = File(flowPath).readText()
        // apply() is async; if the process dies while the browser is in the
        // foreground the redirect would land with no pending attempt and be
        // dropped.  commit() makes the write durable first.
        assertTrue(
            "LichessLinkFlow must persist the pending verifier.",
            src.contains("KEY_PENDING_VERIFIER"),
        )
        assertTrue(
            "LichessLinkFlow must persist the pending CSRF state.",
            src.contains("KEY_PENDING_STATE"),
        )
        assertTrue(
            "LichessLinkFlow must persist the pending PKCE material with " +
                "commit() (synchronous) before launching the browser — apply() " +
                "could be lost to process death mid-redirect.",
            src.contains(".commit()"),
        )
    }

    @Test
    fun `MANIFEST_REGISTERS_REDIRECT - the link redirect activity is exported on its host`() {
        val manifest = File(manifestPath).readText()
        val activityBlock = Regex(
            """<activity[^>]*android:name="\.LichessLinkRedirectActivity"[\s\S]*?</activity>""",
        ).find(manifest)?.value
        assertTrue(
            "AndroidManifest.xml must declare a <activity> for " +
                ".LichessLinkRedirectActivity so the browser can deliver the " +
                "redirect VIEW intent.",
            activityBlock != null,
        )
        assertTrue(
            "LichessLinkRedirectActivity must be exported (the browser fires the " +
                "VIEW intent from outside the app).",
            activityBlock!!.contains("android:exported=\"true\""),
        )
        assertTrue(
            "LichessLinkRedirectActivity's intent-filter must match host " +
                "\"lichess-link\" — distinct from sign-in's \"lichess-auth\" so " +
                "the two flows never cross wires.",
            activityBlock.contains("android:host=\"lichess-link\""),
        )
        // Regression pin (crash caught on-device 2026-07-19): the activity
        // extends AppCompatActivity, which throws "You need to use a
        // Theme.AppCompat theme (or descendant)" at creation under a raw
        // framework theme — killing the redirect before the OAuth code is
        // exchanged.  Its theme MUST descend from the app's AppCompat/
        // Material theme, i.e. an @style/ reference, never @android:style/.
        val themeMatch = Regex("""android:theme="([^"]*)"""").find(activityBlock)
        assertTrue(
            "LichessLinkRedirectActivity must declare a theme (translucent trampoline).",
            themeMatch != null,
        )
        assertFalse(
            "LichessLinkRedirectActivity's theme must NOT be a framework " +
                "@android:style/ theme (e.g. Theme.Translucent.NoTitleBar) — that " +
                "crashes AppCompatActivity on launch.  Use an @style/ theme that " +
                "descends from the app's Material/AppCompat theme " +
                "(Theme.Cereveon.Translucent).",
            themeMatch!!.groupValues[1].startsWith("@android:style/"),
        )
    }

    @Test
    fun `VIEWMODEL_HAS_NO_USERNAME_LINK - the insecure username-link path is gone`() {
        val src = File(viewModelPath).readText()
        // Regression pin: the old flow let a logged-in user POST any
        // {username} with no ownership proof.  The ViewModel must no longer
        // expose a link() operation or a username validator.
        assertFalse(
            "LichessConnectViewModel must NOT expose a link() operation — linking " +
                "is now an OAuth browser round-trip (LichessLinkFlow), not a " +
                "self-asserted username POST.",
            Regex("""fun\s+link\s*\(""").containsMatchIn(src),
        )
        assertFalse(
            "LichessConnectViewModel must NOT carry isValidUsername — the " +
                "self-asserted username path was removed with the security fix.",
            src.contains("isValidUsername"),
        )
    }

    @Test
    fun `REQUEST_MODEL_CARRIES_VERIFIER - the link request serialises code_verifier`() {
        val src = File(modelsPath).readText()
        assertTrue(
            "LichessLinkRequest must serialise the PKCE code_verifier so the " +
                "server can complete the token exchange.",
            src.contains("@SerialName(\"code_verifier\")"),
        )
    }
}
