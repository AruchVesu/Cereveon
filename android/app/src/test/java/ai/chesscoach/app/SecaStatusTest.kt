package ai.chesscoach.app

import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [SecaStatusDto] parsing helpers and invariants.
 *
 * All tests are pure JVM (no Android context required).
 *
 * The DTO was trimmed to a single field after the backend reduced
 * /seca/status to ``{"safe_mode": <bool>}``.  Earlier shape included
 * ``bandit_enabled`` (redundant, just !safeModeEnabled) and
 * ``version`` (no client decision used it); both were dropped for
 * information-leak reduction.
 *
 * Invariants pinned
 * -----------------
 *  SECA_STATUS_SAFE_MODE_DEFAULT   SecaStatusDto carries safeModeEnabled.
 *  SECA_STATUS_SINGLE_FIELD        Construction needs exactly one argument
 *                                  — guards against accidental restoration of
 *                                  the old bandit_enabled / version fields.
 */
class SecaStatusTest {

    @Test
    fun `SECA_STATUS_SAFE_MODE_DEFAULT - canonical safe response has safeModeEnabled true`() {
        val dto = SecaStatusDto(safeModeEnabled = true)
        assertTrue("safeModeEnabled must be true in SAFE_MODE build", dto.safeModeEnabled)
    }

    @Test
    fun `SECA_STATUS_SINGLE_FIELD - DTO exposes only safeModeEnabled`() {
        // The trim is enforced structurally: SecaStatusDto must be
        // constructible with exactly one argument and have a single
        // declared property.  If a future change adds bandit_enabled or
        // version back, this test catches it before review.
        val dto = SecaStatusDto(safeModeEnabled = true)
        val declared = dto.javaClass.declaredFields
            .filterNot { it.isSynthetic }
            .map { it.name }
        assertTrue(
            "SecaStatusDto must declare only safeModeEnabled, got: $declared",
            declared == listOf("safeModeEnabled"),
        )
    }
}
