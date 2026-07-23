package com.cereveon.myapp

import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Modifier

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
        // The trim is enforced structurally: SecaStatusDto must declare
        // exactly one instance property.  If a future change adds
        // bandit_enabled or version back, this test catches it before
        // review.
        //
        // Sprint 4.3.C: filter out static fields too — the
        // ``@Serializable`` annotation now adds a static ``Companion``
        // field on the JVM class which is non-synthetic.  That's a
        // serialization-mechanism artefact, not a wire-shape change,
        // so it must not count toward the property budget.
        val dto = SecaStatusDto(safeModeEnabled = true)
        val declared = dto.javaClass.declaredFields
            .filterNot { it.isSynthetic }
            .filterNot { Modifier.isStatic(it.modifiers) }
            .map { it.name }
        assertTrue(
            "SecaStatusDto must declare only safeModeEnabled, got: $declared",
            declared == listOf("safeModeEnabled"),
        )
    }
}
