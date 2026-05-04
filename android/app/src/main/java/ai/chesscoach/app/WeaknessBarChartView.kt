package ai.chesscoach.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View

/**
 * Horizontal bar chart for player weakness categories.
 *
 * Each bar entry has a label, a value [0.0–1.0], and an optional priority tag
 * ("high", "medium", "low") that controls bar colour.  The chart is entirely
 * Canvas-based — no external dependencies.
 *
 * Call [setEntries] to populate data and trigger a redraw.
 */
class WeaknessBarChartView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    data class Entry(val label: String, val value: Float, val priority: String = "")

    private var entries: List<Entry> = emptyList()

    // Atrium muted ink for label / value text (mono labels are tiny;
    // muted contrast preserves the "official document" calm).
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#9AA0B4") // atrium_muted
        textSize = 36f
        typeface = android.graphics.Typeface.MONOSPACE
    }

    private val valuePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#9AA0B4") // atrium_muted
        textSize = 32f
        typeface = android.graphics.Typeface.MONOSPACE
    }

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    // Atrium hairline (8% white) — same primitive used for dividers.
    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#14FFFFFF")
        style = Paint.Style.FILL
    }

    private val rowHeight = 56f
    private val labelWidth = 280f
    private val valueWidth = 80f
    private val barRadius = 6f
    private val horizontalPad = 20f

    fun setEntries(data: List<Entry>) {
        entries = data
        requestLayout()
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val h = (entries.size * rowHeight + rowHeight).toInt().coerceAtLeast(rowHeight.toInt())
        setMeasuredDimension(
            resolveSize(MeasureSpec.getSize(widthMeasureSpec), widthMeasureSpec),
            resolveSize(h, heightMeasureSpec),
        )
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (entries.isEmpty()) return

        val totalW = width.toFloat()
        val barAreaStart = horizontalPad + labelWidth
        val barAreaEnd = totalW - horizontalPad - valueWidth
        val barAreaWidth = (barAreaEnd - barAreaStart).coerceAtLeast(1f)

        entries.forEachIndexed { i, entry ->
            val top = i * rowHeight + rowHeight * 0.15f
            val bottom = top + rowHeight * 0.7f
            val midY = (top + bottom) / 2f

            // Label
            val labelBounds = android.graphics.Rect()
            labelPaint.getTextBounds(entry.label, 0, entry.label.length, labelBounds)
            canvas.drawText(
                entry.label,
                horizontalPad,
                midY - labelBounds.exactCenterY(),
                labelPaint,
            )

            // Background track
            val trackRect = RectF(barAreaStart, top, barAreaEnd, bottom)
            canvas.drawRoundRect(trackRect, barRadius, barRadius, trackPaint)

            // Filled bar
            val fillW = (entry.value.coerceIn(0f, 1f) * barAreaWidth).coerceAtLeast(barRadius * 2)
            barPaint.color = priorityColor(entry.priority, entry.value)
            val fillRect = RectF(barAreaStart, top, barAreaStart + fillW, bottom)
            canvas.drawRoundRect(fillRect, barRadius, barRadius, barPaint)

            // Value label
            val pct = "${(entry.value * 100).toInt()}%"
            canvas.drawText(
                pct,
                barAreaEnd + 8f,
                midY - android.graphics.Rect().let {
                    valuePaint.getTextBounds(pct, 0, pct.length, it); it.exactCenterY()
                },
                valuePaint,
            )
        }
    }

    // Atrium two-tone signal: amber for high-severity weaknesses
    // (mirrors opponent / warning role), cyan for low-severity
    // (player-side / improving).  Medium reads as a muted amber so
    // the strip never feels alarming — Atrium is calm about telemetry.
    private fun priorityColor(priority: String, value: Float): Int = when (priority) {
        "high"   -> Color.parseColor("#FFC069") // atrium_accent_amber
        "medium" -> Color.parseColor("#CCFFC069") // amber @ 80%
        "low"    -> Color.parseColor("#4FD9E5") // atrium_accent_cyan
        else     -> when {
            value > 0.12f -> Color.parseColor("#FFC069")
            value > 0.06f -> Color.parseColor("#CCFFC069")
            else          -> Color.parseColor("#4FD9E5")
        }
    }
}
