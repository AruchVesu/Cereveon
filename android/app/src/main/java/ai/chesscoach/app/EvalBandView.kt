package ai.chesscoach.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View

/**
 * Atrium eval band — the only allowed visual representation of the
 * engine's evaluation in this UI.
 *
 * Per architecture rule and Atrium handoff: NO numeric eval, NO PV
 * lines, NO move arrows.  The band shows the current ESV band as one
 * of five steps along a horizontal track:
 *
 *     LOSING — WORSE — EQUAL — BETTER — WINNING
 *
 * A glowing dot sits at the current band's tick.  The leading
 * gradient fills from track-start to the dot in either accent_cyan
 * (player) or accent_amber (opponent), depending on which side the
 * advantage favours.
 *
 * Animate band changes by calling [setBand] with `animate = true`.
 */
class EvalBandView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    enum class Band { LOSING, WORSE, EQUAL, BETTER, WINNING }
    enum class Side { WHITE, BLACK }

    private var band: Band = Band.EQUAL
    private var side: Side = Side.WHITE

    /** Animated 0–1 position of the indicator (matches band ordinal / 4). */
    private var position: Float = 0.5f

    private val trackHeight = dp(6f)
    private val dotRadius = dp(6f)
    private val dotGlowRadius = dp(12f)

    private val trackBg = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = HAIRLINE
        style = Paint.Style.FILL
    }
    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = HAIRLINE_STRONG
        style = Paint.Style.FILL
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }

    private val trackRect = RectF()

    init {
        if (attrs != null) {
            val a = context.obtainStyledAttributes(attrs, R.styleable.EvalBandView)
            val bandIdx = a.getInt(R.styleable.EvalBandView_atriumBand, 2)
            val sideIdx = a.getInt(R.styleable.EvalBandView_atriumSide, 0)
            band = Band.entries[bandIdx.coerceIn(0, Band.entries.size - 1)]
            side = if (sideIdx == 1) Side.BLACK else Side.WHITE
            position = band.ordinal / 4f
            a.recycle()
        }
    }

    /**
     * Update the displayed band.  When [animate] is true, the indicator
     * position transitions over 400ms with a cubic-bezier(.3,.9,.3,1)
     * curve as specified by the handoff motion notes.
     */
    fun setBand(newBand: Band, newSide: Side = side, animate: Boolean = true) {
        band = newBand
        side = newSide
        val target = newBand.ordinal / 4f
        if (!animate) {
            position = target
            invalidate()
            return
        }
        animate()
            .cancel()
        // Manual property animator — single value, no allocation in onDraw.
        val start = position
        val anim = android.animation.ValueAnimator.ofFloat(start, target).apply {
            duration = 400
            interpolator = android.view.animation.PathInterpolator(0.3f, 0.9f, 0.3f, 1f)
            addUpdateListener { va ->
                position = va.animatedValue as Float
                invalidate()
            }
        }
        anim.start()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        // Band needs vertical room for the glowing dot; use the larger of
        // dot diameter or the requested height.
        val desiredH = (dotRadius * 2 + dp(2f)).toInt()
        setMeasuredDimension(
            resolveSize(MeasureSpec.getSize(widthMeasureSpec), widthMeasureSpec),
            resolveSize(desiredH, heightMeasureSpec),
        )
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        val cy = h / 2f

        // Track background — hairline-faint capsule.
        trackRect.set(0f, cy - trackHeight / 2f, w, cy + trackHeight / 2f)
        canvas.drawRoundRect(trackRect, trackHeight / 2f, trackHeight / 2f, trackBg)

        // Five band ticks.
        for (i in 0..4) {
            val tx = (i / 4f) * w
            canvas.drawRect(tx - 0.5f, cy - trackHeight / 2f, tx + 0.5f, cy + trackHeight / 2f, tickPaint)
        }

        val signalColor = if (band == Band.WINNING || band == Band.BETTER) {
            if (side == Side.WHITE) ACCENT_CYAN else ACCENT_AMBER
        } else NEUTRAL_GREY

        // Filled portion — left edge to indicator, gradient transparent → signal.
        val fillEnd = (position * w).coerceIn(0f, w)
        if (fillEnd > 0f) {
            fillPaint.shader = LinearGradient(
                0f, 0f, fillEnd, 0f,
                Color.TRANSPARENT, signalColor,
                Shader.TileMode.CLAMP,
            )
            val fillRect = RectF(0f, cy - trackHeight / 2f, fillEnd, cy + trackHeight / 2f)
            canvas.drawRoundRect(fillRect, trackHeight / 2f, trackHeight / 2f, fillPaint)
            fillPaint.shader = null
        }

        // Indicator dot with soft halo.
        val dotX = (position * w).coerceIn(dotRadius, w - dotRadius)
        dotPaint.color = signalColor
        dotPaint.setShadowLayer(dotGlowRadius, 0f, 0f, signalColor)
        // setShadowLayer requires software layer — apply once at first draw.
        if (layerType != LAYER_TYPE_SOFTWARE) setLayerType(LAYER_TYPE_SOFTWARE, null)
        canvas.drawCircle(dotX, cy, dotRadius, dotPaint)
    }

    private fun dp(value: Float): Float =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, resources.displayMetrics)

    companion object {
        private val HAIRLINE        = Color.parseColor("#14FFFFFF")
        private val HAIRLINE_STRONG = Color.parseColor("#1FFFFFFF")
        private val ACCENT_CYAN     = Color.parseColor("#4FD9E5")
        private val ACCENT_AMBER    = Color.parseColor("#FFC069")
        private val NEUTRAL_GREY    = Color.parseColor("#7A8094")
    }
}
