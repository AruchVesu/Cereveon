package ai.chesscoach.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.abs

/**
 * Atrium banded evaluation graph for the post-game review.
 *
 * Renders the server's per-position BAND series (§39: five
 * player-relative steps, never centipawns) as a stepped line across
 * the game, with tappable critical-moment markers.  This is the
 * review-screen sibling of [EvalBandView] and inherits its charter:
 * NO numeric eval axis, NO PV lines, NO move arrows — the y-axis is
 * the five band steps and nothing finer, which is exactly the
 * resolution the wire provides.
 *
 * Marker colours follow the Atrium two-tone signal convention:
 * amber for "something went wrong" moments (blunder / missed win /
 * mistake), cyan for the positive moment (punished mistake), muted
 * for strategic notes.  Tapping within [TOUCH_SLOP_DP] of a marker
 * fires [onMomentTap] with that moment's ply.
 */
class ReviewEvalGraphView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    /** One tappable marker: the moment's ply + its type for colouring. */
    data class Marker(val ply: Int, val momentType: String)

    companion object {
        /**
         * Band string → vertical level (0 = losing … 4 = winning).
         * Unknown strings clamp to the middle so a future band value
         * degrades to "equal" rather than crashing the graph.
         * Pure — pinned by GameReviewMappingTest.
         */
        fun bandLevel(band: String?): Int = when (band) {
            "losing" -> 0
            "worse" -> 1
            "equal" -> 2
            "better" -> 3
            "winning" -> 4
            else -> 2
        }

        /** True for moment types drawn in the amber "warning" role. */
        fun isNegativeType(momentType: String): Boolean = when (momentType) {
            ReviewMoment.TYPE_BLUNDER,
            ReviewMoment.TYPE_MISSED_WIN,
            ReviewMoment.TYPE_MISTAKE,
            -> true
            else -> false
        }

        private const val TOUCH_SLOP_DP = 24f
        private const val LEVELS = 5
    }

    /** Fired with the tapped marker's ply.  Set by the review sheet. */
    var onMomentTap: ((Int) -> Unit)? = null

    private var bands: List<String> = emptyList()
    private var markers: List<Marker> = emptyList()

    private val accentCyan = ContextCompat.getColor(context, R.color.atrium_accent_cyan)
    private val accentAmber = ContextCompat.getColor(context, R.color.atrium_accent_amber)
    private val muted = ContextCompat.getColor(context, R.color.atrium_muted)
    private val hairline = ContextCompat.getColor(context, R.color.atrium_hairline)

    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = hairline
        strokeWidth = 1f
        style = Paint.Style.STROKE
    }
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = accentCyan
        strokeWidth = dp(2f)
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }
    private val markerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val markerRingPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(1.5f)
    }

    /** Replace graph data and redraw.  [bands] indexes = board positions. */
    fun setData(bands: List<String>, markers: List<Marker>) {
        this.bands = bands
        this.markers = markers
        invalidate()
    }

    private fun dp(v: Float): Float =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, resources.displayMetrics)

    private fun xForIndex(index: Int, w: Float): Float =
        if (bands.size <= 1) 0f else index / (bands.size - 1).toFloat() * w

    private fun yForLevel(level: Int, h: Float): Float {
        // 10% padding top and bottom; level 4 (winning) at the top.
        val usable = h * 0.8f
        return h * 0.1f + (LEVELS - 1 - level) / (LEVELS - 1).toFloat() * usable
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (bands.size < 2) return
        val w = width.toFloat()
        val h = height.toFloat()

        // Five faint level guides — the banded "axis".
        for (level in 0 until LEVELS) {
            val y = yForLevel(level, h)
            canvas.drawLine(0f, y, w, y, gridPaint)
        }

        // Stepped band line.
        val path = Path()
        bands.forEachIndexed { i, band ->
            val x = xForIndex(i, w)
            val y = yForLevel(bandLevel(band), h)
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        canvas.drawPath(path, linePaint)

        // Critical-moment markers (position AFTER the move at index=ply).
        markers.forEach { marker ->
            if (marker.ply !in bands.indices) return@forEach
            val x = xForIndex(marker.ply, w)
            val y = yForLevel(bandLevel(bands[marker.ply]), h)
            val color = when {
                isNegativeType(marker.momentType) -> accentAmber
                marker.momentType == ReviewMoment.TYPE_PUNISHED_MISTAKE -> accentCyan
                else -> muted
            }
            markerPaint.color = color
            markerRingPaint.color = color
            canvas.drawCircle(x, y, dp(4f), markerPaint)
            canvas.drawCircle(x, y, dp(7f), markerRingPaint)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_UP && bands.size > 1) {
            val slop = dp(TOUCH_SLOP_DP)
            val hit = markers
                .filter { it.ply in bands.indices }
                .minByOrNull { abs(xForIndex(it.ply, width.toFloat()) - event.x) }
                ?.takeIf { abs(xForIndex(it.ply, width.toFloat()) - event.x) <= slop }
            if (hit != null) {
                onMomentTap?.invoke(hit.ply)
                performClick()
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }
}
