package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import java.io.File

/**
 * Bidirectional source-pin: the XML view type for `R.id.trainingCard`
 * MUST match the Kotlin `findViewById<TYPE>(R.id.trainingCard)` cast in
 * `GameSummaryBottomSheet`.
 *
 * Why this test exists
 * --------------------
 * On 2026-05-15 the Atrium design-system migration changed the
 * `trainingCard` view in `bottom_sheet_game_summary.xml` from
 * `<LinearLayout>` to `<ai.chesscoach.app.AtriumCardView>`, but the
 * Kotlin side still cast it as `findViewById<LinearLayout>(...)`.
 * That produces a `ClassCastException` at runtime when the post-game
 * summary inflates — the `BottomSheetDialogFragment` dies before
 * rendering and the user gets silently bounced back to `HomeActivity`
 * (no error UI; the crash log is the only signal).  Caught on-device
 * during the PR #165 verification test; this test pins both files in
 * lockstep so the next contributor migrating either side can't ship
 * the regression without CI failing first.
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_TRAININGCARD       — the layout has exactly one
 *                                       element with android:id=
 *                                       "@+id/trainingCard".
 *  2. KOTLIN_CASTS_TRAININGCARD       — GameSummaryBottomSheet contains
 *                                       a findViewById<...>(R.id.
 *                                       trainingCard) call.
 *  3. CAST_MATCHES_VIEW_TYPE          — the Kotlin cast type's short
 *                                       name equals the XML tag's
 *                                       short name (modulo package
 *                                       qualifier).
 */
class GameSummaryTrainingCardSourcePinTest {

    private val xmlPath = "src/main/res/layout/bottom_sheet_game_summary.xml"
    private val ktPath  = "src/main/java/ai/chesscoach/app/GameSummaryBottomSheet.kt"

    // XML element opening tag for the view with android:id="@+id/trainingCard".
    //
    // Matches things like:
    //   <ai.chesscoach.app.AtriumCardView ... android:id="@+id/trainingCard"
    //   <LinearLayout android:id="@+id/trainingCard"
    //
    // Group ``tag`` captures the view-class part.  We tolerate any
    // amount of attribute / whitespace between the tag and the id
    // attribute because Android XML formatters routinely reflow
    // attributes onto separate lines.
    private val xmlTrainingCardRe = Regex(
        """<\s*(?<tag>[\w.]+)\b[^>]*?android:id\s*=\s*"@\+id/trainingCard"""",
        RegexOption.DOT_MATCHES_ALL,
    )

    // Kotlin findViewById<TYPE>(R.id.trainingCard) call.
    private val ktTrainingCardRe = Regex(
        """findViewById<\s*(?<cast>[\w.]+)\s*>\s*\(\s*R\.id\.trainingCard\s*\)""",
    )

    @Test
    fun `XML_DECLARES_TRAININGCARD - layout declares R_id_trainingCard exactly once`() {
        val xml = File(xmlPath).readText()
        val matches = xmlTrainingCardRe.findAll(xml).toList()
        assertEquals(
            "Expected exactly one XML element with android:id=\"@+id/trainingCard\" " +
                "in $xmlPath, found ${matches.size}.  If you intentionally split " +
                "the training card across multiple views, update this pin test " +
                "and GameSummaryBottomSheet.kt accordingly.",
            1, matches.size,
        )
    }

    @Test
    fun `KOTLIN_CASTS_TRAININGCARD - GameSummaryBottomSheet finds R_id_trainingCard`() {
        val kt = File(ktPath).readText()
        val match = ktTrainingCardRe.find(kt)
        assertNotNull(
            "Could not find findViewById<TYPE>(R.id.trainingCard) in $ktPath.  " +
                "If the training-card view was retired, delete this pin test and " +
                "the corresponding XML element together.",
            match,
        )
    }

    @Test
    fun `CAST_MATCHES_VIEW_TYPE - Kotlin cast short name equals XML tag short name`() {
        val xml = File(xmlPath).readText()
        val kt  = File(ktPath).readText()

        val xmlTag = xmlTrainingCardRe.find(xml)
            ?.groups
            ?.get("tag")
            ?.value
            ?: error("XML missing R.id.trainingCard — see XML_DECLARES_TRAININGCARD")
        val ktCast = ktTrainingCardRe.find(kt)
            ?.groups
            ?.get("cast")
            ?.value
            ?: error("Kotlin missing findViewById<...>(R.id.trainingCard) — see KOTLIN_CASTS_TRAININGCARD")

        // XML uses the fully-qualified class name for custom views
        // (`ai.chesscoach.app.AtriumCardView`) and the bare class name
        // for android.widget / android.view classes (`LinearLayout`).
        // Kotlin always uses the bare name + an import.  Normalise both
        // to the short name before comparing.
        val xmlShort = xmlTag.substringAfterLast('.')
        val ktShort  = ktCast.substringAfterLast('.')

        assertEquals(
            "trainingCard view-type mismatch between layout and Kotlin: XML " +
                "declares <$xmlTag> but Kotlin casts to <$ktCast>.  On " +
                "2026-05-15 this exact mismatch crashed the post-game summary " +
                "(ClassCastException at GameSummaryBottomSheet.onViewCreated) and " +
                "silently dumped users back to HomeActivity.  Either update the " +
                "XML view type to match the Kotlin cast, or vice versa — but " +
                "both sides MUST resolve to the same class.",
            xmlShort, ktShort,
        )
    }
}
