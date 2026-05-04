package ai.chesscoach.app

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View

/**
 * Atrium typing-dots indicator.
 *
 * Three cyan dots, staggered pulse — opacity 1 ↔ 0.45 over 1.8s with
 * a 0.15s offset between dots, matching the handoff `cv-pulse`
 * keyframe.  Drawn in Canvas so the staggered animation runs at the
 * View level without three child Views.
 *
 * Show by setting `visibility = View.VISIBLE`; the animator starts on
 * attach and stops on detach.  Cheap to leave in the layout — when
 * GONE the View consumes no draw cycles.
 */
class AtriumTypingDotsView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ACCENT_CYAN
        style = Paint.Style.FILL
    }

    private val dotRadius = dp(3f)
    private val dotSpacing = dp(8f)

    /** Phase per dot, 0..1.  Each ValueAnimator drives one slot. */
    private val phase = floatArrayOf(0f, 0f, 0f)
    private val animators = arrayOfNulls<ValueAnimator>(3)

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val desiredW = (dotRadius * 2 * 3 + dotSpacing * 2).toInt()
        val desiredH = (dotRadius * 2 + dp(2f)).toInt()
        setMeasuredDimension(
            resolveSize(desiredW, widthMeasureSpec),
            resolveSize(desiredH, heightMeasureSpec),
        )
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        for (i in 0..2) {
            animators[i]?.cancel()
            animators[i] = ValueAnimator.ofFloat(0f, 1f).apply {
                duration = 1800
                repeatCount = ValueAnimator.INFINITE
                repeatMode = ValueAnimator.RESTART
                startDelay = (i * 150L)
                addUpdateListener { va ->
                    phase[i] = va.animatedValue as Float
                    invalidate()
                }
                start()
            }
        }
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        for (i in animators.indices) {
            animators[i]?.cancel()
            animators[i] = null
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val cy = height / 2f
        val firstX = dotRadius
        for (i in 0..2) {
            val cx = firstX + i * (dotRadius * 2 + dotSpacing)
            // Triangle-wave between 1.0 and 0.45 with the cv-pulse shape.
            val t = phase[i]
            val pulse = if (t < 0.5f) 1f - (t * 2f) * 0.55f else 0.45f + ((t - 0.5f) * 2f) * 0.55f
            dotPaint.alpha = (pulse * 255f).toInt().coerceIn(0, 255)
            canvas.drawCircle(cx, cy, dotRadius, dotPaint)
        }
    }

    private fun dp(value: Float): Float =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, resources.displayMetrics)

    companion object {
        private val ACCENT_CYAN = Color.parseColor("#4FD9E5")
    }
}
