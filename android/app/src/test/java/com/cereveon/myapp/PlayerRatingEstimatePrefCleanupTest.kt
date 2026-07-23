package com.cereveon.myapp

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pin: every code path that successfully PATCHes
 * ``/auth/me`` MUST clear the local ``PREF_PLAYER_RATING_ESTIMATE``
 * (and matching confidence PREF) on the Success branch.
 *
 * Why this test exists
 * --------------------
 * 2026-05-18 prod bug: the user saw their rating RISING on the
 * 'Your trends' dashboard after losing two games.  Hetzner logs +
 * DB inspection showed both games dropped rating from 1210 → 1196.59
 * via the standard Elo math, but ``MainActivity``'s cold-start
 * reconcile path then PATCHed /auth/me with the stale onboarding
 * ``PREF_PLAYER_RATING_ESTIMATE=1210`` between games, clobbering
 * the gameplay-driven update.
 *
 * Root cause: the PREF was set during Onboarding (and again on every
 * SettingsBottomSheet "Adjust your rating" save) but never cleared.
 * The cold-start reconcile saw |local − server| > epsilon and kept
 * pushing the stale value forever.
 *
 * Fix (PR #175): the cold-start reconcile path is retired entirely
 * (server is authoritative after first /auth/me round-trip), AND
 * every PATCH set-site clears the PREF on success so a future
 * regression that reintroduces the reconcile path can't immediately
 * re-trigger the bug.  This test pins both halves of that contract.
 *
 * Pinned invariants
 * -----------------
 *  1. MAIN_CLEARS_ON_AUTH_ME_SUCCESS         the cold-start
 *                                            ``authApiClient.me``
 *                                            success branch in
 *                                            MainActivity removes
 *                                            ``PREF_PLAYER_RATING_ESTIMATE``.
 *  2. ONBOARDING_CLEARS_ON_PATCH_SUCCESS     OnboardingActivity's
 *                                            firePatchAuthMe success
 *                                            branch removes the PREF.
 *  3. SETTINGS_CLEARS_ON_PATCH_SUCCESS       SettingsBottomSheet's
 *                                            firePatchAuthMe success
 *                                            branch removes the PREF.
 *  4. NO_COLD_START_RECONCILE_PATCH          the cold-start sync in
 *                                            MainActivity does NOT
 *                                            call ``authApiClient.updateMe``
 *                                            (the reconcile path is
 *                                            retired).
 */
class PlayerRatingEstimatePrefCleanupTest {

    private val mainPath = "src/main/java/com/cereveon/myapp/MainActivity.kt"
    private val onboardPath = "src/main/java/com/cereveon/myapp/OnboardingActivity.kt"
    private val settingsPath = "src/main/java/com/cereveon/myapp/SettingsBottomSheet.kt"

    private val ratingPref = "PREF_PLAYER_RATING_ESTIMATE"

    private fun read(path: String): String = File(path).readText()

    @Test
    fun `MAIN_CLEARS_ON_AUTH_ME_SUCCESS - MainActivity clears the PREF in the cold-start me() success path`() {
        val src = read(mainPath)
        // The cold-start sync is gated on ``ApiResult.Success`` from
        // ``authApiClient.me(authToken)``.  Inside that branch we must
        // wipe both onboarding-time PREFs.  Scan-by-text rather than
        // by AST keeps the test cheap and resilient to formatting
        // changes — the literal ``.remove(PREF_PLAYER_RATING_ESTIMATE)``
        // is what matters.
        assertTrue(
            "MainActivity.kt must call ``.remove($ratingPref)`` inside the " +
                "``authApiClient.me`` Success branch — see PR #175.  Without " +
                "this clear, the stale onboarding rating estimate would " +
                "linger and (if a future reconcile path is reintroduced) " +
                "re-PATCH the server on every cold-start.",
            src.contains(".remove(PREF_PLAYER_RATING_ESTIMATE)") ||
                src.contains(".remove(MainActivity.PREF_PLAYER_RATING_ESTIMATE)") ||
                src.contains(".remove(OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE)"),
        )
    }

    @Test
    fun `ONBOARDING_CLEARS_ON_PATCH_SUCCESS - OnboardingActivity firePatchAuthMe clears the PREF on Success`() {
        val src = read(onboardPath)
        assertTrue(
            "OnboardingActivity.firePatchAuthMe must clear " +
                "$ratingPref on the ``ApiResult.Success`` branch — see PR #175.",
            src.contains(".remove(PREF_PLAYER_RATING_ESTIMATE)"),
        )
    }

    @Test
    fun `SETTINGS_CLEARS_ON_PATCH_SUCCESS - SettingsBottomSheet firePatchAuthMe clears the PREF on Success`() {
        val src = read(settingsPath)
        assertTrue(
            "SettingsBottomSheet.firePatchAuthMe must clear " +
                "$ratingPref on the ``ApiResult.Success`` branch — see PR #175.",
            src.contains(".remove(OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE)") ||
                src.contains(".remove(PREF_PLAYER_RATING_ESTIMATE)"),
        )
    }

    @Test
    fun `NO_COLD_START_RECONCILE_PATCH - MainActivity cold-start sync does NOT call authApiClient_updateMe`() {
        val src = read(mainPath)
        // The cold-start sync block starts after ``// Sync full profile
        // from server at cold-start`` and ends roughly at the next
        // top-level construct.  We grep for any ``updateMe`` invocation
        // inside MainActivity — if a future change reintroduces a
        // reconcile PATCH from this file, this test fails fast.
        //
        // SettingsBottomSheet still calls updateMe (legitimate manual
        // recalibration); that path is unaffected.  MainActivity
        // should call ONLY ``authApiClient.me`` (GET), never
        // ``authApiClient.updateMe`` (PATCH).
        assertTrue(
            "MainActivity must NOT call ``authApiClient.updateMe(...)`` — " +
                "the cold-start reconcile path was retired in PR #175 " +
                "because it clobbered game-driven rating updates.  " +
                "Settings-side rating edits go through SettingsBottomSheet, " +
                "not MainActivity.",
            !src.contains("authApiClient.updateMe"),
        )
    }
}
