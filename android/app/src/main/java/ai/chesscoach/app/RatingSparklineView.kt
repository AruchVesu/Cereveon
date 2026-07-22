package ai.chesscoach.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat

/**
 * Pure-Canvas sparkline showing a player's rating trend.
 *
 * Call [setRatings] with an ordered list of rating values (oldest → newest).
 * The view renders a polyline connecting each data point on a dark background.
 *
 * Requires at least 2 data points to draw; renders nothing for 0–1 points.
 * All rendering is done in [onDraw] — no Bitmap allocation, no library deps.
 */
class RatingSparklineView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    // Atrium accent_cyan — restraint: neon only for signal.  Token
    // read so the line follows the active palette (bright mode flips
    // the accent via values-notnight/colors.xml).
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_accent_cyan)
        strokeWidth = 3f
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }

    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_accent_cyan)
        style = Paint.Style.FILL
    }

    private var ratings: List<Float> = emptyList()

    /** Replace the current data set and trigger a redraw. */
    fun setRatings(values: List<Float>) {
        ratings = values
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val pts = ratings
        if (pts.size < 2) return

        val w = width.toFloat()
        val h = height.toFloat()
        val minVal = pts.min()
        val maxVal = pts.max()
        // Avoid division by zero when all values are identical.
        val range = (maxVal - minVal).coerceAtLeast(1f)

        // Map a rating value to a y-coordinate with 10% vertical padding.
        fun toY(rating: Float): Float = h * 0.9f - (rating - minVal) / range * h * 0.8f

        val path = Path()
        pts.forEachIndexed { i, rating ->
            val x = if (pts.size > 1) i / (pts.size - 1).toFloat() * w else 0f
            val y = toY(rating)
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        canvas.drawPath(path, linePaint)

        // Mark the latest data point with a dot.
        canvas.drawCircle(w, toY(pts.last()), 5f, dotPaint)
    }
}
