package ai.chesscoach.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.util.TypedValue

/**
 * Atrium card background.
 *
 * Renders:
 *   - 1px hairline border at [borderColor].
 *   - Optional corner ticks (12dp by default) at the top-left and
 *     bottom-right corners in [tickColor] for the "official document"
 *     feel called out in the handoff:
 *
 *         "Cards: 1px hairline border, optional 4 corner ticks
 *          (12px, accent color, top-left and bottom-right)"
 *
 * Use as the background of any FrameLayout / LinearLayout / View:
 *
 *     view.background = AtriumCardBackground(context, showTicks = true)
 *
 * Square-ish 2dp corner radius is intentional — Atrium never uses
 * pill or fully-rounded shapes.
 */
class AtriumCardBackground(
    context: Context,
    private val borderColor: Int = Color.parseColor("#1FFFFFFF"), // atrium_hairline_strong
    private val tickColor: Int = Color.parseColor("#4FD9E5"),     // atrium_accent_cyan
    private val showTicks: Boolean = true,
    tickLengthDp: Float = 12f,
    borderWidthDp: Float = 1f,
    cornerRadiusDp: Float = 2f,
) : Drawable() {

    private val tickLength: Float = dp(context, tickLengthDp)
    private val borderWidth: Float = dp(context, borderWidthDp)
    private val cornerRadius: Float = dp(context, cornerRadiusDp)

    private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = borderColor
        style = Paint.Style.STROKE
        strokeWidth = this@AtriumCardBackground.borderWidth
    }

    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = tickColor
        style = Paint.Style.STROKE
        strokeWidth = this@AtriumCardBackground.borderWidth
        strokeCap = Paint.Cap.SQUARE
    }

    private val borderRect = RectF()

    override fun draw(canvas: Canvas) {
        val b = bounds
        // Hairline border — inset by half the stroke so the line sits
        // inside the bounds (Android paints stroke centred on the path).
        val inset = borderWidth / 2f
        borderRect.set(
            b.left + inset, b.top + inset,
            b.right - inset, b.bottom - inset,
        )
        canvas.drawRoundRect(borderRect, cornerRadius, cornerRadius, borderPaint)

        if (!showTicks) return

        // Top-left tick (L-shape: horizontal + vertical from the corner).
        val tlx = b.left + inset
        val tly = b.top + inset
        canvas.drawLine(tlx, tly, tlx + tickLength, tly, tickPaint)
        canvas.drawLine(tlx, tly, tlx, tly + tickLength, tickPaint)

        // Bottom-right tick.
        val brx = b.right - inset
        val bry = b.bottom - inset
        canvas.drawLine(brx, bry, brx - tickLength, bry, tickPaint)
        canvas.drawLine(brx, bry, brx, bry - tickLength, tickPaint)
    }

    override fun setAlpha(alpha: Int) {
        borderPaint.alpha = alpha
        tickPaint.alpha = alpha
    }

    override fun setColorFilter(colorFilter: ColorFilter?) {
        borderPaint.colorFilter = colorFilter
        tickPaint.colorFilter = colorFilter
    }

    @Deprecated("getOpacity() is deprecated in Android API 29; required by Drawable contract.")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT

    private companion object {
        fun dp(ctx: Context, value: Float): Float =
            TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, value, ctx.resources.displayMetrics,
            )
    }
}
