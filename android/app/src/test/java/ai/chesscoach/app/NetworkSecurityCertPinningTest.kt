package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Base64

/**
 * Bidirectional source-pin: the SPKI hashes in this test file MUST match
 * the SHA-256 pins declared in ``network_security_config.xml`` for the
 * ``cereveon.com`` domain-config.
 *
 * Why this test exists
 * --------------------
 * Cert pinning is security-critical: a mismatch between the pins shipped
 * in an APK and the live cert chain bricks every Android client until a
 * new release lands.  The XML is the source of truth at runtime; this
 * test is the source of truth at review time.  Pinning both sides in
 * lockstep means:
 *
 *   * A contributor who edits the XML without updating this test fails
 *     CI with a clear "pin set drifted" message.
 *   * A contributor who edits this test without updating the XML fails
 *     the same way.
 *   * A contributor who adds a fourth pin (e.g., a backup intermediate
 *     for an upcoming rotation) is forced to add it here too — the test
 *     asserts EXACT equality of the pin set, not a subset.
 *
 * The expiration check is the brick-recovery floor: if pins aren't
 * rotated by the documented expiration date and the chain has drifted
 * in some way the three pins don't cover, ``NetworkSecurityConfig``
 * falls back to system-CA trust rather than failing every connection.
 * This test catches the case where expiration has already silently
 * lapsed so the operator sees CI red before shipping a release whose
 * pinning is effectively disabled.
 *
 * Pinned invariants
 * -----------------
 *  1. PIN_SET_PRESENT          — the cereveon.com domain-config exists
 *                                and contains a <pin-set> element.
 *  2. PIN_SET_EXACT_MATCH      — XML pin set equals
 *                                EXPECTED_PINS exactly (set equality).
 *  3. PIN_SET_NOT_EMPTY        — at least one pin is declared (and
 *                                NetworkSecurityConfig REQUIRES >= 2;
 *                                see the parser check below).
 *  4. EXPIRATION_FUTURE        — the expiration attribute is parseable
 *                                as ISO date AND is in the future.
 *  5. LEAF_NOT_PINNED          — the leaf SPKI hash (which we know
 *                                from the live cert at test-author time)
 *                                is NOT in the pin set; pinning a
 *                                Let's Encrypt leaf is an anti-pattern
 *                                because leaves rotate every ~90 days.
 *  6. PIN_FORMAT_VALID         — every pin is a well-formed base64-
 *                                encoded SHA-256 (32 bytes decoded).
 *  7. DOMAIN_TARGETS_CEREVEON  — the pin-set is scoped to the
 *                                production hostname, not a wildcard
 *                                or a test/staging domain.
 *
 * Rotation procedure: ``docs/CERT_PIN_ROTATION.md``.
 */
class NetworkSecurityCertPinningTest {

    private val xmlPath = "src/main/res/xml/network_security_config.xml"

    /**
     * The pins we expect in the XML.  Adding / removing / changing
     * a pin requires updating BOTH this list AND the XML in the same
     * commit — the EXACT_MATCH test below enforces that.
     *
     * Each entry's comment names the cert it identifies; rotation
     * procedure in CERT_PIN_ROTATION.md.
     */
    private val EXPECTED_PINS: Set<String> = setOf(
        // Let's Encrypt YE1 ECDSA intermediate (the leaf's direct issuer).
        // Matches the current chain (leaf → YE1 → ISRG Root YE → X2 → X1).
        "brzvtCELCIZUo4sD/qPX0ccRtPsd3DY6RfmxpOU9oB4=",
        // ISRG Root X1 (RSA root, valid until 2030).  Long-term anchor
        // that survives Let's Encrypt intermediate rotation.
        "C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=",
        // ISRG Root X2 (ECDSA root, valid until 2035).  Backup root for
        // a future migration where the chain terminates at X2.
        "diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI=",
    )

    /**
     * The cereveon.com LEAF SPKI hash as observed at test-author time.
     * Pinning a Let's Encrypt leaf is an anti-pattern because the leaf
     * rotates every ~90 days; this constant is documented here purely
     * so the LEAF_NOT_PINNED test can detect a future contributor
     * accidentally adding a leaf pin.
     */
    private val LEAF_SPKI_OBSERVED = "yPSNqddxnuIWRyxl1NWJWareyguSyZc6W8pjb+gUCOE="

    private val EXPECTED_EXPIRATION_FLOOR: LocalDate = LocalDate.now()

    // Capture group: every <pin digest="SHA-256">HASH</pin>.  Matches
    // both the same-line and reflowed-attribute forms Android's XML
    // formatter routinely produces.
    private val pinRe = Regex(
        """<pin\s+digest\s*=\s*"SHA-256"\s*>\s*([^<]+?)\s*</pin>""",
        RegexOption.DOT_MATCHES_ALL,
    )

    // Capture the pin-set element and its surrounding domain-config so
    // we can verify scoping in a single pass.
    private val cereveonDomainConfigRe = Regex(
        """<domain-config[^>]*>\s*<domain[^>]*>cereveon\.com</domain>\s*<pin-set\s+expiration\s*=\s*"([^"]+)"\s*>(.*?)</pin-set>\s*</domain-config>""",
        RegexOption.DOT_MATCHES_ALL,
    )

    private fun readXml(): String = File(xmlPath).readText()

    @Test
    fun `PIN_SET_PRESENT - cereveon_com domain-config contains a pin-set`() {
        val xml = readXml()
        val match = cereveonDomainConfigRe.find(xml)
        assertNotNull(
            "Could not find a <domain-config> for cereveon.com with a <pin-set> in " +
                "$xmlPath.  If the pin set was intentionally removed, also remove " +
                "this test and update docs/THREAT_MODEL.md § T2 to re-document the " +
                "no-pinning residual risk.",
            match,
        )
    }

