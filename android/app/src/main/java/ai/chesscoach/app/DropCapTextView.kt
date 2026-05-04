package ai.chesscoach.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.text.Layout
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.LeadingMarginSpan
import android.util.AttributeSet
import android.util.TypedValue
import androidx.appcompat.widget.AppCompatTextView

/**
 * Atrium drop-cap text view.
 *
 * The first letter of the prose renders at ~2.6× the body size in
 * accent_cyan italic with a soft cyan glow, while the body wraps
 * around it for the first two lines — the "Notice how your pieces are
 * working together…" pattern from the in-game coaching screen and the
 * "Yet the position settled before I expected it…" reflection on the
 * game-end summary screen.
 *
 * Implementation is a [LeadingMarginSpan.LeadingMarginSpan2] applied
 * to the body remainder.  The first character is consumed and drawn
 * directly into the leading margin reserved for the first two lines,
 * giving a true float-left drop cap (not just a big inline character
 * that pushes subsequent lines down).
 *
 * Set the text via the standard `text` attribute or `setText(...)` —
 * the cap treatment is applied automatically when the first char is
 * a letter or digit.  Strings starting with whitespace, punctuation,
 * or empty content fall through to plain TextView rendering.
 */
class DropCapTextView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : AppCompatTextView(context, attrs, defStyleAttr) {

    /** Cap glyph colour — defaults to atrium_accent_cyan. */
    var capColor: Int = Color.parseColor("#4FD9E5")

    /** How many body lines the cap should occupy (handoff: 2). */
    var capLines: Int = 2

    /** Cap glyph size relative to body text size (handoff: ~44sp on 17sp body ≈ 2.6×). */
    var capScale: Float = 2.6f

    /** Gap between cap glyph and body text in dp. */
    var capGapDp: Float = 6f

    override fun setText(text: CharSequence?, type: BufferType?) {
        if (text.isNullOrEmpty() || !text[0].isLetterOrDigit()) {
            // Plain prose — let AppCompatTextView render verbatim.
            super.setText(text, type)
            return
        }
        // Force SOFTWARE layer so the cap paint's setShadowLayer renders.
        // Idempotent — no-op if already on software.  Set inside setText
        // (not init) because the super constructor processes android:text
        // from XML before subclass init blocks run.
        if (layerType != LAYER_TYPE_SOFTWARE) {
            setLayerType(LAYER_TYPE_SOFTWARE, null)
        }
        val cap = text[0].toString()
        val body = text.substring(1)

        val capPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = capColor
            typeface = this@DropCapTextView.typeface
            textSkewX = -0.18f                          // italic skew
            textSize = this@DropCapTextView.textSize * capScale
            setShadowLayer(12f, 0f, 0f, capColor)       // cyan halo per spec
        }
        val gapPx = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, capGapDp, resources.displayMetrics,
        )

        val ssb = SpannableStringBuilder(body)
        ssb.setSpan(
            DropCapSpan(cap, capPaint, capLines, gapPx),
            0, body.length, Spanned.SPAN_INCLUSIVE_INCLUSIVE,
        )
        super.setText(ssb, type)
    }

    /**
     * LeadingMarginSpan2 that reserves a leading margin for the first
     * `lines` body lines and draws the cap glyph into that margin.
     *
     * The cap baseline is aligned to the top of the FIRST body line so
     * the glyph rises to the top of the paragraph; visually the body
     * lines wrap around the right side of the cap.
     */
    private class DropCapSpan(
        private val cap: String,
        private val capPaint: Paint,
        private val lines: Int,
        private val gapPx: Float,
    ) : LeadingMarginSpan.LeadingMarginSpan2 {

        private val capWidth: Float = capPaint.measureText(cap)
        private var firstLineTop: Int = -1

        override fun getLeadingMargin(first: Boolean): Int =
            if (first) (capWidth + gapPx).toInt() else 0

        override fun getLeadingMarginLineCount(): Int = lines

        override fun drawLeadingMargin(
            c: Canvas, p: Paint, x: Int, dir: Int,
            top: Int, baseline: Int, bottom: Int,
            text: CharSequence?, start: Int, end: Int,
            first: Boolean, layout: Layout?,
        ) {
            // Only draw the cap on the absolute first line of the body.
            if (start != 0) return
            if (firstLineTop < 0) firstLineTop = top
            // Cap baseline = top of paragraph + cap ascent magnitude
            // → glyph rises to the top edge of the first line.
            val capBaseline = firstLineTop - capPaint.fontMetrics.ascent
            c.drawText(cap, x.toFloat(), capBaseline, capPaint)
        }
    }
}
