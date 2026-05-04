package ai.chesscoach.app

import android.content.Context
import android.util.AttributeSet
import android.widget.FrameLayout

/**
 * Atrium card — FrameLayout with [AtriumCardBackground] applied.
 *
 * Use from XML to skip the per-screen Kotlin boilerplate of building
 * the background drawable manually:
 *
 *     <ai.chesscoach.app.AtriumCardView
 *         android:layout_width="match_parent"
 *         android:layout_height="wrap_content"
 *         android:padding="16dp"
 *         app:atriumCardTicks="true">
 *         …card content…
 *     </ai.chesscoach.app.AtriumCardView>
 *
 * Set `app:atriumCardTicks="false"` to suppress the corner ticks
 * (e.g. for nested rows or quieter cards).
 */
class AtriumCardView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : FrameLayout(context, attrs, defStyleAttr) {

    init {
        var showTicks = true
        if (attrs != null) {
            val a = context.obtainStyledAttributes(attrs, R.styleable.AtriumCardView)
            showTicks = a.getBoolean(R.styleable.AtriumCardView_atriumCardTicks, true)
            a.recycle()
        }
        background = AtriumCardBackground(context, showTicks = showTicks)
    }
}
