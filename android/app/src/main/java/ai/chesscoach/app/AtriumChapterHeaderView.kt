package ai.chesscoach.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Atrium chapter header — the top band on every Atrium screen.
 *
 * Layout (top → bottom):
 *   1.  Mono kicker        ("CHAPTER IX · MOVE 14")    — Atrium.Kicker
 *   2.  Italic display title ("The Pin")               — Atrium.Display
 *   3.  Hairline + ✦ ornament rule
 *
 * Built in code so screens can drop one in without authoring three
 * TextViews + a custom drawable each time.  Set [kicker] / [title] from
 * Kotlin (`view.kicker = "ANALYSIS · MOVE 14"`) or in XML via
 * `app:atriumKicker` / `app:atriumTitle` (declared in attrs.xml).
 */
class AtriumChapterHeaderView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : LinearLayout(context, attrs, defStyleAttr) {

    private val kickerView: TextView = TextView(context).apply {
        setTextAppearance(R.style.Atrium_Kicker)
    }
    private val titleView: TextView = TextView(context).apply {
        setTextAppearance(R.style.Atrium_Display)
        setPadding(0, dp(2), 0, 0)
    }
    private val ornamentRule: OrnamentRule = OrnamentRule(context).apply {
        layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, dp(14)).apply {
            topMargin = dp(10)
        }
    }

    var kicker: CharSequence?
        get() = kickerView.text
        set(value) { kickerView.text = value ?: "" }

    var title: CharSequence?
        get() = titleView.text
        set(value) { titleView.text = value ?: "" }

    init {
        orientation = VERTICAL
        // 16dp top, 20dp horizontal, 8dp bottom — matches the Atrium
        // chapter-header padding from design/coach-variant-atrium.jsx
        // (20px h, 16px t, 8px b in design pixels, dp-equivalent on Android).
        setPadding(dp(20), dp(16), dp(20), dp(8))

        addView(kickerView)
        addView(titleView)
        addView(ornamentRule)

        // Attribute parsing.  Both attributes are optional; without them
        // the view renders empty (useful in layout previews while you
        // wire copy from the activity).
        if (attrs != null) {
            val a = context.obtainStyledAttributes(attrs, R.styleable.AtriumChapterHeaderView)
            kicker = a.getString(R.styleable.AtriumChapterHeaderView_atriumKicker)
            title = a.getString(R.styleable.AtriumChapterHeaderView_atriumTitle)
            a.recycle()
        }
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP,
            value.toFloat(),
            resources.displayMetrics,
        ).toInt()

    /**
     * The ✦ ornament rule itself: a hairline gradient that fades into
     * the centre glyph and back out.  Drawn in Canvas so the
     * transparent-to-hairline gradient renders cleanly without a
     * dedicated drawable resource per density.
     */
    private class OrnamentRule(context: Context) : FrameLayout(context) {
        private val hairline = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = HAIRLINE
            strokeWidth = TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, 1f, context.resources.displayMetrics,
            )
        }
        private val ornamentPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = ORNAMENT
            textAlign = Paint.Align.CENTER
            textSize = TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_SP, 12f, context.resources.displayMetrics,
            )
        }

        init { setWillNotDraw(false) }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val w = width.toFloat()
            val h = height.toFloat()
            val centerY = h / 2f
            val glyph = "✦"
            val glyphWidth = ornamentPaint.measureText(glyph)
            val gap = TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, 8f, resources.displayMetrics,
            )
            val centerX = w / 2f
            val leftEnd = centerX - glyphWidth / 2f - gap
            val rightStart = centerX + glyphWidth / 2f + gap

            canvas.drawLine(0f, centerY, leftEnd, centerY, hairline)
            canvas.drawLine(rightStart, centerY, w, centerY, hairline)
            // Vertical bias so the ornament glyph sits visually centred
            // — text baselines render below their numeric y position.
            canvas.drawText(glyph, centerX, centerY - (ornamentPaint.descent() + ornamentPaint.ascent()) / 2f, ornamentPaint)
        }

        companion object {
            // atrium_hairline_strong + ornament glyph in muted ink.
            private val HAIRLINE = Color.parseColor("#1FFFFFFF")
            private val ORNAMENT = Color.parseColor("#9AA0B4")
        }
    }
}