    @Test
    fun `PIN_SET_EXACT_MATCH - XML pins equal EXPECTED_PINS`() {
        val xml = readXml()
        val match = cereveonDomainConfigRe.find(xml)
            ?: error("Pin set not found — see PIN_SET_PRESENT for diagnostic.")
        val pinSetBody = match.groupValues[2]
        val xmlPins: Set<String> = pinRe.findAll(pinSetBody).map { it.groupValues[1] }.toSet()
        assertEquals(
            "Pin set in network_security_config.xml diverged from " +
                "EXPECTED_PINS in this test.  If you intentionally added / removed / " +
                "rotated a pin, update BOTH files in the same commit.  See " +
                "docs/CERT_PIN_ROTATION.md for the rotation procedure.\n\n" +
                "  XML pins:      $xmlPins\n" +
                "  EXPECTED_PINS: $EXPECTED_PINS",
            EXPECTED_PINS, xmlPins,
        )
    }

    @Test
    fun `PIN_SET_NOT_EMPTY - at least two pins declared`() {
        // NetworkSecurityConfig docs note: "The configuration MUST
        // include at least two pins" so a single rotation can't brick
        // the app.  We currently declare three; this guard catches a
        // future contributor reducing the set to one.
        assertTrue(
            "Pin set must contain at least 2 pins (Android NetworkSecurityConfig " +
                "requirement; also our pin-rotation strategy needs >=2 to survive " +
                "an intermediate rotation without a release).  Found: ${EXPECTED_PINS.size}",
            EXPECTED_PINS.size >= 2,
        )
    }

    @Test
    fun `EXPIRATION_FUTURE - pin-set expiration is in the future`() {
        val xml = readXml()
        val match = cereveonDomainConfigRe.find(xml)
            ?: error("Pin set not found — see PIN_SET_PRESENT for diagnostic.")
        val expirationStr = match.groupValues[1]
        val expiration = try {
            LocalDate.parse(expirationStr, DateTimeFormatter.ISO_LOCAL_DATE)
        } catch (e: Exception) {
            error(
                "expiration attribute $expirationStr is not a valid ISO date " +
                    "(YYYY-MM-DD).  NetworkSecurityConfig won't parse it; pinning " +
                    "may be silently disabled.  Fix the XML."
            )
        }
        assertTrue(
            "Pin-set expiration $expiration has already passed " +
                "(today is $EXPECTED_EXPIRATION_FLOOR).  Pinning has effectively " +
                "fallen back to system-CA trust on release builds.  Rotate the " +
                "pins per docs/CERT_PIN_ROTATION.md and bump the expiration " +
                "attribute.",
            expiration.isAfter(EXPECTED_EXPIRATION_FLOOR),
        )
    }

    @Test
    fun `LEAF_NOT_PINNED - the observed leaf SPKI is NOT in the pin set`() {
        assertFalse(
            "The cereveon.com leaf SPKI ($LEAF_SPKI_OBSERVED) appears in " +
                "EXPECTED_PINS.  This is an anti-pattern: Let's Encrypt leaves " +
                "rotate every ~90 days, so every renewal would brick the app " +
                "until a release ships with the new leaf hash.  Pin the " +
                "intermediate + roots instead — see docs/CERT_PIN_ROTATION.md.",
            EXPECTED_PINS.contains(LEAF_SPKI_OBSERVED),
        )
    }

    @Test
    fun `PIN_FORMAT_VALID - every pin is base64 of a 32-byte SHA-256 digest`() {
        // SHA-256 produces 32 bytes → base64(32 bytes) is exactly 44
        // characters (including the trailing `=` padding).  A pin
        // shorter / longer / non-base64 indicates a paste error.
        EXPECTED_PINS.forEach { pin ->
            assertEquals(
                "Pin $pin is not 44 characters long.  A SHA-256 SPKI hash in " +
                    "base64 is always exactly 44 chars (32 raw bytes → 44 b64).  " +
                    "Re-derive the pin with `openssl x509 -in cert.pem -pubkey " +
                    "-noout | openssl pkey -pubin -outform der | openssl dgst " +
                    "-sha256 -binary | openssl enc -base64`.",
                44, pin.length,
            )
            val decoded = try {
                Base64.getDecoder().decode(pin)
            } catch (e: IllegalArgumentException) {
                error("Pin $pin is not valid base64: ${e.message}")
            }
            assertEquals(
                "Pin $pin decodes to ${decoded.size} bytes, expected 32 (SHA-256).",
                32, decoded.size,
            )
        }
    }

    @Test
    fun `DOMAIN_TARGETS_CEREVEON - pin-set scoped to the production hostname`() {
        val xml = readXml()
        val match = cereveonDomainConfigRe.find(xml)
            ?: error("Pin set not found — see PIN_SET_PRESENT for diagnostic.")
        // The regex itself requires the literal cereveon.com — passing
        // PIN_SET_PRESENT already proves the scoping.  This test exists
        // to make the requirement explicit so a future contributor
        // refactoring the regex understands what the scoping promise is.
        val full = match.value
        assertTrue(
            "Pin-set must include includeSubdomains=\"true\" so " +
                "potential api.cereveon.com / chat.cereveon.com subdomains " +
                "inherit pinning by default.  Found:\n$full",
            full.contains("includeSubdomains=\"true\""),
        )
    }
}
