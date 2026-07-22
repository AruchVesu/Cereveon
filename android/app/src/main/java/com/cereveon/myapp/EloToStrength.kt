package com.cereveon.myapp

import android.app.AlertDialog
import android.content.Intent
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/**
 * Account-section flows shared by every host of [SettingsBottomSheet]
 * — MainActivity's drawer entry and HomeActivity's avatar entry.
 *
 * Extracted verbatim from MainActivity when the Settings sheet gained
 * its second host: password validation, error copy, and the logout
 * routing are user-visible contracts that must not drift between the
 * two hosts.
 */
object AccountFlows {

    /**
     * Change-password dialog: two password fields, client-side
     * validation (non-blank, new ≥ 8 chars — mirrors the server rule),
     * then POST via [AuthApiClient.changePassword] with a toast for
     * each outcome.  Cancel is a no-op.
     */
    fun showChangePasswordDialog(
        activity: AppCompatActivity,
        authRepo: AuthRepository,
        authApiClient: AuthApiClient,
    ) {
        if (activity.isFinishing || activity.isDestroyed) return
        val layout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(64, 32, 64, 16)
        }
        val etCurrent = EditText(activity).apply {
            hint = "Current password"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val etNew = EditText(activity).apply {
            hint = "New password (min 8 characters)"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        layout.addView(etCurrent)
        layout.addView(etNew)

        AlertDialog.Builder(activity)
            .setTitle("Change Password")
            .setView(layout)
            .setPositiveButton("Save") { _, _ ->
                val current = etCurrent.text.toString()
                val new = etNew.text.toString()
                if (current.isBlank() || new.isBlank()) {
                    Toast.makeText(activity, "Fields must not be empty.", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                if (new.length < 8) {
                    Toast.makeText(activity, "New password must be at least 8 characters.", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                val token = authRepo.getToken() ?: return@setPositiveButton
                activity.lifecycleScope.launch {
                    when (authApiClient.changePassword(current, new, token)) {
                        is ApiResult.Success ->
                            Toast.makeText(activity, "Password updated.", Toast.LENGTH_SHORT).show()
                        is ApiResult.HttpError ->
                            Toast.makeText(activity, "Incorrect current password.", Toast.LENGTH_SHORT).show()
                        else ->
                            Toast.makeText(activity, "Network error. Please try again.", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Best-effort server logout, local token clear, then a hard route
     * to [LoginActivity] with NEW_TASK|CLEAR_TASK so back-navigation
     * can never re-enter an authenticated surface.
     */
    fun performLogout(
        activity: AppCompatActivity,
        authRepo: AuthRepository,
        authApiClient: AuthApiClient,
    ) {
        val token = authRepo.getToken()
        activity.lifecycleScope.launch {
            if (token != null) {
                authApiClient.logout(token)   // best-effort; ignore result
            }
            authRepo.clearToken()
            activity.startActivity(
                Intent(activity, LoginActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            activity.finish()
        }
    }
}
package com.cereveon.myapp

/**
 * Schema-version constants for the coaching backend HTTP API.
 *
 * Every outgoing request to the backend carries
 * ``X-API-Version: <COACH_API_VERSION>``.  The server reads the header
 * and rejects mismatched versions with HTTP 400; missing headers are
 * tolerated in Phase 1 (server-side lenient mode) but expected to flip
 * to strict-on-missing once production telemetry confirms reliability.
 *
 * Bump this constant when the request/response shape of any coaching
 * endpoint changes in a way that older client builds would interpret
 * incorrectly.  Additive changes (new optional response fields, new
 * optional request fields with safe defaults) do NOT need a bump —
 * old clients continue to ignore the new fields.
 *
 * Single source of truth, deliberately at the package root rather than
 * per-client, so all four HTTP surfaces (GameApiClient, AuthApiClient,
 * LiveMoveApiClient, EngineEvalApiClient, CoachApiClient) read the same
 * value with no risk of drift.  See `docs/API_CONTRACTS.md` >
 * "API schema versioning" for the migration policy.
 */
const val COACH_API_VERSION: String = "2"

const val COACH_API_VERSION_HEADER: String = "X-API-Version"
package com.cereveon.myapp

import android.content.Context
import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.util.TypedValue
import androidx.core.content.ContextCompat

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
    // Token reads (not literals) so the card chrome follows the active
    // palette — bright mode flips these via values-notnight/colors.xml.
    private val borderColor: Int = ContextCompat.getColor(context, R.color.atrium_hairline_strong),
    private val tickColor: Int = ContextCompat.getColor(context, R.color.atrium_accent_cyan),
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
package com.cereveon.myapp

import android.content.Context
import android.util.AttributeSet
import android.widget.FrameLayout

/**
 * Atrium card — FrameLayout with [AtriumCardBackground] applied.
 *
 * Use from XML to skip the per-screen Kotlin boilerplate of building
 * the background drawable manually:
 *
 *     <com.cereveon.myapp.AtriumCardView
 *         android:layout_width="match_parent"
 *         android:layout_height="wrap_content"
 *         android:padding="16dp"
 *         app:atriumCardTicks="true">
 *         …card content…
 *     </com.cereveon.myapp.AtriumCardView>
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
package com.cereveon.myapp

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat

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
        // Token reads so the rule follows the active palette (bright
        // mode flips both via values-notnight/colors.xml).
        private val hairline = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = ContextCompat.getColor(context, R.color.atrium_hairline_strong)
            strokeWidth = TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, 1f, context.resources.displayMetrics,
            )
        }
        private val ornamentPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = ContextCompat.getColor(context, R.color.atrium_muted)
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
    }
}
package com.cereveon.myapp

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import androidx.core.content.ContextCompat

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

    // Token read so the dots follow the active palette (bright mode
    // flips the accent via values-notnight/colors.xml).
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_accent_cyan)
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
}
package com.cereveon.myapp

import kotlinx.serialization.encodeToString
import java.net.HttpURLConnection

/**
 * Shared client interface for the backend authentication endpoints.
 *
 * Returns [ApiResult] on every call — callers never see raw exceptions.
 * Implementations are safe to call from any coroutine context.
 */
interface AuthApiClient {

    /**
     * POST /auth/login.
     *
     * @return [ApiResult.Success] with [LoginResponse] on HTTP 200.
     *         [ApiResult.HttpError(401)] for invalid credentials.
     *         [ApiResult.Timeout] or [ApiResult.NetworkError] on transport failures.
     */
    suspend fun login(email: String, password: String): ApiResult<LoginResponse>

    /**
     * POST /auth/logout.
     *
     * Sends [token] in the Authorization header; invalidates the server-side
     * session so the token can no longer be used.
     *
     * @return [ApiResult.Success(Unit)] on HTTP 200; error variants otherwise.
     */
    suspend fun logout(token: String): ApiResult<Unit>

    /**
     * GET /auth/me.
     *
     * Returns the authenticated player's profile (id, email, rating, confidence).
     * Called at cold-start so the rating header is populated before the first game.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so that test
     * fakes that only override login/logout do not need to implement this method.
     *
     * @return [ApiResult.Success] with [MeResponse] on HTTP 200;
     *         [ApiResult.HttpError(401)] when token is invalid or expired;
     *         [ApiResult.Timeout] or [ApiResult.NetworkError] on transport failures.
     */
    suspend fun me(token: String): ApiResult<MeResponse> = ApiResult.HttpError(501)

    /**
     * POST /auth/register.
     *
     * Creates a new player account.  Returns the same shape as [login] on
     * success (HTTP 200/201) so the caller can immediately save the token
     * and navigate to [MainActivity].
     *
     * Default implementation returns [ApiResult.HttpError(501)] so that test
     * fakes do not need to override this method.
     *
     * @return [ApiResult.Success] with [LoginResponse] on success;
     *         [ApiResult.HttpError(409)] when email is already registered;
     *         [ApiResult.Timeout] or [ApiResult.NetworkError] on transport failures.
     */
    suspend fun register(email: String, password: String): ApiResult<LoginResponse> =
        ApiResult.HttpError(501)

    /**
     * POST /auth/lichess — "Sign in with Lichess" (OAuth PKCE).
     *
     * Forwards the one-time authorization [code] from the Lichess
     * redirect plus the PKCE [codeVerifier] that [LichessOAuth] minted
     * for this attempt; the backend performs the code exchange and
     * returns the same token shape as [login] (`docs/API_CONTRACTS.md`
     * §16a).  Transparently creates the account on first sign-in.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test
     * fakes do not need to override this method.
     *
     * @return [ApiResult.Success] with [LoginResponse] on HTTP 200;
     *         [ApiResult.HttpError(401)] when Lichess rejected the grant
     *         (restart the authorization flow); (502)/(503) on Lichess
     *         upstream / rate-limit; transport variants otherwise.
     */
    suspend fun loginWithLichess(
        code: String,
        codeVerifier: String,
    ): ApiResult<LoginResponse> = ApiResult.HttpError(501)

    /**
     * POST /auth/change-password.
     *
     * Requires a valid [token] (Bearer). Returns [ApiResult.HttpError(400)] when
     * [currentPassword] does not match the stored hash.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test fakes do
     * not need to override this method.
     */
    suspend fun changePassword(
        currentPassword: String,
        newPassword: String,
        token: String,
    ): ApiResult<Unit> = ApiResult.HttpError(501)

    /**
     * PATCH /auth/me — partial profile update.
     *
     * Forwards the calibration estimate produced by the Onboarding
     * screen so the server's adaptation layer can dispatch the first
     * opponent at the right level instead of waiting for rating drift
     * to converge.
     *
     * At least one of [rating] / [confidence] must be non-null, otherwise
     * the backend returns 400.  Both null is allowed at the call site
     * (callers can compose the request from optional fields without
     * pre-filtering); the backend is the source of truth for the bound
     * checks (rating in (0, 4000], confidence in [0, 1]).
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test
     * fakes that only override login/logout do not need to implement
     * this method.
     *
     * @return [ApiResult.Success] with the *post-update* [MeResponse]
     *         on HTTP 200 — same shape as [me] so the client can
     *         replace its cache from this single round-trip.
     *         [ApiResult.HttpError(400)] on out-of-bounds values or
     *         empty payload; [ApiResult.HttpError(401)] on invalid
     *         token; transport variants otherwise.
     */
    suspend fun updateMe(
        token: String,
        rating: Float? = null,
        confidence: Float? = null,
    ): ApiResult<MeResponse> = ApiResult.HttpError(501)
}

/**
 * Production [AuthApiClient] backed by [HttpURLConnection].
 *
 * All I/O is dispatched to [Dispatchers.IO] — safe to call from any coroutine.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash
 *                         (e.g. "http://10.0.2.2:8000").
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 */
class HttpAuthApiClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    /**
     * Optional sink for the X-Auth-Token refresh header — see
     * [TokenRefresh].  When provided, every successful authenticated
     * response (currently [me], [updateMe], [changePassword]) hands
     * the freshly-minted JWT to this lambda so callers can rotate
     * their stored token transparently.  Null disables the rotation
     * (test fakes / clients that don't store tokens).
     */
    val tokenSink: ((String) -> Unit)? = null,
) : AuthApiClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val LOGIN_PATH = "/auth/login"
        private const val LICHESS_LOGIN_PATH = "/auth/lichess"
        private const val LOGOUT_PATH = "/auth/logout"
        private const val ME_PATH = "/auth/me"
        private const val REGISTER_PATH = "/auth/register"
        private const val CHANGE_PASSWORD_PATH = "/auth/change-password"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    private fun bearerHeader(token: String): Map<String, String> =
        mapOf("Authorization" to "Bearer $token")

    private fun refreshOnSuccess(): (java.net.HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun login(
        email: String,
        password: String,
    ): ApiResult<LoginResponse> = http.request(
        path = LOGIN_PATH,
        method = "POST",
        body = ApiJson.encodeToString(
            LoginRequest(email = email, password = password, deviceInfo = "android")
        ),
        parse = { body -> ApiJson.decodeFromString<LoginResponse>(body) },
    )

    override suspend fun loginWithLichess(
        code: String,
        codeVerifier: String,
    ): ApiResult<LoginResponse> = http.request(
        path = LICHESS_LOGIN_PATH,
        method = "POST",
        body = ApiJson.encodeToString(
            LichessLoginRequest(code = code, codeVerifier = codeVerifier, deviceInfo = "android")
        ),
        parse = { body -> ApiJson.decodeFromString<LoginResponse>(body) },
    )

    override suspend fun logout(token: String): ApiResult<Unit> = http.requestNoBody(
        path = LOGOUT_PATH,
        method = "POST",
        headers = bearerHeader(token),
    )

    override suspend fun me(token: String): ApiResult<MeResponse> = http.request(
        path = ME_PATH,
        method = "GET",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<MeResponse>(body) },
    )

    override suspend fun updateMe(
        token: String,
        rating: Float?,
        confidence: Float?,
    ): ApiResult<MeResponse> = http.request(
        // POST + X-HTTP-Method-Override: PATCH — the JDK's HttpURLConnection
        // rejects PATCH as a request method on JDK 17 (the host JVM tests
        // target), but Android's OkHttp-backed implementation accepts it.
        // Going via the override header keeps a single code path that
        // works on both runtimes; the backend strips the header in the
        // http_method_override middleware in server.py and routes it
        // as a real PATCH /auth/me.
        path = ME_PATH,
        method = "POST",
        headers = bearerHeader(token) + ("X-HTTP-Method-Override" to "PATCH"),
        // ``ApiJson.encodeDefaults = false`` strips null fields from the
        // wire payload so the server-side validators (rating: float | None,
        // confidence: float | None) get exactly the keys the client
        // intended to update.  Sending {} (both null) produces a 400 from
        // the backend — preserved so a malformed call surfaces
        // immediately rather than appearing as a no-op success.
        body = ApiJson.encodeToString(
            UpdateMeRequest(rating = rating, confidence = confidence)
        ),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<MeResponse>(body) },
    )

    override suspend fun changePassword(
        currentPassword: String,
        newPassword: String,
        token: String,
    ): ApiResult<Unit> = http.requestNoBody(
        path = CHANGE_PASSWORD_PATH,
        method = "POST",
        headers = bearerHeader(token),
        body = ApiJson.encodeToString(
            ChangePasswordRequest(
                currentPassword = currentPassword,
                newPassword = newPassword,
            )
        ),
        onResponse = refreshOnSuccess(),
    )

    override suspend fun register(
        email: String,
        password: String,
    ): ApiResult<LoginResponse> = http.request(
        path = REGISTER_PATH,
        method = "POST",
        body = ApiJson.encodeToString(
            RegisterRequest(email = email, password = password, deviceInfo = "android")
        ),
        // Register is the only endpoint that returns 201 Created in
        // addition to 200 OK; widen the success set accordingly.
        successCodes = setOf(HttpURLConnection.HTTP_OK, HttpURLConnection.HTTP_CREATED),
        parse = { body -> ApiJson.decodeFromString<LoginResponse>(body) },
    )
}
package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed request/response models for the backend authentication endpoints.
 *
 * Sprint 4.3.C migrated these off hand-rolled ``org.json.JSONObject``
 * parsing onto kotlinx-serialization.  ``@SerialName`` annotations
 * preserve the snake_case wire format the FastAPI backend emits while
 * keeping the Kotlin properties camelCase.
 */

/**
 * Request body for POST /auth/login.
 */
@Serializable
data class LoginRequest(
    val email: String,
    val password: String,
    /** Device fingerprint forwarded to the backend session record. */
    @SerialName("device_info") val deviceInfo: String = "",
)

/**
 * Typed response from POST /auth/login and POST /auth/register.
 */
@Serializable
data class LoginResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("player_id") val playerId: String,
    @SerialName("token_type") val tokenType: String = "bearer",
)

/** Response from POST /auth/logout. Backend returns {"status": "logged_out"}. */
@Serializable
data class LogoutResponse(val status: String)

/**
 * Response from GET /auth/me.
 *
 * Returns the authenticated player's current profile.  Used to sync the
 * rating display at cold-start without waiting for a /game/finish round.
 */
@Serializable
data class MeResponse(
    val id: String = "",
    val email: String = "",
    val rating: Float = 0f,
    val confidence: Float = 0f,
    /**
     * Per-skill weakness scores from the SECA skill tracker.
     * Keys are skill names (e.g. "tactics", "endgame"); values are 0.0–1.0
     * where higher means more weakness in that area.
     * Empty when the player has no game history yet.
     */
    @SerialName("skill_vector") val skillVector: Map<String, Float> = emptyMap(),
    /**
     * Monotonic XP counter incremented when the player completes a training
     * exercise (seed = replay of an engine-flagged mistake; derivatives =
     * weekly micro-tasks of the same mistake pattern in new positions).
     *
     * Replaces the user-visible Elo rating on the Home screen — ``rating``
     * and ``confidence`` are still returned because they drive adaptive
     * opponent selection internally, but they are no longer displayed.
     */
    @SerialName("training_xp") val trainingXp: Int = 0,
)

/**
 * Request body for POST /auth/register — same shape as /auth/login.
 * (The previous hand-rolled client inlined the JSON body; this class
 * lets every endpoint go through ``ApiJson.encodeToString`` uniformly.)
 *
 * Default for [deviceInfo] is empty so that the explicit ``"android"``
 * the production client passes is *not* equal to the default — with
 * ``ApiJson.encodeDefaults = false`` a value equal to the declared
 * default would be stripped from the wire payload (regression caught
 * by ``INT_REG_DEVICE_INFO``).
 */
@Serializable
data class RegisterRequest(
    val email: String,
    val password: String,
    @SerialName("device_info") val deviceInfo: String = "",
)

/**
 * Request body for POST /auth/lichess — "Sign in with Lichess".
 *
 * [code] is the one-time OAuth authorization code from the Lichess
 * redirect; [codeVerifier] is the PKCE verifier that [LichessOAuth]
 * generated for this attempt.  The SERVER performs the code exchange
 * (see `docs/API_CONTRACTS.md` §16a) so no Lichess token ever reaches
 * the device.  The response is a [LoginResponse] superset — the extra
 * `created` / `lichess_username` fields are ignored by the shared
 * [ApiJson] config (`ignoreUnknownKeys`).
 */
@Serializable
data class LichessLoginRequest(
    val code: String,
    @SerialName("code_verifier") val codeVerifier: String,
    /** Device fingerprint forwarded to the backend session record. */
    @SerialName("device_info") val deviceInfo: String = "",
)

/**
 * Request body for POST /auth/change-password.  Both fields are
 * length-bounded server-side (1000 char max).
 */
@Serializable
data class ChangePasswordRequest(
    @SerialName("current_password") val currentPassword: String,
    @SerialName("new_password") val newPassword: String,
)

/**
 * Request body for PATCH /auth/me — partial profile update.  Either
 * or both fields may be absent.  Sending both null produces a 400 from
 * the backend.  ``encodeDefaults = false`` on the shared
 * [ApiJson] config ensures null fields are stripped from the wire
 * payload so the server-side ``rating: float | None = None`` /
 * ``confidence: float | None = None`` validators get exactly the
 * keys the client intended to update.
 */
@Serializable
data class UpdateMeRequest(
    val rating: Float? = null,
    val confidence: Float? = null,
)

/**
 * Current authentication state of the user in the application.
 *
 * Callers must handle both variants; use an exhaustive `when` expression.
 */
sealed class AuthState {
    /** User is logged in and holds a non-expired [token]. */
    data class Authenticated(val token: String, val playerId: String) : AuthState()

    /** User is not logged in, or the stored token has expired. */
    object Unauthenticated : AuthState()
}

// ---------------------------------------------------------------------------
// JWT utility functions — pure JVM; no Android dependencies
// ---------------------------------------------------------------------------

/**
 * Parse the `exp` (expiry) Unix timestamp from a JWT payload without full
 * signature validation. Returns null if the token is structurally malformed
 * or does not contain an `exp` field.
 *
 * Uses [java.util.Base64] (Java 8+), available in both JVM unit tests and
 * the Android runtime, so no additional dependencies are required.
 */
fun parseJwtExpiry(token: String): Long? {
    return try {
        val parts = token.split(".")
        if (parts.size != 3) return null
        // Base64url padding: must be a multiple of 4.
        val padded = parts[1].padEnd((parts[1].length + 3) / 4 * 4, '=')
        val payloadBytes = java.util.Base64.getUrlDecoder().decode(padded)
        val payload = String(payloadBytes, Charsets.UTF_8)
        Regex(""""exp"\s*:\s*(\d+)""").find(payload)?.groupValues?.get(1)?.toLongOrNull()
    } catch (_: Exception) {
        null
    }
}

/**
 * Parse the `player_id` claim from a JWT payload without full signature
 * validation. Returns null if the token is malformed or lacks the field.
 */
fun parseJwtPlayerId(token: String): String? {
    return try {
        val parts = token.split(".")
        if (parts.size != 3) return null
        val padded = parts[1].padEnd((parts[1].length + 3) / 4 * 4, '=')
        val payloadBytes = java.util.Base64.getUrlDecoder().decode(padded)
        val payload = String(payloadBytes, Charsets.UTF_8)
        Regex(""""player_id"\s*:\s*"([^"]+)"""").find(payload)?.groupValues?.get(1)
    } catch (_: Exception) {
        null
    }
}

/**
 * Returns true if the JWT [token] is expired (i.e., its `exp` claim is in
 * the past, using the current system clock). Returns true (fail-closed) for
 * any malformed or unsigned token — the server always performs authoritative
 * validation; this check is only a client-side optimisation to avoid sending
 * known-expired tokens.
 */
fun isJwtExpired(token: String): Boolean {
    val exp = parseJwtExpiry(token) ?: return true
    return System.currentTimeMillis() / 1000 >= exp
}
package com.cereveon.myapp

/**
 * Manages the JWT lifecycle for the logged-in user.
 *
 * Wraps [TokenStorage] to provide semantic operations (save, retrieve, check
 * expiry, clear) without exposing raw storage details to callers.
 *
 * [isJwtExpired] is used for client-side optimisation only — the server
 * always performs authoritative JWT validation on every request.
 *
 * Thread safety: individual [TokenStorage] calls are atomic; composite
 * operations such as [isLoggedIn] are NOT atomic. Access only from the
 * main thread (or synchronise externally) if you share an instance.
 *
 * @param storage Backing store — use [EncryptedTokenStorage] in production,
 *                or an in-memory fake in JVM unit tests.
 */
class AuthRepository(private val storage: TokenStorage) {

    /**
     * Persist [token] to durable storage.
     * Call this immediately after a successful login or register response.
     *
     * @throws IllegalArgumentException if [token] is blank.
     */
    fun saveToken(token: String) {
        require(token.isNotBlank()) { "token must not be blank" }
        storage.save(token)
    }

    /**
     * Return the stored token, or null if the user has never logged in,
     * logged out, or the token was cleared for any other reason.
     *
     * Returns null (and clears storage) if the underlying [TokenStorage]
     * throws — e.g. on Android Keystore corruption. The caller receives null
     * exactly as if the user had never logged in, which causes [authState] to
     * return [AuthState.Unauthenticated] and redirect the user to login.
     *
     * The returned token may be expired — use [isLoggedIn] to combine
     * the presence check with an expiry check.
     */
    fun getToken(): String? = try {
        storage.load()
    } catch (e: Exception) {
        runCatching { storage.clear() }
        null
    }

    /**
     * Return the current [AuthState]:
     *  - [AuthState.Authenticated] when a non-expired token is stored.
     *  - [AuthState.Unauthenticated] otherwise.
     *
     * The `player_id` in [AuthState.Authenticated] is parsed from the JWT
     * payload without signature validation — treat it as informational only.
     */
    fun authState(): AuthState {
        val token = getToken() ?: return AuthState.Unauthenticated
        if (isJwtExpired(token)) return AuthState.Unauthenticated
        val playerId = parseJwtPlayerId(token) ?: ""
        return AuthState.Authenticated(token = token, playerId = playerId)
    }

    /**
     * Return true if a non-expired token is present in storage.
     *
     * This is a convenience wrapper over [authState]; prefer [authState]
     * when you also need the token or player ID.
     */
    fun isLoggedIn(): Boolean = authState() is AuthState.Authenticated

    /**
     * Remove the stored token, logging the user out on the client side.
     *
     * The caller is responsible for also calling the /auth/logout backend
     * endpoint to invalidate the server-side session.
     */
    fun clearToken() {
        storage.clear()
    }
}
package com.cereveon.myapp

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/**
 * Shared HTTP helper for the production API clients.
 *
 * Each concrete client (`CoachApiClient`, `AuthApiClient`, `GameApiClient`,
 * `EngineEvalApiClient`, `LiveMoveApiClient`) used to inline the same
 * ~25-line HTTP ceremony — open connection, set headers, set timeouts,
 * write the JSON body, branch on status code, map `SocketTimeoutException`
 * to [ApiResult.Timeout], map other exceptions to [ApiResult.NetworkError].
 * The audit (Sprint 4 review) called this out as the highest-leverage
 * duplication in the Android client: `GameApiClient.kt` alone carried
 * 15 nearly-identical copies of this block.
 *
 * This class centralises the ceremony into a single suspend function
 * [request]; concrete clients now only provide the path, method, headers,
 * body, and a per-method `parse` lambda.  Behavioural parity with the
 * pre-refactor clients is the load-bearing property:
 *
 *  - Same status-code branching: 200 → success; any other code →
 *    [ApiResult.HttpError(code)].
 *  - Same exception-to-result mapping: `SocketTimeoutException` →
 *    [ApiResult.Timeout]; everything else → [ApiResult.NetworkError].
 *  - Same response-body discipline: body is read only on 200; non-200
 *    responses are NOT consumed (preserves the existing pattern across
 *    auth/coach/game/engine/live-move clients).
 *  - `X-API-Version` is set on every request (matches the existing
 *    behaviour pinned by `ApiVersionHeaderTest`).
 *  - `Content-Type: application/json` is set automatically when [body]
 *    is non-null (matches existing POST/PATCH behaviour).
 *
 * The [onResponse] hook is for endpoints that need to inspect the
 * response *before* the parser runs — currently used only by the auth
 * client's me/updateMe/changePassword endpoints to consume the
 * `X-Auth-Token` rotation header (see [consumeRefreshedToken]).
 *
 * I/O is dispatched on [Dispatchers.IO]; callers can invoke from any
 * coroutine context.
 */
class BaseHttpClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
) {
    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = 8_000
        const val DEFAULT_READ_TIMEOUT_MS = 15_000
    }

    /**
     * Perform an HTTP request and map the response to [ApiResult].
     *
     * @param path       URL path (no scheme/host).  Concatenated onto [baseUrl].
     * @param method     HTTP method (GET / POST / PATCH / DELETE).
     * @param headers    Extra request headers.  `X-API-Version` is always set
     *                   automatically; `Content-Type: application/json` is set
     *                   automatically when [body] is non-null.
     * @param body         Request body string.  When non-null, the connection is
     *                     put in output mode, `Content-Type: application/json` is
     *                     set, and the body is written as UTF-8 bytes.
     * @param successCodes HTTP codes treated as success.  Defaults to `{200}`;
     *                     widen for endpoints that return 201 Created (auth
     *                     register).
     * @param onResponse   Optional hook called with the [HttpURLConnection] AFTER
     *                     a successful response is read but BEFORE [parse] runs.
     *                     Used to inspect response headers (e.g. the
     *                     `X-Auth-Token` refresh header on auth endpoints).
     * @param parse        Function that converts the response body text into [T].
     */
    suspend fun <T> request(
        path: String,
        method: String,
        headers: Map<String, String> = emptyMap(),
        body: String? = null,
        successCodes: Set<Int> = setOf(HttpURLConnection.HTTP_OK),
        onResponse: ((HttpURLConnection) -> Unit)? = null,
        parse: (String) -> T,
    ): ApiResult<T> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$baseUrl$path")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = method
            conn.setRequestProperty(COACH_API_VERSION_HEADER, COACH_API_VERSION)
            headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            if (body != null) {
                conn.setRequestProperty("Content-Type", "application/json")
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }

            val code = conn.responseCode
            if (code in successCodes) {
                // HttpURLConnection.inputStream throws FileNotFoundException
                // on any code >= 400 — the bytes live on errorStream
                // instead.  Most successCodes are 2xx (where inputStream
                // is the right channel), but callers can opt into wider
                // sets (e.g. getActiveGame treats 404 as Success(null)).
                // Pick the stream that actually has the body for the
                // observed code, and tolerate a null errorStream (some
                // 4xx responses have no body at all).
                val stream =
                    if (code >= 400) (conn.errorStream ?: java.io.ByteArrayInputStream(ByteArray(0)))
                    else conn.inputStream
                val text = stream.bufferedReader(Charsets.UTF_8).readText()
                onResponse?.invoke(conn)
                ApiResult.Success(parse(text))
            } else {
                // Carry the (bounded) error body so callers can act on
                // structured error contracts — e.g. the entitlements 402
                // quota body — rather than only the status code.  Error
                // bodies here are small JSON; 4 KB is a defensive cap.
                val errorBody = try {
                    conn.errorStream
                        ?.bufferedReader(Charsets.UTF_8)
                        ?.use { it.readText().take(4096) }
                } catch (_: Exception) {
                    null
                }
                ApiResult.HttpError(code, errorBody?.takeIf { it.isNotBlank() })
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    /**
     * Convenience for endpoints whose success response has no useful body
     * (POST /auth/logout, DELETE /repertoire/{eco} when the caller doesn't
     * need the response).  Returns [ApiResult.Success(Unit)] on success.
     */
    suspend fun requestNoBody(
        path: String,
        method: String,
        headers: Map<String, String> = emptyMap(),
        body: String? = null,
        successCodes: Set<Int> = setOf(HttpURLConnection.HTTP_OK),
        onResponse: ((HttpURLConnection) -> Unit)? = null,
    ): ApiResult<Unit> = request(
        path = path,
        method = method,
        headers = headers,
        body = body,
        successCodes = successCodes,
        onResponse = onResponse,
        parse = { Unit },
    )
}
package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString

/**
 * Client for POST /billing/google/verify (docs/API_CONTRACTS.md §36).
 *
 * After the Play Billing flow completes, the purchase token is posted
 * here so the SERVER verifies it against the Google Play Developer API
 * and flips the authenticated player's plan.  The client never treats
 * a local purchase as entitlement — Pro state comes only from a
 * successful verify response (`plan == "pro"`), which is also why the
 * purchase is acknowledged only after this call succeeds (an
 * unacknowledged purchase is auto-refunded by Play, so a lost verify
 * can never silently eat the user's money).
 *
 * The route is `Depends(get_current_player)` on the server, so a valid
 * `Authorization: Bearer <jwt>` is required — same posture as
 * [HttpLiveMoveClient].
 */
interface BillingApiClient {

    /**
     * Verify [purchaseToken] for [productId] server-side.
     *
     * @return [ApiResult.Success] with the activated plan on HTTP 200;
     *         [ApiResult.HttpError] on non-200 (402 = Google says the
     *         token carries no entitlement, 502 = verification upstream
     *         unavailable — retry later, 503 = server has no Play
     *         credentials configured); [ApiResult.Timeout] /
     *         [ApiResult.NetworkError] for transport failures.
     */
    suspend fun verifyGooglePurchase(
        purchaseToken: String,
        productId: String,
    ): ApiResult<BillingVerifyResponse>
}

/**
 * Request body for POST /billing/google/verify.
 *
 * [purchaseToken]  From Play Billing's `Purchase.getPurchaseToken()`.
 * [productId]      The Play product that was bought ("pro_monthly" /
 *                  "pro_yearly" — see [PaywallActivity.PLAY_PRODUCT_IDS]).
 */
@Serializable
data class BillingVerifyRequest(
    @SerialName("purchase_token") val purchaseToken: String,
    @SerialName("product_id") val productId: String,
)

/**
 * Response from POST /billing/google/verify.
 *
 * [plan]       The plan now active for the player ("pro").  The client
 *              treats ONLY `plan == "pro"` as activation.
 * [productId]  Echo of the verified product.
 * [state]      Google's `subscriptionState` verbatim (diagnostic).
 */
@Serializable
data class BillingVerifyResponse(
    val plan: String = "",
    @SerialName("product_id") val productId: String = "",
    val state: String = "",
)

/**
 * Production implementation backed by [BaseHttpClient] /
 * [java.net.HttpURLConnection].  Each call opens its own connection;
 * the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param tokenProvider    Supplier of the JWT Bearer token.  Required (no
 *                         default) — the route is a certain 401 without it,
 *                         same rationale as [HttpLiveMoveClient.tokenProvider].
 * @param tokenSink        Optional sink for the X-Auth-Token refresh header
 *                         so the verify call participates in JWT rotation
 *                         (docs/API_CONTRACTS.md §10).
 */
class HttpBillingApiClient(
    val baseUrl: String,
    val apiKey: String,
    val tokenProvider: () -> String?,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : BillingApiClient {

    companion object {
        private const val VERIFY_PATH = "/billing/google/verify"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun verifyGooglePurchase(
        purchaseToken: String,
        productId: String,
    ): ApiResult<BillingVerifyResponse> = http.request(
        path = VERIFY_PATH,
        method = "POST",
        headers = buildMap {
            put("X-Api-Key", apiKey)
            tokenProvider.invoke()?.let { put("Authorization", "Bearer $it") }
        },
        body = ApiJson.encodeToString(
            BillingVerifyRequest(purchaseToken = purchaseToken, productId = productId)
        ),
        onResponse = { conn -> consumeRefreshedToken(conn, tokenSink) },
        parse = { body -> ApiJson.decodeFromString<BillingVerifyResponse>(body) },
    )
}
package com.cereveon.myapp

import android.app.Application
import android.util.Log
import androidx.appcompat.app.AppCompatDelegate
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Process-wide singletons + cold-start warm-up.
 *
 * Created once by the Android framework before the first activity
 * runs (because we register it in the application tag of the
 * AndroidManifest).  Used here for one job: pre-warming the
 * Android Keystore-backed [EncryptedTokenStorage].
 *
 * Why pre-warm
 * ------------
 * `EncryptedSharedPreferences.create` (and its prerequisite
 * `MasterKey.Builder().build()`) hits the Android Keystore on
 * first call.  The Keystore is hardware-backed on most modern
 * devices; the first KeyStore operation in a process does a
 * key-material fetch / unwrap that can take 200–800ms — sometimes
 * worse on cold-boot or low-end hardware.
 *
 * The token is read on the main thread by every screen that needs
 * auth (LoginActivity, HomeActivity, MainActivity).  Without
 * pre-warming, the first activity to read it stalls its onCreate
 * for the duration of that key fetch — which is part of why the
 * user reported "the New game button takes ~10 taps before it
 * opens" on cold start.
 *
 * The fix moves that one-shot cost off the main thread to a
 * background coroutine fired here.  Any activity that subsequently
 * constructs an [EncryptedTokenStorage] re-uses the warm
 * MasterKey + EncryptedSharedPreferences instance and the
 * .load() returns immediately.
 *
 * The coroutine is intentionally fire-and-forget; if it loses a
 * race against an activity that reads the token first, the
 * activity-side call still works (just pays the same cost it
 * would have without us).  Failures are silent — auth still
 * works, the keystore just isn't pre-warmed.
 */
class CereveonApplication : Application() {

    /**
     * Application-scoped supervisor scope for fire-and-forget
     * background work.  SupervisorJob so a single failure (e.g.
     * Keystore corruption) doesn't cancel future warm-ups.
     */
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        applyPersistedAppearance()
        prewarmEncryptedTokenStorage()
    }

    /**
     * Apply the persisted Bright-mode setting BEFORE the first
     * activity inflates.  Atrium's palette is app-controlled: the
     * mode is always FORCED (MODE_NIGHT_YES = default dark,
     * MODE_NIGHT_NO = opt-in bright from Settings › Appearance), so
     * the system light/dark toggle never selects the palette — the
     * guarantee the dark-only theme previously provided by parentage
     * alone.  The pref read is a synchronous SharedPreferences load
     * of a file every launch screen reads moments later anyway.
     */
    private fun applyPersistedAppearance() {
        AppCompatDelegate.setDefaultNightMode(
            SettingsBottomSheet.nightModeFor(
                SettingsBottomSheet.readBrightModeEnabled(this),
            ),
        )
    }

    /**
     * Touch the token storage so the Keystore unwrap happens off
     * the main thread before any activity needs it.  We call
     * .load() (not .save()) because save() does a real keystore
     * encrypt + apply() write that we don't want on cold-start —
     * load() is sufficient to drive the lazy initialiser.
     */
    private fun prewarmEncryptedTokenStorage() {
        appScope.launch {
            try {
                EncryptedTokenStorage(this@CereveonApplication).load()
                Log.d("CereveonApplication", "EncryptedTokenStorage warm")
            } catch (e: Exception) {
                // Activity-side construction will throw the same
                // way and surface the issue through the existing
                // try/catch in AuthRepository.getToken; nothing to
                // do here besides note the failure.
                Log.w("CereveonApplication", "EncryptedTokenStorage warm-up failed", e)
            }
        }
    }
}
package com.cereveon.myapp

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView

data class ChatMessage(
    val role: String,
    val text: String,
)

/**
 * Atrium chat adapter — inflates `item_chat_coach.xml` for assistant
 * messages and `item_chat_user.xml` for everything else.
 *
 * Per the Atrium handoff, voices are separated by typography rather
 * than by bubble background:
 *   - Coach: left-aligned, mono cyan COACH kicker, italic Cormorant
 *     body 16sp, 1px gradient gutter on the leading edge.
 *   - User:  right-aligned, mono dim YOU kicker, Inter italic 14sp
 *     muted, no fill.
 *
 * Feedback (👍 / 👎) is preserved on coach messages — the layout uses
 * star drawables tinted to cyan / amber / dim depending on tap state.
 * The onFeedback callback shape is unchanged so ChatBottomSheet's
 * existing wiring keeps working.
 */
class ChatAdapter : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private val messages = mutableListOf<ChatMessage>()

    /** Called when the user taps the helpful / not-helpful icon on a coach message. */
    var onFeedback: ((position: Int, isHelpful: Boolean) -> Unit)? = null

    fun addMessage(msg: ChatMessage) {
        messages.add(msg)
        notifyItemInserted(messages.size - 1)
    }

    fun updateLastMessage(text: String) {
        if (messages.isEmpty()) return
        val lastIndex = messages.size - 1
        messages[lastIndex] = messages[lastIndex].copy(text = text)
        notifyItemChanged(lastIndex)
    }

    fun clear() {
        val count = messages.size
        messages.clear()
        notifyItemRangeRemoved(0, count)
    }

    override fun getItemCount() = messages.size

    override fun getItemViewType(position: Int): Int =
        if (messages[position].role == "assistant") TYPE_COACH else TYPE_USER

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return if (viewType == TYPE_COACH) {
            CoachVH(inflater.inflate(R.layout.item_chat_coach, parent, false))
        } else {
            UserVH(inflater.inflate(R.layout.item_chat_user, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val msg = messages[position]
        when (holder) {
            is CoachVH -> {
                holder.body.text = msg.text
                // Reset feedback tint on rebind so a recycled view from
                // a previously rated row doesn't carry the colour over.
                val ctx = holder.itemView.context
                val dim = ContextCompat.getColor(ctx, R.color.atrium_dim)
                holder.up.setColorFilter(dim)
                holder.down.setColorFilter(dim)
                holder.up.setOnClickListener {
                    onFeedback?.invoke(holder.bindingAdapterPosition, true)
                    holder.up.setColorFilter(ContextCompat.getColor(ctx, R.color.atrium_accent_cyan))
                    holder.down.setColorFilter(dim)
                }
                holder.down.setOnClickListener {
                    onFeedback?.invoke(holder.bindingAdapterPosition, false)
                    holder.down.setColorFilter(ContextCompat.getColor(ctx, R.color.atrium_accent_amber))
                    holder.up.setColorFilter(dim)
                }
            }
            is UserVH -> {
                holder.body.text = msg.text
            }
        }
    }

    private class CoachVH(view: View) : RecyclerView.ViewHolder(view) {
        val body: TextView = view.findViewById(R.id.coachBody)
        val up: ImageButton = view.findViewById(R.id.btnFeedbackUp)
        val down: ImageButton = view.findViewById(R.id.btnFeedbackDown)
    }

    private class UserVH(view: View) : RecyclerView.ViewHolder(view) {
        val body: TextView = view.findViewById(R.id.userBody)
    }

    companion object {
        private const val TYPE_COACH = 1
        private const val TYPE_USER = 0
    }
}
package com.cereveon.myapp

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.util.Log
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.DialogFragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.recyclerview.widget.SimpleItemAnimator
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Long-form chat coaching panel.
 *
 * Displays a GPT-style chess conversation that:
 *  - Sends full conversation context (FEN + history) to the backend /chat endpoint.
 *  - Displays a structured response that always references engine evaluation.
 *  - Shows an engine context header (evaluation band + game phase).
 *  - Falls back gracefully when the backend is unavailable or returns no reply.
 *
 * Presentation: a NON-MODAL, bottom-anchored ~half-height dialog (see
 * [onStart]).  It is deliberately NOT a `BottomSheetDialogFragment`: a
 * bottom-sheet dialog owns a full-screen window whose scrim eats every
 * touch, which freezes the live board behind it.  Here the window covers
 * only the lower half and carries `FLAG_NOT_TOUCH_MODAL` with no dim, so
 * taps above the panel fall through to [MainActivity]'s board — the user
 * can keep playing while the coach is open.  The in-panel preview board
 * is hidden because the real, interactive board is now visible above.
 *
 * Auth tokens, timeouts, and base URL are configured in [BuildConfig] per build
 * variant and owned by [HttpCoachApiClient] — not duplicated here.
 *
 * No RL adaptation. All coaching logic lives server-side in chat_pipeline.py.
 */
class ChatBottomSheet : DialogFragment() {

    // ---------------------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------------------

    private lateinit var recyclerMessages: RecyclerView
    private lateinit var input: EditText
    private lateinit var sendBtn: Button
    private lateinit var miniBoard: ChessBoardView
    private lateinit var engineContextHeader: LinearLayout
    private lateinit var txtEngineContext: TextView
    private lateinit var btnExpandChat: TextView
    /**
     * Atrium typing-dots indicator.  Visible while the chat stream is
     * waiting for the first chunk; hidden as soon as text starts
     * accumulating or the request finishes / errors.
     */
    private lateinit var typingDots: AtriumTypingDotsView

    // ---------------------------------------------------------------------------
    // State
    // ---------------------------------------------------------------------------

    private var currentFen: String? = null

    // The player's most recent move (UCI), refreshed from [MainActivity] at
    // send time so the coach can name it in plain English. Null → no move line.
    private var currentLastMove: String? = null

    // The seat the player is on ("black" while reviewing an imported game
    // played as Black), refreshed from [MainActivity] at send time so the
    // coach's "you" framing follows the player's side. Null → White anchor.
    private var currentPlayerColor: String? = null

    /**
     * Current server game id, refreshed from [MainActivity] at send/load time
     * so chat saves + history are scoped to this game. Null → player-global.
     */
    private var gameId: String? = null
    private var isStreaming = false

    /**
     * Panel size state. Opens COLLAPSED so the board is immediately visible
     * and tappable (the point of "play while chatting" — an expanded panel
     * covers the board and reads as "frozen"). The header chevron expands it
     * to a tall reading height on demand. See [applyPanelHeight].
     */
    private var panelExpanded = false

    /**
     * Whether the streamed reply should auto-scroll to follow new text. True
     * while the user is at the bottom; the RecyclerView scroll listener clears
     * it when they drag up to read and restores it when they return. Reset to
     * true at the start of each send.
     */
    private var followStream = true

    /**
     * True while [preloadServerHistory] is in flight on a fresh open.
     * Gates [sendBtn] so a user who taps Send before the server's
     * history fetch lands does not race the preloaded turns —
     * otherwise the about-to-be-sent message renders first and the
     * server's older turns append after it, breaking chronological
     * order in the bubble list.
     */
    private var isPreloading = false

    /**
     * Player profile injected by the host Activity when a game has been completed.
     * Sourced from [GameFinishResponse.newRating] and [GameFinishResponse.confidence].
     * Null when no game has finished in this session (opening/pre-game chat).
     */
    private var playerProfile: PlayerProfileDto? = null

    /**
     * Weakness categories from the most recent game, used to personalise coaching.
     * Derived from [GameFinishResponse.coachAction.weakness] when available.
     * Null when no game has finished in this session.
     */
    private var pastMistakes: List<String>? = null

    /**
     * Number of half-moves played so far in the current game.
     * Passed to [CoachApiClient.chat] as `move_count` so the backend can infer
     * game phase during mid-game chat (opening / middlegame / endgame).
     * Zero when chat is opened before any moves have been played.
     */
    private var moveCount: Int = 0

    /**
     * Optional seed prompt a caller can pass via [newInstance].
     * When non-null, it is auto-submitted as the first user turn instead of
     * showing the generic greeting.
     */
    private var seedPrompt: String? = null

    private val sessionStore = ChatSessionStore(maxMessages = 50)
    private val chatAdapter = ChatAdapter()

    /**
     * Auth repository — wired in [onAttach] so the Context is available.
     * Provides the JWT to [coachApiClient] via the [tokenProvider] lambda.
     */
    private var authRepository: AuthRepository? = null

    /**
     * Shared API client — constructed once from [BuildConfig] constants.
     * Injects the current JWT (if any) via [tokenProvider] so user-specific
     * backend endpoints receive an Authorization: Bearer header automatically.
     *
     * Read timeout is bumped to [CHAT_READ_TIMEOUT_MS] because the server's
     * `/chat/stream` route (see ``llm/server.py::chat_stream``) waits for
     * ``generate_chat_reply`` — and therefore the underlying DeepSeek
     * non-streaming call (``llm/seca/coach/explain_pipeline.py::call_llm``
     * uses ``stream: False`` with a 120 s server-side timeout) — to finish
     * BEFORE emitting any SSE bytes.  The default 15 s ``BaseHttpClient``
     * read timeout is tuned for 1-2-sentence Mode-1 hints (~1-3 s on
     * DeepSeek); Mode-2 chat replies are full coaching prose and routinely
     * take 15-45 s.  At the default the stream times out before the first
     * chunk lands and the user sees the "Coach is offline" fallback even
     * when DeepSeek would have answered.  The real fix is true LLM
     * streaming server-side; this widened timeout is the bridge.
     */
    private val coachApiClient: CoachApiClient by lazy {
        HttpCoachApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
            readTimeoutMs = CHAT_READ_TIMEOUT_MS,
            tokenProvider = { authRepository?.getToken() },
            // Rotate the JWT on every successful coach response — without
            // this, a user who chats for 24h+ without ending a game would
            // be bounced to login despite continuous activity.
            tokenSink = { newToken -> authRepository?.saveToken(newToken) },
        )
    }

    companion object {
        private const val ARG_FEN = "arg_fen"
        private const val ARG_HAS_PROFILE = "arg_has_profile"
        private const val ARG_PLAYER_RATING = "arg_player_rating"
        private const val ARG_PLAYER_CONFIDENCE = "arg_player_confidence"
        private const val ARG_PAST_MISTAKES = "arg_past_mistakes"
        private const val ARG_MOVE_COUNT = "arg_move_count"
        private const val ARG_SEED_PROMPT = "arg_seed_prompt"
        private const val KEY_MSG_ROLES = "chat_msg_roles"
        private const val KEY_MSG_TEXTS = "chat_msg_texts"
        private const val KEY_PANEL_EXPANDED = "chat_panel_expanded"

        private const val STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

        private const val FALLBACK_REPLY =
            "Coach is offline. Review the position and consider piece activity, " +
                "centre control, and king safety."

        /**
         * Per-request read deadline for Mode-2 chat (POST /chat/stream).
         *
         * Server-side budget: the route calls ``generate_chat_reply``
         * which awaits the DeepSeek HTTP call with a 120 s timeout
         * (``llm/seca/coach/explain_pipeline.py``).  Real DeepSeek
         * non-streaming replies for full coaching prose range from
         * ~5 s (terse settings) to ~30-45 s (long replies, network
         * jitter).  60 s gives DeepSeek room to land the slow tail
         * before the client gives up; the deterministic fallback
         * (still ~instant) covers anything past that.
         *
         * Why not the [BaseHttpClient.DEFAULT_READ_TIMEOUT_MS] (15 s):
         * Mode-1 hints (1-2 sentences) fit inside 15 s comfortably,
         * but a chat reply at the default deadline times out before
         * the LLM's first byte and the user sees "Coach is offline"
         * even when the server would have answered.
         */
        private const val CHAT_READ_TIMEOUT_MS = 60_000

        /**
         * Typewriter reveal pace. The server can stream a full reply in a few
         * seconds — far faster than reading — so instead of rendering whatever
         * has arrived, a ticker reveals the buffered text at a fixed rate:
         * [TYPEWRITER_CHARS_PER_TICK] characters every [TYPEWRITER_TICK_MS] ms
         * (≈ 75 chars/s). Slow enough to read along, brisk enough not to drag;
         * also bounds the re-render rate so a burst can't flood the UI thread.
         * Tune these two numbers to change the feel.
         */
        private const val TYPEWRITER_CHARS_PER_TICK = 3
        private const val TYPEWRITER_TICK_MS = 40L

        /** logcat tag for chat-stream transport/validation errors. */
        private const val STREAM_TAG = "ChatStream"

        /**
         * Collapsed panel height as a fraction of screen height (see
         * [applyPanelHeight]). Short enough that the full 332dp board + rails
         * clear the panel's top edge and stay tappable — the "play while
         * chatting" state. The board bottom sits ~53% down the screen, so ≤45%
         * keeps the whole board (incl. rank 1) clear with margin. The EXPANDED
         * state is full-height (MATCH_PARENT) so ADJUST_RESIZE can lift a
         * multi-line composer above the keyboard.
         */
        private const val CHAT_PANEL_COLLAPSED_FRACTION = 0.45f

        /**
         * Server ChatRequest limits the conversation history (see
         * `llm/server_schemas.py` ChatRequest / ChatTurnModel): at most 50
         * turns, each turn's content ≤ 2000 chars, role ∈ {user, assistant}.
         * The client must conform or the whole request 422s — which the UI
         * shows as the silent "Coach is offline" fallback. A long coach reply
         * (replies routinely run >1.5k chars) stored in history is the usual
         * trip-wire.
         */
        private const val MAX_CHAT_HISTORY = 50
        private const val MAX_CHAT_CONTENT = 2000

        /**
         * Fraction of the panel height the grab handle must be dragged DOWN
         * before release dismisses the panel; a shorter drag snaps back.
         */
        private const val DRAG_DISMISS_FRACTION = 0.25f

        /**
         * Create a new instance with the current board position and optional
         * player context for personalised coaching.
         *
         * @param fen           Current board position in FEN notation.
         * @param playerProfile Rating + confidence from the last [GameFinishResponse];
         *                      null when no game has completed in this session.
         * @param pastMistakes  Weakness categories from the last game; null when not available.
         */
        fun newInstance(
            fen: String,
            playerProfile: PlayerProfileDto? = null,
            pastMistakes: List<String>? = null,
            moveCount: Int = 0,
            seedPrompt: String? = null,
        ): ChatBottomSheet {
            val fragment = ChatBottomSheet()
            val args = Bundle()
            args.putString(ARG_FEN, fen)
            if (playerProfile != null) {
                args.putBoolean(ARG_HAS_PROFILE, true)
                args.putFloat(ARG_PLAYER_RATING, playerProfile.rating)
                args.putFloat(ARG_PLAYER_CONFIDENCE, playerProfile.confidence)
            }
            pastMistakes?.let { args.putStringArrayList(ARG_PAST_MISTAKES, ArrayList(it)) }
            args.putInt(ARG_MOVE_COUNT, moveCount)
            seedPrompt?.let { args.putString(ARG_SEED_PROMPT, it) }
            fragment.arguments = args
            return fragment
        }
    }

    // ---------------------------------------------------------------------------
    // Lifecycle
    // ---------------------------------------------------------------------------

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        val msgs = sessionStore.messages
        outState.putStringArray(KEY_MSG_ROLES, Array(msgs.size) { msgs[it].role })
        outState.putStringArray(KEY_MSG_TEXTS, Array(msgs.size) { msgs[it].text })
        outState.putBoolean(KEY_PANEL_EXPANDED, panelExpanded)
    }

    override fun onAttach(context: Context) {
        super.onAttach(context)
        authRepository = AuthRepository(EncryptedTokenStorage(context))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        currentFen = arguments?.getString(ARG_FEN)
        if (arguments?.getBoolean(ARG_HAS_PROFILE, false) == true) {
            val rating = arguments!!.getFloat(ARG_PLAYER_RATING)
            val confidence = arguments!!.getFloat(ARG_PLAYER_CONFIDENCE)
            playerProfile = PlayerProfileDto(rating = rating, confidence = confidence)
        }
        pastMistakes = arguments?.getStringArrayList(ARG_PAST_MISTAKES)?.toList()
        moveCount = arguments?.getInt(ARG_MOVE_COUNT, 0) ?: 0
        seedPrompt = arguments?.getString(ARG_SEED_PROMPT)
        isCancelable = true
        // Restore the panel size across rotation (messages are restored in
        // onViewCreated); without this the panel snaps back to collapsed
        // while the conversation survives, which reads as a glitch.
        savedInstanceState?.let {
            panelExpanded = it.getBoolean(KEY_PANEL_EXPANDED, panelExpanded)
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.sheet_chat, container, false)

    /**
     * Configure the dialog window: bottom-anchored, ~half the screen tall,
     * no scrim, and `FLAG_NOT_TOUCH_MODAL` so pointer events above the panel
     * pass through to the live board in [MainActivity] behind it — this is
     * what lets the user keep playing while the coach is open. Done here
     * because the window only exists once the dialog is shown.
     */
    override fun onStart() {
        super.onStart()
        val window = dialog?.window ?: return
        window.setGravity(Gravity.BOTTOM)
        // sheet_chat paints its own panel surface — drop the default frame.
        window.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        // No dim: the board above must look (and be) live, not greyed out.
        window.setDimAmount(0f)
        window.clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
        // The crux of "play while chatting": touches outside this now-partial
        // window reach the board window behind it. Keep the window focusable
        // (NO FLAG_NOT_FOCUSABLE) so the composer keyboard still works.
        window.addFlags(WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL)
        // ADJUST_RESIZE so the panel's content (the message list shrinks, the
        // composer stays at the bottom) reflows ABOVE the keyboard — this keeps
        // even a multi-line composer fully visible while typing. It only takes
        // effect on a non-fixed-height window, which is why the EXPANDED state
        // (where typing happens — focus auto-expands) is MATCH_PARENT; the
        // collapsed "play" state stays a fixed partial height.
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        applyPanelHeight()
    }

    /**
     * Resize the dialog window to the current [panelExpanded] state and
     * sync the chevron glyph. Only the height changes (how much board
     * shows above); width is always full-bleed, anchored to the bottom.
     */
    private fun applyPanelHeight() {
        val window = dialog?.window ?: return
        // Expanded is full-height (MATCH_PARENT) so ADJUST_RESIZE can reflow the
        // content above the keyboard while typing; collapsed is a fixed partial
        // height that leaves the live board visible + tappable above it.
        val height =
            if (panelExpanded) {
                ViewGroup.LayoutParams.MATCH_PARENT
            } else {
                (resources.displayMetrics.heightPixels * CHAT_PANEL_COLLAPSED_FRACTION).toInt()
            }
        window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, height)
        window.setGravity(Gravity.BOTTOM)
        if (::btnExpandChat.isInitialized) {
            // ⌄ = tap to collapse (panel is tall now); ⌃ = tap to expand.
            btnExpandChat.text = if (panelExpanded) "⌄" else "⌃"
        }
    }

    // The grab-handle drag is a swipe-to-dismiss gesture, not a click, so the
    // accessibility-performClick lint check doesn't apply.
    @SuppressLint("ClickableViewAccessibility")
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        // Bind views
        recyclerMessages = view.findViewById(R.id.recyclerMessages)
        input = view.findViewById(R.id.inputMessage)
        sendBtn = view.findViewById(R.id.btnSend)
        miniBoard = view.findViewById(R.id.miniBoard)
        engineContextHeader = view.findViewById(R.id.engineContextHeader)
        txtEngineContext = view.findViewById(R.id.txtEngineContext)
        typingDots = view.findViewById(R.id.typingDots)
        btnExpandChat = view.findViewById(R.id.btnExpandChat)

        // Expand/collapse the panel: tall for reading, short to free the
        // board for play. onStart() applies the initial (expanded) height.
        btnExpandChat.setOnClickListener {
            panelExpanded = !panelExpanded
            applyPanelHeight()
        }

        // In-panel preview board — hidden now that the chat is a half-height
        // non-modal panel and the real, interactive board is visible above it
        // (see class KDoc / onStart). Hiding its mat container reclaims the
        // vertical space for the conversation. Kept seeded for safety in case
        // the panel is ever shown full-height again.
        miniBoard.isInteractive = false
        currentFen?.let { miniBoard.setFEN(it) }
        (miniBoard.parent as? View)?.visibility = View.GONE

        // Wire feedback thumbs — fire-and-forget; ignore result
        chatAdapter.onFeedback = { _, isHelpful ->
            val fen = currentFen ?: STARTING_FEN
            val token = authRepository?.getToken()
            viewLifecycleOwner.lifecycleScope.launch {
                coachApiClient.submitFeedback(fen, isHelpful, token)
            }
        }

        // RecyclerView — stable message rendering.
        // stackFromEnd is OFF: with it on, the LayoutManager re-anchors to the
        // bottom every time the streaming bubble grows — that auto-pull is what
        // kept dragging the view down even after the user touched, and no
        // scroll guard can stop it. Top-anchored instead preserves the user's
        // scroll position; following the stream is done explicitly by
        // scrollToBottom() (which scrolls to the content's TRUE bottom), gated
        // on [followStream] (cleared the moment the user drags).
        val layoutManager = LinearLayoutManager(requireContext())
        recyclerMessages.layoutManager = layoutManager
        recyclerMessages.adapter = chatAdapter
        recyclerMessages.addOnScrollListener(
            object : RecyclerView.OnScrollListener() {
                override fun onScrollStateChanged(rv: RecyclerView, newState: Int) {
                    when (newState) {
                        // Finger grabbed the list — stop auto-following so the
                        // per-tick scroll doesn't fight the user.
                        RecyclerView.SCROLL_STATE_DRAGGING -> followStream = false
                        // The user let go: resume following only if they settled
                        // at the bottom; otherwise stay put so they can read.
                        // (Our own scrollToPosition is INSTANT and never enters
                        // DRAGGING/SETTLING, so it can't re-enable following
                        // behind the user's back — which is what made it fight.)
                        RecyclerView.SCROLL_STATE_IDLE ->
                            followStream = !rv.canScrollVertically(1)
                        else -> Unit
                    }
                }
            },
        )
        // Streaming re-binds the last bubble many times per second; the
        // default change animation cross-fades on every notifyItemChanged,
        // stacking into a visible shimmer and extra main-thread work.
        // Disable change animations so the streamed bubble grows cleanly.
        (recyclerMessages.itemAnimator as? SimpleItemAnimator)?.supportsChangeAnimations = false

        // Window sizing/flags are applied in onStart() (the dialog window
        // exists by then). The panel is bottom-anchored, half-height, and
        // non-touch-modal so the live board above stays interactive.

        // Restore messages after rotation; otherwise preload
        // server-persisted history before falling back to the greeting.
        val roles = savedInstanceState?.getStringArray(KEY_MSG_ROLES)
        val texts = savedInstanceState?.getStringArray(KEY_MSG_TEXTS)
        if (roles != null && texts != null && roles.size == texts.size && roles.isNotEmpty()) {
            roles.zip(texts).forEach { (role, text) ->
                sessionStore.addMessage(role, text)
                chatAdapter.addMessage(ChatMessage(role = role, text = text))
            }
            scrollToBottom()
        } else {
            // Fresh open — pull server history (cross-device, persistent
            // across process restarts) then route the seed prompt or the
            // greeting based on what landed.  Network failure is
            // non-fatal: an empty history shows the greeting just like a
            // brand-new player would see it.
            //
            // Disable Send synchronously, BEFORE the coroutine launches,
            // so a user who taps the button between launch and preload
            // completion does not append their message above the
            // about-to-land server history.  Re-enabled in the launch's
            // finally block to cover network failure / coroutine cancel.
            isPreloading = true
            sendBtn.isEnabled = false
            viewLifecycleOwner.lifecycleScope.launch {
                try {
                    preloadServerHistory()
                    val seed = seedPrompt
                    when {
                        seed != null -> {
                            appendUser(seed)
                            sendToBackend(seed)
                        }
                        sessionStore.messages.isEmpty() -> {
                            appendAssistant(
                                "Hi! Ask me about the current position, strategy, or your recent mistakes."
                            )
                        }
                        // else: history loaded — let the user see their
                        // prior conversation without an injected greeting.
                    }
                } finally {
                    // Re-enable Send regardless of preload outcome —
                    // network failure, success, or coroutine cancel.
                    // Without the finally, a getHistory() throw would
                    // leave the button permanently dead until reopen.
                    isPreloading = false
                    sendBtn.isEnabled = true
                }
            }
        }

        sendBtn.setOnClickListener {
            val text = input.text.toString().trim()
            if (text.isNotEmpty() && !isStreaming && !isPreloading) {
                input.setText("")
                appendUser(text)
                sendToBackend(text)
            }
        }

        // Raise the panel when the composer is focused so the keyboard doesn't
        // crowd the typing area: expand to full height, where ADJUST_RESIZE
        // reflows the content (list shrinks, composer stays) above the IME — so
        // even a multi-line message stays visible. Collapse again with the
        // chevron. Only auto-expands from the collapsed state.
        input.setOnFocusChangeListener { _, hasFocus ->
            if (hasFocus && !panelExpanded) {
                panelExpanded = true
                applyPanelHeight()
                scrollToBottom()
            }
        }

        // Swipe the grab handle DOWN to dismiss: drag the whole panel with the
        // finger, then dismiss past DRAG_DISMISS_FRACTION or snap back. Lives on
        // its own touch target so it never competes with message scrolling.
        var dragStartY = 0f
        view.findViewById<View>(R.id.dragHandle).setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    dragStartY = event.rawY
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dy = event.rawY - dragStartY
                    if (dy > 0f) view.translationY = dy // only drag downward
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    val dy = event.rawY - dragStartY
                    if (dy > view.height * DRAG_DISMISS_FRACTION) {
                        view.animate()
                            .translationY(view.height.toFloat())
                            .setDuration(160L)
                            .withEndAction { if (isAdded) dismissAllowingStateLoss() }
                            .start()
                    } else {
                        view.animate().translationY(0f).setDuration(160L).start()
                    }
                    true
                }
                else -> false
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Message helpers
    // ---------------------------------------------------------------------------

    private fun appendUser(text: String) {
        sessionStore.addMessage("user", text)
        chatAdapter.addMessage(ChatMessage(role = "user", text = text))
        scrollToBottom()
    }

    private fun appendAssistant(text: String) {
        sessionStore.addMessage("assistant", text)
        chatAdapter.addMessage(ChatMessage(role = "assistant", text = text))
        scrollToBottom()
    }

    private fun scrollToBottom() {
        val count = chatAdapter.itemCount
        if (count == 0) return
        val lm = recyclerMessages.layoutManager as? LinearLayoutManager
        val lastVisible = lm?.findLastVisibleItemPosition() ?: RecyclerView.NO_POSITION
        // Only jump to the last item if it isn't already on screen (e.g. after
        // a history load). During streaming it is, so skip the jump (which
        // would flash the bubble's top) and just nudge to the content's true
        // bottom — scrollToPosition shows the item's TOP, but a tall streaming
        // bubble's latest text sits below the fold.
        if (lastVisible == RecyclerView.NO_POSITION || lastVisible < count - 1) {
            recyclerMessages.scrollToPosition(count - 1)
        }
        recyclerMessages.post {
            if (recyclerMessages.canScrollVertically(1)) {
                recyclerMessages.scrollBy(0, recyclerMessages.computeVerticalScrollRange())
            }
        }
    }

    /**
     * Render a streamed reply update, auto-scrolling to follow it only while
     * [followStream] is set. The scroll listener clears [followStream] the
     * moment the user drags to read back, and restores it when they return to
     * the bottom — so the per-tick scroll never fights their finger.
     */
    private fun renderStreamUpdate(text: String) {
        chatAdapter.updateLastMessage(text)
        if (followStream) scrollToBottom()
    }

    /**
     * Pull server-persisted chat history into [sessionStore] + [chatAdapter].
     *
     * Called on a fresh chat-sheet open (not rotation-restore, which
     * uses the saved instance state).  Network failure is non-fatal —
     * we silently fall through with an empty store, and the caller
     * surfaces the greeting just like a brand-new player would see.
     *
     * Caps the request at the in-memory ChatSessionStore capacity so a
     * very long server-side history doesn't immediately overflow the
     * client cache.  The server's own `HISTORY_MAX_LIMIT` (200) is a
     * hard upper bound; we ask for 50 to match
     * `ChatSessionStore(maxMessages = 50)`.
     */
    private suspend fun preloadServerHistory() {
        // Scope the preloaded history to the current game's thread (each game
        // its own chat); null → player-global, as before.
        gameId = (activity as? MainActivity)?.currentGameId()
        val result = coachApiClient.getHistory(limit = 50, gameId = gameId)
        val turns = (result as? ApiResult.Success)?.data?.turns ?: return
        if (turns.isEmpty()) return
        turns.forEach { t ->
            sessionStore.addMessage(t.role, t.content)
            chatAdapter.addMessage(ChatMessage(role = t.role, text = t.content))
        }
        scrollToBottom()
    }

    // ---------------------------------------------------------------------------
    // Engine context header
    // ---------------------------------------------------------------------------

    /**
     * Update the engine context bar from the [EngineSignalDto] returned by /chat.
     * Always shows evaluation band and game phase; hides the bar if both are empty.
     */
    private fun updateEngineContextHeader(signal: EngineSignalDto) {
        val band = signal.evaluation?.band ?: ""
        val side = signal.evaluation?.side ?: ""
        val phase = signal.phase ?: ""

        if (band.isEmpty() && phase.isEmpty()) return

        // Reframe the evaluation from the player's (always White) perspective —
        // the raw "BLACK: EQUAL" read oddly to users. Equal has no side; for a
        // real edge it's YOU (White) or your OPPONENT (Black) who's ahead.
        val evalPart =
            when {
                band.isEmpty() -> ""
                band == "equal" -> "equal"
                side == "white" -> "you: ${band.replace('_', ' ')}"
                side == "black" -> "opponent: ${band.replace('_', ' ')}"
                else -> band.replace('_', ' ')
            }

        val label =
            buildString {
                if (phase.isNotEmpty()) append(phase.uppercase())
                if (phase.isNotEmpty() && evalPart.isNotEmpty()) append("  ·  ")
                append(evalPart)
            }
        txtEngineContext.text = label
        engineContextHeader.visibility = View.VISIBLE
    }

    // ---------------------------------------------------------------------------
    // Token expiry
    // ---------------------------------------------------------------------------

    /**
     * Called when the backend returns HTTP 401, indicating the stored JWT has
     * expired or been invalidated. Shows a non-disruptive dialog so the user
     * can choose to re-authenticate without losing the current game state.
     *
     * The stored token is cleared ONLY when the user picks "Log in".
     * Clearing on dialog-show used to lock chat into a permanent
     * "no Authorization header → 401 → Session expired" loop after a
     * single transient 401 (proxy hiccup, brief server restart) — Dismiss
     * is a soft escape hatch and must not log the user out.
     */
    private fun handleTokenExpiry() {
        val ctx = context ?: return
        android.app.AlertDialog.Builder(ctx)
            .setTitle("Session expired")
            .setMessage("Your session has expired. Log in again to continue with coaching.")
            .setPositiveButton("Log in") { _, _ ->
                authRepository?.clearToken()
                val intent =
                    Intent(ctx, LoginActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                startActivity(intent)
            }
            .setNegativeButton("Dismiss", null)
            .show()
    }

    // ---------------------------------------------------------------------------
    // Backend integration
    // ---------------------------------------------------------------------------

    /**
     * Send the current position and conversation history to [coachApiClient]
     * via the streaming SSE endpoint, displaying the reply word-by-word as
     * chunks arrive, then finalising the engine context header on completion.
     *
     * Falls back to [FALLBACK_REPLY] when no text was received before the
     * stream closed or errored — no crash on missing explanation.
     */
    private fun sendToBackend(@Suppress("UNUSED_PARAMETER") query: String) {
        isStreaming = true
        // A fresh reply follows from the bottom until the user scrolls up.
        followStream = true
        sendBtn.isEnabled = false
        // Atrium: 3 cyan staggered-pulse dots while we wait for the
        // first chunk.  Hidden as soon as text begins accumulating so
        // it doesn't compete with the streamed prose.
        typingDots.visibility = View.VISIBLE

        // Refresh coach context from the LIVE board. The chat is a non-modal
        // panel, so the user may have played moves since it opened — pull the
        // current FEN + move count so the coach answers about the position on
        // the board NOW, not the snapshot captured when the panel opened.
        (activity as? MainActivity)?.let { act ->
            act.currentBoardFen()?.let { currentFen = it }
            moveCount = act.currentMoveCount()
            currentLastMove = act.currentLastMove()
            gameId = act.currentGameId()
            currentPlayerColor = act.currentPlayerColor()
        }

        viewLifecycleOwner.lifecycleScope.launch {
            // Conform the history to the server's ChatRequest contract so the
            // request can't 422 (which surfaces as "Coach is offline"):
            //  - at most MAX_CHAT_HISTORY most-recent turns,
            //  - each content truncated to MAX_CHAT_CONTENT chars (a long
            //    coach reply in history is the usual offender),
            //  - role coerced to the Literal["user","assistant"] the server
            //    accepts. Truncation is request-only; the store keeps full text.
            val messages =
                sessionStore.messages
                    .takeLast(MAX_CHAT_HISTORY)
                    .map { msg ->
                        ChatMessageDto(
                            role = if (msg.role == "user") "user" else "assistant",
                            content = msg.text.take(MAX_CHAT_CONTENT),
                        )
                    }

            // Insert an empty assistant placeholder so the user sees a response
            // bubble immediately. The adapter is updated in-place as chunks arrive;
            // the session store is only written once the full reply is assembled.
            chatAdapter.addMessage(ChatMessage(role = "assistant", text = ""))
            scrollToBottom()

            // Full text received from the server so far; the typewriter ticker
            // reveals it to the user at a readable pace (see TYPEWRITER_*).
            var accumulated = ""
            var streamFinished = false
            // Set when the server answers the entitlements 402 (daily chat
            // quota) — drives the paywall reaction after the collect loop.
            var limitNotice: ChatLimitNotice? = null

            // Typewriter reveal — paces the (often very fast) server stream to a
            // readable rate, running concurrently with the collector below.
            // renderStreamUpdate auto-scrolls only while the user is pinned to
            // the bottom, so they can scroll up and read while it streams.
            val reveal = launch {
                var shown = 0
                while (isActive) {
                    val full = accumulated
                    if (shown > full.length) shown = full.length // an abort shortened it
                    if (shown < full.length) {
                        shown = minOf(shown + TYPEWRITER_CHARS_PER_TICK, full.length)
                        renderStreamUpdate(full.substring(0, shown))
                        if (typingDots.visibility == View.VISIBLE) {
                            typingDots.visibility = View.GONE
                        }
                    } else if (streamFinished) {
                        break
                    }
                    delay(TYPEWRITER_TICK_MS)
                }
            }

            try {
                coachApiClient.chatStream(
                    fen = currentFen ?: STARTING_FEN,
                    messages = messages,
                    // Server now derives player_profile + past_mistakes from the
                    // authenticated player row + skill_vector_json (see
                    // llm/server.py::_derive_player_profile). Sending null keeps
                    // the wire payload minimal and prevents a stale local cache
                    // from overriding the server-authoritative coach context; the
                    // fields stay on the request type for back-compat
                    // (encodeDefaults=false drops them).
                    playerProfile = null,
                    pastMistakes = null,
                    moveCount = moveCount.takeIf { it > 0 },
                    // Coach voice pulled fresh each turn so a Settings change
                    // takes effect on the very next reply.
                    coachVoice = SettingsBottomSheet.readCoachVoice(requireContext()),
                    // Scope this exchange to the current game's chat thread.
                    gameId = gameId,
                    // The player's last move, so the coach names it in words.
                    lastMove = currentLastMove,
                    // The player's seat ("black" on imported-Black replays) so
                    // the coach's "you" isn't inverted.
                    playerColor = currentPlayerColor,
                ).collect { chunk ->
                    when (chunk) {
                        is StreamChunk.Chunk -> accumulated += chunk.text
                        is StreamChunk.Done ->
                            chunk.engineSignal?.let { updateEngineContextHeader(it) }
                        is StreamChunk.Abort -> {
                            // Replace whatever partial we have with the
                            // deterministic fallback; the reveal loop picks it up.
                            accumulated = chunk.reply
                            chunk.engineSignal?.let { updateEngineContextHeader(it) }
                        }
                        is StreamChunk.StreamError -> {
                            Log.w(STREAM_TAG, "stream error: ${chunk.message}")
                            if (chunk.message.startsWith("HTTP 401")) handleTokenExpiry()
                            // Entitlements 402: the daily chat quota is used up.
                            // Parsed here, acted on after the collect loop.
                            ChatLimitNotice.fromStreamErrorMessage(chunk.message)
                                ?.let { limitNotice = it }
                        }
                    }
                }
            } finally {
                streamFinished = true
            }
            reveal.join()

            val quotaNotice = limitNotice
            if (quotaNotice != null) {
                // Render the quota explanation in the coach bubble but do NOT
                // persist it: sessionStore is replayed to the server as
                // conversation history, and a quota notice is not coaching
                // context.  Then surface the paywall — the 402 body's
                // upgrade hint is exactly this screen (API_CONTRACTS.md §5).
                renderStreamUpdate(
                    "You've used all ${quotaNotice.limit} coach questions for today. " +
                        "Upgrade to Premium for unlimited coaching — or come back tomorrow.",
                )
                if (isAdded) {
                    startActivity(Intent(requireContext(), PaywallActivity::class.java))
                }
            } else {
                // Ensure the complete reply is shown (fallback if the stream produced
                // nothing; also covers an abort that shortened the text), then persist.
                val displayReply = accumulated.takeIf { it.isNotBlank() } ?: FALLBACK_REPLY
                renderStreamUpdate(displayReply)
                sessionStore.addMessage("assistant", displayReply)
            }

            isStreaming = false
            sendBtn.isEnabled = true
            typingDots.visibility = View.GONE
        }
    }
}
package com.cereveon.myapp

/**
 * In-memory chat session store for the long-form coaching mode.
 *
 * Holds the ordered conversation history for the current session.
 * The history is capped at [maxMessages] entries; when the cap is exceeded
 * the oldest message is dropped from the front to keep memory bounded.
 *
 * Pure Kotlin — no Android framework dependencies, fully JVM-testable.
 *
 * Thread safety: NOT thread-safe. Access only from the main thread.
 */
class ChatSessionStore(val maxMessages: Int = 50) {

    private val _messages = mutableListOf<ChatMessage>()

    /** Snapshot of the current message history (immutable copy). */
    val messages: List<ChatMessage> get() = _messages.toList()

    /** Number of messages currently in the store. */
    val size: Int get() = _messages.size

    /** True when no messages are stored. */
    val isEmpty: Boolean get() = _messages.isEmpty()

    /**
     * Append a message to the history and return it.
     *
     * @param role    Must be "user" or "assistant".
     * @param text    Must not be blank.
     * @throws IllegalArgumentException if [role] or [text] are invalid.
     */
    fun addMessage(role: String, text: String): ChatMessage {
        require(role == "user" || role == "assistant") {
            "role must be 'user' or 'assistant', got: '$role'"
        }
        require(text.isNotBlank()) {
            "message text must not be blank"
        }
        val msg = ChatMessage(role = role, text = text)
        _messages.add(msg)
        if (_messages.size > maxMessages) {
            _messages.removeAt(0)
        }
        return msg
    }

    /** Remove all messages. */
    fun clear() {
        _messages.clear()
    }

    /** All user messages in insertion order. */
    fun userMessages(): List<ChatMessage> = _messages.filter { it.role == "user" }

    /** All assistant messages in insertion order. */
    fun assistantMessages(): List<ChatMessage> = _messages.filter { it.role == "assistant" }
}
package com.cereveon.myapp

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.util.Log
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import kotlin.math.*

enum class GameResult { WHITE_WINS, BLACK_WINS, DRAW }

class ChessBoardView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    data class Arrow(val sr: Int, val sc: Int, val tr: Int, val tc: Int, val color: Int = Color.parseColor("#FF4444"))

    /* ================= STATE ================= */
    private val board = Array(8) { CharArray(8) { '.' } }
    private var whiteToMove = true
    private var selectedRow = -1
    private var selectedCol = -1
    private var enPassantTarget: Pair<Int, Int>? = null
    private var gameOver = false
    
    // Board interaction mode
    var isInteractive = true

    // Visual annotations
    private val arrows = mutableListOf<Arrow>()

    // Highlight state
    private var lastMoveFrom: Pair<Int, Int>? = null
    private var lastMoveTo: Pair<Int, Int>? = null

    private var whiteKingMoved = false
    private var blackKingMoved = false
    private var whiteRookAMoved = false
    private var whiteRookHMoved = false
    private var blackRookAMoved = false
    private var blackRookHMoved = false

    var onMovePlayed: ((Int, Int, Int, Int) -> Unit)? = null
    var coachListener: ((String) -> Unit)? = null
    var promotionListener: ((Int, Int) -> Unit)? = null

    // Set for the duration of an AI promotion move so executeMove promotes
    // the pawn inline (to this piece) instead of firing promotionListener,
    // which is the human-only dialog.  Null on every human move.
    private var aiPromotionPiece: Char? = null
    /** Emits a structured [QuickCoachUpdate] after each AI move. */
    var quickCoachListener: ((QuickCoachUpdate) -> Unit)? = null
    /**
     * Set (not invoked) when checkmate or stalemate is detected, so the caller
     * records the game-ending move BEFORE acting on game-over.  Drained via
     * [consumePendingGameOver] after the move is appended to the ViewModel's
     * move history, so the exported PGN includes the final (mating) move.
     */
    private var pendingGameResult: GameResult? = null

    private data class MoveRecord(
        val sr: Int, val sc: Int, val tr: Int, val tc: Int,
        val piece: Char, val captured: Char,
        val epTarget: Pair<Int, Int>?,
        val wKM: Boolean, val bKM: Boolean,
        val wRAM: Boolean, val wRHM: Boolean,
        val bRAM: Boolean, val bRHM: Boolean
    )
    private val history = mutableListOf<MoveRecord>()

    /* ================= PAINT — Cereveon · Atrium palette ================= */
    // Tokens mirrored from res/values/colors.xml so this Canvas view matches
    // the rest of the Atrium re-skin without going through Resources at every
    // paint pass.  Keep these literals in sync with atrium_board_*, atrium_piece_*,
    // atrium_accent_cyan, atrium_accent_amber if any palette tweak ships later.
    //
    // DELIBERATELY literal, not token reads: the board is its own designed
    // object (dark warm wood) and keeps this palette in Bright mode too —
    // values-notnight/colors.xml intentionally does NOT override the board
    // tokens (pinned by BrightModeSettingTest).  Do not convert these to
    // per-configuration resource reads.
    private val lightSquare = Paint().apply { color = Color.parseColor("#302C24") }    // atrium_board_light (warm wood)
    private val darkSquare  = Paint().apply { color = Color.parseColor("#1A1712") }    // atrium_board_dark
    private val selectPaint = Paint().apply { color = Color.parseColor("#4FD9E5"); alpha = 120 }   // accent_cyan @ ~47%
    private val highlightPaint = Paint().apply { color = Color.parseColor("#4FD9E5"); alpha = 80 } // last-move tint

    // Pieces — ivory on cyan rim for white, warm obsidian on amber rim for
    // black.  The amber rim makes black pieces legible on the dark board
    // squares, per the handoff piece-rendering note.
    private val piecePaintWhite = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#F4EFE1")                                 // atrium_piece_white (ivory)
        textAlign = Paint.Align.CENTER
        setShadowLayer(8f, 0f, 0f, Color.parseColor("#4FD9E5"))             // soft cyan halo
    }

    private val piecePaintBlack = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#1A1108")                                 // atrium_piece_black (warm obsidian)
        textAlign = Paint.Align.CENTER
        setShadowLayer(6f, 0f, 0f, Color.parseColor("#FFC069"))             // amber rim glow
    }

    private val coordinatePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#6B7080")                                 // atrium_dim
        alpha = 200
        textSize = 24f
    }

    private val arrowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        alpha = 180
    }

    // Atrium board variants — see SettingsBottomSheet.PREF_BOARD_STYLE.
    // Engraved adds an inset bevel per square (TL shadow / BR highlight),
    // Wireframe overlays a thin cyan grid on the existing checker fill.
    private val engravedShadowEdge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#0A0805")
        style = Paint.Style.STROKE
    }
    private val engravedLightEdge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#5A5346")
        style = Paint.Style.STROKE
        alpha = 140
    }
    private val wireframeGridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#4FD9E5")                                 // atrium_accent_cyan
        style = Paint.Style.STROKE
        alpha = 130
    }

    /**
     * Board variant selected from Settings → Board style.  Persisted via
     * [SettingsBottomSheet.PREF_BOARD_STYLE]; MainActivity reads the
     * value on resume and assigns here.  Unsupported strings fall back
     * to [DEFAULT_BOARD_STYLE] silently — the setting is user-facing so
     * we don't want a typo to crash the board.
     */
    var boardStyle: String = DEFAULT_BOARD_STYLE
        set(value) {
            val normalized = if (value in SUPPORTED_BOARD_STYLES) value else DEFAULT_BOARD_STYLE
            if (field == normalized) return
            field = normalized
            invalidate()
        }

    /**
     * Atrium focus ring — a pulsing dashed amber circle on the square
     * the coach is referencing.  Per handoff: "Focus ring on the piece
     * being discussed (pulsing dashed amber, NOT an arrow)".  Atrium
     * deliberately disallows move arrows; focus ring is the
     * single-square emphasis primitive.
     *
     * The ring is enabled via [setFocusSquare] / [clearFocusSquare].
     * The pulse animates opacity 1.0 ↔ 0.45 over 1.8s — same cv-pulse
     * keyframe used by AtriumTypingDotsView and the EvalBand dot glow.
     */
    private var focusSquare: Pair<Int, Int>? = null
    private var focusPulseAlpha: Float = 1f
    private var focusPulseAnimator: ValueAnimator? = null

    private val focusRingPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#FFC069") // atrium_accent_amber
        style = Paint.Style.STROKE
        // strokeWidth and pathEffect are set per-draw because they
        // depend on squareSize (only known after measure).
    }

    private var squareSize = 0f

    init { 
        setLayerType(LAYER_TYPE_SOFTWARE, null) 
        resetBoard() 
        isHapticFeedbackEnabled = true
    }

    /* ================= PUBLIC API ================= */

    /**
     * Number of half-moves played since the last [resetBoard].  Backed
     * by the internal undo history list, so it stays in sync with both
     * human and AI moves.  Used by [MainActivity] to drive the dynamic
     * Atrium chapter header copy ("Chapter · Move 14") on the in-game
     * coaching screen.
     */
    val moveCount: Int get() = history.size

    /**
     * Render an Atrium focus ring on ([row],[col]).  The ring pulses
     * opacity 1 ↔ 0.45 over 1.8s.  Coordinates use the same row/col
     * convention as [applyMove] — row 0 is the top rank from white's
     * perspective.  Calling repeatedly with the same square keeps the
     * existing animator running (no flicker); calling with a different
     * square moves the ring.
     */
    fun setFocusSquare(row: Int, col: Int) {
        if (row !in 0..7 || col !in 0..7) return
        if (focusSquare?.first == row && focusSquare?.second == col) return
        focusSquare = row to col
        startFocusPulse()
        invalidate()
    }

    /** Clear the focus ring and stop the pulse animator. */
    fun clearFocusSquare() {
        if (focusSquare == null) return
        focusSquare = null
        focusPulseAnimator?.cancel()
        focusPulseAnimator = null
        invalidate()
    }

    private fun startFocusPulse() {
        focusPulseAnimator?.cancel()
        focusPulseAnimator = ValueAnimator.ofFloat(1f, 0.45f, 1f).apply {
            duration = 1800
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.RESTART
            addUpdateListener { va ->
                focusPulseAlpha = va.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        focusPulseAnimator?.cancel()
        focusPulseAnimator = null
    }

    fun resetBoard() {
        val start = arrayOf("rnbqkbnr", "pppppppp", "........", "........", "........", "........", "PPPPPPPP", "RNBQKBNR")
        for (r in 0..7) for (c in 0..7) board[r][c] = start[r][c]
        whiteToMove = true; gameOver = false
        selectedRow = -1; selectedCol = -1; enPassantTarget = null
        lastMoveFrom = null; lastMoveTo = null
        whiteKingMoved = false; blackKingMoved = false
        whiteRookAMoved = false; whiteRookHMoved = false
        blackRookAMoved = false; blackRookHMoved = false
        arrows.clear()
        history.clear(); invalidate()
    }

    fun setFEN(fen: String) {
        val parts = fen.split(" ")
        if (parts.isEmpty()) return
        
        for (r in 0..7) board[r].fill('.')

        val rows = parts[0].split("/")
        for (r in 0..7) {
            if (r >= rows.size) break
            var c = 0
            for (char in rows[r]) {
                if (char.isDigit()) {
                    val empty = char.toString().toInt()
                    repeat(empty) { if (c < 8) board[r][c++] = '.' }
                } else {
                    if (c < 8) board[r][c++] = char
                }
            }
        }
        if (parts.size > 1) whiteToMove = parts[1] == "w"
        lastMoveFrom = null; lastMoveTo = null; selectedRow = -1; selectedCol = -1
        arrows.clear()
        invalidate()
    }

    fun addArrow(arrow: Arrow) {
        arrows.add(arrow)
        invalidate()
    }

    fun clearArrows() {
        arrows.clear()
        invalidate()
    }

    fun applyMove(sr: Int, sc: Int, tr: Int, tc: Int): MoveResult {
        if (gameOver || !isLegal(sr, sc, tr, tc)) {
            performHapticFeedback(HapticFeedbackConstants.REJECT)
            return MoveResult.FAILED
        }
        val piece = board[sr][sc]
        val isPromotion = piece.lowercaseChar() == 'p' && (tr == 0 || tr == 7)
        executeMove(sr, sc, tr, tc)
        performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
        return if (isPromotion) MoveResult.PROMOTION else {
            whiteToMove = !whiteToMove
            checkAndRecordGameOver()
            invalidate()
            MoveResult.SUCCESS
        }
    }

    /**
     * 🛡️ SAFE AI EXECUTION:
     * Validates that the engine's move is legal before applying.
     *
     * Returns the piece that was on the target square before the move
     * ('.' if nothing was captured or the move was rejected).
     * The caller (ChessViewModel) uses this to build the Quick Coach update.
     */
    fun applyAIMove(fr: Int, fc: Int, tr: Int, tc: Int, promo: Char = ' '): Char {
        if (fr !in 0..7 || fc !in 0..7 || tr !in 0..7 || tc !in 0..7) {
            Log.e("CHESS_BOARD", "AI Move out of bounds: $fr,$fc -> $tr,$tc")
            return '.'
        }

        if (!isLegal(fr, fc, tr, tc)) {
            Log.e("CHESS_BOARD", "AI ATTEMPTED ILLEGAL MOVE: $fr,$fc -> $tr,$tc")
            return '.'
        }

        val capturedPiece = board[tr][tc]
        // The AI applies its own promotion choice inline: executeMove
        // consults aiPromotionPiece so the pawn is promoted directly and
        // the HUMAN promotion dialog is NOT fired for the engine's move
        // (which would otherwise ask the human to choose a piece for the
        // AI, double-flip whiteToMove, and skip the human's turn).
        // Default to Queen if the engine somehow reported a promotion
        // rank without a piece — never leave a pawn stranded on rank 1/8.
        val isPromotion = board[fr][fc].lowercaseChar() == 'p' && (tr == 0 || tr == 7)
        aiPromotionPiece = if (isPromotion) promo.takeIf { it.isLetter() } ?: 'Q' else null
        // try/finally so the flag can never leak into a later HUMAN
        // promotion (which would silently auto-apply this piece instead of
        // showing the picker), even if executeMove is one day changed to
        // throw — today it cannot, the move is already legality-checked.
        try {
            executeMove(fr, fc, tr, tc)
        } finally {
            aiPromotionPiece = null
        }
        whiteToMove = !whiteToMove
        checkAndRecordGameOver()
        invalidate()
        return capturedPiece
    }

    fun undoMove(): Boolean? {
        if (history.isEmpty()) return null
        val last = history.removeAt(history.size - 1)
        board[last.sr][last.sc] = last.piece
        board[last.tr][last.tc] = last.captured
        if (last.piece.lowercaseChar() == 'k' && abs(last.tc - last.sc) == 2) {
            if (last.tc > last.sc) { board[last.sr][7] = board[last.sr][5]; board[last.sr][5] = '.' }
            else { board[last.sr][0] = board[last.sr][3]; board[last.sr][3] = '.' }
        }
        enPassantTarget = last.epTarget
        whiteKingMoved = last.wKM; blackKingMoved = last.bKM
        whiteRookAMoved = last.wRAM; whiteRookHMoved = last.wRHM
        blackRookAMoved = last.bRAM; blackRookHMoved = last.bRHM
        whiteToMove = last.piece.isUpperCase()
        if (history.isNotEmpty()) {
            val prev = history.last()
            lastMoveFrom = prev.sr to prev.sc; lastMoveTo = prev.tr to prev.tc
        } else {
            lastMoveFrom = null; lastMoveTo = null
        }
        gameOver = false; arrows.clear(); invalidate()
        performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
        return whiteToMove
    }

    fun undoBoth() { if (undoMove() == false) undoMove() }

    fun exportFEN(): String {
        val rows = board.joinToString("/") {
            var empty = 0
            val row = StringBuilder()
            for (char in it) {
                if (char == '.') empty++
                else {
                    if (empty > 0) { row.append(empty); empty = 0 }
                    row.append(char)
                }
            }
            if (empty > 0) row.append(empty)
            row.toString()
        }
        val side = if (whiteToMove) "w" else "b"
        // Server's _validate_fen_field (llm/server.py:518) requires all 6 FEN
        // fields and parses with python-chess; a malformed string surfaces as
        // the silent "Coach is offline" fallback.  We now emit the REAL
        // castling rights, en-passant square, and move counters (the board
        // already tracks the king/rook-moved flags and the en-passant target)
        // so the coach's position grounding and the engine's analysis reflect
        // the true game state instead of the old "- - 0 1" placeholder, which
        // told the engine neither side could castle.
        //
        // Castling: a flag stays false until that king/rook first moves, but a
        // rook can be CAPTURED without moving, so also require the king and
        // rook to still stand on their home squares.  board[7] is rank 1
        // (White back rank), board[0] is rank 8; files a..h are columns 0..7.
        val castling = buildString {
            if (!whiteKingMoved && !whiteRookHMoved && board[7][4] == 'K' && board[7][7] == 'R') append('K')
            if (!whiteKingMoved && !whiteRookAMoved && board[7][4] == 'K' && board[7][0] == 'R') append('Q')
            if (!blackKingMoved && !blackRookHMoved && board[0][4] == 'k' && board[0][7] == 'r') append('k')
            if (!blackKingMoved && !blackRookAMoved && board[0][4] == 'k' && board[0][0] == 'r') append('q')
        }.ifEmpty { "-" }
        // En-passant target = the square a pawn just skipped over (set in
        // executeMove on a two-square pawn advance), in algebraic form.
        val enPassant = enPassantTarget?.let { "${'a' + it.second}${8 - it.first}" } ?: "-"
        // Halfmove clock: plies since the last capture or pawn move (50-move rule).
        var halfmove = 0
        for (rec in history.asReversed()) {
            if (rec.piece.lowercaseChar() == 'p' || rec.captured != '.') break
            halfmove++
        }
        // Fullmove number: starts at 1, increments after each Black move.
        val fullmove = history.size / 2 + 1
        return "$rows $side $castling $enPassant $halfmove $fullmove"
    }

    fun promotePawn(r: Int, c: Int, to: Char) {
        board[r][c] = if (board[r][c].isUpperCase()) to.uppercaseChar() else to.lowercaseChar()
        whiteToMove = !whiteToMove
        // A promotion can itself be the game-ending move (promote to a
        // queen that delivers mate, or a stalemating under-promotion).
        // Without this the game stays "live", the mating side never sees
        // a result, and the caller would dispatch an AI reply into a
        // finished position.  iOS's promote(at:to:) does the same.
        checkAndRecordGameOver()
        invalidate()
    }

    private fun hasAnyLegalMove(): Boolean {
        for (r in 0..7) for (c in 0..7) {
            val p = board[r][c]
            if (p == '.' || p.isUpperCase() != whiteToMove) continue
            for (tr in 0..7) for (tc in 0..7) {
                if (isLegal(r, c, tr, tc)) return true
            }
        }
        return false
    }

    private fun checkAndRecordGameOver() {
        if (hasAnyLegalMove()) return
        gameOver = true
        val inCheck = isInCheck(whiteToMove)
        pendingGameResult = when {
            inCheck && whiteToMove -> GameResult.BLACK_WINS
            inCheck && !whiteToMove -> GameResult.WHITE_WINS
            else -> GameResult.DRAW
        }
    }

    /**
     * Returns and clears the result recorded by the last move, or null if the
     * game is still live.  The caller invokes its game-over hook only after
     * appending that move to history, so [ChessViewModel.exportPGN] includes
     * the final (mating) move.
     */
    fun consumePendingGameOver(): GameResult? {
        val r = pendingGameResult
        pendingGameResult = null
        return r
    }

    private fun isLegal(sr: Int, sc: Int, tr: Int, tc: Int): Boolean {
        val piece = board[sr][sc]
        if (piece == '.' || piece.isUpperCase() != whiteToMove) return false
        if (!isLegalGeometry(piece, sr, sc, tr, tc)) return false
        val target = board[tr][tc]
        board[tr][tc] = piece; board[sr][sc] = '.'
        val inCheck = isInCheck(piece.isUpperCase())
        board[sr][sc] = piece; board[tr][tc] = target
        return !inCheck
    }

    /**
     * [allowCastle] gates the king's 2-square castle branch.  Attack tests
     * ([isSquareAttacked]) MUST pass false: castling is not an attacking
     * move, and — critically — evaluating it from an attack probe recurses
     * (isLegalGeometry → canCastle → isInCheck → isSquareAttacked →
     * isLegalGeometry …).  With two kings on the same rank two files apart
     * (reachable in the drill boards, which self-validate moves) the two
     * kings' castle branches probed each other forever → StackOverflowError
     * (2026-07-02 on-device crash solving a study-plan puzzle).
     */
    private fun isLegalGeometry(
        p: Char, sr: Int, sc: Int, tr: Int, tc: Int, allowCastle: Boolean = true,
    ): Boolean {
        if (sr == tr && sc == tc) return false
        val target = board[tr][tc]
        if (target != '.' && target.isUpperCase() == p.isUpperCase()) return false
        val dr = abs(tr - sr); val dc = abs(tc - sc)
        return when (p.lowercaseChar()) {
            'p' -> pawnGeometry(p, sr, sc, tr, tc)
            'r' -> (sr == tr || sc == tc) && pathClear(sr, sc, tr, tc)
            'n' -> (dr == 2 && dc == 1) || (dr == 1 && dc == 2)
            'b' -> (dr == dc) && pathClear(sr, sc, tr, tc)
            'q' -> (dr == dc || sr == tr || sc == tc) && pathClear(sr, sc, tr, tc)
            'k' -> (dr <= 1 && dc <= 1) ||
                (allowCastle && dr == 0 && dc == 2 && canCastle(p, sr, sc, tr, tc))
            else -> false
        }
    }

    private fun pawnGeometry(p: Char, sr: Int, sc: Int, tr: Int, tc: Int): Boolean {
        val dir = if (p.isUpperCase()) -1 else 1
        if (sc == tc && tr == sr + dir && board[tr][tc] == '.') return true
        if (sc == tc && sr == (if (p.isUpperCase()) 6 else 1) && tr == sr + 2 * dir && board[tr][tc] == '.' && pathClear(sr, sc, tr, tc)) return true
        if (abs(tc - sc) == 1 && tr == sr + dir && (board[tr][tc] != '.' || enPassantTarget == tr to tc)) return true
        return false
    }

    private fun pathClear(sr: Int, sc: Int, tr: Int, tc: Int): Boolean {
        val dr = (tr - sr).coerceIn(-1, 1); val dc = (tc - sc).coerceIn(-1, 1)
        var r = sr + dr; var c = sc + dc
        while (r != tr || c != tc) { if (board[r][c] != '.') return false; r += dr; c += dc }
        return true
    }

    private fun canCastle(k: Char, sr: Int, sc: Int, tr: Int, tc: Int): Boolean {
        val white = k.isUpperCase()
        // Cheap flag checks BEFORE the isInCheck board scan — defense in
        // depth against re-entering attack probes (see isLegalGeometry's
        // allowCastle note) and avoids the scan when rights are gone anyway.
        if (white && whiteKingMoved) return false
        if (!white && blackKingMoved) return false
        if (isInCheck(white)) return false
        val rookCol = if (tc > sc) 7 else 0
        if (white && ((tc > sc && whiteRookHMoved) || (tc < sc && whiteRookAMoved))) return false
        if (!white && ((tc > sc && blackRookHMoved) || (tc < sc && blackRookAMoved))) return false
        if (!pathClear(sr, sc, sr, rookCol)) return false
        val step = if (tc > sc) 1 else -1
        if (isSquareAttacked(sr, sc + step, !white)) return false
        return true
    }

    private fun isInCheck(white: Boolean): Boolean {
        val king = if (white) 'K' else 'k'
        var kr = -1; var kc = -1
        for (r in 0..7) for (c in 0..7) if (board[r][c] == king) { kr = r; kc = c; break }
        if (kr == -1) return false
        return isSquareAttacked(kr, kc, !white)
    }

    private fun isSquareAttacked(r: Int, c: Int, byWhite: Boolean): Boolean {
        for (row in 0..7) {
            for (col in 0..7) {
                val p = board[row][col]
                if (p != '.' && p.isUpperCase() == byWhite) {
                    // allowCastle=false: castling can never deliver an attack,
                    // and probing it from here is the recursion that crashed.
                    if (isLegalGeometry(p, row, col, r, c, allowCastle = false)) return true
                }
            }
        }
        return false
    }

    private fun executeMove(sr: Int, sc: Int, tr: Int, tc: Int) {
        val piece = board[sr][sc]
        val captured = board[tr][tc]
        history.add(MoveRecord(sr, sc, tr, tc, piece, captured, enPassantTarget,
            whiteKingMoved, blackKingMoved, whiteRookAMoved, whiteRookHMoved, blackRookAMoved, blackRookHMoved))
        if (piece.lowercaseChar() == 'k' && abs(tc - sc) == 2) {
            if (tc > sc) { board[sr][5] = board[sr][7]; board[sr][7] = '.' }
            else { board[sr][3] = board[sr][0]; board[sr][0] = '.' }
        }
        if (piece.lowercaseChar() == 'p' && tc != sc && board[tr][tc] == '.') board[sr][tc] = '.'
        board[tr][tc] = piece; board[sr][sc] = '.'
        updateFlags(piece, sr, sc)
        enPassantTarget = if (piece.lowercaseChar() == 'p' && abs(tr - sr) == 2) (sr + tr) / 2 to sc else null
        lastMoveFrom = sr to sc; lastMoveTo = tr to tc
        arrows.clear()
        if (piece.lowercaseChar() == 'p' && (tr == 0 || tr == 7)) {
            val aiPromo = aiPromotionPiece
            if (aiPromo != null) {
                // Engine move: promote inline to the AI's chosen piece,
                // matching the pawn's colour case.  No dialog.
                board[tr][tc] = if (piece.isUpperCase()) aiPromo.uppercaseChar() else aiPromo.lowercaseChar()
            } else {
                // Human move: defer to the UI to pick a piece.
                promotionListener?.invoke(tr, tc)
            }
        }
        invalidate()
    }

    private fun updateFlags(p: Char, r: Int, c: Int) {
        if (p == 'K') whiteKingMoved = true; if (p == 'k') blackKingMoved = true
        if (p == 'R') { if (r == 7 && c == 0) whiteRookAMoved = true; if (r == 7 && c == 7) whiteRookHMoved = true }
        if (p == 'r') { if (r == 0 && c == 0) blackRookAMoved = true; if (r == 0 && c == 7) blackRookHMoved = true }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!isInteractive || gameOver || event.action != MotionEvent.ACTION_DOWN) return true
        val screenCol = (event.x / (width / 8f)).toInt()
        val screenRow = (event.y / (width / 8f)).toInt()
        if (screenRow !in 0..7 || screenCol !in 0..7) return true
        // Invert the screen tap back to board coordinates when flipped so
        // taps land on the right square (defensive — the review board is
        // non-interactive, and live play is never flipped).
        val col = sCol(screenCol)
        val row = sRow(screenRow)
        if (selectedRow == -1) {
            val piece = board[row][col]
            if (piece != '.' && piece.isUpperCase() == whiteToMove) {
                selectedRow = row; selectedCol = col; invalidate()
                performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
            }
        } else {
            val sr = selectedRow; val sc = selectedCol
            selectedRow = -1; selectedCol = -1; invalidate()
            onMovePlayed?.invoke(sr, sc, row, col)
        }
        return true
    }

    override fun onMeasure(w: Int, h: Int) {
        val size = min(MeasureSpec.getSize(w), MeasureSpec.getSize(h))
        setMeasuredDimension(size, size)
    }

    private fun pieceToUnicode(p: Char): String = when (p.lowercaseChar()) {
        'k' -> "♚"; 'q' -> "♛"; 'r' -> "♜"; 'b' -> "♝"; 'n' -> "♞"; 'p' -> "♟"
        else -> ""
    }

    /**
     * When true, render the board from Black's perspective (Black at the
     * bottom, files h→a left→right).  Set when replaying an imported
     * Lichess game the player played as Black, so their pieces sit at the
     * bottom under the fixed "You" label instead of upside-down.  Default
     * false = White at bottom (in-app games are always played as White).
     */
    var flipped: Boolean = false
        set(value) {
            if (field != value) {
                field = value
                invalidate()
            }
        }

    // Board coordinate -> on-screen index, applying [flipped] (a 180°
    // rotation preserves square colour, so parity checks stay board-based).
    private fun sCol(c: Int): Int = if (flipped) 7 - c else c
    private fun sRow(r: Int): Int = if (flipped) 7 - r else r

    override fun onDraw(canvas: Canvas) {
        squareSize = width / 8f
        piecePaintWhite.textSize = squareSize * 0.8f
        piecePaintBlack.textSize = squareSize * 0.8f
        coordinatePaint.textSize = squareSize * 0.22f

        val edgeStroke = squareSize * 0.04f
        engravedShadowEdge.strokeWidth = edgeStroke
        engravedLightEdge.strokeWidth = edgeStroke
        wireframeGridPaint.strokeWidth = edgeStroke * 0.7f

        for (r in 0..7) {
            for (c in 0..7) {
                // Board coord (r,c) drawn at screen cell (sr,sc) so the
                // whole board flips when [flipped] is set.
                val sc = sCol(c)
                val sr = sRow(r)
                val l = sc * squareSize
                val t = sr * squareSize
                val rr = (sc + 1) * squareSize
                val bb = (sr + 1) * squareSize
                canvas.drawRect(l, t, rr, bb, if ((r + c) % 2 == 0) darkSquare else lightSquare)
                if (boardStyle == STYLE_ENGRAVED) {
                    val pad = edgeStroke / 2f
                    canvas.drawLine(l + pad, t + pad, rr - pad, t + pad, engravedShadowEdge)
                    canvas.drawLine(l + pad, t + pad, l + pad, bb - pad, engravedShadowEdge)
                    canvas.drawLine(l + pad, bb - pad, rr - pad, bb - pad, engravedLightEdge)
                    canvas.drawLine(rr - pad, t + pad, rr - pad, bb - pad, engravedLightEdge)
                }
                if ((r == lastMoveFrom?.first && c == lastMoveFrom?.second) || (r == lastMoveTo?.first && c == lastMoveTo?.second)) {
                    canvas.drawRect(l, t, rr, bb, highlightPaint)
                }
                if (r == selectedRow && c == selectedCol) {
                    canvas.drawRect(l, t, rr, bb, selectPaint)
                }
                // Rank label on the screen-left column, file label on the
                // screen-bottom row — the label VALUE stays the board's
                // rank/file so it reads correctly whichever way the board
                // is oriented.
                if (sc == 0) {
                    val rank = (8 - r).toString()
                    canvas.drawText(rank, 8f, t + coordinatePaint.textSize, coordinatePaint)
                }
                if (sr == 7) {
                    val file = ('a' + c).toString()
                    canvas.drawText(file, rr - coordinatePaint.measureText(file) - 8f, 8 * squareSize - 8f, coordinatePaint)
                }
                val p = board[r][c]
                if (p != '.') {
                    val paint = if (p.isUpperCase()) piecePaintWhite else piecePaintBlack
                    canvas.drawText(pieceToUnicode(p), sc * squareSize + squareSize / 2, sr * squareSize + squareSize * 0.82f, paint)
                }
            }
        }

        if (boardStyle == STYLE_WIREFRAME) {
            val span = 8 * squareSize
            for (i in 0..8) {
                val v = i * squareSize
                canvas.drawLine(0f, v, span, v, wireframeGridPaint)
                canvas.drawLine(v, 0f, v, span, wireframeGridPaint)
            }
        }

        for (arrow in arrows) {
            drawArrow(canvas, arrow)
        }

        // Atrium focus ring — pulsing dashed amber circle on the
        // square the coach is referencing.  Drawn last so it sits on
        // top of the piece glyph; alpha is the cv-pulse keyframe
        // updated by focusPulseAnimator.
        focusSquare?.let { (r, c) ->
            val cx = sCol(c) * squareSize + squareSize / 2f
            val cy = sRow(r) * squareSize + squareSize / 2f
            val radius = squareSize * 0.42f
            focusRingPaint.strokeWidth = squareSize * 0.04f
            focusRingPaint.pathEffect = DashPathEffect(
                floatArrayOf(squareSize * 0.06f, squareSize * 0.045f), 0f,
            )
            focusRingPaint.alpha = (focusPulseAlpha * 255f).toInt().coerceIn(0, 255)
            canvas.drawCircle(cx, cy, radius, focusRingPaint)
        }
    }

    private fun drawArrow(canvas: Canvas, arrow: Arrow) {
        arrowPaint.color = arrow.color
        arrowPaint.strokeWidth = squareSize * 0.15f
        arrowPaint.setShadowLayer(10f, 0f, 0f, arrow.color)
        
        val startX = sCol(arrow.sc) * squareSize + squareSize / 2
        val startY = sRow(arrow.sr) * squareSize + squareSize / 2
        val endX = sCol(arrow.tc) * squareSize + squareSize / 2
        val endY = sRow(arrow.tr) * squareSize + squareSize / 2
        
        val angle = atan2((endY - startY).toDouble(), (endX - startX).toDouble())
        val dist = sqrt((endX - startX).pow(2) + (endY - startY).pow(2))
        val newEndX = startX + (dist - squareSize * 0.3f) * cos(angle).toFloat()
        val newEndY = startY + (dist - squareSize * 0.3f) * sin(angle).toFloat()

        canvas.drawLine(startX, startY, newEndX, newEndY, arrowPaint)
        
        val headSize = squareSize * 0.3f
        val headPath = Path()
        headPath.moveTo(newEndX, newEndY)
        headPath.lineTo(
            (newEndX - headSize * cos(angle - PI / 6)).toFloat(),
            (newEndY - headSize * sin(angle - PI / 6)).toFloat()
        )
        headPath.lineTo(
            (newEndX - headSize * cos(angle + PI / 6)).toFloat(),
            (newEndY - headSize * sin(angle + PI / 6)).toFloat()
        )
        headPath.close()
        
        val headPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = arrow.color
            style = Paint.Style.FILL
            setShadowLayer(10f, 0f, 0f, arrow.color)
        }
        canvas.drawPath(headPath, headPaint)
    }

    companion object {
        const val STYLE_FLAT = "flat"
        const val STYLE_ENGRAVED = "engraved"
        const val STYLE_WIREFRAME = "wireframe"
        const val DEFAULT_BOARD_STYLE = STYLE_FLAT

        /** Variant keys recognised by [boardStyle]; mirrored from
         *  [SettingsBottomSheet.PREF_BOARD_STYLE] row tags. */
        val SUPPORTED_BOARD_STYLES: Set<String> = setOf(STYLE_FLAT, STYLE_ENGRAVED, STYLE_WIREFRAME)
    }
}
package com.cereveon.myapp

/**
 * Result of a move attempt.
 * Extracted from View to allow JVM testing without Android View dependencies.
 */
enum class MoveResult { SUCCESS, PROMOTION, FAILED }

/**
 * Kotlin AIMove model
 * Used by JNI to return coordinates.
 *
 * [promo] is the ASCII code of the promotion piece the engine chose
 * ('Q'/'R'/'B'/'N'), or 0 when the move is not a promotion.  The JNI
 * ctor is looked up as ``(IIIII)V`` (native_chess_engine.cpp) — the
 * default keeps host-JVM construction (tests) ergonomic without the
 * native side.  [promoChar] decodes it for the board layer; space
 * means "no promotion".
 */
data class AIMove(
    val fr: Int,
    val fc: Int,
    val tr: Int,
    val tc: Int,
    val promo: Int = 0,
) {
    fun isValid() = fr >= 0

    /** The chosen promotion piece as an uppercase letter, or ' ' if none.
     *  The engine emits uppercase, but we normalise defensively so a
     *  lowercase source can never strand a pawn on the back rank. */
    fun promoChar(): Char {
        val c = promo.toChar().uppercaseChar()
        return if (c in "QRBN") c else ' '
    }
}

/**
 * Mistake severity for the Quick Coach dock.
 * Derived purely from captured material — no inference, no RL.
 */
enum class MistakeClassification {
    GOOD, INACCURACY, MISTAKE, BLUNDER;
    fun label(): String = name
}

/**
 * Structured update emitted after each AI move for the Quick Coach dock.
 *
 * [scoreText]       Formatted score shown in the dock (e.g. "+1.52", "Equal", "?").
 *                   When built from the engine, this is the centipawn evaluation
 *                   formatted by [QuickCoachLogic.formatCentipawns]; when built
 *                   from local material balance it uses [QuickCoachLogic.formatScore].
 * [classification]  Severity of the human's last move.
 * [explanation]     Coaching hint for the position; null when position is solid.
 *                   When the live coaching pipeline is wired, this is the hint
 *                   from POST /live/move; otherwise a static derived string.
 * [bestMove]        Engine's preferred response in UCI notation (e.g. "e2e4");
 *                   null when no engine call was made or engine unavailable.
 * [engineAvailable] False when the engine eval call failed (Timeout / NetworkError /
 *                   HttpError).  True in all other cases, including when
 *                   [engineEvalClient] is null (score is shown as "?").
 */
data class QuickCoachUpdate(
    val scoreText: String,
    val classification: MistakeClassification,
    /** null when position is solid — dock shows fallback text. */
    val explanation: String?,
    /** null when built from local heuristic or when engine is unavailable. */
    val bestMove: String? = null,
    /** False when the eval request failed; used to show the ⚠ indicator. */
    val engineAvailable: Boolean = true,
    /**
     * Engine context signal from POST /live/move; null when the live coaching
     * pipeline is not wired or the backend omitted the field.
     * Reuses [EngineSignalDto] from the chat pipeline so display logic is shared.
     */
    val engineSignal: EngineSignalDto? = null,
    /**
     * True when this update carries the coaching hint for the human's own move
     * (fired immediately after the human moves, before the AI replies).
     * False for AI-score updates emitted after the engine evaluates the position.
     * Only human-move updates should be added to the move classification history.
     */
    val isHumanMoveCoachUpdate: Boolean = false,
    /**
     * True when /live/move answered with `coach_tier.degraded` — the hint came
     * from the deterministic coach because this game is over the plan's daily
     * coached-game quota (API_CONTRACTS.md §4).  Drives the upgrade/limit chip
     * in the coach dock.  Only meaningful on human-move updates; engine
     * analysis is unaffected by degradation.
     */
    val coachDegraded: Boolean = false,
)
package com.cereveon.myapp

import android.util.Log

/**
 * ✅ STEP 6 — JNI CONTRACT (ONE FUNCTION)
 * This is the SINGLE authority for Native calls.
 */
object ChessNative {
    var isLibraryLoaded = false
        private set

    init {
        try {
            System.loadLibrary("chessengine")
            isLibraryLoaded = true
            Log.e("AI_TEST", "✅ Native library loaded")
        } catch (e: Throwable) {
            Log.e("AI_TEST", "❌ Failed to load native library: ${e.message}")
        }
    }

    /**
     * Pure function: FEN -> ONE best move for Black.
     * No side effects in C++.
     */
    external fun getBestMove(fen: String): AIMove?
    external fun getBestMoveWithStrength(fen: String, strengthLevel: Int): AIMove?

    /** No-op in pure architecture, kept for build compatibility */
    fun reset() {}
}
package com.cereveon.myapp

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

enum class Turn { HUMAN, AI }

class ChessViewModel(
    private val engineProvider: EngineProvider = NativeEngineProvider(),
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.Default,
    /** Injected after construction; null disables real engine eval (falls back to "?" score). */
    var engineEvalClient: EngineEvalClient? = null,
    /** Injected after construction; null disables live per-move coaching hints. */
    var liveCoachClient: LiveMoveClient? = null,
    /**
     * Optional source of the server-derived `opponentElo` for the AI
     * strength dial.  Mutable (rather than `private val`) so the
     * activity can wire it in after the ChessViewModel has been
     * resolved by `by viewModels()` — same pattern as
     * [engineEvalClient] / [liveCoachClient].  Without this wiring
     * the strength always falls back to the no-cache default of 100,
     * which is what the AdaptiveEngineWiringTest's
     * NO_CACHE_DEFAULTS_TO_100 case pins.
     *
     * Invalidate the cache (via [PlayerProfileCache.invalidate]) after
     * every /game/finish so the next AI move sees the rating bump
     * the server applied.
     */
    var playerProfileCache: PlayerProfileCache? = null,
    /**
     * Optional safe-mode gate.  When non-null, [dispatchHumanMoveCoach]
     * skips the `POST /live/move` call whenever the gate is not in the
     * [SecaSafetyState.Safe] state.  This is the per-move enforcement
     * of the README "before sending coaching requests, confirm
     * safe_mode" contract.  When null, the legacy unconditional
     * behaviour applies (kept for tests that don't care about the
     * gate).
     *
     * The local engine path (C++ via [engineProvider]) is intentionally
     * NOT gated by this — it doesn't reach the backend's coaching
     * pipeline.  Engine eval (`/analyze`, via [engineEvalClient]) is
     * also not gated: it returns the deterministic ESV with no LLM /
     * adaptive surface.  Only the live coaching hint and chat go
     * through the gate.
     */
    var secaSafetyGate: SecaSafetyGate? = null,
    /**
     * Milliseconds to hold the engine's reply before it lands on the board,
     * sampled once per move.  The native engine answers in milliseconds,
     * which reads as a vending machine rather than an opponent — production
     * (the default) paces every playable reply into the
     * [AI_THINK_PACING_MIN_MS]..[AI_THINK_PACING_MAX_MS] window.  Injectable
     * so unit tests that drive the turn loop in real wall-clock time can pass
     * `{ 0L }`; suites on a virtual-time dispatcher advance through the
     * default transparently.
     */
    private val aiThinkPacingMillis: () -> Long = { defaultAiThinkPacingMillis() },
) : ViewModel() {

    /**
     * Supplier of the current SERVER game id (``games.id`` from
     * POST /game/start) for the free-tier coached-game admission —
     * threaded into every /live/move call as ``game_id``
     * (API_CONTRACTS.md §4).  MainActivity wires this to
     * ``currentGameId()``; null (unwired, or no server game yet) keeps
     * today's behaviour — the server fails open and never degrades.
     */
    var serverGameIdProvider: (() -> String?)? = null

    private var turn: Turn = Turn.HUMAN
    private var aiThinking = false

    private var stateId: Long = 0
    private var aiJob: Job? = null

    private var lastHumanMoveHint: String? = null
    private var lastHumanMoveClassification: MistakeClassification? = null
    private var lastHumanMoveEngineSignal: EngineSignalDto? = null

    // ── Move history for PGN export ──────────────────────────────────────────
    private val moveHistory = mutableListOf<String>()

    // The player's (White's) most recent move in UCI, sent to the chat coach so
    // it can describe "your last move" in plain English instead of misreading
    // the raw FEN.  Null until the human has moved this game.
    private var lastHumanUci: String? = null

    /**
     * Called on the Main thread after each AI move with a [QuickCoachUpdate].
     * When [engineEvalClient] is set, the update contains the real Stockfish
     * centipawn score; otherwise the score field is "?" (engine unavailable).
     * When [liveCoachClient] is set, [QuickCoachUpdate.explanation] is the
     * per-move coaching hint from POST /live/move.
     */
    var onQuickCoachUpdate: ((QuickCoachUpdate) -> Unit)? = null

    /**
     * Fires when a move ends the game (checkmate / stalemate).  Invoked by the
     * move handlers AFTER the move is appended to [moveHistory], so a listener
     * calling [exportPGN] sees the final (mating) move.  Wired by [MainActivity].
     */
    var onGameOver: ((GameResult) -> Unit)? = null

    /** Number of half-moves played so far (human + AI combined). */
    val moveCount: Int get() = moveHistory.size

    /**
     * Comma-separated UCI move list — used by [MainActivity]'s Resume
     * snapshot to persist the full move history without paying the
     * cost of JSON serialisation for what is always a small list of
     * 4–5-character tokens.  Empty string when no moves have been
     * played; [restoreMoveHistory] accepts the inverse.
     */
    fun exportUciHistory(): String = moveHistory.joinToString(",")

    /** The player's most recent move (UCI), or null if they haven't moved yet. */
    fun lastHumanMoveUci(): String? = lastHumanUci

    /**
     * Returns the game moves as a well-formed PGN string including the four
     * mandatory headers required by the backend [GameFinishRequest] validator.
     *
     * Without headers the backend raises a 422 "invalid PGN: no PGN headers
     * found" error, silently failing every /game/finish call.
     *
     * [resultTag] is the PGN `Result` header. White is always "Player" and
     * Black "Engine", so a finished game passes "1-0" (player won), "0-1"
     * (engine won) or "1/2-1/2" (draw). The default "*" (unknown) suits
     * in-progress snapshots. The server reads this header to surface the
     * winner's last move in game history — "*" yields no winner move.
     */
    fun exportPGN(resultTag: String = "*"): String {
        if (moveHistory.isEmpty()) return "(no moves)"
        val moves = moveHistory
            .mapIndexed { index, uci ->
                if (index % 2 == 0) "${index / 2 + 1}. $uci" else uci
            }
            .joinToString(" ")
        return """[Event "Chess Coach Game"]
[White "Player"]
[Black "Engine"]
[Result "$resultTag"]

$moves"""
    }

    private fun uciFromCoords(fr: Int, fc: Int, tr: Int, tc: Int, promo: Char = ' '): String {
        val files = "abcdefgh"
        val suffix = if (promo.isLetter()) promo.lowercaseChar().toString() else ""
        return "${files[fc]}${8 - fr}${files[tc]}${8 - tr}$suffix"
    }

    private fun assertTurn(expected: Turn) {
        check(turn == expected) {
            "ILLEGAL MOVE: $expected expected, but was $turn"
        }
    }

    private fun invalidateState() {
        stateId++
        aiJob?.cancel()
        aiThinking = false
        turn = Turn.HUMAN
        moveHistory.clear()
        lastHumanUci = null
        lastHumanMoveHint = null
        lastHumanMoveClassification = null
        lastHumanMoveEngineSignal = null
        Log.d("STATE", "Game state invalidated. New ID: $stateId")
    }

    fun onHumanMove(
        fr: Int, fc: Int, tr: Int, tc: Int,
        applyHumanMove: () -> MoveResult,
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult? = { null },
    ) {
        if (turn != Turn.HUMAN) return

        val requestId = stateId

        viewModelScope.launch(ioDispatcher) {
            // Capture the pre-move FEN in the same Main hop, BEFORE applying the
            // move (exportFEN() is evaluated first), so the coach can grade move
            // quality from the eval swing fen_before -> post-move fen.
            val (fenBeforeHuman, result) = withContext(Dispatchers.Main) {
                exportFEN() to applyHumanMove()
            }

            withContext(Dispatchers.Main) {
                if (stateId != requestId) return@withContext

                when (result) {
                    MoveResult.SUCCESS -> {
                        val humanUci = uciFromCoords(fr, fc, tr, tc)
                        moveHistory.add(humanUci)
                        lastHumanUci = humanUci
                        // Fire game-over only AFTER recording the move, so
                        // exportPGN() includes the mating move; skip the AI
                        // reply since the game is over.
                        val over = consumeGameOver()
                        if (over != null) {
                            onGameOver?.invoke(over)
                        } else {
                            turn = Turn.AI
                            val fenAfterHuman = exportFEN()
                            dispatchHumanMoveCoach(fenAfterHuman, humanUci, fenBeforeHuman, requestId)
                            requestAIMove(exportFEN, applyAIMove, consumeGameOver)
                        }
                    }
                    MoveResult.PROMOTION -> {
                        Log.d("TURN", "Human promotion pending...")
                    }
                    MoveResult.FAILED -> {}
                }
            }
        }
    }

    fun onPromotionFinished(
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult? = { null },
    ) {
        if (turn != Turn.HUMAN) return
        // The human's promotion may itself have ended the game (a queening
        // mate or a stalemating under-promotion): promotePawn now records
        // that, so consume it BEFORE dispatching an AI reply — mirroring
        // the normal-move path in onHumanMove.  Without this the AI would
        // be asked to move in a finished position.
        val over = consumeGameOver()
        if (over != null) {
            onGameOver?.invoke(over)
            return
        }
        turn = Turn.AI
        requestAIMove(exportFEN, applyAIMove, consumeGameOver)
    }

    private fun requestAIMove(
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult?,
    ) {
        if (aiThinking || turn != Turn.AI) return
        aiThinking = true

        val requestId = stateId

        aiJob = viewModelScope.launch(ioDispatcher) {
            try {
                val fen = withContext(Dispatchers.Main) { exportFEN() }

                // Hybrid cache lookup with a hard ceiling on blocking.
                //
                // The previous implementation called the suspending
                // `getOpponentElo()` directly; that issues
                // `/player/progress` on a cache miss and would wait up
                // to the HTTP read timeout (~15s) when the backend is
                // unhealthy.  During that wait `turn = AI` and the
                // board visually freezes — the symptom users saw when
                // the SECA snackbar showed "Backend safety unverified".
                //
                // Steady-state: warmed by MainActivity at cold-start
                // and after every /game/finish, so the fast path is a
                // cache hit and resolves instantly.  Cold-start race:
                // when the warm hasn't returned yet, fall back to a
                // bounded fetch and degrade to strength 100 after
                // [STRENGTH_FETCH_BUDGET_MS] so the AI dispatch is
                // never blocked for longer than half a second.
                val strengthLevel: Int = playerProfileCache?.let { cache ->
                    cache.cachedOpponentEloOrNull()?.let { return@let EloToStrength.map(it) }
                    val elo = withTimeoutOrNull(STRENGTH_FETCH_BUDGET_MS) {
                        try { cache.getOpponentElo() } catch (_: Exception) { null }
                    }
                    elo?.let { EloToStrength.map(it) } ?: 100
                } ?: 100

                // engineProvider.getBestMove is a JNI call; a bad
                // strength level or a transient native fault must not
                // leave `turn = AI` (board frozen forever).  Catching
                // here lets processAIMoveResult(null, ...) flip turn
                // back to HUMAN so the user can keep playing — same
                // outcome as the native engine returning a no-move.
                val move = try {
                    engineProvider.getBestMove(fen, strengthLevel)
                } catch (t: Throwable) {
                    // Dedicated tag (vs the historic catch-all "AI_TEST"
                    // used by happy-path logs in this file) so an engine
                    // fault is grep-able in production logs without
                    // wading through every routine play log.
                    Log.e("AI_ENGINE", "engineProvider.getBestMove threw", t)
                    null
                }

                // Pacing: hold a playable reply so the opponent reads as
                // thinking — the engine itself answers in milliseconds.
                // Gated on a valid move so an engine fault still flips the
                // turn back to HUMAN immediately (pacing a failure would
                // look like the frozen board the catch above exists to
                // prevent).  delay() keeps the wait cancellable: reset()
                // cancels [aiJob] mid-pacing, and the stateId guard below
                // re-checks after the wait.
                if (move != null && move.isValid()) {
                    delay(aiThinkPacingMillis())
                }

                withContext(Dispatchers.Main) {
                    if (stateId == requestId) {
                        val captured = processAIMoveResult(move, applyAIMove, consumeGameOver)
                        if (captured != null) {
                            // uci is only valid after isValid() passes — compute here
                            val uci = move?.let {
                                uciFromCoords(it.fr, it.fc, it.tr, it.tc, it.promoChar())
                            } ?: ""
                            dispatchEngineEval(captured, uci, exportFEN, requestId)
                        }
                    } else {
                        Log.w("AI_TEST", "Discarding AI move from stale state ($requestId vs $stateId)")
                    }
                }
            } finally {
                aiThinking = false
            }
        }
    }

    /**
     * Fires immediately after the human's move: calls POST /live/move with the
     * human's FEN and UCI, stores the result, and emits a [QuickCoachUpdate]
     * with [QuickCoachUpdate.isHumanMoveCoachUpdate] = true.
     *
     * No-ops when [liveCoachClient] is null.
     */
    private fun dispatchHumanMoveCoach(
        fen: String,
        uci: String,
        fenBefore: String,
        requestId: Long,
    ) {
        val liveClient = liveCoachClient ?: return
        // README contract: confirm safe_mode=true before sending coaching
        // requests.  When the gate is wired and reports Unknown / Unsafe,
        // skip the `/live/move` call rather than fall through to the
        // optimistic path.  Engine eval (dispatched separately) still
        // runs because it's not a coaching request.
        if (secaSafetyGate?.isSafe() == false) {
            Log.d(
                "AI_TEST",
                "dispatchHumanMoveCoach: skipped — SECA gate state=${secaSafetyGate?.state?.value}",
            )
            return
        }
        viewModelScope.launch(ioDispatcher) {
            val liveResult =
                if (uci.length in 4..5) {
                    liveClient.getLiveCoaching(
                        fen,
                        uci,
                        fenBefore = fenBefore,
                        gameId = serverGameIdProvider?.invoke(),
                    )
                } else {
                    null
                }
            withContext(Dispatchers.Main) {
                if (stateId != requestId) return@withContext
                val liveSuccess = liveResult as? ApiResult.Success
                val liveHint = liveSuccess?.data?.hint?.takeIf { it.isNotBlank() }
                val backendClassification = liveSuccess?.data?.moveQuality
                    ?.takeIf { it.isNotBlank() }
                    ?.let { QuickCoachLogic.fromBackendString(it) }
                val liveEngineSignal = liveSuccess?.data?.engineSignal
                lastHumanMoveHint = liveHint
                lastHumanMoveClassification = backendClassification
                lastHumanMoveEngineSignal = liveEngineSignal
                val update = QuickCoachLogic.buildUpdateFromEngine(
                    capturedPiece = '.',
                    engineScore = null,
                    liveHint = liveHint,
                    engineAvailable = true,
                    classificationOverride = backendClassification,
                    engineSignal = liveEngineSignal,
                    isHumanMoveCoachUpdate = true,
                    // Entitlements posture: over-quota games get the
                    // deterministic hint; surface the chip driver.
                    coachDegraded = liveSuccess?.data?.coachTier?.degraded == true,
                )
                onQuickCoachUpdate?.invoke(update)
            }
        }
    }

    /**
     * Obtains the Stockfish centipawn evaluation after the AI move and emits a
     * [QuickCoachUpdate] via [onQuickCoachUpdate].
     *
     * The coaching hint displayed is sourced from [lastHumanMoveHint], which was
     * stored by [dispatchHumanMoveCoach] earlier in the same turn (Mode-1 fires
     * after the human's move, not the AI's move).
     *
     * Falls back to a "?" score when [engineEvalClient] is null.
     * [QuickCoachUpdate.engineAvailable] is set to false on eval errors.
     *
     * Must be called on the Main thread immediately after [processAIMoveResult].
     *
     * @param capturedPiece Piece char that the AI captured ('.' if none).
     * @param uci           The AI move in UCI notation (unused — kept for signature compat).
     * @param exportFEN     Lambda that exports the current board FEN (post-AI).
     * @param requestId     State snapshot to guard against stale results after reset.
     */
    private fun dispatchEngineEval(
        capturedPiece: Char,
        uci: String,
        exportFEN: () -> String,
        requestId: Long,
    ) {
        val evalClient = engineEvalClient

        if (evalClient == null) {
            onQuickCoachUpdate?.invoke(
                QuickCoachLogic.buildUpdateFromEngine(
                    capturedPiece,
                    null,
                    liveHint = lastHumanMoveHint,
                    classificationOverride = lastHumanMoveClassification,
                    engineSignal = lastHumanMoveEngineSignal,
                )
            )
            return
        }

        val fenAfterAI = exportFEN()
        viewModelScope.launch(ioDispatcher) {
            val evalResult = evalClient.evaluate(fenAfterAI)

            withContext(Dispatchers.Main) {
                if (stateId == requestId) {
                    val evalSuccess = evalResult as? ApiResult.Success
                    val score = evalSuccess?.data?.score
                    val bestMove = evalSuccess?.data?.bestMove
                    val engineAvailable = evalResult is ApiResult.Success

                    val update = QuickCoachLogic.buildUpdateFromEngine(
                        capturedPiece,
                        score,
                        bestMove,
                        liveHint = lastHumanMoveHint,
                        engineAvailable = engineAvailable,
                        classificationOverride = lastHumanMoveClassification,
                        engineSignal = lastHumanMoveEngineSignal,
                    )
                    onQuickCoachUpdate?.invoke(update)
                }
            }
        }
    }

    private fun processAIMoveResult(
        move: AIMove?,
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult?,
    ): Char? {
        if (turn != Turn.AI) return null

        if (move == null || !move.isValid()) {
            turn = Turn.HUMAN
            return null
        }

        assertTurn(Turn.AI)
        turn = Turn.HUMAN
        val promo = move.promoChar()
        val captured = applyAIMove(move.fr, move.fc, move.tr, move.tc, promo)
        moveHistory.add(uciFromCoords(move.fr, move.fc, move.tr, move.tc, promo))
        // AI's move recorded — surface a game-ending AI move now that
        // exportPGN() would include it.
        consumeGameOver()?.let { onGameOver?.invoke(it) }
        return captured
    }

    fun reset() {
        invalidateState()
    }

    /**
     * Restore the client-side move history after a HomeActivity Resume
     * tap.  Used by [MainActivity] when the saved snapshot contains a
     * non-empty UCI list — the board's position is restored via
     * [ChessBoardView.setFEN], and this call resyncs the ViewModel so:
     *
     *   - [exportPGN] at the next /game/finish includes the pre-resume
     *     moves (otherwise the PGN would be a stub starting from the
     *     resumed position, which the backend rejects as a tactical
     *     anomaly when the game ends in 2 moves)
     *   - [moveCount] reflects the true half-move number, so the
     *     Atrium chapter header doesn't read "Move 1" after restoring
     *     a 14-move game
     *
     * The native engine is stateless ([ChessNative.getBestMove] is pure
     * FEN → move), so no JNI sync is required.  AI turn is inferred
     * from list parity: even count → HUMAN to move next, odd → AI.
     */
    fun restoreMoveHistory(uciList: List<String>) {
        invalidateState()
        moveHistory.addAll(uciList)
        turn = if (uciList.size % 2 == 0) Turn.HUMAN else Turn.AI
    }

    companion object {
        /**
         * Hard ceiling on how long [requestAIMove] is allowed to wait
         * for [PlayerProfileCache.getOpponentElo] when the cache is
         * cold.  Without this bound the AI dispatch waited up to the
         * underlying HTTP read timeout (~15s) on a slow / unhealthy
         * backend, which left `turn = AI` and made the board appear
         * frozen between human moves.  500ms keeps the interaction
         * snappy and the cold-start fallback to strength 100 is
         * recovered on the next move once the warm completes.
         */
        const val STRENGTH_FETCH_BUDGET_MS: Long = 500L

        /**
         * Opponent "think" pacing window: every playable engine reply is
         * held for a uniform-random duration in this range (sampled per
         * move) before it lands on the board, so the near-instant native
         * engine feels like an opponent taking 2–3 seconds over a move.
         * iOS mirrors these values in PlayViewModel.aiThinkPacing*Nanos —
         * keep the platforms in lock-step.
         */
        const val AI_THINK_PACING_MIN_MS: Long = 2_000L
        const val AI_THINK_PACING_MAX_MS: Long = 3_000L

        /** Production pacing sample — uniform in the window above. */
        internal fun defaultAiThinkPacingMillis(): Long =
            (AI_THINK_PACING_MIN_MS..AI_THINK_PACING_MAX_MS).random()
    }
}
package com.cereveon.myapp

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/**
 * Shared HTTP client interface for all coach backend endpoints.
 *
 * Each call returns an [ApiResult]; callers never see raw exceptions.
 * Implementations are safe to call from any coroutine context — I/O
 * dispatch is handled internally.
 */
interface CoachApiClient {

    /**
     * Send the current position, conversation history, and optional player context
     * to POST /chat.
     *
     * @param fen           Board position in FEN notation.
     * @param messages      Conversation history (most-recent last).
     * @param playerProfile Optional player context (rating, confidence) for personalised
     *                      coaching; omitted from the request when null.
     * @param pastMistakes  Optional list of weakness categories from the last game; omitted
     *                      from the request when null.
     * @param moveCount     Number of half-moves played so far; gives the backend context
     *                      about game phase during mid-game chat (null omits the field).
     * @return              [ApiResult.Success] on HTTP 200 with a valid body;
     *                      [ApiResult.HttpError] on non-200; [ApiResult.Timeout]
     *                      on deadline exceeded; [ApiResult.NetworkError] otherwise.
     */
    suspend fun chat(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto? = null,
        pastMistakes: List<String>? = null,
        moveCount: Int? = null,
        /**
         * Coach voice setting from the user's Settings sheet
         * (formal / conversational / terse).  Shapes the LLM's tone
         * but never its content.  Null → server uses its default
         * Mode-2 tone.
         */
        coachVoice: String? = null,
        // The player's last move (UCI) so the coach can name it in plain
        // English; null → no move line in the prompt.
        lastMove: String? = null,
        // "black" when chatting on an imported/replayed game the user played
        // as Black; null → server anchors White (live-game default).
        playerColor: String? = null,
    ): ApiResult<ChatResponseBody>

    /**
     * Stream the coaching reply for the current position as Server-Sent Events
     * from POST /chat/stream.
     *
     * Emits [StreamChunk.Chunk] for each text fragment, a single
     * [StreamChunk.Done] when the server closes the stream, or
     * [StreamChunk.StreamError] on transport or HTTP failure.
     *
     * The default implementation delegates to [chat] and wraps the complete
     * reply in a single Chunk + Done, so existing test fakes need not override
     * this method.
     */
    fun chatStream(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto? = null,
        pastMistakes: List<String>? = null,
        moveCount: Int? = null,
        coachVoice: String? = null,
        // Per-game chat thread key. Only the real streaming override
        // (HttpCoachApiClient) forwards it; this non-stream fallback drops it.
        gameId: String? = null,
        lastMove: String? = null,
        playerColor: String? = null,
    ): Flow<StreamChunk> = flow {
        when (
            val result =
                chat(
                    fen, messages, playerProfile, pastMistakes, moveCount, coachVoice,
                    lastMove, playerColor,
                )
        ) {
            is ApiResult.Success -> {
                emit(StreamChunk.Chunk(result.data.reply))
                emit(StreamChunk.Done(result.data.engineSignal, "CHAT_V1"))
            }
            is ApiResult.HttpError -> {
                // Mirror the streaming override's "HTTP <code>: <body>"
                // shape so ChatLimitNotice.fromStreamErrorMessage works
                // identically through both paths.
                val suffix = result.body?.takeIf { it.isNotBlank() }?.let { ": $it" } ?: ""
                emit(StreamChunk.StreamError("HTTP ${result.code}$suffix"))
            }
            is ApiResult.NetworkError -> emit(StreamChunk.StreamError("Network error"))
            ApiResult.Timeout -> emit(StreamChunk.StreamError("Timeout"))
        }
    }

    /**
     * POST /game/coach-feedback.
     *
     * Records whether the coaching reply for a given position was helpful.
     * Fire-and-forget: callers should not block the UI on this result.
     * Returns [ApiResult.HttpError(501)] by default so test fakes don't need
     * to override this method.
     */
    suspend fun submitFeedback(
        fen: String,
        isHelpful: Boolean,
        token: String?,
    ): ApiResult<Unit> = ApiResult.HttpError(501)

    /**
     * GET /chat/history — load the most recent persisted chat turns for
     * the authenticated player.
     *
     * The Android client calls this on [ChatBottomSheet.onAttach] so a
     * conversation survives process restarts, device swaps, and
     * reinstalls — the server is the source of truth for chat
     * history; the local [ChatSessionStore] is a UI cache seeded from
     * the server response.
     *
     * Server returns turns in chronological order (oldest first), so
     * the caller can iterate the list and `addAll` directly into the
     * adapter without re-sorting.  The server caps [limit] at
     * ``HISTORY_MAX_LIMIT`` (200) regardless of the requested value;
     * a value > the cap returns the cap.  Default 50 matches the
     * in-memory ``ChatSessionStore`` capacity so a fresh seed never
     * over-fills the cache.
     *
     * Returns [ApiResult.HttpError(501)] by default so test fakes
     * don't need to override this method.
     */
    suspend fun getHistory(
        limit: Int = 50,
        gameId: String? = null,
    ): ApiResult<ChatHistoryResponseBody> = ApiResult.HttpError(501)
}

/**
 * Production implementation of [CoachApiClient] backed by [HttpURLConnection].
 *
 * All I/O is dispatched to [Dispatchers.IO] — the caller needs no special
 * dispatcher. Constructed once and shared; the instance is thread-safe because
 * each [chat] call opens its own connection.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash
 *                         (e.g. "http://10.0.2.2:8000").
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 * @param tokenProvider    Optional supplier of a JWT Bearer token. When
 *                         non-null and returns a non-null string, the token
 *                         is sent as `Authorization: Bearer <token>` alongside
 *                         the X-Api-Key header. Required for endpoints that
 *                         enforce user-level auth (/game/finish, /next-training,
 *                         /curriculum/next).
 */
class HttpCoachApiClient(
    val baseUrl: String,
    val apiKey: String,
    val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
    val tokenProvider: (() -> String?)? = null,
    /**
     * Optional sink for the X-Auth-Token refresh header — see
     * [TokenRefresh].  Without this, a user who chats for a full
     * day without ending a game would lose their session even
     * though they're continuously active.
     */
    val tokenSink: ((String) -> Unit)? = null,
) : CoachApiClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val CHAT_PATH = "/chat"
        private const val CHAT_STREAM_PATH = "/chat/stream"
        private const val CHAT_HISTORY_PATH = "/chat/history"
        private const val FEEDBACK_PATH = "/game/coach-feedback"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    /** Build the standard auth header set (X-Api-Key plus optional Bearer token). */
    private fun authHeaders(extraToken: String? = null): Map<String, String> = buildMap {
        put("X-Api-Key", apiKey)
        val bearer = extraToken ?: tokenProvider?.invoke()
        if (bearer != null) put("Authorization", "Bearer $bearer")
    }

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun chat(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto?,
        pastMistakes: List<String>?,
        moveCount: Int?,
        coachVoice: String?,
        lastMove: String?,
        playerColor: String?,
    ): ApiResult<ChatResponseBody> = withRetry(maxAttempts = 2) {
        http.request(
            path = CHAT_PATH,
            method = "POST",
            headers = authHeaders(),
            body = buildJson(
                fen, messages, playerProfile, pastMistakes, moveCount, coachVoice,
                lastMove = lastMove,
                playerColor = playerColor,
            ),
            onResponse = refreshOnSuccess(),
            parse = ::parseResponse,
        )
    }

    override fun chatStream(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto?,
        pastMistakes: List<String>?,
        moveCount: Int?,
        coachVoice: String?,
        gameId: String?,
        lastMove: String?,
        playerColor: String?,
    ): Flow<StreamChunk> = channelFlow {
        withContext(Dispatchers.IO) {
            // Declared outside the body try so the finally block can
            // disconnect on any exit path — happy completion, HTTP
            // error, or transport exception.  Without the disconnect,
            // ``inputStream.use {}`` closes the read pipe but the
            // underlying socket can linger up to ``readTimeoutMs``
            // (60 s) before the platform reclaims it.
            //
            // Residual gap: under coroutine cancellation while
            // ``reader.readLine()`` is blocked, the finally only runs
            // after the JVM read returns (worst case: readTimeoutMs).
            // ``Dispatchers.IO`` does not interrupt threads on cancel,
            // so a future improvement is to register a cancel-hook
            // (suspendCancellableCoroutine / runInterruptible) that
            // closes the socket from outside the blocked thread.
            var conn: HttpURLConnection? = null
            try {
                val url = URL("$baseUrl$CHAT_STREAM_PATH")
                conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("Accept", "text/event-stream")
                conn.setRequestProperty("X-Api-Key", apiKey)
                conn.setRequestProperty(COACH_API_VERSION_HEADER, COACH_API_VERSION)
                tokenProvider?.invoke()?.let { token ->
                    conn.setRequestProperty("Authorization", "Bearer $token")
                }
                conn.doOutput = true
                conn.connectTimeout = connectTimeoutMs
                conn.readTimeout = readTimeoutMs

                conn.outputStream.bufferedWriter(Charsets.UTF_8).use {
                    it.write(
                        buildJson(
                            fen, messages, playerProfile, pastMistakes, moveCount, coachVoice,
                            gameId, lastMove, playerColor,
                        ),
                    )
                }

                val code = conn.responseCode
                if (code != HttpURLConnection.HTTP_OK) {
                    // DIAGNOSTIC: include the error body (e.g. FastAPI 422
                    // validation detail naming the offending field) so the
                    // client log shows WHY, not just the status code.
                    val errBody = try {
                        conn.errorStream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
                    } catch (_: Exception) {
                        null
                    }
                    val suffix = errBody?.takeIf { it.isNotBlank() }?.let { ": " + it.take(400) } ?: ""
                    send(StreamChunk.StreamError("HTTP $code$suffix"))
                    return@withContext
                }

                // Response headers are available the moment the server
                // commits to a 200 — well before the SSE stream itself
                // starts emitting.  Rotate the JWT now rather than
                // waiting for the stream to close, in case the user
                // backgrounds mid-stream.
                consumeRefreshedToken(conn, tokenSink)

                conn.inputStream.bufferedReader(Charsets.UTF_8).use { reader ->
                    var line: String?
                    while (reader.readLine().also { line = it } != null) {
                        val l = line!!.trim()
                        if (!l.startsWith("data: ")) continue
                        parseStreamChunk(l.removePrefix("data: "))?.let { chunk -> send(chunk) }
                    }
                }
            } catch (_: SocketTimeoutException) {
                send(StreamChunk.StreamError("Timeout"))
            } catch (e: Exception) {
                send(StreamChunk.StreamError(e.message ?: "Network error"))
            } finally {
                // ``disconnect()`` is documented to be safe to call
                // multiple times and on connections that never opened
                // a socket.  Swallow any teardown exception so a
                // dying socket doesn't mask the original error path.
                try {
                    conn?.disconnect()
                } catch (_: Exception) {
                    // intentionally ignored — teardown best-effort
                }
            }
        }
    }

    /**
     * Decode one SSE ``data:`` payload from /chat/stream into a
     * [StreamChunk].  Three discriminator values, all carrying a
     * top-level ``type`` field:
     *
     *   - ``{"type":"chunk","text":"..."}``               → [StreamChunk.Chunk]
     *   - ``{"type":"done","engine_signal":...,"mode":...}`` → [StreamChunk.Done]
     *   - ``{"type":"abort","reply":...,"engine_signal":...,"mode":...}`` → [StreamChunk.Abort]
     *   - ``{"type":"error","message":"..."}``            → [StreamChunk.StreamError]
     *
     * Decoded as a generic [kotlinx.serialization.json.JsonObject] so the
     * type tag can be inspected before committing to a concrete shape —
     * keeps the parser tolerant of new/unknown event types (returns null,
     * which the caller drops).
     */
    private fun parseStreamChunk(text: String): StreamChunk? =
        try {
            val root = ApiJson.parseToJsonElement(text).jsonObject
            when (root["type"]?.jsonPrimitive?.contentOrNull) {
                "chunk" -> StreamChunk.Chunk(
                    root["text"]?.jsonPrimitive?.contentOrNull ?: ""
                )
                "done" -> {
                    val signalEl = root["engine_signal"]?.takeUnless { it is JsonNull }
                    val engineSignal = signalEl?.let {
                        ApiJson.decodeFromJsonElement(EngineSignalDto.serializer(), it)
                    }
                    StreamChunk.Done(
                        engineSignal = engineSignal,
                        mode = root["mode"]?.jsonPrimitive?.contentOrNull ?: "CHAT_V1",
                    )
                }
                "abort" -> {
                    val signalEl = root["engine_signal"]?.takeUnless { it is JsonNull }
                    val engineSignal = signalEl?.let {
                        ApiJson.decodeFromJsonElement(EngineSignalDto.serializer(), it)
                    }
                    StreamChunk.Abort(
                        reply = root["reply"]?.jsonPrimitive?.contentOrNull ?: "",
                        engineSignal = engineSignal,
                        mode = root["mode"]?.jsonPrimitive?.contentOrNull ?: "CHAT_V1",
                    )
                }
                "error" -> StreamChunk.StreamError(
                    root["message"]?.jsonPrimitive?.contentOrNull ?: "Server error"
                )
                else -> null
            }
        } catch (_: Exception) {
            null
        }

    override suspend fun submitFeedback(
        fen: String,
        isHelpful: Boolean,
        token: String?,
    ): ApiResult<Unit> = http.requestNoBody(
        path = FEEDBACK_PATH,
        method = "POST",
        headers = authHeaders(extraToken = token),
        body = ApiJson.encodeToString(
            CoachFeedbackRequest(sessionFen = fen, isHelpful = isHelpful)
        ),
        onResponse = refreshOnSuccess(),
    )

    override suspend fun getHistory(
        limit: Int,
        gameId: String?,
    ): ApiResult<ChatHistoryResponseBody> {
        // Scope history to the current game when present (per-game threads);
        // omit game_id → player-global history (server default).
        val path = buildString {
            append(CHAT_HISTORY_PATH).append("?limit=").append(limit)
            gameId?.takeIf { it.isNotBlank() }?.let {
                append("&game_id=").append(java.net.URLEncoder.encode(it, "UTF-8"))
            }
        }
        return http.request(
            path = path,
            method = "GET",
            headers = authHeaders(),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<ChatHistoryResponseBody>(body) },
        )
    }

    // -----------------------------------------------------------------------
    // JSON serialisation / deserialisation (private — not unit tested directly)
    // -----------------------------------------------------------------------

    /**
     * Encode the /chat (and /chat/stream) request payload.  Null
     * optional fields are dropped by the shared [ApiJson]
     * ``encodeDefaults = false`` config so the wire shape matches
     * the pre-migration ``buildJson`` output (omit-when-null for
     * ``player_profile`` / ``past_mistakes`` / ``move_count`` /
     * ``coach_voice``).  ``coachVoice`` is normalised to null when
     * blank to preserve parity with the prior ``isNotBlank`` guard.
     */
    private fun buildJson(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto?,
        pastMistakes: List<String>?,
        moveCount: Int?,
        coachVoice: String?,
        gameId: String? = null,
        lastMove: String? = null,
        playerColor: String? = null,
    ): String = ApiJson.encodeToString(
        ChatRequestBody(
            fen = fen,
            messages = messages,
            playerProfile = playerProfile,
            pastMistakes = pastMistakes,
            moveCount = moveCount,
            coachVoice = coachVoice?.takeIf { it.isNotBlank() },
            gameId = gameId?.takeIf { it.isNotBlank() },
            lastMove = lastMove?.takeIf { it.isNotBlank() },
            playerColor = playerColor?.takeIf { it.isNotBlank() },
        )
    )

    private fun parseResponse(body: String): ChatResponseBody =
        ApiJson.decodeFromString<ChatResponseBody>(body)
}
package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed request/response models for the coach backend API.
 *
 * Pure Kotlin — no Android or org.json dependencies; fully JVM-testable.
 * Sprint 4.3.C migrated these onto kotlinx-serialization; the shared
 * [ApiJson] config preserves the snake_case wire format
 * (``@SerialName("player_profile")`` / ``@SerialName("past_mistakes")``
 * / etc.).  ``encodeDefaults = false`` ensures optional fields are
 * absent from the wire payload when they are null, matching the
 * pre-migration ``buildJson`` behaviour.
 */

/**
 * A single message in the conversation history, matching the backend schema.
 *
 * [role]    must be "user" or "assistant".
 * [content] is the message text (backend field name is "content", not "text").
 */
@Serializable
data class ChatMessageDto(val role: String, val content: String)

/**
 * Player context sent with every POST /chat request for personalised coaching.
 *
 * Values are sourced from the most recent [GameFinishResponse]:
 *  - [rating]     Current Glicko-2 skill estimate (backend field: `rating`).
 *  - [confidence] Rating confidence in the range 0.0–1.0 (backend field: `confidence`).
 *
 * Maps to the `player_profile` dict accepted by chat_pipeline.generate_chat_reply().
 */
@Serializable
data class PlayerProfileDto(
    val rating: Float,
    val confidence: Float,
)

/**
 * Request body for POST /chat (and /chat/stream — same wire shape).
 *
 * [fen]           current board position in Forsyth-Edwards Notation.
 * [messages]      conversation history (most-recent last).
 * [playerProfile] optional player context for personalised replies; null
 *                 omits the ``player_profile`` field (``encodeDefaults=false``).
 * [pastMistakes]  optional list of weakness categories from the last game;
 *                 null omits the ``past_mistakes`` field.
 * [moveCount]     optional half-move count for game-phase context during
 *                 mid-game chat; null omits ``move_count``.
 * [coachVoice]    optional coach-voice setting from the user's settings sheet
 *                 (formal / conversational / terse).  Shapes tone, not
 *                 content.  Null omits ``coach_voice``.
 */
@Serializable
data class ChatRequestBody(
    val fen: String,
    val messages: List<ChatMessageDto>,
    @SerialName("player_profile") val playerProfile: PlayerProfileDto? = null,
    @SerialName("past_mistakes") val pastMistakes: List<String>? = null,
    @SerialName("move_count") val moveCount: Int? = null,
    @SerialName("coach_voice") val coachVoice: String? = null,
    // Current server game id, so the server scopes this exchange to its game
    // thread (per-game chat history). Null when no active game → player-global.
    @SerialName("game_id") val gameId: String? = null,
    // The player's last move (UCI), so the coach can describe it in plain
    // English instead of misreading the raw FEN. Null → omitted.
    @SerialName("last_move") val lastMove: String? = null,
    // The colour the player is playing, for the coach's "you" framing.
    // "black" when chat is opened on an imported/replayed game where the
    // user played Black (the review board orients to their side); null for
    // live games → omitted → server anchors White (pre-feature behaviour).
    @SerialName("player_color") val playerColor: String? = null,
)

/**
 * Request body for POST /game/coach-feedback.  Fire-and-forget thumbs-up /
 * thumbs-down for the latest coaching reply at the given position.
 */
@Serializable
data class CoachFeedbackRequest(
    @SerialName("session_fen") val sessionFen: String,
    @SerialName("is_helpful") val isHelpful: Boolean,
)

/**
 * Centipawn evaluation band returned by the engine for display in the context header.
 * Null fields indicate the server omitted the field.
 */
@Serializable
data class EvaluationDto(
    val band: String? = null,
    val side: String? = null,
)

/**
 * Engine context signal attached to each /chat response.
 * Null fields indicate the server omitted the field.
 */
@Serializable
data class EngineSignalDto(
    val evaluation: EvaluationDto? = null,
    val phase: String? = null,
)

/**
 * Typed response from POST /chat.
 *
 * [reply]        the coaching text to display in the chat UI.
 * [engineSignal] optional engine context for the context header; null when omitted.
 */
@Serializable
data class ChatResponseBody(
    val reply: String = "",
    @SerialName("engine_signal") val engineSignal: EngineSignalDto? = null,
)

/**
 * One persisted chat turn returned by GET /chat/history.
 *
 * The server stores every user message + assistant reply that
 * survived boundary validation (see ``llm/seca/chat/repo.py``).
 * The client seeds [ChatSessionStore] from these on
 * [ChatBottomSheet.onAttach] so a conversation survives process
 * restarts, device swaps, and reinstalls.
 *
 * Roles are ``"user"`` or ``"assistant"`` (matching
 * [ChatMessageDto.role]).  Server-stored ``"system"`` rows
 * (compaction summaries) are NOT yet emitted by the persistence
 * layer but the field is left wide-typed for future expansion.
 */
@Serializable
data class ChatHistoryTurnDto(
    val id: String,
    val role: String,
    val content: String,
    val fen: String? = null,
    val mode: String = "CHAT_V1",
    @SerialName("created_at") val createdAt: String? = null,
)

/**
 * Response body for GET /chat/history?limit=N.
 *
 * Turns are returned in chronological (oldest first) order so the
 * client can `addAll` into its message list without re-sorting.
 * Empty list when the player has no persisted history yet.
 */
@Serializable
data class ChatHistoryResponseBody(
    val turns: List<ChatHistoryTurnDto> = emptyList(),
)

/**
 * Discriminated union for all possible outcomes of a [CoachApiClient] call.
 *
 * Callers should handle all four variants; use `when` with exhaustive branches.
 *
 *  - [Success]      HTTP 200 with a valid parsed body.
 *  - [HttpError]    Server returned a non-200 status code.
 *  - [NetworkError] Transport-level failure (DNS, refused connection, etc.).
 *  - [Timeout]      Connect or read deadline exceeded.
 */
sealed class ApiResult<out T> {
    data class Success<out T>(val data: T) : ApiResult<T>()

    /**
     * Server returned a non-success status code.  [body] carries the
     * error response body (bounded; null when absent/unreadable) so
     * callers can act on STRUCTURED error contracts — e.g. the 402
     * chat-quota body (`{"error": "chat_daily_limit", ...}`, see
     * API_CONTRACTS.md "Error responses") — instead of only the code.
     * Additive: every pre-existing `HttpError(code)` construction and
     * `.code` read is untouched.
     */
    data class HttpError(val code: Int, val body: String? = null) : ApiResult<Nothing>()
    data class NetworkError(val cause: Throwable) : ApiResult<Nothing>()
    object Timeout : ApiResult<Nothing>()
}

/**
 * Parsed body of the entitlements 402 on POST /chat and /chat/stream
 * (API_CONTRACTS.md §5 "Errors"): the caller's plan and quota so the
 * paywall surface can say exactly what ran out.
 *
 * `upgrade.product` is intentionally not modelled — the client's Play
 * catalogue is [PaywallActivity.PLAY_PRODUCT_IDS]; the server hint is
 * advisory.
 */
@Serializable
data class ChatLimitNotice(
    val error: String = "",
    val plan: String = "",
    val limit: Int = 0,
    val used: Int = 0,
) {
    companion object {
        private const val ERROR_KEY = "chat_daily_limit"

        /** Parse a raw HTTP error body; null unless it IS the chat-quota contract. */
        fun fromBody(body: String?): ChatLimitNotice? {
            if (body.isNullOrBlank()) return null
            val parsed = try {
                ApiJson.decodeFromString<ChatLimitNotice>(body)
            } catch (_: Exception) {
                return null
            }
            return parsed.takeIf { it.error == ERROR_KEY }
        }

        /**
         * Parse a [StreamChunk.StreamError] message of the shape
         * `"HTTP 402: {json body}"` (the stream client appends the error
         * body to the status line — see [HttpCoachApiClient.chatStream]).
         * Null for any other status or a body that isn't the quota
         * contract, so callers can probe every stream error safely.
         */
        fun fromStreamErrorMessage(message: String): ChatLimitNotice? {
            if (!message.startsWith("HTTP 402")) return null
            val jsonStart = message.indexOf('{')
            if (jsonStart < 0) return null
            return fromBody(message.substring(jsonStart))
        }
    }
}

/**
 * Parsed body of the entitlements 402 on POST /game/start
 * (API_CONTRACTS.md §11 "Errors"): the free tier is 1 coached game/day,
 * hard-blocked.  The client renders this as a non-dismissible paywall
 * ("come back tomorrow") and does NOT enter a game.
 *
 * Same Shape B envelope as [ChatLimitNotice] with a distinct `error`
 * discriminator so the game gate is never confused with the chat gate
 * (or any other 402, e.g. the billing-endpoint Shape A body).
 */
@Serializable
data class GameLimitNotice(
    val error: String = "",
    val plan: String = "",
    val limit: Int = 0,
    val used: Int = 0,
) {
    companion object {
        private const val ERROR_KEY = "game_daily_limit"

        /** Parse a raw HTTP error body; null unless it IS the game-limit contract. */
        fun fromBody(body: String?): GameLimitNotice? {
            if (body.isNullOrBlank()) return null
            val parsed = try {
                ApiJson.decodeFromString<GameLimitNotice>(body)
            } catch (_: Exception) {
                return null
            }
            return parsed.takeIf { it.error == ERROR_KEY }
        }
    }
}

/**
 * Discriminated union for a single Server-Sent Event from POST /chat/stream.
 *
 *  - [Chunk]       A partial text fragment to be appended to the assistant message.
 *  - [Done]        Final event carrying the engine signal and pipeline mode.
 *  - [Abort]       Terminal event when the server could not safely complete the
 *                  stream (validate-before-emit aborted): [reply] is the
 *                  deterministic fallback to render IN PLACE of any partial.
 *  - [StreamError] Server or transport error; [message] describes the failure.
 */
sealed class StreamChunk {
    data class Chunk(val text: String) : StreamChunk()
    data class Done(val engineSignal: EngineSignalDto?, val mode: String) : StreamChunk()
    data class Abort(
        val reply: String,
        val engineSignal: EngineSignalDto?,
        val mode: String,
    ) : StreamChunk()
    data class StreamError(val message: String) : StreamChunk()
}
package com.cereveon.myapp

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.text.Layout
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.LeadingMarginSpan
import android.util.AttributeSet
import android.util.TypedValue
import androidx.appcompat.widget.AppCompatTextView
import androidx.core.content.ContextCompat

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

    /** Cap glyph colour — defaults to atrium_accent_cyan (token read
     *  so bright mode flips it via values-notnight/colors.xml). */
    var capColor: Int = ContextCompat.getColor(context, R.color.atrium_accent_cyan)

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
package com.cereveon.myapp

object EloToStrength {
    private const val ELO_MIN = 600
    private const val ELO_MAX = 2400

    fun map(opponentElo: Int): Int =
        ((opponentElo - ELO_MIN) * 100 / (ELO_MAX - ELO_MIN)).coerceIn(0, 100)
}
package com.cereveon.myapp

import kotlinx.serialization.encodeToString

/**
 * Client for POST /engine/eval (server.py — migrated from host_app.py
 * in the 2026-05-12 retirement pass).
 *
 * The endpoint requires X-Api-Key (or Bearer SECA_API_KEY) auth.
 * Implementations are safe to call from any coroutine context; I/O
 * dispatch is handled internally.
 */
interface EngineEvalClient {

    /**
     * Evaluate the position given by [fen] using the Stockfish engine.
     *
     * Returns [ApiResult.Success] with an [EngineEvalResponse] on HTTP 200;
     * [ApiResult.HttpError] on any non-200 response; [ApiResult.Timeout] when
     * the connect or read deadline is exceeded; [ApiResult.NetworkError] for
     * all other transport failures.
     *
     * @param fen Board position in FEN notation.
     */
    suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse>
}

/**
 * Production implementation of [EngineEvalClient] backed by [BaseHttpClient].
 *
 * Each [evaluate] call opens its own connection; the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash
 *                         (e.g. "http://10.0.2.2:8000").
 * @param apiKey           X-Api-Key value.  Sent on every call when
 *                         non-empty; the server-side route is auth-gated
 *                         (Sprint 4.x host_app retirement tightened the
 *                         contract from unauthenticated to X-Api-Key /
 *                         Bearer).  An empty key here will surface as 401.
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 */
class HttpEngineEvalClient(
    val baseUrl: String,
    val apiKey: String = "",
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
) : EngineEvalClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val EVAL_PATH = "/engine/eval"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse> =
        withRetry(maxAttempts = 2) {
            val headers = if (apiKey.isNotEmpty()) mapOf("X-Api-Key" to apiKey) else emptyMap()
            http.request(
                path = EVAL_PATH,
                method = "POST",
                headers = headers,
                body = ApiJson.encodeToString(EngineEvalRequest(fen = fen)),
                parse = { body -> ApiJson.decodeFromString<EngineEvalResponse>(body) },
            )
        }
}
package com.cereveon.myapp

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.nullable
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder

/**
 * Serializer that maps an empty JSON string to a Kotlin null on
 * deserialization (and a Kotlin null to a JSON null on serialization).
 *
 * Preserves the pre-Sprint-4.3.C parser contract where
 * ``optString("best_move").takeIf { it.isNotEmpty() }`` folded the
 * empty-string sentinel into null — the engine emits ``""`` when no
 * legal move exists, and the UI relies on ``bestMove == null`` to
 * suppress the move arrow rather than rendering an empty hint.
 */
@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
private object EmptyStringAsNullSerializer : KSerializer<String?> {
    private val nullableDelegate = String.serializer().nullable
    override val descriptor: SerialDescriptor = nullableDelegate.descriptor
    override fun serialize(encoder: Encoder, value: String?) {
        if (value == null) encoder.encodeNull() else encoder.encodeString(value)
    }
    override fun deserialize(decoder: Decoder): String? =
        decoder.decodeSerializableValue(nullableDelegate)?.takeIf { it.isNotEmpty() }
}

/**
 * Request/response models for POST /engine/eval (server.py).
 *
 * Schema documented in docs/API_CONTRACTS.md §1.
 *
 * Migrated from hand-rolled ``org.json.JSONObject`` parsing onto
 * kotlinx-serialization in Sprint 4.3.C.  ``@SerialName("best_move")``
 * preserves the snake_case wire format that server.py emits while
 * keeping the camelCase Kotlin property name [bestMove].
 */

/**
 * Request body for POST /engine/eval.
 *
 * [fen] Current board position in Forsyth-Edwards Notation.  Note:
 * unlike the pre-Sprint-4.x host_app contract, the new server.py
 * route does NOT accept ``"startpos"`` — send a real FEN.
 */
@Serializable
data class EngineEvalRequest(val fen: String)

/**
 * Response from POST /engine/eval.
 *
 * [score]    Centipawn evaluation from White's perspective.
 *            Positive → White is ahead; negative → Black is ahead.
 *            Null when the engine is unavailable (fallback path).
 * [bestMove] Best move in UCI notation (e.g. "e2e4").
 *            Null when there are no legal moves or engine unavailable.
 * [source]   ``"engine"`` on the happy path; ``"unavailable"`` when
 *            the engine pool is down (post-host_app-retirement
 *            server.py contract — old ``"cache"`` / ``"book"`` source
 *            values are gone).
 */
@Serializable
data class EngineEvalResponse(
    val score: Int? = null,
    @SerialName("best_move")
    @Serializable(with = EmptyStringAsNullSerializer::class)
    val bestMove: String? = null,
    val source: String = "engine",
)
package com.cereveon.myapp

interface EngineProvider {
    fun getBestMove(fen: String): AIMove?
    fun getBestMove(fen: String, strengthLevel: Int): AIMove? = getBestMove(fen)
}

/**
 * The real implementation that calls our JNI code.
 */
class NativeEngineProvider : EngineProvider {
    override fun getBestMove(fen: String): AIMove? {
        if (!ChessNative.isLibraryLoaded) return null
        val move = ChessNative.getBestMove(fen) ?: return null
        return JniMoveBridge.normalize(move, fen)
    }

    override fun getBestMove(fen: String, strengthLevel: Int): AIMove? {
        if (!ChessNative.isLibraryLoaded) return null
        val move = ChessNative.getBestMoveWithStrength(fen, strengthLevel) ?: return null
        return JniMoveBridge.normalize(move, fen)
    }
}
package com.cereveon.myapp

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
import androidx.core.content.ContextCompat

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

    // Token reads so the band chrome follows the active palette
    // (bright mode flips these via values-notnight/colors.xml).
    // NEUTRAL_GREY stays a literal: it has no Atrium token and the
    // mid-grey reads on both the dark and paper surfaces.
    private val accentCyan = ContextCompat.getColor(context, R.color.atrium_accent_cyan)
    private val accentAmber = ContextCompat.getColor(context, R.color.atrium_accent_amber)

    private val trackBg = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_hairline)
        style = Paint.Style.FILL
    }
    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_hairline_strong)
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
            if (side == Side.WHITE) accentCyan else accentAmber
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
        private val NEUTRAL_GREY = Color.parseColor("#7A8094")
    }
}
package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString

/**
 * Client for POST /feedback (docs/API_CONTRACTS.md §38).
 *
 * Persists one free-form "Send feedback" message from the game drawer's
 * form.  Fire-and-forget from the product's perspective: the server
 * stores the row for the operator to read; nothing is read back into
 * coaching.  The route is `Depends(get_current_player)` on the server,
 * so a valid `Authorization: Bearer <jwt>` is required — same posture
 * as [BillingApiClient].
 */
interface FeedbackApiClient {

    /**
     * Submit [message] (server trims + enforces 1..2000 chars) with an
     * optional [appVersion] (`BuildConfig.VERSION_NAME`; server caps at
     * 64 chars).
     *
     * @return [ApiResult.Success] with the receipt on HTTP 200;
     *         [ApiResult.HttpError] on non-200 (422 = blank/overlong
     *         message, 429 = rate limited); [ApiResult.Timeout] /
     *         [ApiResult.NetworkError] for transport failures.
     */
    suspend fun submitFeedback(
        message: String,
        appVersion: String?,
    ): ApiResult<FeedbackSubmitResponse>
}

/**
 * Request body for POST /feedback.
 *
 * [message]     Free-form feedback text.
 * [appVersion]  Client build version; omitted from the wire when null
 *               (`encodeDefaults = false` in [ApiJson]).
 */
@Serializable
data class FeedbackSubmitRequest(
    val message: String,
    @SerialName("app_version") val appVersion: String? = null,
)

/**
 * Response from POST /feedback.
 *
 * [status]  Fixed literal "received" on success.
 * [id]      Server-issued row id (support-conversation reference).
 */
@Serializable
data class FeedbackSubmitResponse(
    val status: String = "",
    val id: String = "",
)

/**
 * Production implementation backed by [BaseHttpClient] /
 * [java.net.HttpURLConnection].  Each call opens its own connection;
 * the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param tokenProvider    Supplier of the JWT Bearer token.  Required (no
 *                         default) — the route is a certain 401 without it,
 *                         same rationale as [HttpBillingApiClient.tokenProvider].
 * @param tokenSink        Optional sink for the X-Auth-Token refresh header
 *                         so the submit call participates in JWT rotation
 *                         (docs/API_CONTRACTS.md §10).
 */
class HttpFeedbackApiClient(
    val baseUrl: String,
    val apiKey: String,
    val tokenProvider: () -> String?,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : FeedbackApiClient {

    companion object {
        private const val FEEDBACK_PATH = "/feedback"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun submitFeedback(
        message: String,
        appVersion: String?,
    ): ApiResult<FeedbackSubmitResponse> = http.request(
        path = FEEDBACK_PATH,
        method = "POST",
        headers = buildMap {
            put("X-Api-Key", apiKey)
            tokenProvider.invoke()?.let { put("Authorization", "Bearer $it") }
        },
        body = ApiJson.encodeToString(
            FeedbackSubmitRequest(message = message, appVersion = appVersion)
        ),
        onResponse = { conn -> consumeRefreshedToken(conn, tokenSink) },
        parse = { body -> ApiJson.decodeFromString<FeedbackSubmitResponse>(body) },
    )
}
package com.cereveon.myapp

import android.app.AlertDialog
import android.text.InputFilter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/**
 * "Send feedback" dialog form, opened from the game drawer's
 * btnSendFeedback.
 *
 * Same construction idiom as [AccountFlows.showChangePasswordDialog]
 * (programmatic AlertDialog + EditText, toast per outcome, Cancel is a
 * no-op) so the two drawer-adjacent form flows read identically.  The
 * message is POSTed to /feedback (docs/API_CONTRACTS.md §38) with the
 * app version attached; the server stores it for the operator and
 * never feeds it back into coaching.
 */
object FeedbackFlows {

    /**
     * Client-side mirror of the server's MAX_FEEDBACK_MESSAGE_LEN so an
     * over-long paste is truncated at input time instead of bouncing
     * off the server's 422.
     */
    const val MAX_FEEDBACK_MESSAGE_LEN = 2000

    fun showSendFeedbackDialog(
        activity: AppCompatActivity,
        feedbackApiClient: FeedbackApiClient,
    ) {
        if (activity.isFinishing || activity.isDestroyed) return
        val layout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(64, 32, 64, 16)
        }
        val etMessage = EditText(activity).apply {
            hint = activity.getString(R.string.feedback_input_hint)
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 4
            gravity = android.view.Gravity.TOP or android.view.Gravity.START
            filters = arrayOf(InputFilter.LengthFilter(MAX_FEEDBACK_MESSAGE_LEN))
        }
        layout.addView(etMessage)

        AlertDialog.Builder(activity)
            .setTitle(R.string.feedback_dialog_title)
            .setView(layout)
            .setPositiveButton(R.string.feedback_send) { _, _ ->
                val message = etMessage.text.toString().trim()
                if (message.isBlank()) {
                    Toast.makeText(
                        activity,
                        R.string.feedback_empty_error,
                        Toast.LENGTH_SHORT,
                    ).show()
                    return@setPositiveButton
                }
                activity.lifecycleScope.launch {
                    when (
                        feedbackApiClient.submitFeedback(
                            message = message,
                            appVersion = BuildConfig.VERSION_NAME,
                        )
                    ) {
                        is ApiResult.Success ->
                            Toast.makeText(
                                activity,
                                R.string.feedback_sent,
                                Toast.LENGTH_SHORT,
                            ).show()
                        else ->
                            Toast.makeText(
                                activity,
                                R.string.feedback_send_failed,
                                Toast.LENGTH_SHORT,
                            ).show()
                    }
                }
            }
            .setNegativeButton(R.string.feedback_cancel, null)
            .show()
    }
}
package com.cereveon.myapp

import kotlinx.serialization.encodeToString
import java.net.HttpURLConnection

// ── Interface ─────────────────────────────────────────────────────────────────

interface GameApiClient {
    suspend fun startGame(playerId: String): ApiResult<GameStartResponse>
    suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse>

    /**
     * Fetch the SECA curriculum recommendation from POST /curriculum/next.
     *
     * Requires Bearer token authentication (uses the configured [tokenProvider]).
     * The server derives the player identity from the JWT — no body is sent
     * (pre-PR-27 this method sent `{"player_id": ...}` which the server
     * silently dropped).  Returns a [CurriculumRecommendation] driven by
     * real per-player history.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so that test
     * fakes implementing only the other methods do not need to override this.
     */
    suspend fun getNextCurriculum(): ApiResult<CurriculumRecommendation> =
        ApiResult.HttpError(501)

    /**
     * GET /game/history — recent finished games, newest-first.
     *
     * @param source  Provenance filter: `"lichess"` (imported games),
     *                `"app"` (in-app games, incl. legacy NULL-source), or
     *                null for all sources intermixed by recency.  A value
     *                outside {app, lichess} is rejected 422 by the server.
     * @param limit   Max rows (1..100).  The default (20) matches the
     *                server default; the source-filtered tabs pass a
     *                larger value so an imported-games view isn't
     *                truncated by unrelated recent in-app games.
     *
     * Requires Bearer token authentication.  Default implementation
     * returns [ApiResult.HttpError(501)] so test fakes do not need to
     * override this method.
     */
    suspend fun getGameHistory(
        source: String? = null,
        limit: Int = 20,
    ): ApiResult<List<GameHistoryItem>> = ApiResult.HttpError(501)

    /** GET /game/{eventId}/positions — per-ply FENs + SANs for game replay. */
    suspend fun getGamePositions(eventId: String): ApiResult<GamePositionsResponse> =
        ApiResult.HttpError(501)

    /**
     * GET /seca/status — open endpoint, no auth required.
     *
     * Returns SECA runtime safety flags. Called at cold-start to confirm that
     * [SecaStatusDto.safeModeEnabled] is true before sending coaching requests.
     * Logs a warning if the backend reports safe_mode=false.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test fakes
     * do not need to override this method.
     */
    suspend fun getSecaStatus(): ApiResult<SecaStatusDto> = ApiResult.HttpError(501)

    /**
     * GET /player/progress — requires Bearer token authentication.
     *
     * Returns the full progress dashboard snapshot: current world-model state,
     * last 20 games with per-game weaknesses, and HistoricalAnalysisPipeline output.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test fakes
     * do not need to override this method.
     */
    suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> = ApiResult.HttpError(501)

    /**
     * POST /game/{gameId}/checkpoint — store the in-progress board state.
     *
     * Used by the cross-device resume feature: every move on the
     * client triggers a checkpoint so the server knows the latest
     * FEN + UCI history for this game.  When the user later opens
     * the app on a different device (or after a reinstall), they
     * pull this state via [getActiveGame].
     *
     * Bearer auth required.  Returns:
     *   - [ApiResult.Success(Unit)] on HTTP 200 (server returns
     *     {"status":"checkpointed"} but we don't surface the field)
     *   - [ApiResult.HttpError(404)] when the game_id is unknown
     *   - [ApiResult.HttpError(403)] when the game belongs to
     *     another player
     *   - [ApiResult.HttpError(409)] when the game is already
     *     finished (a stale checkpoint after /game/finish)
     *   - Transport variants on failure
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun checkpointGame(
        gameId: String,
        fen: String,
        uciHistory: String,
    ): ApiResult<Unit> = ApiResult.HttpError(501)

    /**
     * GET /game/active — fetch the most recent unfinished game's
     * checkpoint for the authenticated player, or null on 404.
     *
     * Used at cold-start when the local SharedPreferences resume
     * snapshot is missing (fresh install / device swap).  Returning
     * `null` on 404 (rather than HttpError) treats "no resumable
     * game" as the same case as "no checkpoint stored" — both
     * mean "start fresh".
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun getActiveGame(): ApiResult<ActiveGameResponse?> =
        ApiResult.HttpError(501)

    /**
     * GET /repertoire — fetch the player's opening repertoire.
     *
     * Backs the AtriumOpenings screen.  When the player has no saved
     * entries the server returns a canonical 4-entry default list
     * (so a fresh user sees a populated screen); the client doesn't
     * distinguish saved-vs-default here, just renders what comes back.
     *
     * Bearer auth required.  Default implementation returns
     * [ApiResult.HttpError(501)] so test fakes don't have to
     * implement it.
     */
    suspend fun getRepertoire(): ApiResult<List<RepertoireOpeningDto>> =
        ApiResult.HttpError(501)

    /**
     * POST /repertoire — add or upsert one opening.
     *
     * Server returns the full updated list so callers can re-render
     * in one round-trip.  When the player has no saved entries yet,
     * the server materialises the canonical defaults first then
     * appends the new line — UX continuity with the GET defaults.
     */
    suspend fun addOpening(
        eco: String,
        name: String,
        line: String,
        mastery: Float = 0.0f,
    ): ApiResult<List<RepertoireOpeningDto>> = ApiResult.HttpError(501)

    /**
     * DELETE /repertoire/{eco} — remove an opening from the
     * player's repertoire.  404 means "already gone" from the
     * caller's perspective — the client should still refresh the
     * list either way.
     */
    suspend fun deleteOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        ApiResult.HttpError(501)

    /**
     * POST /repertoire/{eco}/active — promote one opening to active,
     * demoting any other active line atomically.  Returns the full
     * updated list.
     */
    suspend fun setActiveOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        ApiResult.HttpError(501)

    /**
     * POST /repertoire/{eco}/drill-result — apply one drill outcome
     * to the named opening's mastery.  [outcome] is the user's
     * self-rated score in [0.0, 1.0]; the server applies an EMA
     * step toward it.  Returns the full updated list.
     */
    suspend fun recordDrillResult(
        eco: String,
        outcome: Float,
    ): ApiResult<List<RepertoireOpeningDto>> = ApiResult.HttpError(501)

    /**
     * POST /training/verify-replay — Phase 3 mistake-replay verifier.
     *
     * Sends the position FEN the user was looking at + the move
     * they're proposing as a fix; the server runs Stockfish and
     * returns whether the move is within 30 cp of the engine's best.
     * The Android replay sheet calls this BEFORE calling
     * [submitTrainingSolve] so an unverified move never moves the
     * XP counter.
     *
     * Bearer auth required.  Returns:
     *   - 200 + [VerifyReplayResponse] — engine ran; ``isCorrect``
     *     reflects the verdict.  Note: a wrong move is NOT an error,
     *     it's a successful response with ``isCorrect=false``.
     *   - 400 — malformed FEN, illegal move, or over-long input.
     *   - 503 — engine pool unavailable (boot-time failure or
     *     queue timeout).  Client should show a soft retry message.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun verifyReplayMove(
        fen: String,
        moveUci: String,
    ): ApiResult<VerifyReplayResponse> = ApiResult.HttpError(501)

    /**
     * POST /training/solve — Phase 2 verified-solve persistence.
     *
     * Bumps [Player.training_xp] by 10 (server-side constant) and
     * inserts a TrainingCompletion row.  Idempotent on
     * ``(player, source_type, source_ref)``: a retry returns
     * ``xpAwarded=0`` plus the original completion's timestamp.
     *
     * Bearer auth required.  Returns:
     *   - 200 + [TrainingSolveResponse] — XP credit recorded.
     *   - 400 — invalid source_type or over-long source_ref.
     *   - 429 — rate limit exceeded.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun submitTrainingSolve(
        sourceType: String,
        sourceRef: String?,
    ): ApiResult<TrainingSolveResponse> = ApiResult.HttpError(501)

    /**
     * GET /coach/plan/today — Phase 4 study-plan surface.
     *
     * Returns the player's most recent active per-mistake study plan
     * + the puzzle currently due (the lowest-day-offset puzzle whose
     * ``due_at <= now()`` AND ``completed_at IS NULL``), or
     * [ApiResult.Success] wrapping ``null`` when no active plan
     * exists (no qualifying game played yet, or every plan
     * completed).
     *
     * Bearer auth required.  Returns:
     *   - 200 + [CoachPlanResponse] — active plan + populated theme,
     *     verdict, and (optional) today's puzzle.
     *   - 200 + ``null`` body — no active plan; Android caller hides
     *     the TodaysDrillCard.  Modelled as [ApiResult.Success(null)]
     *     so the caller doesn't have to special-case a 404 (there
     *     is no 404 on this endpoint — absence is a normal product
     *     state).
     *
     * Default implementation returns [ApiResult.HttpError(501)] so
     * test fakes don't have to implement it.
     */
    suspend fun getCoachPlanToday(): ApiResult<CoachPlanResponse?> = ApiResult.HttpError(501)

    /**
     * POST /coach/plan/puzzle/complete — mark one day's puzzle solved
     * and advance the study plan (§35).  Called after a verified-correct
     * solve (verify-replay → training/solve).  Returns the refreshed
     * plan, which may now carry ``status == "completed"`` so the caller
     * can show the week-complete state without a second round-trip.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test
     * fakes don't have to implement it.
     */
    suspend fun completePlanPuzzle(
        planId: String,
        dayOffset: Int,
    ): ApiResult<CoachPlanResponse> = ApiResult.HttpError(501)

    /**
     * GET /puzzles/next — one practice puzzle for the standalone
     * trainer behind the Puzzles tab (§37).  Live-fetched from Lichess
     * at the player's rating-derived difficulty, with a server-side
     * fallback to the curated corpus — the client sees one shape either
     * way and can render [PuzzleNextDto.source] as attribution.
     *
     * Bearer auth required.  Returns:
     *   - 200 + [PuzzleNextDto] — a playable position.
     *   - 503 — Lichess unreachable AND the corpus is empty; show a
     *     soft retry message.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test
     * fakes don't have to implement it.
     */
    suspend fun getNextPuzzle(): ApiResult<PuzzleNextDto> = ApiResult.HttpError(501)
}

// ── HTTP implementation ───────────────────────────────────────────────────────

class HttpGameApiClient(
    val baseUrl: String,
    val apiKey: String,
    val connectTimeoutMs: Int = 8_000,
    val readTimeoutMs: Int = 30_000,
    val tokenProvider: (() -> String?)? = null,
    /**
     * Optional sink for the X-Auth-Token refresh header — see
     * [TokenRefresh].  Every successful authenticated response
     * (game/start, game/finish, etc.) hands the freshly-minted JWT
     * here so the activity's AuthRepository can rotate the stored
     * token transparently.  Without this, the user's JWT would
     * expire after 24 h despite continuous gameplay.
     */
    val tokenSink: ((String) -> Unit)? = null,
) : GameApiClient {

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    /**
     * Header set used by most game endpoints: X-Api-Key plus optional
     * Bearer token from [tokenProvider].  Some endpoints (getNextCurriculum,
     * getGameHistory, getPlayerProgress) omit the X-Api-Key — set
     * [includeApiKey] = false for those.
     */
    private fun authHeaders(includeApiKey: Boolean = true): Map<String, String> = buildMap {
        if (includeApiKey) put("X-Api-Key", apiKey)
        tokenProvider?.invoke()?.let { token -> put("Authorization", "Bearer $token") }
    }

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> = http.request(
        path = "/game/start",
        method = "POST",
        headers = authHeaders(),
        // T3: /game/start now requires a JWT-authenticated session.
        // The server derives player_id from the token; the body
        // player_id is accepted for back-compat but ignored.
        body = ApiJson.encodeToString(GameStartRequest(playerId = playerId)),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameStartResponse>(body) },
    )

    override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> =
        withRetry(maxAttempts = 3) {
            // ``encodeDefaults = false`` on [ApiJson] strips null
            // player_id / game_id from the wire payload so the server
            // sees the same shape as the prior hand-rolled builder.
            // game_id ties this finish back to the original /game/start
            // row server-side; an empty-but-not-null id is normalised
            // away to keep parity with the pre-resume contract.
            val normalised = req.copy(gameId = req.gameId?.takeIf { it.isNotBlank() })
            http.request(
                path = "/game/finish",
                method = "POST",
                headers = authHeaders(),
                body = ApiJson.encodeToString(normalised),
                onResponse = refreshOnSuccess(),
                parse = { body -> ApiJson.decodeFromString<GameFinishResponse>(body) },
            )
        }

    override suspend fun getNextCurriculum(): ApiResult<CurriculumRecommendation> =
        http.request(
            path = "/curriculum/next",
            method = "POST",
            // Bearer-only; X-Api-Key is intentionally omitted here.
            // No body — pre-PR-27 this sent `{"player_id": ...}` which the
            // server silently ignored (it derives the player from the JWT).
            headers = authHeaders(includeApiKey = false),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<CurriculumRecommendation>(body) },
        )

    override suspend fun getGameHistory(
        source: String?,
        limit: Int,
    ): ApiResult<List<GameHistoryItem>> = http.request(
        // Base path kept as its own closed "/game/history" literal (the
        // API-contract coverage test greps Android source for it); the
        // query is concatenated separately.  BaseHttpClient does not
        // URL-encode, but `source` is a fixed enum ("app"/"lichess") and
        // `limit` an Int, so neither needs encoding.  `source` is omitted
        // when null so the server returns all provenances.
        path = "/game/history" + buildString {
            append("?limit=")
            append(limit)
            if (source != null) {
                append("&source=")
                append(source)
            }
        },
        method = "GET",
        // Bearer-only; pre-refactor wire shape did not send X-Api-Key.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameHistoryResponse>(body).games },
    )

    override suspend fun getGamePositions(eventId: String): ApiResult<GamePositionsResponse> =
        http.request(
            // eventId is a server-minted UUID (path-safe); the server caps it
            // at 64 chars and enforces ownership.
            path = "/game/$eventId/positions",
            method = "GET",
            headers = authHeaders(includeApiKey = false),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<GamePositionsResponse>(body) },
        )

    override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> = http.request(
        path = "/player/progress",
        method = "GET",
        // Bearer-only; pre-refactor wire shape did not send X-Api-Key.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<PlayerProgressResponse>(body) },
    )

    override suspend fun getSecaStatus(): ApiResult<SecaStatusDto> = http.request(
        path = "/seca/status",
        method = "GET",
        // Open endpoint — no auth headers.
        parse = { body -> ApiJson.decodeFromString<SecaStatusDto>(body) },
    )

    override suspend fun checkpointGame(
        gameId: String,
        fen: String,
        uciHistory: String,
    ): ApiResult<Unit> = http.requestNoBody(
        path = "/game/${gameId}/checkpoint",
        method = "POST",
        headers = authHeaders(),
        body = ApiJson.encodeToString(CheckpointRequest(fen = fen, uciHistory = uciHistory)),
        onResponse = refreshOnSuccess(),
    )

    override suspend fun getActiveGame(): ApiResult<ActiveGameResponse?> {
        // Special case: 404 is the documented "no resumable game" signal —
        // a normal absence-of-data response, not an error.  We treat both
        // 200 and 404 as success; the parser distinguishes them by the
        // response code captured in [observedCode] from the onResponse
        // hook.  FastAPI emits a non-empty ``{"detail": ...}`` body on
        // 404 so we cannot rely on body-blankness to discriminate.
        var observedCode = 0
        return http.request<ActiveGameResponse?>(
            path = "/game/active",
            method = "GET",
            headers = authHeaders(),
            successCodes = setOf(200, 404),
            onResponse = { conn ->
                observedCode = conn.responseCode
                consumeRefreshedToken(conn, tokenSink)
            },
            parse = { body ->
                if (observedCode == 404) {
                    null
                } else {
                    ApiJson.decodeFromString<ActiveGameResponse>(body)
                }
            },
        )
    }

    override suspend fun getRepertoire(): ApiResult<List<RepertoireOpeningDto>> = http.request(
        path = "/repertoire",
        method = "GET",
        headers = authHeaders(),
        onResponse = refreshOnSuccess(),
        parse = ::parseRepertoireResponse,
    )

    override suspend fun addOpening(
        eco: String,
        name: String,
        line: String,
        mastery: Float,
    ): ApiResult<List<RepertoireOpeningDto>> = http.request(
        path = "/repertoire",
        method = "POST",
        headers = authHeaders(),
        body = ApiJson.encodeToString(
            AddOpeningRequest(eco = eco, name = name, line = line, mastery = mastery)
        ),
        onResponse = refreshOnSuccess(),
        parse = ::parseRepertoireResponse,
    )

    override suspend fun deleteOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        http.request(
            path = "/repertoire/$eco",
            method = "DELETE",
            headers = authHeaders(),
            onResponse = refreshOnSuccess(),
            parse = ::parseRepertoireResponse,
        )

    override suspend fun setActiveOpening(eco: String): ApiResult<List<RepertoireOpeningDto>> =
        http.request(
            path = "/repertoire/$eco/active",
            method = "POST",
            headers = authHeaders(),
            // Empty body — the eco from the path is the only input;
            // the endpoint takes no JSON.
            body = "{}",
            onResponse = refreshOnSuccess(),
            parse = ::parseRepertoireResponse,
        )

    override suspend fun recordDrillResult(
        eco: String,
        outcome: Float,
    ): ApiResult<List<RepertoireOpeningDto>> = http.request(
        path = "/repertoire/$eco/drill-result",
        method = "POST",
        headers = authHeaders(),
        body = ApiJson.encodeToString(DrillResultRequest(outcome = outcome)),
        onResponse = refreshOnSuccess(),
        parse = ::parseRepertoireResponse,
    )

    override suspend fun verifyReplayMove(
        fen: String,
        moveUci: String,
    ): ApiResult<VerifyReplayResponse> = http.request(
        path = "/training/verify-replay",
        method = "POST",
        // Bearer-only — /training/* never carries X-Api-Key.  The
        // server identifies the player via JWT; the FEN + move come
        // from the replay sheet's state.
        headers = authHeaders(includeApiKey = false),
        body = ApiJson.encodeToString(VerifyReplayRequest(fen = fen, moveUci = moveUci)),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<VerifyReplayResponse>(body) },
    )

    override suspend fun submitTrainingSolve(
        sourceType: String,
        sourceRef: String?,
    ): ApiResult<TrainingSolveResponse> = http.request(
        path = "/training/solve",
        method = "POST",
        headers = authHeaders(includeApiKey = false),
        // ``encodeDefaults = false`` on ApiJson strips a null
        // sourceRef from the wire payload so the server's NULL-distinct
        // dedup branch fires correctly (a serialised ``"source_ref": null``
        // would still be a present-but-null key and is treated the
        // same on the FastAPI side, but stripping it keeps the wire
        // shape minimal and matches the request schema example in
        // docs/API_CONTRACTS.md §32).
        body = ApiJson.encodeToString(
            TrainingSolveRequest(sourceType = sourceType, sourceRef = sourceRef)
        ),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<TrainingSolveResponse>(body) },
    )

    override suspend fun getCoachPlanToday(): ApiResult<CoachPlanResponse?> = http.request(
        path = "/coach/plan/today",
        method = "GET",
        // Bearer-only — /coach/plan/today never carries X-Api-Key.
        // The server identifies the player via JWT.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = { body ->
            // The endpoint returns JSON ``null`` (HTTP 200) when the
            // player has no active plan.  kotlinx-serialization
            // happily decodes a literal ``null`` to a nullable type
            // via ``decodeFromString<CoachPlanResponse?>``.
            val trimmed = body.trim()
            if (trimmed.isEmpty() || trimmed == "null") {
                null
            } else {
                ApiJson.decodeFromString<CoachPlanResponse>(trimmed)
            }
        },
    )

    override suspend fun completePlanPuzzle(
        planId: String,
        dayOffset: Int,
    ): ApiResult<CoachPlanResponse> = http.request(
        path = "/coach/plan/puzzle/complete",
        method = "POST",
        // Bearer-only — same auth posture as /coach/plan/today.
        headers = authHeaders(includeApiKey = false),
        body = ApiJson.encodeToString(
            CompletePuzzleRequest(planId = planId, dayOffset = dayOffset)
        ),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<CoachPlanResponse>(body) },
    )

    override suspend fun getNextPuzzle(): ApiResult<PuzzleNextDto> = http.request(
        path = "/puzzles/next",
        method = "GET",
        // Bearer-only — /puzzles/next never carries X-Api-Key; the
        // server derives everything from the JWT-identified player.
        headers = authHeaders(includeApiKey = false),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<PuzzleNextDto>(body) },
    )

    /**
     * Decode the ``{"openings": [...]}`` envelope to the bare list
     * the [GameApiClient] surface returns.  Shared by every /repertoire
     * verb (GET, POST, DELETE, /active, /drill-result) — all five emit
     * the same wrapper shape so callers always receive the full
     * post-mutation list.
     */
    private fun parseRepertoireResponse(body: String): List<RepertoireOpeningDto> =
        ApiJson.decodeFromString<RepertoireListResponse>(body).openings
}
package com.cereveon.myapp

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

/**
 * Typed request/response models for the backend game endpoints.
 *
 * Sprint 4.3.C migrated these off hand-rolled ``org.json.JSONObject``
 * parsing onto kotlinx-serialization.  ``@SerialName`` annotations
 * preserve the snake_case wire format the FastAPI backend emits while
 * keeping the Kotlin properties camelCase.
 *
 * Defaults are intentionally permissive — they mirror what the
 * pre-Sprint-4.3.C ``opt*`` calls used so the deserialiser tolerates
 * older / partial payloads in the same way the old parser did.  The
 * ``ApiJson`` config also flips ``coerceInputValues = true`` so an
 * explicit ``null`` for a non-nullable field falls back to the default
 * rather than throwing.
 */

/**
 * Custom serializer for ``Map<String, String>`` fields that the
 * backend ships as a heterogeneous JSON object (mixed numbers, bools,
 * strings).  The pre-Sprint-4.3.C parser used
 * ``JSONObject.opt(key)?.toString()`` which coerced every value to its
 * string form; this serializer preserves that contract by lifting each
 * primitive into [JsonElement] first, then unwrapping ``JsonPrimitive``s
 * via ``content`` (so ``"win"`` decodes to ``win`` without surrounding
 * quotes) and falling back to ``toString()`` for nested objects /
 * arrays.
 *
 * Used by:
 *  - [CurriculumRecommendation.payload]
 *  - [CoachContentDto.payload]
 */
private object JsonAsStringMapSerializer : KSerializer<Map<String, String>> {
    private val delegate = MapSerializer(String.serializer(), JsonElement.serializer())
    override val descriptor = delegate.descriptor
    override fun serialize(encoder: Encoder, value: Map<String, String>) {
        delegate.serialize(encoder, value.mapValues { (_, v) -> JsonPrimitive(v) })
    }
    override fun deserialize(decoder: Decoder): Map<String, String> =
        delegate.deserialize(decoder).mapValues { (_, v) ->
            if (v is JsonPrimitive) v.content else v.toString()
        }
}

// ── /seca/status ─────────────────────────────────────────────────────────────

/**
 * Response from GET /seca/status.
 *
 * Open endpoint (no auth). Android reads this at cold-start to confirm that
 * the backend is running in SAFE_MODE before sending coaching requests.
 *
 * [safeModeEnabled]  True when SECA bandit/policy training is disabled.
 *
 * The backend previously also returned `bandit_enabled` and `version`.
 * Both were dropped for information-leak reduction: `bandit_enabled` was
 * redundant (`!safeModeEnabled`) and `version` had no client behavioural
 * use.  The Kotlin DTO mirrors the trimmed contract.
 */
@Serializable
data class SecaStatusDto(
    @SerialName("safe_mode") val safeModeEnabled: Boolean = true,
)

// ── /curriculum/next ─────────────────────────────────────────────────────────

/**
 * Training recommendation returned by POST /curriculum/next.
 *
 * Driven by the SECA brain using real per-player history.  This is the
 * authoritative training-recommendation surface; the legacy
 * GET /next-training/{player_id} endpoint + its companion
 * ``TrainingRecommendation`` DTO were retired in PR 26 (2026-05-15).
 *
 * ``difficulty`` is one of ``"easy" | "medium" | "hard"`` — the band
 * string emitted by ``CurriculumPolicy.choose_difficulty()`` on the
 * server.  Earlier revisions declared it as ``Float = 0.5f`` to align
 * with a draft contract that anticipated a 0..1 float, but the live
 * Python implementation has always shipped the band string; with
 * kotlinx-serialization 1.8.1 the type mismatch threw
 * ``JsonDecodingException`` at every call site
 * (``coerceInputValues = true`` covers null-for-non-null and unknown
 * enums but does NOT coerce string-where-number-expected).  The result
 * was that the Training tab silently surfaced "Training unavailable"
 * on every tap.
 *
 * Backend contract: docs/API_CONTRACTS.md §18.
 */
@Serializable
data class CurriculumRecommendation(
    val topic: String = "",
    val difficulty: String = "medium",
    @SerialName("exercise_type") val exerciseType: String = "",
    @Serializable(with = JsonAsStringMapSerializer::class)
    val payload: Map<String, String> = emptyMap(),
)

// CurriculumNextRequest retired in PR 27 (2026-05-15).  POST /curriculum/next
// is body-less now — the server derives the player from the JWT.  Pre-PR-27
// Android sent `{"player_id": ...}` which the server silently dropped
// (wire-noise flagged in the SECA-Android wiring audit § C-1).

// ── /game/history ─────────────────────────────────────────────────────────────

/**
 * Summary of a single completed game returned by GET /game/history.
 *
 * [result]      One of "win", "loss", "draw".
 * [accuracy]    Move accuracy 0.0–1.0 as recorded at the time of /game/finish.
 * [ratingAfter] Player rating after this game; null when no rating update was stored.
 * [createdAt]   ISO-8601 datetime string (e.g. "2026-03-21T14:05:00").
 */
@Serializable
data class GameHistoryItem(
    val id: String = "",
    // The live game id (games.id) this finished game maps to, used to load
    // its coaching chat via GET /chat/history?game_id=.  Null for legacy /
    // imported / pre-game_id rows (no per-game chat thread).  Server: #230.
    @SerialName("game_id") val gameId: String? = null,
    // SAN of the game's final move (e.g. "Qh7#"), for a history-row preview.
    // Null for moveless / legacy rows.  Server: GET /game/history.
    @SerialName("last_move") val lastMove: String? = null,
    // SAN of the winning side's final move, per the PGN Result header.
    // Differs from lastMove when the loser moved last.  Null for draws /
    // ongoing / moveless rows.  Server: GET /game/history.
    @SerialName("winner_move") val winnerMove: String? = null,
    val result: String = "",
    val accuracy: Float = 0f,
    @SerialName("rating_after") val ratingAfter: Float? = null,
    @SerialName("created_at") val createdAt: String = "",
    // Provenance: "lichess" for imported games, "app" for in-app games
    // (the server normalises legacy NULL-source rows to "app").  Drives
    // the history screen's source filter + the "LICHESS" row badge.
    // Defaults to "app" so a payload from a server predating the field
    // decodes as an in-app game rather than throwing.
    val source: String = "app",
)

/**
 * Wire shape for GET /game/{event_id}/positions — game-replay data.
 *
 * [positions] N+1 FENs: index 0 is the start, index i is the board AFTER ply i
 *             (positions.last() is the final position).
 * [moves]     N SANs: moves[i] produced positions[i+1] (for move-list labels).
 */
@Serializable
data class GamePositionsResponse(
    val positions: List<String> = emptyList(),
    val moves: List<String> = emptyList(),
    // Which side the player was on ("white" / "black"), for replay board
    // orientation.  Null for in-app games (always white) and legacy rows —
    // the review renders null as white (no flip).
    @SerialName("player_color") val playerColor: String? = null,
)

/**
 * Wire shape for GET /game/history.  The backend wraps the array in a
 * ``{"games": [...]}`` envelope; this wrapper lets the client decode
 * the envelope and the caller surface receives the bare list.
 */
@Serializable
internal data class GameHistoryResponse(
    val games: List<GameHistoryItem> = emptyList(),
)

// ── /player/progress ─────────────────────────────────────────────────────────

/**
 * Current player world-model snapshot from GET /player/progress.
 *
 * [rating]           Current Elo-style rating.
 * [confidence]       Confidence estimate [0.0–1.0].
 * [skillVector]      Aggregated weakness scores per skill category.
 * [tier]             Human-readable skill tier: "beginner" | "intermediate" | "advanced".
 * [teachingStyle]    Coach verbosity: "simple" | "intermediate" | "advanced".
 * [opponentElo]      Effective opponent strength the engine currently plays at.
 * [explanationDepth] Normalised pedagogy depth [0.0–1.0].
 * [conceptComplexity] Normalised concept complexity [0.0–1.0].
 */
@Serializable
data class ProgressCurrentDto(
    val rating: Float = 0f,
    val confidence: Float = 0f,
    @SerialName("skill_vector") val skillVector: Map<String, Float> = emptyMap(),
    val tier: String = "intermediate",
    @SerialName("teaching_style") val teachingStyle: String = "intermediate",
    @SerialName("opponent_elo") val opponentElo: Int = 1200,
    @SerialName("explanation_depth") val explanationDepth: Float = 0.5f,
    @SerialName("concept_complexity") val conceptComplexity: Float = 0.5f,
)

/**
 * Single game entry in the progress history from GET /player/progress.
 *
 * [weaknesses] per-phase mistake rates: keys "opening", "middlegame", "endgame".
 */
@Serializable
data class ProgressHistoryItem(
    @SerialName("game_id") val gameId: String = "",
    val result: String = "",
    val accuracy: Float = 0f,
    @SerialName("rating_after") val ratingAfter: Float? = null,
    @SerialName("confidence_after") val confidenceAfter: Float? = null,
    val weaknesses: Map<String, Float> = emptyMap(),
    @SerialName("created_at") val createdAt: String = "",
)

/** One training recommendation in the analysis block. */
@Serializable
data class ProgressRecommendation(
    val category: String = "",
    val priority: String = "low",   // "high" | "medium" | "low"
    val rationale: String = "",
)

/**
 * Analysis block in GET /player/progress — output of HistoricalAnalysisPipeline.
 *
 * [categoryScores]  Category → score [0.0–1.0].
 * [phaseRates]      Phase → mistake rate [0.0–1.0].
 */
@Serializable
data class ProgressAnalysisDto(
    @SerialName("dominant_category") val dominantCategory: String? = null,
    @SerialName("games_analyzed") val gamesAnalyzed: Int = 0,
    @SerialName("category_scores") val categoryScores: Map<String, Float> = emptyMap(),
    @SerialName("phase_rates") val phaseRates: Map<String, Float> = emptyMap(),
    val recommendations: List<ProgressRecommendation> = emptyList(),
)

/** Full response from GET /player/progress. */
@Serializable
data class PlayerProgressResponse(
    val current: ProgressCurrentDto = ProgressCurrentDto(),
    val history: List<ProgressHistoryItem> = emptyList(),
    val analysis: ProgressAnalysisDto = ProgressAnalysisDto(),
)

// ── /game/start ──────────────────────────────────────────────────────────────

/** Request body for POST /game/start. */
@Serializable
data class GameStartRequest(
    @SerialName("player_id") val playerId: String,
)

/** Response from POST /game/start. */
@Serializable
data class GameStartResponse(
    @SerialName("game_id") val gameId: String = "",
)

/**
 * Response from GET /game/active — the most recent unfinished
 * game's checkpoint for the authenticated player.  Null is returned
 * by the client when the server responds 404 (= "no resumable game").
 */
@Serializable
data class ActiveGameResponse(
    @SerialName("game_id") val gameId: String = "",
    @SerialName("current_fen") val currentFen: String = "",
    /** Comma-separated UCI moves; empty when no moves were made yet. */
    @SerialName("current_uci_history") val currentUciHistory: String = "",
)

/**
 * Body for POST /game/{gameId}/checkpoint.  The path carries the game
 * id; the body just snapshots the current FEN and UCI history.
 */
@Serializable
data class CheckpointRequest(
    val fen: String,
    @SerialName("uci_history") val uciHistory: String,
)

/**
 * One opening in the player's repertoire — wire shape of GET /repertoire.
 * Kept (with the /repertoire client methods) after the Openings screen
 * was removed from the UI — the backend contract (§14) is unchanged and
 * a future repertoire surface can rewire without an API-layer change.
 */
@Serializable
data class RepertoireOpeningDto(
    val eco: String = "",
    val name: String = "",
    val line: String = "",
    val mastery: Float = 0f,
    @SerialName("is_active") val isActive: Boolean = false,
    val ordinal: Int = 0,
)

/**
 * Wire shape for /repertoire endpoints.  The backend wraps the array
 * in ``{"openings": [...]}``; this wrapper lets the client decode the
 * envelope and the caller surface receives the bare list.
 */
@Serializable
internal data class RepertoireListResponse(
    val openings: List<RepertoireOpeningDto> = emptyList(),
)

/** Body for POST /repertoire (add opening). */
@Serializable
data class AddOpeningRequest(
    val eco: String,
    val name: String,
    val line: String,
    val mastery: Float,
)

/** Body for POST /repertoire/{eco}/drill-result. */
@Serializable
data class DrillResultRequest(
    val outcome: Float,
)

// ── /game/finish ─────────────────────────────────────────────────────────────

@Serializable
data class GameFinishRequest(
    val pgn: String,
    val result: String, // "win" | "loss" | "draw"
    val accuracy: Float, // 0..1
    val weaknesses: Map<String, Float> = emptyMap(),
    @SerialName("player_id") val playerId: String? = null,
    /**
     * Optional game_id captured from the corresponding /game/start
     * response.  When forwarded, the backend marks the matching `games`
     * row complete (result + finished_at columns) instead of leaving it
     * orphaned in NULL purgatory.  Null is accepted by the server for
     * backwards-compat with older clients that didn't track the id.
     *
     * The Resume flow reuses the same id across the original session
     * and the resumed-with-the-same-position one — that's how a
     * resumed game finishes against exactly one games row server-side.
     */
    @SerialName("game_id") val gameId: String? = null,
)

@Serializable
data class CoachActionDto(
    val type: String = "NONE",
    val weakness: String? = null,
    val reason: String? = null,
)

@Serializable
data class CoachContentDto(
    val title: String = "Keep playing",
    val description: String = "",
    @Serializable(with = JsonAsStringMapSerializer::class)
    val payload: Map<String, String> = emptyMap(),
)

/**
 * Nested ``learning`` block in the /game/finish response.
 *
 * Surfaced flat through [GameFinishResponse.learningStatus] so callers
 * keep a single-field accessor while the wire shape stays a nested
 * object.  An empty string from the backend collapses to null at the
 * accessor (pre-migration parity).
 */
@Serializable
data class LearningStatusDto(
    val status: String? = null,
)

/**
 * The first move in the game whose centipawn loss cleared the
 * server-side mistake threshold (150 cp).
 *
 * Surfaced on the /game/finish response (Phase 3) so the Android
 * client can show a "Replay your mistake" CTA on the post-game sheet
 * and launch ``MistakeReplayBottomSheet`` with the position + the
 * move the player originally played.  Server-side selection policy
 * is "first above threshold" rather than "largest loss" so the user
 * learns the originating mistake before its downstream cascade.
 *
 * Always present on the wire, but ``null`` when (a) the engine
 * recompute fell back to client values, or (b) no move clears the
 * server-side ``MIN_MISTAKE_LOSS_CP`` threshold (150 cp).  The
 * client just hides the CTA in those cases.
 *
 * The DTO is named ``BiggestMistakeDto`` and decodes the
 * ``biggest_mistake`` wire field for backward compatibility with
 * PR #192's original "biggest loss" picker; the selection semantics
 * flipped to "first above threshold" without breaking the wire
 * contract.  See ``docs/API_CONTRACTS.md`` §3.
 */
@Serializable
data class BiggestMistakeDto(
    /** FEN of the position the player was looking at, BEFORE the bad move. */
    val fen: String = "",
    /** UCI of the move the player actually played at that position. */
    @SerialName("played_move") val playedMove: String = "",
    /** 1-indexed Nth player half-move.  Used in the replay sheet header copy. */
    @SerialName("move_number") val moveNumber: Int = 0,
    /** Centipawn loss this single move cost the player. Always >= 150 when populated. */
    @SerialName("eval_loss_cp") val evalLossCp: Int = 0,
    /**
     * Opaque identifier to forward to POST /training/solve as
     * ``source_ref`` on a verified-correct replay.  The server
     * constructs it as ``event_<event_id>:move_<n>`` so the
     * ``(player, source_type, source_ref)`` dedup triple stays
     * stable across retries.
     */
    @SerialName("source_ref") val sourceRef: String = "",
)

@Serializable
data class GameFinishResponse(
    val status: String = "stored",
    @SerialName("new_rating") val newRating: Float = 0f,
    val confidence: Float = 0f,
    @SerialName("coach_action") val coachAction: CoachActionDto = CoachActionDto(),
    @SerialName("coach_content") val coachContent: CoachContentDto = CoachContentDto(),
    /**
     * Backing for [learningStatus].  Deserialised from the nested
     * ``{"learning": {"status": ...}}`` block of the /game/finish
     * response; callers read the flat [learningStatus] accessor below.
     */
    val learning: LearningStatusDto? = null,
    /**
     * Phase 3 mistake-replay payload.  ``null`` (the default) means
     * either no mistake worth replaying or the accuracy recompute
     * fell back to client values; the Android client hides the
     * "Replay your mistake" CTA in both cases.
     */
    @SerialName("biggest_mistake") val biggestMistake: BiggestMistakeDto? = null,
) {
    /**
     * Status string from the `learning` object in the /game/finish response
     * (e.g. "stored", "updated").  Null when the backend omitted the field
     * or sent an empty string.
     */
    val learningStatus: String?
        get() = learning?.status?.takeIf { it.isNotEmpty() }
}


// ── /training/verify-replay ──────────────────────────────────────────────────


/**
 * Request body for POST /training/verify-replay.
 *
 * The Android replay sheet sends this after the user submits a move
 * on the embedded ChessBoardView.  The server runs Stockfish, checks
 * whether the move is within 30 cp of the engine's best, and returns
 * the verdict.  Only on ``isCorrect=true`` does the client follow up
 * with POST /training/solve to actually credit XP.
 */
@Serializable
data class VerifyReplayRequest(
    val fen: String,
    @SerialName("move_uci") val moveUci: String,
)


/**
 * Response from POST /training/verify-replay.
 *
 * [isCorrect] = true means the user's move gave up at most 30 cp vs
 * the engine's best move; false means "try again, this one was too
 * loose".  [engineBestUci] is always populated so the UI can offer a
 * "Show me the engine's move" peek without a second round-trip.
 * [evalLossCp] is signed (player POV) — positive when the user's
 * move was worse than the engine's.
 */
@Serializable
data class VerifyReplayResponse(
    @SerialName("is_correct") val isCorrect: Boolean = false,
    @SerialName("engine_best_uci") val engineBestUci: String = "",
    @SerialName("eval_loss_cp") val evalLossCp: Int = 0,
)


// ── /training/solve ──────────────────────────────────────────────────────────


/**
 * Request body for POST /training/solve (Phase 2 endpoint).
 *
 * The Android replay sheet posts this on a verified-correct attempt
 * with ``sourceType = "mistake_replay"`` and ``sourceRef`` copied
 * from [BiggestMistakeDto.sourceRef] so the server-side
 * ``(player, source_type, source_ref)`` dedup triple stays stable.
 */
@Serializable
data class TrainingSolveRequest(
    @SerialName("source_type") val sourceType: String,
    @SerialName("source_ref") val sourceRef: String? = null,
)


/**
 * Response from POST /training/solve.
 *
 * [xpAwarded] = 0 indicates a dedup hit (the client retried the same
 * solve) — the client should NOT toast "+10 XP" in that case, but
 * may still update [trainingXp] in PREF_TRAINING_XP since the server
 * value is authoritative.
 */
@Serializable
data class TrainingSolveResponse(
    @SerialName("xp_awarded") val xpAwarded: Int = 0,
    @SerialName("training_xp") val trainingXp: Int = 0,
    @SerialName("completed_at") val completedAt: String = "",
)


// ── /coach/plan/today ────────────────────────────────────────────────────────


/**
 * Today's due puzzle from the per-mistake study plan.
 *
 * Nullable inside [CoachPlanResponse] because a plan can be active
 * but have no puzzle currently due (e.g. day-0 has been completed
 * and day-3 isn't due for another two days).  Android renders
 * "Next drill in N days" copy in that case.
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §34.
 */
@Serializable
data class TodayPuzzleDto(
    /** One of 0, 3, 7 — which step of the spaced-repetition program. */
    @SerialName("day_offset") val dayOffset: Int = 0,
    /** Position the puzzle drops the user into. */
    val fen: String = "",
    /** Engine's preferred move at [fen] (UCI), used by the verifier. */
    @SerialName("expected_move_uci") val expectedMoveUci: String = "",
    /** ``"original"`` for day-0 (the player's actual mistake) or
     *  ``"library"`` for theme-matched corpus variants. */
    @SerialName("source_type") val sourceType: String = "",
    /** ISO-8601 UTC timestamp; invariant: ``due_at <= now()`` when this
     *  object is non-null. */
    @SerialName("due_at") val dueAt: String = "",
)


/**
 * One day-slot in the week-overview schedule (`days[]`).
 *
 * Powers the week-overview screen: each day is [completed] (done),
 * [isDue] (available to start now), or neither (locked behind its
 * [dueAt]).  Unlike [TodayPuzzleDto] this carries no FEN / expected
 * move — the playable position comes from [CoachPlanResponse.todayPuzzle].
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §34.
 */
@Serializable
data class PlanDayDto(
    /** One of 0, 3, 7. */
    @SerialName("day_offset") val dayOffset: Int = 0,
    /** ISO-8601 UTC timestamp of when this day unlocks. */
    @SerialName("due_at") val dueAt: String = "",
    /** True once the day's puzzle has been solved. */
    val completed: Boolean = false,
    /** True when available now — ``due_at <= now()`` AND not completed. */
    @SerialName("is_due") val isDue: Boolean = false,
    /** ``"original"`` (the player's actual mistake) or ``"library"``. */
    @SerialName("source_type") val sourceType: String = "",
)


/**
 * Top-level shape of ``GET /coach/plan/today`` when the player has an
 * active per-mistake study plan.  Also the response of
 * ``POST /coach/plan/puzzle/complete``.
 *
 * The endpoint also returns JSON ``null`` (HTTP 200) when no active
 * plan exists; the Android client decodes that to a Kotlin ``null``
 * via the [GameApiClient.getCoachPlanToday] parse step.
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §34 / §35.
 */
@Serializable
data class CoachPlanResponse(
    @SerialName("plan_id") val planId: String = "",
    /** One of [llm.seca.coach.study_plan.verdict.THEME_VOCABULARY]
     *  on the server side; the Android client treats it as opaque
     *  and renders it via [formatTheme] for display. */
    val theme: String = "generic",
    /** LLM-written ≤ 60-word retrospective.  Empty string when the
     *  LLM was unreachable or failed validators — TodaysDrillCard
     *  hides the verdict line in that case. */
    val verdict: String = "",
    /** The aggregate dominant weakness the week is built around — one of
     *  opening_preparation / tactical_vision / positional_play /
     *  endgame_technique, or ``null`` for legacy plans / too little
     *  history.  Rendered as the week's focus in the overview screen. */
    @SerialName("anchor_category") val anchorCategory: String? = null,
    /** Plan lifecycle: ``"active"`` while in progress, ``"completed"``
     *  once every day is solved.  GET returns only active plans; the
     *  completion endpoint returns the freshly-completed plan so the
     *  client can show the week-complete state. */
    val status: String = "active",
    /** Always 3 today; surfaced for "Day N of M" rendering. */
    @SerialName("total_days") val totalDays: Int = 3,
    /** ``null`` when no puzzle's ``due_at`` has elapsed yet (e.g.
     *  day-0 solved, day-3 not yet due). */
    @SerialName("today_puzzle") val todayPuzzle: TodayPuzzleDto? = null,
    /** The full week schedule, ordered by day_offset (always
     *  [totalDays] entries).  Empty only when decoding an older
     *  server response that predates the field. */
    val days: List<PlanDayDto> = emptyList(),
)


/**
 * Body for ``POST /coach/plan/puzzle/complete`` — mark one day's puzzle
 * solved and advance the plan.  Sent after a verified-correct solve
 * (verify-replay → training/solve).
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §35.
 */
@Serializable
data class CompletePuzzleRequest(
    @SerialName("plan_id") val planId: String,
    @SerialName("day_offset") val dayOffset: Int,
)


// ── /puzzles/next ────────────────────────────────────────────────────────────


/**
 * One practice puzzle from ``GET /puzzles/next`` — the standalone
 * puzzle-trainer feed behind the Puzzles tab.
 *
 * [puzzleId] is the stable identifier the trainer passes back to
 * ``POST /training/solve`` as ``source_ref`` (with
 * ``source_type = "standard_puzzle"``) so each puzzle is credit-once.
 * Lichess picks arrive namespaced ``lichess_<id>``; corpus picks carry
 * the curated YAML id.
 *
 * [expectedMoveUci] is a display / short-circuit hint only — whether an
 * attempt counts as solved is judged by the local engine via
 * ``POST /training/verify-replay``, same as every other training source.
 *
 * Wire shape pinned by ``docs/API_CONTRACTS.md`` §37.
 */
@Serializable
data class PuzzleNextDto(
    @SerialName("puzzle_id") val puzzleId: String = "",
    /** The solver's position; side to move = the side the user plays. */
    val fen: String = "",
    @SerialName("expected_move_uci") val expectedMoveUci: String = "",
    /** Corpus theme tag for library picks; ``"mix"`` for Lichess picks. */
    val theme: String = "mix",
    /** ``beginner`` / ``intermediate`` / ``advanced``. */
    val difficulty: String = "",
    /** ``"lichess"`` (live fetch) or ``"library"`` (curated corpus). */
    val source: String = "",
    /** Lichess puzzle rating when known; null for corpus picks. */
    val rating: Int? = null,
)
package com.cereveon.myapp

import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * Bottom sheet showing the player's recent games, with an All / In-app /
 * Lichess source filter so imported Lichess games are visible, labelled,
 * and reachable independently of how many recent in-app games exist.
 *
 * Data is fetched from GET /game/history (Bearer auth). The [HistoryFilter]
 * maps each tab to the endpoint's `source` query param + a row limit — the
 * "All" tab keeps the 20-game recent window (so the rating sparkline reads
 * as recent form), while the source-filtered tabs request more so a view
 * isn't truncated by unrelated recent games. Imported rows carry a
 * "LICHESS" badge.
 *
 * Tapping a game loads it onto the main board for replay + live coaching
 * (see [MainActivity.openFinishedGameReview]) and dismisses this sheet.
 * Replay works for every game (positions are replayed server-side from the
 * stored PGN), so every row with an event id is tappable.
 */
class GameHistoryBottomSheet : BottomSheetDialogFragment() {

    /**
     * A history tab: the `source` query value it requests (null = all
     * provenances) and the row [limit] for that view.
     */
    enum class HistoryFilter(val source: String?, val limit: Int) {
        // "All" keeps the recent 20-game window (sparkline = recent form).
        ALL(null, 20),
        // Source views request a deeper slice so they aren't truncated by
        // unrelated recent games of the other provenance.
        APP("app", 50),
        LICHESS("lichess", 50),
    }

    companion object {
        /**
         * Extract non-null [GameHistoryItem.ratingAfter] values for the sparkline,
         * returned in chronological order (oldest game first).
         *
         * Takes at most the 10 most recent games from [games] (which arrive
         * newest-first from the server), reverses them to chronological order,
         * then filters out null ratings.
         *
         * Pure function — safe to call from unit tests without Android context.
         */
        fun extractSparklineRatings(games: List<GameHistoryItem>): List<Float> =
            games.take(10).reversed().mapNotNull { it.ratingAfter }

        /**
         * True when a history row was imported from Lichess (vs played
         * in-app).  Case-insensitive; pure — unit-testable without Android.
         */
        fun isImported(game: GameHistoryItem): Boolean =
            game.source.equals("lichess", ignoreCase = true)
    }

    /** Injected by [MainActivity] before [show] is called. */
    var gameApiClient: GameApiClient? = null

    private lateinit var historyList: LinearLayout
    private lateinit var txtHistoryEmpty: TextView
    private lateinit var sparkline: RatingSparklineView
    private lateinit var filterAll: TextView
    private lateinit var filterApp: TextView
    private lateinit var filterLichess: TextView

    private var selected: HistoryFilter = HistoryFilter.ALL
    private var loadJob: Job? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_game_history, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        historyList = view.findViewById(R.id.historyList)
        txtHistoryEmpty = view.findViewById(R.id.txtHistoryEmpty)
        sparkline = view.findViewById(R.id.ratingSparkline)
        filterAll = view.findViewById(R.id.filterAll)
        filterApp = view.findViewById(R.id.filterApp)
        filterLichess = view.findViewById(R.id.filterLichess)

        filterAll.setOnClickListener { select(HistoryFilter.ALL) }
        filterApp.setOnClickListener { select(HistoryFilter.APP) }
        filterLichess.setOnClickListener { select(HistoryFilter.LICHESS) }

        select(HistoryFilter.ALL)
    }

    private fun select(filter: HistoryFilter) {
        selected = filter
        styleTabs()
        load(filter)
    }

    private fun styleTabs() {
        applyTabStyle(filterAll, selected == HistoryFilter.ALL)
        applyTabStyle(filterApp, selected == HistoryFilter.APP)
        applyTabStyle(filterLichess, selected == HistoryFilter.LICHESS)
    }

    private fun applyTabStyle(tab: TextView, isSelected: Boolean) {
        val ctx = requireContext()
        tab.setBackgroundResource(
            if (isSelected) R.drawable.history_filter_chip_selected
            else R.drawable.history_filter_chip_unselected
        )
        tab.setTextColor(
            ContextCompat.getColor(
                ctx,
                if (isSelected) R.color.atrium_accent_cyan else R.color.atrium_dim,
            )
        )
        tab.setTypeface(null, if (isSelected) Typeface.BOLD else Typeface.NORMAL)
    }

    private fun load(filter: HistoryFilter) {
        val client = gameApiClient
        // Fresh view: clear the previous tab's rows / states before fetching
        // so a slow network can't leave stale rows under the new selection.
        historyList.removeAllViews()
        sparkline.visibility = View.GONE
        txtHistoryEmpty.visibility = View.GONE
        if (client == null) {
            txtHistoryEmpty.text = "No games recorded yet."
            txtHistoryEmpty.visibility = View.VISIBLE
            return
        }

        // Cancel any in-flight load so rapid tab taps can't interleave two
        // render passes into historyList.  Scoped to the VIEW lifecycle so
        // a network return after onDestroyView never touches detached views.
        loadJob?.cancel()
        loadJob = viewLifecycleOwner.lifecycleScope.launch {
            when (val result = client.getGameHistory(source = filter.source, limit = filter.limit)) {
                is ApiResult.Success -> render(result.data, filter)
                else -> {
                    txtHistoryEmpty.text = "Could not load history. Check your connection."
                    txtHistoryEmpty.visibility = View.VISIBLE
                }
            }
            resizeSheetToContent()
        }
    }

    /**
     * Re-measure the bottom-sheet frame against the CURRENT content.
     *
     * Material's bottom sheet grows with its content but does not shrink
     * on its own: switching from a long tab (ALL, 20 rows) to a short one
     * (IN-APP, 2 rows) swapped the rows but left the frame at its old
     * height — a dead void under the list (on-device report, 2026-07-15).
     * Re-pin the frame's height to wrap_content and request layout after
     * every render so the sheet hugs whatever the tab now shows.
     */
    private fun resizeSheetToContent() {
        val root = view ?: return
        root.post {
            val dlg = dialog as? com.google.android.material.bottomsheet.BottomSheetDialog
                ?: return@post
            // The M3 dialog machinery keeps the sheet frame (and the
            // fragment root inside it) at full window height regardless
            // of layout params (measured on-device, 2026-07-15), and the
            // default 'auto' peek is ~16:9 of the width (~1928px here) —
            // so a short tab showed the sheet background as a dead void
            // under the rows.  Instead of fighting the frame, clamp the
            // BEHAVIOR to the content: the scroll region (or the empty-
            // state text) is the last visual element, so its bottom edge
            // + the root's bottom padding IS the content height.
            // peekHeight = what COLLAPSED shows; maxHeight = hard cap in
            // every state, so dragging up can't expose the void either.
            // Both recomputed after every render, so switching back to a
            // taller tab grows the sheet again.
            val scrollRegion = historyList.parent as? View ?: return@post
            val contentBottom = (
                if (txtHistoryEmpty.visibility == View.VISIBLE) txtHistoryEmpty.bottom
                else scrollRegion.bottom
            ) + root.paddingBottom
            if (contentBottom <= 0) return@post
            dlg.behavior.maxHeight = contentBottom
            dlg.behavior.peekHeight = contentBottom
        }
    }

    private fun render(games: List<GameHistoryItem>, filter: HistoryFilter) {
        if (games.isEmpty()) {
            txtHistoryEmpty.text = when (filter) {
                HistoryFilter.LICHESS ->
                    "No imported Lichess games yet. Link your account to import them."
                HistoryFilter.APP -> "No in-app games yet."
                HistoryFilter.ALL -> "No games recorded yet."
            }
            txtHistoryEmpty.visibility = View.VISIBLE
            return
        }
        val sparkRatings = extractSparklineRatings(games)
        if (sparkRatings.size >= 2) {
            sparkline.setRatings(sparkRatings)
            sparkline.visibility = View.VISIBLE
        }
        games.forEach { game ->
            historyList.addView(buildGameRow(game))
            historyList.addView(buildDivider())
        }
    }

    /**
     * One history entry: a tappable row (result · accuracy · rating · date)
     * with a trailing "LICHESS" badge on imported games. Tapping opens the
     * full game review — step through the board (◀/▶) with the coaching
     * chat below. Replay works for every game (positions are replayed from
     * the stored PGN); the chat section appears only for games with a
     * linked thread.
     */
    private fun buildGameRow(game: GameHistoryItem): View {
        val ctx = requireContext()
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val label = TextView(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            text = headerText(game)
            setTextColor(resultColor(game))
            textSize = 13f
            typeface = Typeface.MONOSPACE
            setPadding(0, 14, 0, 14)
        }
        row.addView(label)

        if (isImported(game)) {
            row.addView(buildImportedBadge())
        }

        // Every finished game has a replayable position list (keyed by its
        // event id), so every row opens the review.
        if (game.id.isNotBlank() && gameApiClient != null) {
            row.setOnClickListener { openReview(game) }
        }
        return row
    }

    private fun buildImportedBadge(): View {
        val ctx = requireContext()
        return TextView(ctx).apply {
            text = "LICHESS"
            textSize = 9f
            typeface = Typeface.MONOSPACE
            letterSpacing = 0.1f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_accent_cyan))
            setBackgroundResource(R.drawable.lichess_badge_bg)
            setPadding(12, 4, 12, 4)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = 16 }
        }
    }

    private fun openReview(game: GameHistoryItem) {
        // Load the finished game onto the main board for replay + live coaching,
        // then close this sheet so the user lands on the board.  The source
        // gates the replay bar's "Coach review" button (Lichess imports only).
        (activity as? MainActivity)?.openFinishedGameReview(game.id, game.gameId, game.source)
        dismiss()
    }

    // Atrium two-tone signal — cyan for wins, amber for losses (warning
    // role), atrium_muted for draws.  Matches the rest of the dashboard.
    private fun resultColor(game: GameHistoryItem): Int {
        val ctx = requireContext()
        return when (game.result.lowercase()) {
            "win"  -> ContextCompat.getColor(ctx, R.color.atrium_accent_cyan)
            "loss" -> ContextCompat.getColor(ctx, R.color.atrium_accent_amber)
            else   -> ContextCompat.getColor(ctx, R.color.atrium_muted)
        }
    }

    private fun headerText(game: GameHistoryItem): String {
        val resultLabel = game.result.uppercase()
        val accuracy = "${(game.accuracy * 100).toInt()}% acc"
        val rating = game.ratingAfter?.let { "  ·  %.0f pts".format(it) } ?: ""
        val lastMove = game.lastMove?.takeIf { it.isNotBlank() }?.let { "  ·  last $it" } ?: ""
        val winnerMove = game.winnerMove?.takeIf { it.isNotBlank() }?.let { "  ·  won $it" } ?: ""
        val date = formatDate(game.createdAt)
        // ▸ signals the row is tappable (opens the full game review).
        return "$resultLabel  ·  $accuracy$rating$lastMove$winnerMove  ▸\n$date"
    }

    private fun buildDivider(): View = View(requireContext()).apply {
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 1
        )
        // atrium_hairline (8% white) — same primitive used by the
        // Atrium.Divider style across the rest of the app.
        setBackgroundColor(
            ContextCompat.getColor(requireContext(), R.color.atrium_hairline)
        )
    }

    private fun formatDate(iso: String): String = try {
        val dt = LocalDateTime.parse(iso, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
        "%02d/%02d  %02d:%02d".format(dt.monthValue, dt.dayOfMonth, dt.hour, dt.minute)
    } catch (_: Exception) {
        iso.take(10)
    }
}
package com.cereveon.myapp

import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Bottom sheet showing the post-game AI review of one imported Lichess
 * game (docs/API_CONTRACTS.md §39/§39a), opened from the replay nav
 * bar's "Coach review" button while the game is on the main board.
 *
 * Rendering is progressive ("three waves"): on open the sheet POSTs
 * /game/{event_id}/review (idempotent — an existing review answers
 * immediately) and then polls every [POLL_INTERVAL_MS] while the job
 * is non-terminal, re-rendering whatever stages have landed:
 *
 *   Wave 1 — meta line from the row (instant).
 *   Wave 2 — banded eval graph + stats + moment cards (engine stage).
 *   Wave 3 — coach texts + verdict (LLM stage).
 *
 * "Explore" on a moment card (and tapping a graph marker) jumps the
 * main-board replay to that ply via [onExplorePly] and dismisses the
 * sheet — from there the existing replay + coach-chat surface takes
 * over ("Ask coach" is the ordinary Coach button on that position).
 *
 * Entitlement UX: past the free cap the review still shows the full
 * engine waves; the LLM section renders the quota line + an UPGRADE
 * action instead (`llm.outcome == "skipped_entitlement"`).  A
 * degraded LLM outcome (`"fallback"`) offers "TRY COACH REVIEW
 * AGAIN", which re-POSTs — the server re-runs only the LLM stage.
 */
class GameReviewBottomSheet : BottomSheetDialogFragment() {

    companion object {
        const val POLL_INTERVAL_MS = 2_000L

        /** Sheet-level action derived from a review payload.  Pure. */
        enum class ReviewAction { NONE, RETRY_COACH, UPGRADE, RETRY_FAILED }

        fun actionFor(review: GameReviewResponse): ReviewAction = when {
            review.status == GameReviewResponse.STATUS_FAILED -> ReviewAction.RETRY_FAILED
            review.status != GameReviewResponse.STATUS_COMPLETE -> ReviewAction.NONE
            review.llm?.outcome == ReviewLlm.OUTCOME_SKIPPED_ENTITLEMENT ->
                // A pro user hitting the daily/monthly cap has nothing to
                // buy — showing UPGRADE to a subscriber reads as a bug.
                if (review.entitlement?.plan == ReviewEntitlement.PLAN_PRO) ReviewAction.NONE
                else ReviewAction.UPGRADE
            review.llm?.outcome == ReviewLlm.OUTCOME_FALLBACK -> ReviewAction.RETRY_COACH
            else -> ReviewAction.NONE
        }

        /** Status line under the header.  Pure. */
        fun statusLine(review: GameReviewResponse): String = when (review.status) {
            GameReviewResponse.STATUS_QUEUED,
            GameReviewResponse.STATUS_RUNNING,
            -> "Analyzing the game…"
            GameReviewResponse.STATUS_ENGINE_DONE -> "Coach is looking at the game…"
            GameReviewResponse.STATUS_FAILED ->
                "The review could not be completed. Try again."
            else -> when (review.llm?.outcome) {
                ReviewLlm.OUTCOME_SKIPPED_ENTITLEMENT -> when {
                    review.entitlement?.metric == ReviewEntitlement.METRIC_DAILY ->
                        "Engine review ready. Daily coach-review limit reached — more tomorrow."
                    review.entitlement?.plan == ReviewEntitlement.PLAN_PRO ->
                        "Engine review ready. Monthly coach-review limit reached."
                    else ->
                        "Engine review ready. Coach commentary is a Pro feature this month."
                }
                ReviewLlm.OUTCOME_FALLBACK ->
                    "Review ready (coach used quick notes this time)."
                else -> "Review ready."
            }
        }

        /** "vs opponent1234 (1234) · 600+5 · Ruy Lopez" — from meta.  Pure. */
        fun metaLine(engine: ReviewEngine?): String {
            if (engine == null) return ""
            val meta = engine.meta
            val playerIsWhite = !engine.playerColor.equals("black", ignoreCase = true)
            val opponent = (if (playerIsWhite) meta.black else meta.white) ?: "opponent"
            val opponentElo = (if (playerIsWhite) meta.blackElo else meta.whiteElo)
                ?.let { " ($it)" } ?: ""
            val timeControl = meta.timeControl?.let { " · $it" } ?: ""
            val opening = meta.opening?.let { " · $it" } ?: ""
            return "vs $opponent$opponentElo$timeControl$opening"
        }

        /** "61% acc · 1 blunder · 0 mistakes · 2 inaccuracies".  Pure. */
        fun statsLine(engine: ReviewEngine): String {
            val acc = (engine.accuracy * 100).toInt()
            val c = engine.counts
            return "$acc% acc · ${c.blunders} blunder${plural(c.blunders)} · " +
                "${c.mistakes} mistake${plural(c.mistakes)} · " +
                "${c.inaccuracies} inaccurac${if (c.inaccuracies == 1) "y" else "ies"}"
        }

        private fun plural(n: Int) = if (n == 1) "" else "s"

        /** Card header: "MOVE 11 · MIDDLEGAME".  Pure. */
        fun momentHeader(moment: ReviewMoment): String {
            val phase = moment.phase?.uppercase() ?: "MOMENT"
            return "MOVE ${moment.moveNumber} · $phase"
        }

        /** Band words for the transition line.  Pure. */
        fun bandPhrase(band: String?): String = when (band) {
            "losing" -> "clearly losing"
            "worse" -> "somewhat worse"
            "equal" -> "level"
            "better" -> "somewhat better"
            "winning" -> "clearly winning"
            else -> "unclear"
        }

        /** "level → clearly losing", or "stayed clearly losing" when the
         *  move didn't change the band — an arrow-to-itself read as a
         *  glitch on-device ("clearly losing → clearly losing", 2026-07-15
         *  report).  Pure. */
        fun momentTransition(moment: ReviewMoment): String =
            if (moment.bandBefore == moment.bandAfter) {
                "stayed ${bandPhrase(moment.bandAfter)}"
            } else {
                "${bandPhrase(moment.bandBefore)} → ${bandPhrase(moment.bandAfter)}"
            }

        /** Quota line, or null when nothing useful to show.  Pure.  The
         *  server reports the BINDING bucket: the pro daily smoothing cap
         *  reads "today", the monthly ceiling "this month". */
        fun quotaLine(entitlement: ReviewEntitlement?): String? {
            val remaining = entitlement?.remaining ?: return null
            val limit = entitlement.limit ?: return null
            val window =
                if (entitlement.metric == ReviewEntitlement.METRIC_DAILY) "today"
                else "this month"
            return "$remaining of $limit coach reviews left $window."
        }
    }

    /** Injected by [MainActivity] before [show] is called. */
    var reviewApiClient: ReviewApiClient? = null
    var tokenProvider: (() -> String?)? = null
    var eventId: String? = null

    /** Jump the main-board replay to a ply (Explore / marker tap). */
    var onExplorePly: ((Int) -> Unit)? = null

    private lateinit var txtMeta: TextView
    private lateinit var txtStatus: TextView
    private lateinit var graph: ReviewEvalGraphView
    private lateinit var txtStats: TextView
    private lateinit var momentsList: LinearLayout
    private lateinit var txtVerdictHeader: TextView
    private lateinit var txtVerdict: TextView
    private lateinit var txtQuota: TextView
    private lateinit var btnAction: Button

    private var pollJob: Job? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_game_review, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        txtMeta = view.findViewById(R.id.txtReviewMeta)
        txtStatus = view.findViewById(R.id.txtReviewStatus)
        graph = view.findViewById(R.id.reviewEvalGraph)
        txtStats = view.findViewById(R.id.txtReviewStats)
        momentsList = view.findViewById(R.id.reviewMomentsList)
        txtVerdictHeader = view.findViewById(R.id.txtVerdictHeader)
        txtVerdict = view.findViewById(R.id.txtVerdict)
        txtQuota = view.findViewById(R.id.txtReviewQuota)
        btnAction = view.findViewById(R.id.btnReviewAction)

        graph.onMomentTap = { ply -> explore(ply) }
        txtStatus.text = "Starting the review…"
        startReviewAndPoll()
    }

    private fun explore(ply: Int) {
        onExplorePly?.invoke(ply)
        dismiss()
    }

    /**
     * POST once (idempotent server-side), then poll GET while the row
     * is non-terminal.  Scoped to the view lifecycle so a poll tick
     * can never touch detached views.
     */
    private fun startReviewAndPoll() {
        val client = reviewApiClient
        val token = tokenProvider?.invoke()
        val id = eventId
        if (client == null || token.isNullOrBlank() || id.isNullOrBlank()) {
            txtStatus.text = "The review is unavailable right now."
            return
        }
        pollJob?.cancel()
        pollJob = viewLifecycleOwner.lifecycleScope.launch {
            when (val started = client.startReview(id, token)) {
                is ApiResult.Success -> render(started.data)
                is ApiResult.HttpError -> {
                    renderStartError(started)
                    return@launch
                }
                else -> {
                    txtStatus.text = "Couldn't reach the coach. Check your connection."
                    return@launch
                }
            }
            while (isActive) {
                delay(POLL_INTERVAL_MS)
                val current = tokenProvider?.invoke() ?: token
                when (val polled = client.getReview(id, current)) {
                    is ApiResult.Success -> {
                        render(polled.data)
                        if (polled.data.isTerminal) return@launch
                    }
                    // Transient poll failures keep trying until the view
                    // goes away; the server job advances regardless.
                    else -> Unit
                }
            }
        }
    }

    private fun renderStartError(error: ApiResult.HttpError) {
        txtStatus.text = when {
            error.code == 400 && error.body?.contains("too_short") == true ->
                "This game was too short for a review. Try a longer game."
            error.code == 400 && error.body?.contains("not_lichess") == true ->
                "Reviews are available for imported Lichess games."
            error.code == 429 ->
                "Too many requests — give it a few seconds and try again."
            else -> "The review is unavailable right now (error ${error.code})."
        }
    }

    /**
     * Clamp the sheet to its content (same recipe as the history sheet):
     * the M3 dialog's default 'auto' peek is ~16:9 of the width, so the
     * short Wave-1 state ("Analyzing the game…") trailed a dead void
     * until the taller waves filled it.  peekHeight = what COLLAPSED
     * shows; maxHeight = cap in every state.  Recomputed on every poll
     * render, so the sheet grows as waves land.
     */
    private fun resizeSheetToContent() {
        val root = view ?: return
        root.post {
            val dlg = dialog as? com.google.android.material.bottomsheet.BottomSheetDialog
                ?: return@post
            val scrollRegion = (txtMeta.parent as? View)?.parent as? View ?: return@post
            val contentBottom = scrollRegion.bottom + root.paddingBottom
            if (contentBottom <= 0) return@post
            dlg.behavior.maxHeight = contentBottom
            dlg.behavior.peekHeight = contentBottom
        }
    }

    private fun render(review: GameReviewResponse) {
        txtStatus.text = statusLine(review)

        val engine = review.engine
        if (engine != null) {
            txtMeta.text = metaLine(engine)
            txtStats.text = statsLine(engine)
            txtStats.visibility = View.VISIBLE
            if (engine.bands.size >= 2) {
                graph.setData(
                    engine.bands,
                    review.moments.orEmpty().map {
                        ReviewEvalGraphView.Marker(it.ply, it.momentType)
                    },
                )
                graph.visibility = View.VISIBLE
            }
        }

        renderMoments(review)
        renderVerdict(review)
        renderQuotaAndAction(review)
        resizeSheetToContent()
    }

    private fun renderMoments(review: GameReviewResponse) {
        val moments = review.moments.orEmpty()
        momentsList.removeAllViews()
        if (moments.isEmpty()) return
        val textsByPly = review.llm?.moments.orEmpty().associateBy { it.ply }
        moments.forEachIndexed { index, moment ->
            momentsList.addView(
                buildMomentCard(
                    index = index,
                    total = moments.size,
                    moment = moment,
                    coachText = textsByPly[moment.ply]?.text,
                    llmSettled = review.llm != null ||
                        review.status == GameReviewResponse.STATUS_FAILED,
                )
            )
        }
    }

    private fun buildMomentCard(
        index: Int,
        total: Int,
        moment: ReviewMoment,
        coachText: String?,
        llmSettled: Boolean,
    ): View {
        val ctx = requireContext()
        val card = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            // Hairline card, no corner ticks — ticks are reserved for the
            // single "featured" card of a surface (Home drill card).
            background = AtriumCardBackground(ctx, showTicks = false)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(8) }
        }

        val negative = ReviewEvalGraphView.isNegativeType(moment.momentType)
        val accent = ContextCompat.getColor(
            ctx,
            when {
                negative -> R.color.atrium_accent_amber
                moment.momentType == ReviewMoment.TYPE_PUNISHED_MISTAKE ->
                    R.color.atrium_accent_cyan
                else -> R.color.atrium_muted
            },
        )

        card.addView(TextView(ctx).apply {
            text = "● ${momentHeader(moment)}  ·  ${index + 1}/$total"
            typeface = Typeface.MONOSPACE
            textSize = 10f
            letterSpacing = 0.1f
            setTextColor(accent)
        })

        card.addView(TextView(ctx).apply {
            text = "You played ${moment.san}   (${momentTransition(moment)})"
            typeface = Typeface.MONOSPACE
            textSize = 12f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_ink))
            setPadding(0, dp(6), 0, 0)
        })

        card.addView(TextView(ctx).apply {
            text = when {
                coachText != null -> coachText
                llmSettled -> "" // settled with no text for this card — hide below
                else -> "Coach is looking at this moment…"
            }
            setTextAppearance(R.style.Atrium_Body)
            setPadding(0, dp(6), 0, 0)
            visibility =
                if (coachText == null && llmSettled) View.GONE else View.VISIBLE
        })

        card.addView(TextView(ctx).apply {
            text = "EXPLORE ▸"
            typeface = Typeface.MONOSPACE
            textSize = 11f
            letterSpacing = 0.1f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_accent_cyan))
            setPadding(0, dp(10), 0, 0)
            setOnClickListener { explore(moment.ply) }
        })

        return card
    }

    private fun renderVerdict(review: GameReviewResponse) {
        val verdict = review.llm?.verdict?.text
        if (!verdict.isNullOrBlank()) {
            txtVerdictHeader.visibility = View.VISIBLE
            txtVerdict.text = verdict
            txtVerdict.visibility = View.VISIBLE
        }
    }

    private fun renderQuotaAndAction(review: GameReviewResponse) {
        val quota = quotaLine(review.entitlement)
        txtQuota.text = quota ?: ""
        txtQuota.visibility = if (quota != null) View.VISIBLE else View.GONE

        when (actionFor(review)) {
            ReviewAction.RETRY_COACH -> showAction("TRY COACH REVIEW AGAIN")
            ReviewAction.RETRY_FAILED -> showAction("RETRY REVIEW")
            ReviewAction.UPGRADE -> showAction("UPGRADE FOR COACH REVIEWS") {
                startActivity(Intent(requireContext(), PaywallActivity::class.java))
            }
            ReviewAction.NONE -> btnAction.visibility = View.GONE
        }
    }

    private fun showAction(label: String, onClick: (() -> Unit)? = null) {
        btnAction.text = label
        btnAction.visibility = View.VISIBLE
        btnAction.setOnClickListener {
            btnAction.visibility = View.GONE
            if (onClick != null) onClick() else startReviewAndPoll()
        }
    }

    private fun dp(v: Int): Int =
        (v * resources.displayMetrics.density).toInt()
}
package com.cereveon.myapp

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.encodeToString

/**
 * Bottom sheet shown after a game ends.
 *
 * Displays:
 *  - New rating and confidence progress bar (Gap 6)
 *  - Coach action type badge (Gap 2)
 *  - Coach content title and description (Gap 2)
 *  - Inline training recommendation card from GET /next-training (Gap 3)
 *
 * Arguments are passed via [newInstance]; see [ARG_*] constants.
 */
class GameSummaryBottomSheet : BottomSheetDialogFragment() {

    companion object {
        private const val ARG_RATING          = "rating"
        private const val ARG_CONFIDENCE      = "confidence"
        private const val ARG_ACTION_TYPE     = "action_type"
        private const val ARG_TITLE           = "title"
        private const val ARG_DESCRIPTION     = "description"
        private const val ARG_PLAYER_ID       = "player_id"
        private const val ARG_PAYLOAD_JSON    = "payload_json"
        private const val ARG_LEARNING_STATUS = "learning_status"
        // Atrium hero card — populated by the activity from local
        // game-state, not from the /game/finish response (which does
        // not currently carry result / move-count fields).
        private const val ARG_RESULT          = "result"
        private const val ARG_MOVE_COUNT      = "move_count"
        // Coach decision passed-through for downstream persistence —
        // see ProgressDashboardBottomSheet's "Coach's plan" section.
        private const val ARG_COACH_WEAKNESS  = "coach_weakness"
        private const val ARG_COACH_REASON    = "coach_reason"
        // Phase 3 — biggest_mistake (the mistake-replay payload) serialised
        // as a JSON string so the bundle stays primitive-only.  Optional;
        // absent / blank means the response didn't carry the field.
        private const val ARG_BIGGEST_MISTAKE_JSON = "biggest_mistake_json"

        const val PREFS_NAME  = MainActivity.PREFS_NAME
        const val PREF_RATING = MainActivity.PREF_RATING

        /**
         * Serializer for the ``payload`` map transported through the
         * fragment's [Bundle].  Pinned to ``Map<String, String>`` because
         * the JsonAsStringMapSerializer in [GameApiModels] has already
         * stringified every value by the time it reaches this fragment,
         * so the bundle blob can stay a plain string-map.
         */
        private val PAYLOAD_SERIALIZER =
            MapSerializer(String.serializer(), String.serializer())

        fun newInstance(
            response: GameFinishResponse,
            playerId: String,
            result: GameResult? = null,
            moveCount: Int = 0,
        ): GameSummaryBottomSheet = GameSummaryBottomSheet().apply {
            // Serialise payload map to JSON string for bundle transport
            val payloadJson = ApiJson.encodeToString(
                PAYLOAD_SERIALIZER,
                response.coachContent.payload,
            )
            arguments = Bundle().apply {
                putFloat(ARG_RATING,      response.newRating)
                putFloat(ARG_CONFIDENCE,  response.confidence)
                putString(ARG_ACTION_TYPE, response.coachAction.type)
                putString(ARG_TITLE,       response.coachContent.title)
                putString(ARG_DESCRIPTION, response.coachContent.description)
                putString(ARG_PLAYER_ID,   playerId)
                putString(ARG_PAYLOAD_JSON, payloadJson)
                // Pass coach_action.weakness / coach_action.reason through
                // for the dashboard persistence step in onViewCreated.
                // Not used directly by the in-sheet UI today (the title /
                // description carry the user-visible coach copy), but
                // needed downstream by ProgressDashboardBottomSheet to
                // render the "Coach's plan" section between games.
                response.coachAction.weakness?.let { putString(ARG_COACH_WEAKNESS, it) }
                response.coachAction.reason?.let   { putString(ARG_COACH_REASON, it) }
                response.learningStatus?.let { putString(ARG_LEARNING_STATUS, it) }
                result?.let { putString(ARG_RESULT, it.name) }
                if (moveCount > 0) putInt(ARG_MOVE_COUNT, moveCount)
                // Phase 3 — pass the biggest_mistake DTO through as a
                // JSON string so the bundle stays primitive-only.
                // Decoded back into a [BiggestMistakeDto] in
                // onViewCreated when the card is wired up.  Encoding
                // wrapped defensively: a kotlinx-serialization
                // hiccup must never block the post-game sheet from
                // appearing.  If the encode throws, the bundle just
                // doesn't carry the field and the sheet renders
                // without the replay CTA.
                try {
                    response.biggestMistake?.let {
                        putString(ARG_BIGGEST_MISTAKE_JSON, ApiJson.encodeToString(it))
                    }
                } catch (e: Throwable) {
                    android.util.Log.w(
                        "MISTAKE_REPLAY",
                        "Failed to encode biggest_mistake; sheet will show without replay CTA",
                        e,
                    )
                }
            }
        }

        /**
         * Atrium hero result label — italic display text shown with a
         * cyan halo on the game-end summary card.
         *
         * Maps the game-engine [GameResult] to the design's W/L/D copy:
         *   WHITE_WINS → "Won · 1–0"
         *   BLACK_WINS → "Lost · 0–1"
         *   DRAW       → "Drew · ½–½"
         *
         * The half-symbol uses the canonical Unicode U+00BD; both
         * scoresheet halves separated by an en-dash, per the handoff.
         */
        fun formatHeroResult(result: GameResult?): String = when (result) {
            GameResult.WHITE_WINS -> "Won · 1–0"
            GameResult.BLACK_WINS -> "Lost · 0–1"
            GameResult.DRAW       -> "Drew · ½–½"
            null                  -> "—"
        }

        /**
         * Atrium hero subline — mono-cyan caps row beneath the result
         * label.  Currently shows just the move count; once the
         * /game/finish response carries duration and termination
         * reason we'll extend to "{N} MOVES · {duration} · {reason}"
         * to match the handoff design ("38 MOVES · 27:41 · OPPONENT
         * RESIGNED").
         *
         * Returns null when [moveCount] <= 0 so the activity can hide
         * the row instead of rendering "0 MOVES".
         */
        fun formatHeroSubline(moveCount: Int): String? =
            if (moveCount > 0) "$moveCount Moves" else null

        // ── Pure helper functions — testable without Android framework ────────

        /**
         * Format confidence 0.0–1.0 as a bare percentage ("72%").
         * The Atrium ACCURACY cell carries its own "ACCURACY" kicker
         * underneath, so the value need not include the label.
         */
        fun formatConfidence(confidence: Float): String =
            "%.0f%%".format(confidence * 100f)

        /** Convert confidence 0.0–1.0 to ProgressBar integer (0–100). */
        fun confidenceProgress(confidence: Float): Int =
            (confidence.coerceIn(0f, 1f) * 100f).toInt()

        /**
         * Map a coach action type string to a display badge label.
         * Unknown types fall back to "COACH".
         */
        fun actionBadgeLabel(type: String): String = when (type.uppercase()) {
            "DRILL"       -> "DRILL"
            "PUZZLE"      -> "PUZZLE"
            "REFLECT"     -> "REFLECT"
            "PLAN_UPDATE" -> "PLAN"
            "REST"        -> "REST"
            "CELEBRATE"   -> "CELEBRATE"
            else          -> "COACH"
        }

        /** Format a training topic string as "Topic: Endgame technique". */
        fun formatTopic(topic: String): String =
            "Topic: ${topic.replaceFirstChar { it.uppercase() }.replace('_', ' ')}"

        /**
         * Phase 3 mistake-replay card subline.
         * "Move 14 — find a stronger move (lost 240 cp)."
         */
        fun formatMistakeSummary(moveNumber: Int, evalLossCp: Int): String =
            "Move $moveNumber — find a stronger move (lost $evalLossCp cp)."

        // ``formatFormat`` and ``formatGain`` retired in PR 26 (2026-05-15)
        // alongside the /next-training/{player_id} fallback path that was
        // their sole caller.  ``CurriculumRecommendation`` (the surviving
        // shape from /curriculum/next) uses ``exerciseType`` + ``difficulty``
        // directly, formatted inline at the call site.

        // difficultyProgress(Float) retired with the Float-based difficulty
        // contract in 2026-05-25.  The String-band helpers below moved
        // here from TrainingSessionBottomSheet.Companion when the
        // standalone Lessons surface was removed — the post-game
        // training card is their only remaining caller.

        /**
         * Format a difficulty band string as "Difficulty: Medium".
         *
         * ``CurriculumPolicy.choose_difficulty`` on the server returns one of
         * ``"easy" | "medium" | "hard"``; anything else falls through to the
         * raw string (capitalised) so a future band ("expert", "novice") still
         * renders sensibly without a code change here.
         */
        fun formatDifficulty(difficulty: String): String =
            "Difficulty: ${difficulty.replaceFirstChar { it.uppercase() }}"

        /**
         * Map a difficulty band string to a ProgressBar integer (0–100).
         *
         * The progress bar is a visual cue, not a quantitative scale — easy
         * sits at 30, medium at 60, hard at 85, and any unknown band lands at
         * the 50 midpoint so the bar still renders.
         */
        fun difficultyProgress(difficulty: String): Int = when (difficulty.lowercase()) {
            "easy"   -> 30
            "medium" -> 60
            "hard"   -> 85
            else     -> 50
        }

        /**
         * Map a raw [learningStatus] string to a user-visible indicator label.
         *
         * Currently every status value resolves to "✓ Progress saved".  The
         * server hard-codes `learning_result = {"status": "safe_mode"}` on
         * every `/game/finish` response (see `llm/seca/events/router.py` —
         * the pre-PR-20 `else` branch was unreachable and got retired), so
         * the only status the Android client has ever seen in production is
         * `safe_mode`.  Earlier copy split that case off as
         * "⏸ Tracking paused", which read to users as a transient outage —
         * but their game IS saved (events table), their rating IS updated
         * (Player.rating), their accuracy IS measured (engine recompute),
         * and their coaching profile IS updated (SkillUpdater).  What's
         * actually "paused" is the bandit's online-learning loop, which is
         * permanently off in production by Project Rule 3 / SAFE_MODE — a
         * detail invisible to the user.
         *
         * The when-block scaffold is kept so a future non-safe-mode
         * deployment (research / staging with `SECA_SAFE_MODE=false`) can
         * branch the label without re-introducing the misleading "paused"
         * wording on the prod path.
         */
        fun learningStatusLabel(status: String): String = when (status.lowercase()) {
            "safe_mode" -> "✓ Progress saved"
            else        -> "✓ Progress saved"
        }
    }

    /** Injected in [newInstance] path; set by [MainActivity] before showing. */
    var gameApiClient: GameApiClient? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_game_summary, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val args = requireArguments()
        val rating          = args.getFloat(ARG_RATING)
        val confidence      = args.getFloat(ARG_CONFIDENCE)
        val actionType      = args.getString(ARG_ACTION_TYPE, "")
        val title           = args.getString(ARG_TITLE, "")
        val description     = args.getString(ARG_DESCRIPTION, "")
        val playerId        = args.getString(ARG_PLAYER_ID, "demo")
        val payloadJsonStr  = args.getString(ARG_PAYLOAD_JSON, "{}")
        val learningStatus  = args.getString(ARG_LEARNING_STATUS)
        val resultName      = args.getString(ARG_RESULT)
        val resultEnum: GameResult? = resultName?.let { runCatching { GameResult.valueOf(it) }.getOrNull() }
        val moveCount       = args.getInt(ARG_MOVE_COUNT, 0)

        // ── Bind views ────────────────────────────────────────────────────────
        // (txtNewRating retired alongside the rest of the user-visible
        // Elo surfaces; ``rating`` is still unpacked below for the
        // PREF_RATING write-back that powers adaptive opponent matching
        // internally.)
        view.findViewById<TextView>(R.id.txtActionBadge).text    = actionBadgeLabel(actionType)
        view.findViewById<TextView>(R.id.txtCoachTitle).text     = title.ifBlank { "Game Over" }
        view.findViewById<TextView>(R.id.txtCoachDescription).text = description

        // Atrium hero card — italic result label with cyan halo +
        // optional mono subline.  Activity passes resultEnum + moveCount
        // via the bundle; both fall back gracefully when the host
        // doesn't supply them (placeholder "—" / hidden subline).
        view.findViewById<TextView>(R.id.heroResult).text = formatHeroResult(resultEnum)
        val heroSub = view.findViewById<TextView>(R.id.heroSubline)
        formatHeroSubline(moveCount)?.let {
            heroSub.text = it
            heroSub.visibility = View.VISIBLE
        } ?: run {
            heroSub.visibility = View.GONE
        }

        val progressBar = view.findViewById<ProgressBar>(R.id.progressConfidence)
        progressBar.progress = confidenceProgress(confidence)
        view.findViewById<TextView>(R.id.txtConfidenceLabel).text = formatConfidence(confidence)

        // ── P3-B: learning status indicator ───────────────────────────────────
        val txtLearningStatus = view.findViewById<TextView>(R.id.txtLearningStatus)
        if (!learningStatus.isNullOrEmpty()) {
            txtLearningStatus.text = learningStatusLabel(learningStatus)
            txtLearningStatus.visibility = View.VISIBLE
        }

        // ── Phase 3: mistake-replay card ──────────────────────────────────────
        // When the /game/finish response carried a non-null
        // ``biggest_mistake``, surface a "Replay your mistake" CTA above
        // the curriculum training card.  Tap → launches
        // MistakeReplayBottomSheet preloaded with the first-mistake
        // position (the player's first move whose centipawn loss
        // cleared the server-side 150 cp threshold) and the move the
        // user actually played.  Falls back gracefully (card stays
        // gone, sheet doesn't launch) when the JSON arg is missing or
        // malformed — neither path 500s the post-game flow.
        try {
            wireMistakeReplayCard(view, args.getString(ARG_BIGGEST_MISTAKE_JSON))
        } catch (e: Throwable) {
            android.util.Log.w(
                "MISTAKE_REPLAY",
                "wireMistakeReplayCard failed; card will stay hidden",
                e,
            )
        }

        // ── P3-A: payload detail section (DRILL / PUZZLE only) ────────────────
        val layoutPayload = view.findViewById<LinearLayout>(R.id.layoutPayload)
        val upperType = actionType.uppercase()
        if (upperType == "DRILL" || upperType == "PUZZLE") {
            try {
                val payload = ApiJson.decodeFromString(
                    PAYLOAD_SERIALIZER,
                    payloadJsonStr ?: "{}",
                )
                if (payload.isNotEmpty()) {
                    payload.forEach { (key, value) ->
                        val tv = TextView(requireContext()).apply {
                            text = "$key: $value"
                            // Token read so bright mode flips it via
                            // values-notnight/colors.xml.
                            setTextColor(
                                androidx.core.content.ContextCompat.getColor(
                                    requireContext(), R.color.atrium_text_soft,
                                ),
                            )
                            textSize = 12f
                        }
                        layoutPayload.addView(tv)
                    }
                    layoutPayload.visibility = View.VISIBLE
                }
            } catch (_: Exception) { /* malformed JSON — skip silently */ }
        }

        // ── Persist rating + confidence to SharedPreferences (Gap 6 / P3-A) ──
        // Also persist the coach decision so ProgressDashboardBottomSheet
        // can render a "Coach's plan" section between games — without
        // this, the action verdict / weakness / reason / coach copy are
        // visible only during the transient post-game sheet and lost the
        // moment the user dismisses it (the gap the user surfaced
        // 2026-05-16: "I see training focus but we actually need to see
        // full information that the coach provides").
        val coachWeakness = args.getString(ARG_COACH_WEAKNESS)
        val coachReason   = args.getString(ARG_COACH_REASON)
        requireContext()
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putFloat(PREF_RATING, rating)
            .putFloat(MainActivity.PREF_CONFIDENCE, confidence)
            .putString(MainActivity.PREF_LAST_COACH_ACTION_TYPE, actionType)
            .putString(MainActivity.PREF_LAST_COACH_WEAKNESS, coachWeakness ?: "")
            .putString(MainActivity.PREF_LAST_COACH_REASON, coachReason ?: "")
            .putString(MainActivity.PREF_LAST_COACH_TITLE, title)
            .putString(MainActivity.PREF_LAST_COACH_DESCRIPTION, description)
            .apply()

        // ── Fetch training recommendation from /curriculum/next ────────────────
        // PR 26 (2026-05-15) retired the /next-training/{player_id} fallback;
        // it was a placeholder with hardcoded "demo weaknesses".  When
        // /curriculum/next fails (auth, network, server outage), the UI
        // surfaces the empty training-card state.
        // R.id.trainingCard is declared as <com.cereveon.myapp.AtriumCardView> in
        // bottom_sheet_game_summary.xml (Atrium design-system migration).  An
        // earlier version of this file cast to LinearLayout and crashed the
        // post-game summary with ClassCastException (caught on-device
        // 2026-05-15) — the user got bounced back to HomeActivity because
        // BottomSheetDialogFragment died before it could render.
        val trainingCard  = view.findViewById<AtriumCardView>(R.id.trainingCard)
        val trainingEmpty = view.findViewById<TextView>(R.id.txtTrainingEmpty)
        val client = gameApiClient
        if (client != null) {
            lifecycleScope.launch {
                val curriculumResult = client.getNextCurriculum()
                if (curriculumResult is ApiResult.Success) {
                    val rec = curriculumResult.data

                    // Persist for the MainActivity chip so the recommendation
                    // survives sheet dismissal.  PREF_CURRICULUM_DIFFICULTY is
                    // intentionally NOT written here — the band string never
                    // flowed into a read site (the drawer chip only renders
                    // topic + exerciseType), so the prior putFloat call was
                    // dead.  Writing a String now would collide with old
                    // installs that already have a Float at the same key
                    // (ClassCastException on read).  The key constant survives
                    // in MainActivity.Companion so the cache-key test passes
                    // unchanged.
                    requireContext()
                        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                        .edit()
                        .putString(MainActivity.PREF_CURRICULUM_TOPIC, rec.topic)
                        .putString(MainActivity.PREF_CURRICULUM_EXERCISE_TYPE, rec.exerciseType)
                        .apply()

                    view.findViewById<TextView>(R.id.txtTrainingTopic).text  = formatTopic(rec.topic)
                    view.findViewById<TextView>(R.id.txtTrainingFormat).text =
                        "Format: ${rec.exerciseType.replaceFirstChar { it.uppercase() }}"
                    // /curriculum/next has no expected_gain; show difficulty
                    // band instead.  Source of truth is the label text — the
                    // ProgressBar is a visual cue derived from a fixed
                    // band → percent map (easy=30 / medium=60 / hard=85).
                    view.findViewById<TextView>(R.id.txtTrainingGain).text =
                        formatDifficulty(rec.difficulty)
                    view.findViewById<ProgressBar>(R.id.progressDifficulty).progress =
                        difficultyProgress(rec.difficulty)
                    trainingCard.visibility  = View.VISIBLE
                    trainingEmpty.visibility = View.GONE
                } else {
                    trainingCard.visibility  = View.GONE
                    trainingEmpty.visibility = View.VISIBLE
                }
            }
        }

        // ── Start training button ─────────────────────────────────────────────
        view.findViewById<Button>(R.id.btnStartTraining).setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            // Open ChatBottomSheet with a training seed prompt
            val fen = "startpos"
            ChatBottomSheet
                .newInstance(fen, null, null, 0)
                .show(parentFragmentManager, "ChatBottomSheet")
            dismiss()
        }
    }

    /**
     * Show the mistake-replay card iff [biggestMistakeJson] decodes to
     * a usable [BiggestMistakeDto].  Card stays gone (the default
     * layout state) on null / blank / malformed input — the rest of
     * the sheet renders normally either way.
     */
    private fun wireMistakeReplayCard(view: View, biggestMistakeJson: String?) {
        val card = view.findViewById<AtriumCardView>(R.id.mistakeReplayCard)
        if (biggestMistakeJson.isNullOrBlank()) {
            card.visibility = View.GONE
            return
        }
        val mistake = try {
            ApiJson.decodeFromString<BiggestMistakeDto>(biggestMistakeJson)
        } catch (_: Exception) {
            // Malformed payload — the activity should never see it
            // (the JSON came from this same process's encode), but be
            // defensive so a wire-shape drift can't 500 the sheet.
            card.visibility = View.GONE
            return
        }
        // FEN is the load-bearing field; a missing FEN means the
        // detector returned None and the wire field shouldn't have
        // been set.  Treat blank FEN as "no mistake" to mirror the
        // server-side semantics.
        if (mistake.fen.isBlank()) {
            card.visibility = View.GONE
            return
        }

        view.findViewById<TextView>(R.id.txtMistakeReplaySummary).text =
            formatMistakeSummary(mistake.moveNumber, mistake.evalLossCp)
        card.visibility = View.VISIBLE
        view.findViewById<Button>(R.id.btnReplayMistake).setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            val sheet = MistakeReplayBottomSheet.newInstance(mistake)
            sheet.gameApiClient = gameApiClient
            sheet.show(parentFragmentManager, "MistakeReplayBottomSheet")
            dismiss()
        }
    }
}
package com.cereveon.myapp

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlin.math.max

/**
 * Cereveon · Atrium · Home / Library (handoff screen #5).
 *
 * Post-auth landing.  Replaces the old "register/login → MainActivity"
 * routing: LoginActivity now lands here (or routes via [OnboardingActivity]
 * for first-run users), and the user picks what they want to do next from
 * the four library rows or the bottom tab bar.
 *
 * Library routing
 * ---------------
 *   I   — New game     → MainActivity (no extras; existing flow takes over)
 *   II  — Past games   → MainActivity + EXTRA_OPEN_SHEET=history
 *
 * Bottom tab bar
 * --------------
 *   Home    — active, no-op
 *   Puzzles → [StudyPlanOverviewBottomSheet] hosted over this activity —
 *             reuses the plan the drill card fetched, or does a one-shot
 *             /coach/plan/today fetch.  With no active plan (or on a
 *             fetch error) it opens [PuzzleTrainerBottomSheet] instead:
 *             the endless Lichess-fed practice stream, so the tab is
 *             never a dead end.  The overview sheet itself carries a
 *             "Practice puzzles" entry into the same trainer.
 *   You     → [ProgressDashboardBottomSheet] hosted over this activity
 *
 * Both sheets open directly over Home (no MainActivity relaunch — the
 * old tab wiring bounced through MainActivity and started a new game
 * session as a side effect just to show a sheet).
 *
 * Header avatar
 * -------------
 * Tapping the initials circle opens [SettingsBottomSheet] over this
 * activity — the same sheet MainActivity's drawer shows in-game, with
 * the Account rows routed through [AccountFlows].  Without this entry
 * the only route to Settings would be starting a game and opening the
 * drawer.
 *
 * Day counter
 * -----------
 * The date kicker reads "<Weekday> · Day <N>" where N is the number of
 * days since the user first opened Home.  We persist the epoch millis of
 * the first visit in [PREF_HOME_FIRST_SEEN_AT] and clamp the displayed
 * value at 1 so a fresh install always shows "Day 1".
 *
 * Resume card
 * -----------
 * MainActivity persists a lightweight snapshot
 * ([MainActivity.PREF_LAST_GAME_*]) after every move, clears it on
 * game-over, and bumps the game number on every new session.  We
 * read those keys in [maybeShowResumeCard] and render the card iff
 * a game is in progress with at least one half-move played.
 *
 * The Resume tap launches MainActivity with [MainActivity.EXTRA_RESUME]
 * set; MainActivity.tryRestoreInProgressGame loads the saved FEN +
 * UCI history into ChessBoardView / ChessViewModel so the user picks
 * up the position they left.  Server-side session resumption is out
 * of scope — the next /game/finish creates a fresh row if the prior
 * server session has timed out.
 */
class HomeActivity : AppCompatActivity() {

    private lateinit var avatar: TextView
    private lateinit var dateKicker: TextView
    private lateinit var resumeBlock: View
    private lateinit var resumeTitle: TextView
    private lateinit var resumeSub: TextView
    private lateinit var syncIndicator: View

    /**
     * Last /coach/plan/today response, cached by
     * [fetchAndPopulateTodaysDrill] so the Puzzles tab can open the
     * week overview without refetching.  Cached even when the plan has
     * no due puzzle today — the overview still renders the Done/locked
     * day rows in that state, only the drill card hides.
     */
    private var latestPlan: CoachPlanResponse? = null

    private val authRepo: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    /**
     * Lazy GameApiClient for Home-hosted surfaces: the pending-finish
     * retry at cold-start, the /coach/plan/today drill-card fetch, and
     * injection into the sheets Home hosts directly (study-plan
     * overview, progress dashboard).  MainActivity owns the equivalent
     * client for live gameplay.
     */
    private val gameApiClient: GameApiClient by lazy {
        HttpGameApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
            tokenProvider = { authRepo.getToken() },
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
    }

    /**
     * Lazy AuthApiClient used for the cold-start GET /auth/me that
     * populates the training-XP kicker (post-Elo-removal).  Same
     * round-trip pattern as the earlier rating-kicker variant — the
     * server response is now read for ``training_xp`` instead of
     * ``rating`` and the cached value lives under
     * [MainActivity.PREF_TRAINING_XP] so a cold-start renders
     * instantly without waiting for the network.  Rating + confidence
     * fields are still read off the same response (they remain in the
     * SharedPreferences cache to power adaptive opponent matching
     * internally) — they are simply not displayed any more.
     */
    private val authApiClient: AuthApiClient by lazy {
        HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Defensive: if the user's session expired between LoginActivity
        // and Home opening, kick them back to login rather than render a
        // half-authenticated surface.
        if (!authRepo.isLoggedIn()) {
            startActivity(
                Intent(this, LoginActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
            return
        }

        setContentView(R.layout.activity_home)

        avatar        = findViewById(R.id.homeAvatar)
        dateKicker    = findViewById(R.id.homeDateKicker)
        resumeBlock   = findViewById(R.id.homeResumeBlock)
        resumeTitle   = findViewById(R.id.homeResumeTitle)
        resumeSub     = findViewById(R.id.homeResumeSub)
        syncIndicator = findViewById(R.id.homeSyncIndicator)

        // Avatar → Settings.  The 48dp FrameLayout around the 32dp
        // circle is the actual touch target (see activity_home.xml).
        findViewById<View>(R.id.homeAvatarTapTarget).setOnClickListener {
            openSettings()
        }

        // Theme runs edge-to-edge — without this listener the bottom
        // tab bar (Home / Lessons / Coach / You) sits underneath the
        // system gesture / 3-button navigation bar, making the tabs
        // unreachable on Android 13+ devices.
        val tabBar = findViewById<LinearLayout>(R.id.homeTabBar)
        val tabBarBasePaddingBottom = tabBar.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(tabBar) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(bottom = tabBarBasePaddingBottom + bars.bottom)
            insets
        }

        val playerId = (authRepo.authState() as? AuthState.Authenticated)?.playerId
        avatar.text = initialsFor(playerId)

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        // Surface any pending offline /game/finish payload BEFORE
        // firing the retry so the user sees the indicator even if
        // the network is fast enough that the retry succeeds in a
        // few hundred ms.
        refreshSyncIndicator(prefs)

        // Long-press the indicator → "Discard offline game?" prompt.
        // Useful escape hatch when a payload keeps failing (e.g. a
        // malformed PGN the server perma-rejects via 5xx instead of
        // 4xx, or the user simply no longer cares).
        syncIndicator.setOnLongClickListener {
            AlertDialog.Builder(this)
                .setTitle("Discard offline game?")
                .setMessage(
                    "This will drop the unsynced game and we won't try " +
                        "to send it again.",
                )
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Discard") { _, _ ->
                    if (PendingGameFinish.discardFromPrefs(prefs)) {
                        refreshSyncIndicator(prefs)
                        Toast.makeText(
                            this@HomeActivity,
                            "Discarded · the game was not synced",
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                }
                .show()
            true
        }

        // If a previous /game/finish failed offline (timeout / 5xx /
        // network), the payload is in prefs.  Try again from here in
        // case the user lands on Home and never enters MainActivity —
        // the shared helper's process-singleton guard prevents
        // double-firing if MainActivity also runs the retry.
        PendingGameFinish.retryFromPrefs(
            prefs = prefs,
            client = gameApiClient,
            scope = lifecycleScope,
            onSuccess = {
                Toast.makeText(
                    this@HomeActivity,
                    "Synced your offline game",
                    Toast.LENGTH_SHORT,
                ).show()
                // Slot was cleared inside the helper; reflect it here.
                refreshSyncIndicator(prefs)
            },
            onSessionExpired = {
                // Token lapsed between save and retry — kick back to
                // login.  The payload stays put for the next session.
                startActivity(
                    Intent(this, LoginActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
                )
                finish()
            },
        )
        val firstSeen = prefs.getLong(PREF_HOME_FIRST_SEEN_AT, -1L)
            .takeIf { it > 0L }
            ?: System.currentTimeMillis().also {
                prefs.edit().putLong(PREF_HOME_FIRST_SEEN_AT, it).apply()
            }
        dateKicker.text = formatDateKicker(System.currentTimeMillis(), firstSeen)

        maybeShowResumeCard(prefs)
        // Cross-device resume: when the local snapshot is missing
        // (fresh install / device swap) but the server has an
        // unfinished game with a checkpoint, pull it down and
        // populate the local snapshot so the user sees a Resume
        // card on the same surface they'd see one on the original
        // device.  Skipped when local already has an in-progress
        // game (we trust the local snapshot — it's strictly fresher).
        maybeFetchCrossDeviceResume(prefs)

        // Today's drill card — hits /coach/plan/today and surfaces the
        // currently-due puzzle from the per-mistake study plan (phase 4).
        // Fires on every Home cold-start; onResume also refires it so a
        // plan generated by a backgrounded /game/finish shows up the
        // moment the user returns to Home.
        fetchAndPopulateTodaysDrill()

        // ── Library rows ─────────────────────────────────────────────
        findViewById<LinearLayout>(R.id.homeRowNewGame).setOnClickListener {
            launchMain(sheet = null)
        }
        findViewById<LinearLayout>(R.id.homeRowPastGames).setOnClickListener {
            launchMain(sheet = MainActivity.OPEN_SHEET_HISTORY)
        }

        // ── Bottom tab bar ───────────────────────────────────────────
        findViewById<LinearLayout>(R.id.homeTabHome).setOnClickListener { /* already here */ }
        findViewById<LinearLayout>(R.id.homeTabPuzzles).setOnClickListener {
            openPuzzles()
        }
        findViewById<LinearLayout>(R.id.homeTabYou).setOnClickListener {
            openProgressDashboard()
        }

        // Render the XP kicker from the cached counter so a Home
        // cold-start shows a value instantly, before the /auth/me
        // round-trip below.  The "I — New game" sub keeps its
        // default "Adaptive opponent" copy — the rating-derived
        // opponent number that used to be appended was removed when
        // Elo was hidden from the UI.
        val personalRatingView = findViewById<TextView>(R.id.homePersonalRating)
        val cachedXp = prefs.getInt(MainActivity.PREF_TRAINING_XP, -1)
        if (cachedXp >= 0) {
            renderXpKicker(personalRatingView, cachedXp)
        }

        // Refresh from the server on every Home cold-start so:
        //   - A fresh install (no SharedPreferences cache) shows the
        //     XP kicker immediately, not just after the first game.
        //   - The kicker stays in sync with server-side XP updates
        //     that landed while the app was backgrounded (e.g.
        //     trainings completed on another device).
        // Same /auth/me round-trip MainActivity already runs at its
        // cold-start; running it here too is cheap (one GET) and the
        // PREF write-back is shared.  PR #183's "no PATCH from cold-
        // start" invariant is preserved — this is pure GET.  We still
        // write rating + confidence to SharedPreferences because they
        // continue to drive adaptive opponent matching internally,
        // even though they are no longer displayed.
        val authToken = authRepo.getToken()
        if (authToken != null) {
            lifecycleScope.launch {
                when (val r = authApiClient.me(authToken)) {
                    is ApiResult.Success -> {
                        val server = r.data
                        renderXpKicker(personalRatingView, server.trainingXp)
                        prefs.edit()
                            .putFloat(MainActivity.PREF_RATING, server.rating)
                            .putFloat(MainActivity.PREF_CONFIDENCE, server.confidence)
                            .putInt(MainActivity.PREF_TRAINING_XP, server.trainingXp)
                            .apply()
                    }
                    else -> {
                        // Cache-hit path already populated the kicker
                        // (or it stays hidden on a fresh install) —
                        // network failure is non-fatal at the home
                        // surface.  MainActivity's own /auth/me on the
                        // next New-game tap is the recovery.
                    }
                }
            }
        }
    }

    /**
     * Populate the XP / Level kicker from a single training-XP value.
     * Centralised so the cache-hit and post-/auth/me code paths can't
     * drift on formatting.
     */
    private fun renderXpKicker(
        personalRating: TextView,
        xp: Int,
    ) {
        personalRating.text = formatXpKicker(xp)
        personalRating.visibility = View.VISIBLE
    }

    /**
     * Build a MainActivity intent that optionally asks the activity to
     * open a specific bottom sheet on startup.  Passing [sheet] = null
     * just launches MainActivity in its default state ("New game").
     */
    private fun launchMain(sheet: String?) {
        val intent = Intent(this, MainActivity::class.java)
        if (sheet != null) {
            intent.putExtra(MainActivity.EXTRA_OPEN_SHEET, sheet)
        }
        startActivity(intent)
    }

    override fun onResume() {
        super.onResume()
        // MainActivity may have synced the pending payload while Home
        // was off-screen.  Re-evaluate so the indicator hides without
        // requiring a full Home re-enter.  Cheap: a single
        // SharedPreferences.contains() lookup.
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (::syncIndicator.isInitialized) {
            refreshSyncIndicator(prefs)
        }
        // The user may have finished a training (or game) in
        // MainActivity while Home was off-screen — the XP cache is
        // updated alongside any /auth/me round-trip, so re-read it
        // here to refresh the kicker without waiting for the next
        // /auth/me on next cold-start.  Defensive ``isInitialized``
        // guard mirrors the sync-indicator one above (HomeActivity
        // #onCreate may have early-returned in the unauthenticated
        // branch).
        val cachedXp = prefs.getInt(MainActivity.PREF_TRAINING_XP, -1)
        if (cachedXp >= 0) {
            val personalRating = findViewById<TextView>(R.id.homePersonalRating)
            if (personalRating != null) {
                renderXpKicker(personalRating, cachedXp)
            }
        }
        // Re-poll /coach/plan/today.  A backgrounded /game/finish
        // (e.g. the user finished a game in MainActivity, then
        // returned to Home) may have spawned a fresh plan + verdict;
        // refire so the TodaysDrillCard surfaces immediately rather
        // than waiting for the next cold-start.
        fetchAndPopulateTodaysDrill()
    }

    /**
     * Fetch /coach/plan/today and toggle the TodaysDrillCard
     * visibility / contents.  No-op when the response is null
     * (no active plan) or carries a null today_puzzle (active plan
     * but no puzzle currently due — the spaced-repetition schedule
     * hasn't ticked over yet).
     *
     * Errors are swallowed silently: the card is non-critical and
     * the rest of the Home screen renders fine without it.  A
     * server outage just leaves the card hidden until the next
     * onResume cycle.
     */
    private fun fetchAndPopulateTodaysDrill() {
        val block = findViewById<LinearLayout>(R.id.homeTodaysDrillBlock) ?: return
        val kicker = findViewById<TextView>(R.id.homeTodaysDrillKicker)
        val themeView = findViewById<TextView>(R.id.homeTodaysDrillTheme)
        val verdictView = findViewById<TextView>(R.id.homeTodaysDrillVerdict)
        val startButton = findViewById<Button>(R.id.homeTodaysDrillStartButton)

        lifecycleScope.launch {
            val result = try {
                gameApiClient.getCoachPlanToday()
            } catch (_: Exception) {
                block.visibility = View.GONE
                return@launch
            }
            val response = (result as? ApiResult.Success)?.data
            if (response != null) {
                // Cache for the Puzzles tab even when there's no due
                // puzzle — the week overview still renders its day rows
                // in that state; only the drill card hides.
                latestPlan = response
            }
            val puzzle = response?.todayPuzzle
            if (response == null || puzzle == null) {
                // No active plan, or active plan with no due puzzle.
                block.visibility = View.GONE
                return@launch
            }

            kicker.text = formatTodaysDrillKicker(
                dayOffset = puzzle.dayOffset,
                totalDays = response.totalDays,
            )
            themeView.text = formatTodaysDrillTheme(response.theme)
            if (response.verdict.isNotBlank()) {
                verdictView.text = response.verdict
                verdictView.visibility = View.VISIBLE
            } else {
                // Verdict failed validators or LLM was unreachable —
                // hide the line cleanly rather than showing an empty
                // gap above the button.
                verdictView.visibility = View.GONE
            }
            block.visibility = View.VISIBLE

            // Tapping the card opens the week-overview sheet — same
            // path as the Puzzles tab, so the two entries can't drift.
            startButton.text = "This week's plan"
            startButton.setOnClickListener {
                openStudyPlanOverview(response)
            }
        }
    }

    /**
     * Puzzles tab.  Reuses the plan the drill card already fetched when
     * available; otherwise does a one-shot /coach/plan/today fetch so a
     * tap that races the card's own fetch (or follows its quiet hide on
     * error) still lands somewhere.  A tab tap must never be a silent
     * no-op: with no active plan (or on a fetch error) it opens the
     * standalone [PuzzleTrainerBottomSheet] — the endless Lichess-fed
     * practice stream — instead of the old "no drills yet" toast
     * dead-end.
     */
    private fun openPuzzles() {
        val cached = latestPlan
        if (cached != null) {
            openStudyPlanOverview(cached)
            return
        }
        lifecycleScope.launch {
            val result = try {
                gameApiClient.getCoachPlanToday()
            } catch (_: Exception) {
                null
            }
            val response = (result as? ApiResult.Success)?.data
            if (response == null) {
                // No active plan (or the fetch failed) — practice
                // puzzles still work, and the trainer sheet handles its
                // own network failures with a visible retry state.
                openPuzzleTrainer()
                return@launch
            }
            latestPlan = response
            openStudyPlanOverview(response)
        }
    }

    /**
     * Standalone puzzle trainer (GET /puzzles/next), hosted over Home.
     * Reached from the Puzzles tab when no study plan exists and from
     * the overview sheet's "Practice puzzles" entry.
     */
    private fun openPuzzleTrainer() {
        if (supportFragmentManager.isStateSaved) return
        val sheet = PuzzleTrainerBottomSheet()
        sheet.gameApiClient = gameApiClient
        sheet.show(supportFragmentManager, "PuzzleTrainerBottomSheet")
    }

    /**
     * Week-overview sheet (the whole spaced-repetition plan + focus),
     * which carries the "Start today's drill" CTA into the existing
     * drill flow.  Shared by the drill card and the Puzzles tab.  The
     * sheet gets the already-fetched plan as JSON so it does no network
     * I/O of its own.
     */
    private fun openStudyPlanOverview(plan: CoachPlanResponse) {
        if (supportFragmentManager.isStateSaved) return
        val sheet = StudyPlanOverviewBottomSheet.newInstance(
            ApiJson.encodeToString(plan)
        )
        sheet.gameApiClient = gameApiClient
        sheet.show(supportFragmentManager, "StudyPlanOverviewBottomSheet")
    }

    /** You tab — the profile / progress dashboard, hosted over Home. */
    private fun openProgressDashboard() {
        if (supportFragmentManager.isStateSaved) return
        val sheet = ProgressDashboardBottomSheet()
        sheet.gameApiClient = gameApiClient
        sheet.show(supportFragmentManager, "ProgressDashboardBottomSheet")
    }

    /**
     * Avatar tap — the Settings sheet, hosted over Home.  Same sheet
     * MainActivity's drawer shows; the Account-section callbacks route
     * through [AccountFlows] so the two hosts can't drift on
     * validation or logout routing.
     */
    private fun openSettings() {
        if (supportFragmentManager.isStateSaved) return
        val sheet = SettingsBottomSheet()
        sheet.onChangePasswordTapped = {
            AccountFlows.showChangePasswordDialog(this, authRepo, authApiClient)
        }
        sheet.onSignOutTapped = {
            AccountFlows.performLogout(this, authRepo, authApiClient)
        }
        sheet.onConnectLichessTapped = {
            LichessConnectBottomSheet()
                .show(supportFragmentManager, LichessConnectBottomSheet.TAG)
        }
        sheet.show(supportFragmentManager, "SettingsBottomSheet")
    }

    /**
     * Show the amber pending-sync row iff there's an unsynced
     * /game/finish payload in [prefs].  Called from onCreate, from
     * the post-retry success callback, and from onResume.
     */
    private fun refreshSyncIndicator(prefs: SharedPreferences) {
        val pending = prefs.contains(PendingGameFinish.PREF_PENDING_FINISH_PAYLOAD)
        syncIndicator.visibility = if (pending) View.VISIBLE else View.GONE
    }

    /**
     * Cross-device resume: when the local snapshot is missing but
     * the server has an unfinished game with a checkpoint, pull it
     * down and populate the snapshot prefs the same way MainActivity
     * would on a normal mid-game.  After populating we re-render
     * the Resume card so the user immediately sees the recovery.
     *
     * Skipped (no-op) when:
     *   - PREF_LAST_GAME_IN_PROGRESS is already true (local snapshot
     *     is at least as fresh as the server; trust it)
     *   - The user has no token (still in some pre-auth state, which
     *     shouldn't happen given the auth check at the top of
     *     onCreate but defends against races)
     */
    private fun maybeFetchCrossDeviceResume(prefs: SharedPreferences) {
        if (prefs.getBoolean(MainActivity.PREF_LAST_GAME_IN_PROGRESS, false)) return
        if (authRepo.getToken() == null) return
        lifecycleScope.launch {
            when (val r = gameApiClient.getActiveGame()) {
                is ApiResult.Success -> {
                    val active = r.data ?: return@launch
                    if (active.currentFen.isBlank()) return@launch
                    val moveCount = active.currentUciHistory
                        .split(',')
                        .count { it.isNotBlank() }
                    prefs.edit()
                        .putBoolean(MainActivity.PREF_LAST_GAME_IN_PROGRESS, true)
                        .putString(MainActivity.PREF_LAST_GAME_FEN, active.currentFen)
                        .putString(
                            MainActivity.PREF_LAST_GAME_UCI_HISTORY,
                            active.currentUciHistory,
                        )
                        .putString(MainActivity.PREF_LAST_GAME_SERVER_ID, active.gameId)
                        .putInt(MainActivity.PREF_LAST_GAME_MOVE_COUNT, moveCount)
                        .putLong(
                            MainActivity.PREF_LAST_GAME_TIMESTAMP,
                            System.currentTimeMillis(),
                        )
                        .apply()
                    maybeShowResumeCard(prefs)
                }
                is ApiResult.HttpError -> { /* 401 handled elsewhere; other 4xx/5xx silent */ }
                is ApiResult.NetworkError, ApiResult.Timeout -> { /* offline; try again next time */ }
            }
        }
    }

    /**
     * Read MainActivity's in-progress snapshot from [prefs] and either
     * populate + reveal the Resume card or hide it entirely.  Hidden
     * when (a) the in-progress flag is false, (b) move count is 0, or
     * (c) the snapshot is older than [RESUME_TTL_MILLIS] (a stale
     * snapshot from days ago shouldn't claim there's an active game).
     */
    private fun maybeShowResumeCard(prefs: SharedPreferences) {
        val inProgress = prefs.getBoolean(MainActivity.PREF_LAST_GAME_IN_PROGRESS, false)
        val moveCount = prefs.getInt(MainActivity.PREF_LAST_GAME_MOVE_COUNT, 0)
        val timestamp = prefs.getLong(MainActivity.PREF_LAST_GAME_TIMESTAMP, 0L)
        val gameNumber = prefs.getInt(MainActivity.PREF_LAST_GAME_NUMBER, 0)
        val now = System.currentTimeMillis()

        if (!inProgress || moveCount <= 0 || timestamp <= 0L ||
            (now - timestamp) > RESUME_TTL_MILLIS
        ) {
            resumeBlock.visibility = View.GONE
            return
        }

        resumeTitle.text = formatResumeTitle(gameNumber, moveCount)
        resumeSub.text   = formatResumeSub(timestamp)
        resumeBlock.visibility = View.VISIBLE
        findViewById<View>(R.id.homeResumeCard).setOnClickListener {
            // EXTRA_RESUME tells MainActivity.onCreate to skip
            // startNewGameSession() and apply the saved FEN / UCI
            // list from PREF_LAST_GAME_FEN / PREF_LAST_GAME_UCI_HISTORY
            // — see MainActivity.tryRestoreInProgressGame().
            startActivity(
                Intent(this, MainActivity::class.java)
                    .putExtra(MainActivity.EXTRA_RESUME, true),
            )
        }
    }

    companion object {
        const val PREFS_NAME = MainActivity.PREFS_NAME
        const val PREF_HOME_FIRST_SEEN_AT = "home_first_seen_at"

        /**
         * Map a plan's ``day_offset`` (0 / 3 / 7) plus ``total_days``
         * to the human-readable "Today's drill · Day N of M" kicker
         * the card header displays.  Day 0 is shown as "Day 1 of M"
         * because the user thinks in 1-indexed terms.
         *
         * Pure function — unit-testable without Robolectric.
         */
        fun formatTodaysDrillKicker(dayOffset: Int, totalDays: Int): String {
            val displayDay = when (dayOffset) {
                0 -> 1
                3 -> 2
                7 -> 3
                else -> 1
            }
            return "Today's drill · Day $displayDay of $totalDays"
        }

        /**
         * Map a server-side theme tag (snake_case, e.g.
         * ``"king_safety"``) to a human-readable label for the card's
         * accent line: ``"Practice · King safety"``.
         *
         * ``"generic"`` collapses to a bare ``"Practice"`` (no second
         * half) since "Practice · Generic" reads as filler.  Pure
         * function — unit-testable.
         */
        fun formatTodaysDrillTheme(theme: String): String {
            val tag = theme.trim().lowercase()
            if (tag.isEmpty() || tag == "generic") {
                return "Practice"
            }
            val pretty = tag
                .split('_')
                .joinToString(" ") { it.replaceFirstChar(Char::uppercaseChar) }
                .let { full ->
                    // Sentence-case: only the first word capitalised, rest lower.
                    val parts = full.split(' ')
                    parts.first() + parts.drop(1).joinToString("") { " ${it.lowercase()}" }
                }
            return "Practice · $pretty"
        }

        /**
         * A Resume snapshot older than this gets treated as stale
         * (e.g. the user backgrounded mid-game and didn't return for
         * days; the AI side and server session have long since timed
         * out).  6h matches the rough "session" semantics the rest of
         * the app uses.
         */
        const val RESUME_TTL_MILLIS = 6L * 60L * 60L * 1000L

        /**
         * Compute up-to-2-letter initials from a player identifier.
         * The auth layer currently surfaces only `playerId` (no email /
         * display name), so we derive initials from whatever it gives
         * us.  Returns "—" for null/blank/"demo" so the avatar reads as
         * "no identity yet" rather than a misleading "DE".
         */
        fun initialsFor(playerId: String?): String {
            if (playerId.isNullOrBlank()) return "—"
            val cleaned = playerId.trim()
            if (cleaned.equals("demo", ignoreCase = true)) return "—"
            // Take the first two alphanumeric chars; if the id is short
            // (e.g. a single-char username), pad with the same char.
            val alnum = cleaned.filter { it.isLetterOrDigit() }
            if (alnum.isEmpty()) return "—"
            val a = alnum[0].uppercaseChar()
            val b = if (alnum.length >= 2) alnum[1].uppercaseChar() else a
            return "$a$b"
        }

        /**
         * "<Weekday> · Day <N>" — N = days between [firstSeenAtMillis]
         * and [nowMillis], floored at 1 so a same-day visit reads as
         * "Day 1" rather than "Day 0".
         *
         * Locale.US / TimeZone.getDefault() — weekday name is rendered
         * in the locale the design ships with (English) but the day
         * arithmetic uses the device's local time so "today" lines up
         * with the user's calendar.
         */
        fun formatDateKicker(nowMillis: Long, firstSeenAtMillis: Long): String {
            val weekday = SimpleDateFormat("EEEE", Locale.US).format(Date(nowMillis))
            val deltaDays = TimeUnit.MILLISECONDS.toDays(nowMillis - firstSeenAtMillis)
            val dayN = max(1L, deltaDays + 1L)
            return "$weekday · Day ${"%03d".format(dayN)}"
        }

        /**
         * "Game NNN · move M" — three-digit game number to match the
         * design ("Game 047 · move 14").  [gameNumber] is the value
         * MainActivity bumps in startNewGameSession; [moveCount] is
         * half-moves played so far.
         */
        fun formatResumeTitle(gameNumber: Int, moveCount: Int): String =
            "Game ${"%03d".format(max(1, gameNumber))} · move $moveCount"

        /**
         * "vs. adaptive · HH:mm" — opponent identity reads as the
         * generic adaptive opponent.  The numeric opponent rating
         * (player rating biased ~40 below) was removed when Elo was
         * hidden from the UI; the opponent-matching math still uses
         * the cached rating internally, the user just no longer sees
         * the derived number.  Time renders in the device's local
         * timezone since the kicker itself is a wall-clock display.
         */
        fun formatResumeSub(timestampMillis: Long): String {
            val time = SimpleDateFormat("HH:mm", Locale.US).format(Date(timestampMillis))
            return "vs. adaptive · $time"
        }

        /**
         * "Level N · X XP" — total XP plus the level it implies.
         * Phase 1 ships a simple linear curve (100 XP per level) so
         * the renderer is testable without a back-end source of
         * truth on the level boundary; later phases can swap the
         * formula without touching callers.
         */
        fun formatXpKicker(xp: Int): String {
            val safeXp = max(0, xp)
            val level = max(1, safeXp / XP_PER_LEVEL + 1)
            return "Level $level · $safeXp XP"
        }

        /** Linear XP-per-level boundary used by [formatXpKicker]. */
        const val XP_PER_LEVEL = 100
    }
}
package com.cereveon.myapp

internal object JniMoveBridge {
    fun normalize(move: AIMove, fen: String): AIMove? {
        val position = Position.fromFen(fen) ?: return move.takeIf { it.isValid() }
        if (!move.isValid()) return null

        val candidates = linkedSetOf<AIMove>()
        for (swapAxes in listOf(false, true)) {
            for (flipRows in listOf(false, true)) {
                for (flipCols in listOf(false, true)) {
                    candidates += move.transform(swapAxes, flipRows, flipCols)
                }
            }
        }

        return candidates.firstOrNull(position::isLegal)
    }

    private fun AIMove.transform(swapAxes: Boolean, flipRows: Boolean, flipCols: Boolean): AIMove {
        fun mapSquare(row: Int, col: Int): Pair<Int, Int> {
            var mappedRow = row
            var mappedCol = col
            if (swapAxes) {
                mappedRow = col
                mappedCol = row
            }
            if (flipRows) mappedRow = 7 - mappedRow
            if (flipCols) mappedCol = 7 - mappedCol
            return mappedRow to mappedCol
        }

        val (mappedFr, mappedFc) = mapSquare(fr, fc)
        val (mappedTr, mappedTc) = mapSquare(tr, tc)
        return AIMove(mappedFr, mappedFc, mappedTr, mappedTc)
    }

    private class Position(
        private val board: Array<CharArray>,
        private val whiteToMove: Boolean,
        private val enPassant: Pair<Int, Int>?
    ) {
        fun isLegal(move: AIMove): Boolean {
            if (!move.isValid()) return false
            if (move.fr !in 0..7 || move.fc !in 0..7 || move.tr !in 0..7 || move.tc !in 0..7) {
                return false
            }

            val piece = board[move.fr][move.fc]
            if (piece == '.' || piece.isUpperCase() != whiteToMove) return false
            if (!isLegalGeometry(piece, move.fr, move.fc, move.tr, move.tc, allowCastle = true)) {
                return false
            }

            val target = board[move.tr][move.tc]
            board[move.tr][move.tc] = piece
            board[move.fr][move.fc] = '.'
            val leavesKingInCheck = isInCheck(piece.isUpperCase())
            board[move.fr][move.fc] = piece
            board[move.tr][move.tc] = target
            return !leavesKingInCheck
        }

        // ``allowCastle`` is true only on the actual move being normalised; the
        // attack-detection path (isSquareAttacked) passes false so a king never
        // counts as "attacking" its 2-square castle-target square.
        private fun isLegalGeometry(
            piece: Char, fr: Int, fc: Int, tr: Int, tc: Int, allowCastle: Boolean
        ): Boolean {
            if (fr == tr && fc == tc) return false
            val target = board[tr][tc]
            if (target != '.' && target.isUpperCase() == piece.isUpperCase()) return false

            val dr = kotlin.math.abs(tr - fr)
            val dc = kotlin.math.abs(tc - fc)
            return when (piece.lowercaseChar()) {
                'p' -> {
                    val dir = if (piece.isUpperCase()) -1 else 1
                    val startRow = if (piece.isUpperCase()) 6 else 1
                    when {
                        fc == tc && tr == fr + dir && target == '.' -> true
                        fc == tc &&
                            fr == startRow &&
                            tr == fr + 2 * dir &&
                            target == '.' &&
                            board[fr + dir][fc] == '.' -> true
                        dc == 1 && tr == fr + dir && target != '.' && target.isUpperCase() != piece.isUpperCase() -> true
                        // En passant reaches the bridge as a diagonal pawn move
                        // onto an EMPTY square; recognise it via the FEN
                        // en-passant target so the right transform is selected.
                        // ChessBoardView.applyAIMove re-validates and removes the
                        // captured pawn.
                        dc == 1 && tr == fr + dir && target == '.' && enPassant == (tr to tc) -> true
                        else -> false
                    }
                }
                'r' -> (fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
                'n' -> (dr == 2 && dc == 1) || (dr == 1 && dc == 2)
                'b' -> dr == dc && pathClear(fr, fc, tr, tc)
                'q' -> (dr == dc || fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
                // Castling reaches the bridge as a bare 2-square king move (the
                // native engine sends no castle flag).  Recognise it so the
                // correct transform is picked; ChessBoardView.applyAIMove
                // re-checks castling rights and moves the rook.
                'k' -> (dr <= 1 && dc <= 1) ||
                    (allowCastle && dr == 0 && dc == 2 && isCastleShape(piece, fr, fc, tc))
                else -> false
            }
        }

        /** True when a 2-square king move lands as a standard castle on this
         *  board: a same-coloured rook sits on the corner toward [destCol] and
         *  the squares between king and rook are empty.  Disambiguates the
         *  coordinate frame only — full rights/through-check legality is
         *  re-checked by [ChessBoardView.applyAIMove]. */
        private fun isCastleShape(king: Char, row: Int, kingCol: Int, destCol: Int): Boolean {
            val rook = if (king.isUpperCase()) 'R' else 'r'
            val rookCol = if (destCol > kingCol) 7 else 0
            if (board[row][rookCol] != rook) return false
            return pathClear(row, kingCol, row, rookCol)
        }

        private fun pathClear(fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
            val dr = (tr - fr).coerceIn(-1, 1)
            val dc = (tc - fc).coerceIn(-1, 1)
            var row = fr + dr
            var col = fc + dc
            while (row != tr || col != tc) {
                if (board[row][col] != '.') return false
                row += dr
                col += dc
            }
            return true
        }

        private fun isInCheck(white: Boolean): Boolean {
            val king = if (white) 'K' else 'k'
            var kingRow = -1
            var kingCol = -1
            loop@ for (row in 0..7) {
                for (col in 0..7) {
                    if (board[row][col] == king) {
                        kingRow = row
                        kingCol = col
                        break@loop
                    }
                }
            }
            if (kingRow == -1) return false
            return isSquareAttacked(kingRow, kingCol, !white)
        }

        private fun isSquareAttacked(row: Int, col: Int, byWhite: Boolean): Boolean {
            for (sourceRow in 0..7) {
                for (sourceCol in 0..7) {
                    val piece = board[sourceRow][sourceCol]
                    if (piece != '.' && piece.isUpperCase() == byWhite) {
                        if (isLegalGeometry(piece, sourceRow, sourceCol, row, col, allowCastle = false)) {
                            return true
                        }
                    }
                }
            }
            return false
        }

        companion object {
            fun fromFen(fen: String): Position? {
                val parts = fen.trim().split(" ")
                if (parts.isEmpty()) return null
                val rows = parts[0].split("/")
                if (rows.size != 8) return null

                val board = Array(8) { CharArray(8) { '.' } }
                for (row in rows.indices) {
                    var col = 0
                    for (symbol in rows[row]) {
                        if (symbol.isDigit()) {
                            col += symbol.digitToInt()
                        } else {
                            if (col !in 0..7) return null
                            board[row][col] = symbol
                            col++
                        }
                    }
                    if (col != 8) return null
                }

                val whiteToMove = parts.getOrNull(1)?.equals("w", ignoreCase = true) ?: false
                // FEN field 4 (index 3) is the en-passant target square ("-" or
                // e.g. "e3").  Absent on the short placement-only FENs used in
                // some call sites, which simply disables EP recognition.
                val enPassant = parts.getOrNull(3)?.let(::squareFromAlgebraic)
                return Position(board, whiteToMove, enPassant)
            }

            private fun squareFromAlgebraic(square: String): Pair<Int, Int>? {
                if (square.length != 2) return null
                val col = square[0] - 'a'
                val rank = square[1] - '0'
                if (col !in 0..7 || rank !in 1..8) return null
                return (8 - rank) to col
            }
        }
    }
}
package com.cereveon.myapp

import kotlinx.serialization.json.Json

/**
 * Single ``Json`` instance shared by every API client.
 *
 * Sprint 4.3.C migrated the client surface off hand-rolled
 * ``org.json.JSONObject`` parsing onto kotlinx-serialization.  Each
 * client used to hide its own JSON setup inside a private companion
 * object; centralising the config here keeps the wire-format
 * compatibility contract (snake_case fields, lenient null handling)
 * stable across every endpoint.
 *
 * Config rationale:
 *
 *   - ``ignoreUnknownKeys = true``: the backend can add new response
 *     fields in a future release without breaking older Android
 *     clients on the field.  Without this, any unknown key throws
 *     ``SerializationException`` and the call fails.
 *
 *   - ``coerceInputValues = true``: lenient coercion of JSON null →
 *     default value for non-nullable fields.  Matches what the
 *     previous hand-rolled parsers did when they called
 *     ``optString("…", "")`` / ``optInt("…", 0)``.
 *
 *   - ``encodeDefaults = false``: requests don't ship default values
 *     the server already assumes.  Smaller payloads, no behavioural
 *     change.
 *
 *   - ``isLenient = false``: reject malformed JSON loudly.  An
 *     upstream proxy or LLM-fallback path emitting non-JSON would
 *     otherwise be silently accepted.
 */
internal val ApiJson: Json = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    encodeDefaults = false
    isLenient = false
}
package com.cereveon.myapp

import kotlinx.serialization.encodeToString
import java.net.HttpURLConnection

/**
 * Shared client interface for the backend Lichess integration endpoints.
 *
 * Returns [ApiResult] on every call — callers never see raw exceptions.
 * Implementations are safe to call from any coroutine context.
 *
 * All endpoints require Bearer-token auth.  Successful responses
 * (2xx) carry an ``X-Auth-Token`` rotation header that
 * [HttpLichessApiClient] forwards to its [tokenSink] for transparent
 * JWT refresh — same pattern as [HttpAuthApiClient].
 *
 * Default implementations return [ApiResult.HttpError(501)] so test
 * fakes can override only the methods they exercise.
 */
interface LichessApiClient {

    /**
     * GET /lichess/status.
     *
     * Returns the player's current Lichess link state plus the count of
     * games imported so far.  When [LichessStatusResponse.linked] is
     * false the remaining fields are absent on the wire (null/default
     * here).
     */
    suspend fun status(token: String): ApiResult<LichessStatusResponse> =
        ApiResult.HttpError(501)

    /**
     * POST /lichess/link.
     *
     * @return [ApiResult.Success] with [LichessLinkResponse] on HTTP 200.
     *         [ApiResult.HttpError(404)] when the Lichess username does
     *         not exist; (409) when the handle is already linked to
     *         another ChessCoach player; (400) on schema validation;
     *         (502/503) on Lichess upstream / rate-limit; transport
     *         variants otherwise.
     */
    suspend fun link(username: String, token: String): ApiResult<LichessLinkResponse> =
        ApiResult.HttpError(501)

    /**
     * POST /lichess/import?max_games=N (v1 synchronous path).
     *
     * Synchronous — blocks the request until the slice is complete (or
     * the cap is reached).  Repeated calls walk forward through history
     * via the server-side ``last_imported_at`` watermark.
     *
     * Retained for backward compat with test fakes (and any caller
     * still wired to the v1 contract).  Production code paths should
     * use [startImport] + [getImportJob] instead — the v2 surface
     * returns 202 immediately and lets the UI render a determinate
     * progress bar via polling.
     *
     * @return [ApiResult.Success] with [LichessImportResponse] on HTTP 200.
     *         [ApiResult.HttpError(400)] when the player has no Lichess
     *         link (link first); (502) on Lichess upstream; (503) when
     *         Lichess rate-limits.
     */
    @Deprecated(
        message = "v1 synchronous import. Use startImport(...) + getImportJob(...) for v2 async.",
        replaceWith = ReplaceWith("startImport(token, maxGames, rated)"),
        level = DeprecationLevel.WARNING,
    )
    suspend fun importGames(
        token: String,
        maxGames: Int = DEFAULT_MAX_IMPORT,
        rated: Boolean = true,
    ): ApiResult<LichessImportResponse> = ApiResult.HttpError(501)

    /**
     * POST /lichess/import?max_games=N with ``X-API-Version: 2`` —
     * the v2 async path.
     *
     * Returns 202 immediately with a [LichessImportAccepted] payload.
     * The actual Lichess stream runs on a server-side worker thread;
     * the caller should poll [getImportJob] every ~2s until
     * ``status`` is terminal (``succeeded`` / ``failed``).
     *
     * Coalescing: a second call with an already-running job for the
     * same player returns the existing job_id (and its current
     * counters) rather than spawning a second worker.
     *
     * @return [ApiResult.Success] with [LichessImportAccepted] on HTTP 202.
     *         [ApiResult.HttpError(400)] when the player has no Lichess
     *         link; (503) when Lichess rate-limits during the worker's
     *         own pull (rare — the job row records the failure, the
     *         POST itself succeeds).
     */
    suspend fun startImport(
        token: String,
        maxGames: Int = DEFAULT_MAX_IMPORT,
        rated: Boolean = true,
    ): ApiResult<LichessImportAccepted> = ApiResult.HttpError(501)

    /**
     * GET /lichess/import/job/{job_id} — poll an in-flight import.
     *
     * Owner-scoped: returns HTTP 404 when the job does not exist OR
     * when it belongs to another player.  The Connect sheet's polling
     * loop maps 404 to "give up + revert to last-known Linked state"
     * because either branch means there is nothing to display.
     */
    suspend fun getImportJob(
        jobId: String,
        token: String,
    ): ApiResult<LichessImportJobStatus> = ApiResult.HttpError(501)

    /**
     * DELETE /lichess/link.
     *
     * Idempotent — returns ``{"unlinked": false}`` when no link existed.
     * Imported ``game_events`` rows are retained as history (backend
     * policy).
     */
    suspend fun unlink(token: String): ApiResult<LichessUnlinkResponse> =
        ApiResult.HttpError(501)

    companion object {
        /**
         * Default per-call import slice, mirroring the backend default
         * (``max_games`` Query param defaults to 50; server hard cap is
         * 100).  The Connect bottom sheet's "Import games" button uses
         * this when the user doesn't override.
         */
        const val DEFAULT_MAX_IMPORT = 50
    }
}

/**
 * Production [LichessApiClient] backed by [BaseHttpClient].
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param connectTimeoutMs TCP connect deadline.
 * @param readTimeoutMs    Read deadline.  The import endpoint may take
 *                         30–60s for a full 100-game slice; if the
 *                         caller bumps the slice cap, also bump this.
 * @param tokenSink        Optional sink for the ``X-Auth-Token`` refresh
 *                         header — every successful response hands the
 *                         freshly-minted JWT here so callers can rotate
 *                         their stored token transparently.  Null
 *                         disables rotation (test fakes).
 */
class HttpLichessApiClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_LICHESS_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : LichessApiClient {

    companion object {
        /**
         * Lichess import streams NDJSON and re-runs Stockfish-free; a
         * 100-game pull on a thibault-scale account is ~30–60s of
         * server-side work plus network.  Default the read budget
         * higher than the auth client's 15s so the import call doesn't
         * Timeout on the happy path.  Callers can override per
         * environment.
         */
        const val DEFAULT_LICHESS_READ_TIMEOUT_MS = 90_000

        private const val STATUS_PATH = "/lichess/status"
        private const val LINK_PATH = "/lichess/link"
        private const val IMPORT_PATH = "/lichess/import"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    private fun bearerHeader(token: String): Map<String, String> =
        mapOf("Authorization" to "Bearer $token")

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun status(token: String): ApiResult<LichessStatusResponse> =
        http.request(
            path = STATUS_PATH,
            method = "GET",
            headers = bearerHeader(token),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<LichessStatusResponse>(body) },
        )

    override suspend fun link(
        username: String,
        token: String,
    ): ApiResult<LichessLinkResponse> = http.request(
        path = LINK_PATH,
        method = "POST",
        headers = bearerHeader(token),
        body = ApiJson.encodeToString(LichessLinkRequest(username = username)),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessLinkResponse>(body) },
    )

    @Suppress("OVERRIDE_DEPRECATION")
    @Deprecated(
        message = "v1 synchronous import. Use startImport(...) + getImportJob(...) for v2 async.",
        replaceWith = ReplaceWith("startImport(token, maxGames, rated)"),
        level = DeprecationLevel.WARNING,
    )
    override suspend fun importGames(
        token: String,
        maxGames: Int,
        rated: Boolean,
    ): ApiResult<LichessImportResponse> = http.request(
        // Query parameters appended to the path — BaseHttpClient
        // does not URL-encode for us, but ``maxGames`` is an Int and
        // ``rated`` is a Boolean so neither needs encoding.  The
        // backend caps maxGames at 100; sending a value above that
        // yields HTTP 422 (FastAPI Query validator), which falls
        // through to ApiResult.HttpError(422) here.
        //
        // NOTE: this method is retained for tests and is NOT what
        // the production v2 client invokes.  Because ``BaseHttpClient``
        // always sends ``X-API-Version: 2`` (post the version bump),
        // hitting this path would return the v2 202 body which would
        // fail to deserialise as LichessImportResponse.  Production
        // code calls [startImport] instead.
        path = "$IMPORT_PATH?max_games=$maxGames&rated=$rated",
        method = "POST",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessImportResponse>(body) },
    )

    override suspend fun startImport(
        token: String,
        maxGames: Int,
        rated: Boolean,
    ): ApiResult<LichessImportAccepted> = http.request(
        // Same path as v1 importGames; the version branch happens
        // server-side via the ``X-API-Version: 2`` header that
        // BaseHttpClient injects on every request.  The server
        // returns 202 + the job payload on the v2 path.
        path = "$IMPORT_PATH?max_games=$maxGames&rated=$rated",
        method = "POST",
        headers = bearerHeader(token),
        // 202 Accepted IS the v2 success (the KDoc above says so) — but
        // BaseHttpClient's default successCodes is {200}, so every
        // successful import used to land in the HttpError(202) branch:
        // the sheet showed the "unknown error" toast, no progress bar,
        // and the games "mysteriously" appeared later via the
        // activeImportJobId resume path.  Same widening the sibling
        // ReviewApiClient.startReview ships for its own 202 contract.
        successCodes = setOf(HttpURLConnection.HTTP_OK, HttpURLConnection.HTTP_ACCEPTED),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessImportAccepted>(body) },
    )

    override suspend fun getImportJob(
        jobId: String,
        token: String,
    ): ApiResult<LichessImportJobStatus> = http.request(
        // jobId is a server-issued UUID (regex-safe per RFC 4122);
        // we still concatenate without URL-encoding because the
        // backend's ``/import/job/{job_id}`` path-converter
        // accepts the UUID grammar verbatim.
        path = "$IMPORT_PATH/job/$jobId",
        method = "GET",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessImportJobStatus>(body) },
    )

    override suspend fun unlink(token: String): ApiResult<LichessUnlinkResponse> =
        http.request(
            path = LINK_PATH,
            method = "DELETE",
            headers = bearerHeader(token),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<LichessUnlinkResponse>(body) },
        )
}
package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed request/response models for the backend Lichess integration
 * endpoints (POST/DELETE /lichess/link, GET /lichess/status, POST
 * /lichess/import).
 *
 * Wire format mirrors the FastAPI shapes documented in
 * docs/API_CONTRACTS.md §§27–30.  ``@SerialName`` keeps the Kotlin
 * properties camelCase while the JSON stays snake_case.
 *
 * Trust-boundary note (mirrored from llm/seca/lichess/client.py):
 * Lichess's own Stockfish evals are never trusted by the backend, so
 * the import response never carries Lichess-derived eval data — the
 * Android client only sees row counts and the watermark.
 */

/**
 * Request body for POST /lichess/link.  Username is shape-validated
 * server-side (2–30 chars, `[A-Za-z0-9_-]`).  We send whatever the
 * user typed verbatim — the backend canonicalises to lowercase id.
 */
@Serializable
data class LichessLinkRequest(val username: String)

/**
 * Response from POST /lichess/link.
 *
 * On first-link, [calibration] reports whether the player's rating +
 * confidence were seeded from Lichess perf data (rapid > blitz >
 * classical).  Calibration only fires when the player is still at
 * default rating (1200) + confidence (0.5) — non-default players
 * keep their in-app rating.  See LichessCalibrationResult.
 */
@Serializable
data class LichessLinkResponse(
    val platform: String,
    @SerialName("external_username") val externalUsername: String,
    @SerialName("linked_at") val linkedAt: String? = null,
    val calibration: LichessCalibrationResult,
)

/**
 * Calibration outcome on first-link.
 *
 * When [applied] is true, the player's rating/confidence were seeded
 * from the chosen perf — [perf], [rating], [confidence] are populated.
 * When false, [reason] explains why ("player_already_calibrated" or
 * "no_eligible_perf") and the perf/rating/confidence fields are absent
 * from the wire payload (treated as null here).
 */
@Serializable
data class LichessCalibrationResult(
    val applied: Boolean,
    val reason: String? = null,
    val perf: String? = null,
    val rating: Float? = null,
    val confidence: Float? = null,
    @SerialName("games_basis") val gamesBasis: Int? = null,
    val provisional: Boolean? = null,
)

/**
 * Response from GET /lichess/status.
 *
 * Union-shaped on the wire: when [linked] is false the server returns
 * just ``{"linked": false}``; when true the platform/username/counts
 * are populated.  We model the union as one class with all-optional
 * fields and let the [linked] flag drive UI branching.
 */
@Serializable
data class LichessStatusResponse(
    val linked: Boolean,
    val platform: String? = null,
    @SerialName("external_username") val externalUsername: String? = null,
    @SerialName("linked_at") val linkedAt: String? = null,
    @SerialName("last_imported_at") val lastImportedAt: String? = null,
    @SerialName("imported_game_count") val importedGameCount: Int = 0,
    /**
     * v2 only: non-null when a Lichess import job is in flight
     * (status ``queued`` or ``running``) for the current player.  The
     * Connect sheet uses this on view-open to rejoin a progress view
     * that survived a sheet dismiss / device restart.  ``null`` on a
     * not-linked response (server omits the field) and on linked
     * responses where no job is active.
     */
    @SerialName("active_import_job_id") val activeImportJobId: String? = null,
)

/**
 * Response from POST /lichess/import.
 *
 * Per the backend contract: a partial-fail mid-stream commits already-
 * inserted rows and returns the counts seen so far; the watermark
 * advances only on a clean iteration.  ``skipped_*`` counts are
 * observability, not errors.
 */
@Serializable
data class LichessImportResponse(
    val inserted: Int = 0,
    @SerialName("skipped_duplicate") val skippedDuplicate: Int = 0,
    @SerialName("skipped_invalid") val skippedInvalid: Int = 0,
    @SerialName("last_imported_at") val lastImportedAt: String? = null,
)

/**
 * Response from DELETE /lichess/link.  ``{"unlinked": true}`` when a
 * link existed and was removed; ``{"unlinked": false}`` when the
 * player had no link (idempotent — not an error).
 */
@Serializable
data class LichessUnlinkResponse(val unlinked: Boolean)

// ─────────────────────────────────────────────────────────────────────
// v2 async import — replaces synchronous LichessImportResponse on the
// new client.  See docs/API_CONTRACTS.md §31 and the plan note in
// LichessConnectViewModel about the polling lifecycle.
// ─────────────────────────────────────────────────────────────────────

/**
 * 202 response from POST /lichess/import when the client sends
 * ``X-API-Version: 2``.  Carries the freshly-created (or coalesced)
 * job's state — counters are 0 on a brand-new job, non-zero when a
 * concurrent caller already started the import.
 *
 * Shape is identical to [LichessImportJobStatus] so a fake / cached
 * decoder can use either type.  Kept as a separate type for clarity
 * at the call-site (POST returns Accepted, GET returns Status).
 */
@Serializable
data class LichessImportAccepted(
    @SerialName("job_id") val jobId: String,
    val status: String,
    val inserted: Int = 0,
    @SerialName("skipped_duplicate") val skippedDuplicate: Int = 0,
    @SerialName("skipped_invalid") val skippedInvalid: Int = 0,
    @SerialName("target_max_games") val targetMaxGames: Int,
    @SerialName("last_imported_at_ms") val lastImportedAtMs: Long? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/**
 * 200 response from GET /lichess/import/job/{job_id}.  Same shape as
 * [LichessImportAccepted]; the field set is stable across the job's
 * lifecycle.  [status] ∈ {``queued``, ``running``, ``succeeded``,
 * ``failed``}.  [errorMessage] is non-null on ``failed`` only.
 */
@Serializable
data class LichessImportJobStatus(
    @SerialName("job_id") val jobId: String,
    val status: String,
    val inserted: Int = 0,
    @SerialName("skipped_duplicate") val skippedDuplicate: Int = 0,
    @SerialName("skipped_invalid") val skippedInvalid: Int = 0,
    @SerialName("target_max_games") val targetMaxGames: Int,
    @SerialName("last_imported_at_ms") val lastImportedAtMs: Long? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
) {
    companion object {
        const val STATUS_QUEUED = "queued"
        const val STATUS_RUNNING = "running"
        const val STATUS_SUCCEEDED = "succeeded"
        const val STATUS_FAILED = "failed"
    }

    val isTerminal: Boolean
        get() = status == STATUS_SUCCEEDED || status == STATUS_FAILED
}
package com.cereveon.myapp

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Trampoline for the Lichess OAuth redirect
 * (``com.cereveon.myapp://lichess-auth?code=...&state=...``).
 *
 * The system browser fires this VIEW intent when Lichess redirects back
 * after the user authorizes (or denies).  It immediately forwards the
 * redirect URI to [LoginActivity] — which owns the pending PKCE state
 * and performs the backend exchange — and finishes itself, mirroring the
 * AppAuth ``RedirectUriReceiverActivity`` pattern without the dependency.
 *
 * Kept separate from [LoginActivity] so the LAUNCHER activity keeps its
 * default ``standard`` launch mode: `CLEAR_TOP or SINGLE_TOP` delivers
 * the redirect to the existing LoginActivity instance (`onNewIntent`)
 * when the user is still sitting on the login screen behind the browser,
 * and cold-starts one (`onCreate` with intent data) after process death.
 *
 * Hostile-input note: this activity is `exported` (any app can fire the
 * intent), but it carries no logic — it only re-posts `intent.data` to
 * [LoginActivity] (itself exported as the LAUNCHER).  The actual defense
 * against forged redirect intents is LoginActivity's `state` check: the
 * redirect must carry the value persisted when WE started the flow, and
 * redirects with no pending attempt are silently dropped.
 */
class LichessAuthRedirectActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(
            Intent(this, LoginActivity::class.java)
                .setData(intent?.data)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        )
        finish()
    }
}
package com.cereveon.myapp

import android.content.Context
import android.content.SharedPreferences
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.isVisible
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelProvider.NewInstanceFactory
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout

/**
 * Cereveon · Atrium · Lichess Connect bottom sheet.
 *
 * Surface invariants (mirror docs/API_CONTRACTS.md §§27–30 + the
 * trust-boundary note from llm/seca/lichess/import_service.py):
 *
 *   - Username pre-validated client-side via
 *     [LichessConnectViewModel.isValidUsername]; the same regex the
 *     backend enforces.  An obviously-bad handle is rejected without a
 *     round-trip.
 *
 *   - On link success the calibration banner is shown ONCE — if the
 *     user reopens the sheet later, the GET /lichess/status response
 *     does not carry calibration data, so the banner stays hidden.
 *     This matches the user-question selection ("Inline status + brief
 *     toast"): calibration is surfaced loudly on the transition and
 *     fades to a plain "Linked" state afterwards.
 *
 *   - On import success a Toast surfaces the counts immediately; the
 *     status block updates inline so the row counts increase without a
 *     manual refresh.
 *
 *   - Unlink is the full-trio MVP scope — single tap, server response
 *     drives the transition back to NotLinked.  Imported game_events
 *     rows are retained server-side per the contract.
 *
 * Lifecycle:
 *   - onViewCreated: refresh status from the backend so the sheet
 *     shows the live state, not a stale cache.
 *   - The Fragment owns the ViewModel directly (no shared scope) —
 *     dismissing the sheet tears it down and any in-flight launch is
 *     cancelled cleanly.
 *
 * Mirrors the SettingsBottomSheet idiom: extends
 * [BottomSheetDialogFragment], inflates a layout, binds views in
 * onViewCreated, no Activity scaffolding.
 */
class LichessConnectBottomSheet : BottomSheetDialogFragment() {

    private lateinit var viewModel: LichessConnectViewModel

    // Cached views — bound in onViewCreated.
    private lateinit var loadingSpinner: ProgressBar
    private lateinit var groupNotLinked: View
    private lateinit var groupLinked: View
    private lateinit var usernameLayout: TextInputLayout
    private lateinit var usernameField: TextInputEditText
    private lateinit var btnLink: Button
    private lateinit var btnImport: Button
    private lateinit var btnUnlink: Button
    private lateinit var linkedHandleText: TextView
    private lateinit var calibrationBanner: TextView
    private lateinit var importedCountText: TextView
    private lateinit var lastSyncedText: TextView

    // v2 async-import progress block — hidden except during Importing.
    private lateinit var importProgressBlock: View
    private lateinit var importProgressBar: ProgressBar
    private lateinit var importProgressCaption: TextView

    /**
     * Test seam — production callers don't pass one and we build the
     * default factory in onCreate; instrumentation tests can swap in a
     * factory backed by a fake [LichessApiClient].
     */
    var viewModelFactoryOverride: ViewModelProvider.Factory? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_lichess_connect, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        loadingSpinner = view.findViewById(R.id.lichessLoadingSpinner)
        groupNotLinked = view.findViewById(R.id.groupNotLinked)
        groupLinked = view.findViewById(R.id.groupLinked)
        usernameLayout = view.findViewById(R.id.lichessUsernameLayout)
        usernameField = view.findViewById(R.id.lichessUsernameField)
        btnLink = view.findViewById(R.id.btnLichessLink)
        btnImport = view.findViewById(R.id.btnLichessImport)
        btnUnlink = view.findViewById(R.id.btnLichessUnlink)
        linkedHandleText = view.findViewById(R.id.lichessLinkedHandle)
        calibrationBanner = view.findViewById(R.id.lichessCalibrationBanner)
        importedCountText = view.findViewById(R.id.lichessImportedCount)
        lastSyncedText = view.findViewById(R.id.lichessLastSyncedValue)
        importProgressBlock = view.findViewById(R.id.lichessImportProgressBlock)
        importProgressBar = view.findViewById(R.id.lichessImportProgressBar)
        importProgressCaption = view.findViewById(R.id.lichessImportProgressCaption)

        val factory = viewModelFactoryOverride ?: defaultFactory(requireContext())
        viewModel = ViewModelProvider(this, factory)[LichessConnectViewModel::class.java]

        viewModel.onStateChanged = { state -> renderState(state) }
        viewModel.onError = { kind -> surfaceError(kind) }

        btnLink.setOnClickListener {
            val raw = usernameField.text?.toString().orEmpty()
            usernameLayout.error = null
            viewModel.link(raw)
        }
        btnImport.setOnClickListener {
            viewModel.importGames()
        }
        btnUnlink.setOnClickListener {
            viewModel.unlink()
        }
        // refreshStatus is fired from onStart() (below) so it ALSO
        // runs when the user foregrounds the app without re-creating
        // the sheet view — that's the resume-on-reopen path that picks
        // up active_import_job_id from /lichess/status and rejoins the
        // determinate progress bar.
    }

    override fun onStart() {
        super.onStart()
        // Single source of truth for "view is visible → load latest state".
        // Fires on both first-open AND app-foreground-while-sheet-open.
        // If the player has no token (signed out), the ViewModel surfaces
        // UNAUTHENTICATED and the sheet dismisses — sheet requires an
        // authenticated session by construction.
        viewModel.refreshStatus()
    }

    override fun onStop() {
        // Pause the v2 import-job poll loop on background / sheet
        // dismiss so we don't hammer the server while the user isn't
        // looking (and so Doze doesn't punish us on resume).  The
        // server-side job continues independently; the next onStart()
        // fires refreshStatus() which rejoins via active_import_job_id.
        viewModel.pausePolling()
        super.onStop()
    }

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------

    private fun renderState(state: LichessConnectViewModel.UiState) {
        when (state) {
            is LichessConnectViewModel.UiState.Initial -> {
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = false
                groupLinked.isVisible = false
            }
            is LichessConnectViewModel.UiState.Loading -> {
                // Loading is an overlay — render the previous state
                // underneath so the sheet doesn't flash empty.
                renderState(state.previousState)
                loadingSpinner.isVisible = true
                setControlsEnabled(false)
            }
            is LichessConnectViewModel.UiState.NotLinked -> {
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = true
                groupLinked.isVisible = false
                setControlsEnabled(true)
            }
            is LichessConnectViewModel.UiState.Linked -> {
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = false
                groupLinked.isVisible = true
                setControlsEnabled(true)
                // Hide the v2 import-progress block when transitioning
                // out of Importing (or just landing on Linked fresh).
                importProgressBlock.isVisible = false
                btnImport.isVisible = true

                linkedHandleText.text = state.username
                importedCountText.text = state.importedGameCount.toString()
                lastSyncedText.text =
                    state.lastImportedAt?.let { formatTimestamp(it) }
                        ?: getString(R.string.lichess_never_synced)

                // One-shot calibration banner.
                val calibration = state.calibration
                if (calibration != null && calibration.applied) {
                    calibrationBanner.isVisible = true
                    calibrationBanner.text = formatCalibrationBanner(calibration)
                } else {
                    calibrationBanner.isVisible = false
                }

                // One-shot import summary toast.
                state.lastImportSummary?.let { surfaceImportSummary(it) }
            }
            is LichessConnectViewModel.UiState.Importing -> {
                // Render the surrounding Linked context (handle, counts)
                // so the user sees what's being imported into.
                val prior = state.previousLinked
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = false
                groupLinked.isVisible = true
                linkedHandleText.text = prior.username
                importedCountText.text = prior.importedGameCount.toString()
                lastSyncedText.text =
                    prior.lastImportedAt?.let { formatTimestamp(it) }
                        ?: getString(R.string.lichess_never_synced)
                calibrationBanner.isVisible = false

                // Swap the Import button for the determinate progress
                // block.  Unlink stays enabled so the user can cancel
                // the in-flight job by unlinking (server-side
                // unlink_account cancels the job).
                btnImport.isVisible = false
                importProgressBlock.isVisible = true
                btnImport.isEnabled = false
                btnLink.isEnabled = false
                btnUnlink.isEnabled = true

                // Bind progress values.  ``target`` is the request
                // cap, not the true game count (which we won't know
                // until the stream ends), hence "of up to N" in the
                // caption.  Clamp progress to [0, target] defensively
                // — the server should never send inserted > target,
                // but a transient race could push it over.
                val target = state.target.coerceAtLeast(1)
                val inserted = state.inserted.coerceIn(0, target)
                importProgressBar.max = target
                importProgressBar.progress = inserted

                val skipped = state.skippedDuplicate + state.skippedInvalid
                importProgressCaption.text = if (skipped == 0) {
                    getString(
                        R.string.lichess_import_progress_caption,
                        inserted,
                        target,
                    )
                } else {
                    getString(
                        R.string.lichess_import_progress_caption_with_skipped,
                        inserted,
                        target,
                        skipped,
                    )
                }
            }
            is LichessConnectViewModel.UiState.Error -> {
                // Error state preserves the previous state under the
                // toast.  The ViewModel already reverted to previousState
                // before invoking onError — we just re-render it.
                renderState(state.previousState)
            }
        }
    }

    private fun setControlsEnabled(enabled: Boolean) {
        btnLink.isEnabled = enabled
        btnImport.isEnabled = enabled
        btnUnlink.isEnabled = enabled
        usernameField.isEnabled = enabled
    }

    // ------------------------------------------------------------------
    // Error surfacing
    // ------------------------------------------------------------------

    private fun surfaceError(kind: LichessConnectViewModel.ErrorKind) {
        val ctx = context ?: return
        val message = when (kind) {
            LichessConnectViewModel.ErrorKind.UNAUTHENTICATED ->
                getString(R.string.lichess_error_unauthenticated)
            LichessConnectViewModel.ErrorKind.USERNAME_INVALID -> {
                usernameLayout.error = getString(R.string.lichess_error_username_invalid)
                return  // inline-only; no toast
            }
            LichessConnectViewModel.ErrorKind.USERNAME_NOT_FOUND ->
                getString(R.string.lichess_error_username_not_found)
            LichessConnectViewModel.ErrorKind.ALREADY_LINKED_TO_OTHER_PLAYER ->
                getString(R.string.lichess_error_already_linked)
            LichessConnectViewModel.ErrorKind.NOT_LINKED ->
                getString(R.string.lichess_error_not_linked)
            LichessConnectViewModel.ErrorKind.RATE_LIMITED ->
                getString(R.string.lichess_error_rate_limited)
            LichessConnectViewModel.ErrorKind.UPSTREAM ->
                getString(R.string.lichess_error_upstream)
            LichessConnectViewModel.ErrorKind.NETWORK ->
                getString(R.string.lichess_error_network)
            LichessConnectViewModel.ErrorKind.TIMEOUT ->
                getString(R.string.lichess_error_timeout)
            LichessConnectViewModel.ErrorKind.UNKNOWN ->
                getString(R.string.lichess_error_unknown)
        }
        Toast.makeText(ctx, message, Toast.LENGTH_LONG).show()
    }

    private fun surfaceImportSummary(summary: LichessImportResponse) {
        val ctx = context ?: return
        val msg = if (summary.inserted > 0) {
            getString(R.string.lichess_import_summary_inserted, summary.inserted)
        } else if (summary.skippedDuplicate > 0) {
            getString(R.string.lichess_import_summary_all_duplicates, summary.skippedDuplicate)
        } else {
            getString(R.string.lichess_import_summary_empty)
        }
        Toast.makeText(ctx, msg, Toast.LENGTH_SHORT).show()
    }

    // ------------------------------------------------------------------
    // Formatting helpers
    // ------------------------------------------------------------------

    private fun formatCalibrationBanner(c: LichessCalibrationResult): String {
        // We still require ``c.rating`` to be non-null before showing
        // anything — that's the marker for "a calibration was applied
        // to this player".  The value itself is no longer rendered:
        // the user-visible Elo display was hidden, so the banner now
        // just confirms the source perf + games-basis.
        c.rating ?: return ""
        val perf = c.perf ?: return ""
        val basis = c.gamesBasis ?: 0
        return if (c.provisional == true) {
            getString(R.string.lichess_calibration_banner_provisional, perf, basis)
        } else {
            getString(R.string.lichess_calibration_banner, perf, basis)
        }
    }

    private fun formatTimestamp(iso: String): String {
        // Backend returns ISO-8601 like "2026-05-13T08:28:57.755000".
        // For the row we just want the date — fine-grained time isn't
        // useful at the user-facing level.
        return iso.substringBefore("T")
    }

    // ------------------------------------------------------------------
    // ViewModel factory — production wiring
    // ------------------------------------------------------------------

    private fun defaultFactory(ctx: Context): ViewModelProvider.Factory {
        val authRepo = AuthRepository(EncryptedTokenStorage(ctx))
        val client: LichessApiClient = HttpLichessApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        return object : NewInstanceFactory() {
            @Suppress("UNCHECKED_CAST")
            override fun <T : androidx.lifecycle.ViewModel> create(modelClass: Class<T>): T {
                return LichessConnectViewModel(client, authRepo) as T
            }
        }
    }

    companion object {
        /** Tag used by [show] so duplicate sheets can't be opened. */
        const val TAG = "LichessConnectBottomSheet"
    }
}
package com.cereveon.myapp

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * State machine for the Lichess Connect bottom sheet.
 *
 * Lifecycle:
 *   - Sheet opens → [refreshStatus] → fetches GET /lichess/status →
 *     transitions to [Linked] or [NotLinked].
 *   - User taps "Link" with a username → [link] → POST /lichess/link →
 *     [Linked] (with optional calibration details).
 *   - User taps "Import games" → [importGames] → POST /lichess/import →
 *     [Linked] with the new counts merged in.
 *   - User taps "Unlink" → [unlink] → DELETE /lichess/link → [NotLinked].
 *
 * Surface invariants:
 *   - Loading is a pure-overlay state — the previous state is preserved
 *     in [previousState] so the UI can render skeleton bones on top of
 *     the last known data instead of flashing empty.
 *   - Errors are non-terminal: after surfacing the message, the
 *     callback re-emits the prior state so the sheet remains usable
 *     (mirrors the toast-on-failure pattern in SettingsBottomSheet
 *     when PATCH /auth/me fails).
 *
 * Callback shape (`onStateChanged`) matches [ChessViewModel]'s
 * mutable-state + callback convention rather than StateFlow — the
 * codebase is callback-first; introducing reactive streams just for
 * this screen would be churn.
 */
class LichessConnectViewModel(
    private val client: LichessApiClient,
    private val authRepository: AuthRepository,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {

    /**
     * Sealed state model.
     *
     * - [Initial]    — sheet opened; no fetch yet.
     * - [Loading]    — fetch in flight.  [previousState] is whatever was
     *                  visible before the request so the UI can render
     *                  it under a spinner.
     * - [NotLinked]  — no Lichess link for this player.  Form is shown.
     * - [Linked]     — link present.  Status fields populated.  Optional
     *                  [calibration] is non-null only on the
     *                  immediately-post-link transition so the sheet can
     *                  display "Rating set to 1907 from rapid" once.
     * - [Error]      — last operation failed.  Carries a user-presentable
     *                  message.  [previousState] holds whatever to revert
     *                  to once the user dismisses the toast.
     */
    sealed class UiState {
        object Initial : UiState()
        data class Loading(val previousState: UiState) : UiState()
        object NotLinked : UiState()
        data class Linked(
            val username: String,
            val linkedAt: String? = null,
            val lastImportedAt: String? = null,
            val importedGameCount: Int = 0,
            val calibration: LichessCalibrationResult? = null,
            val lastImportSummary: LichessImportResponse? = null,
        ) : UiState()
        /**
         * v2 import in flight.  [target] is the request cap (denominator
         * for the progress bar); [inserted] is the live counter.  The
         * UI renders ``inserted / target`` with a `<` sign when not
         * terminal since the true total games could be less than
         * [target] (we don't know up-front).
         *
         * [previousLinked] is the state the sheet should revert to if
         * the user dismisses + the job ends without the sheet seeing
         * the terminal poll (only relevant for short edge cases — the
         * normal terminal path overwrites this state with the merged
         * Linked).
         */
        data class Importing(
            val jobId: String,
            val inserted: Int,
            val target: Int,
            val skippedDuplicate: Int,
            val skippedInvalid: Int,
            val previousLinked: Linked,
        ) : UiState()
        data class Error(val message: String, val previousState: UiState) : UiState()
    }

    /**
     * Operation-level error categories.  The Fragment maps these to
     * concrete user-facing strings so the ViewModel stays
     * resource-free (host-JVM unit-testable).
     */
    enum class ErrorKind {
        UNAUTHENTICATED,
        USERNAME_INVALID,
        USERNAME_NOT_FOUND,
        ALREADY_LINKED_TO_OTHER_PLAYER,
        NOT_LINKED,
        RATE_LIMITED,
        UPSTREAM,
        NETWORK,
        TIMEOUT,
        UNKNOWN,
    }

    private var current: UiState = UiState.Initial

    /**
     * Reference to the currently-active v2 import poll loop, if any.
     * Cancelled before any new long-lived state transition (link /
     * unlink / re-import / explicit refresh) so we never have two
     * polling coroutines for the same player.  ``viewModelScope`` would
     * cancel it on Fragment dismiss anyway; this field handles the
     * intra-lifecycle case.
     */
    private var importPollJob: Job? = null

    /** Latest UiState; primarily for test assertions. */
    val state: UiState get() = current

    /**
     * Invoked on every state transition.  The Fragment binds this in
     * onViewCreated and re-renders.  Single-listener by design: there
     * is exactly one bottom sheet at a time.
     */
    var onStateChanged: ((UiState) -> Unit)? = null

    /**
     * Invoked when an operation fails so the Fragment can surface a
     * Toast / inline error string.  Mapped from the underlying HTTP
     * or transport error.
     */
    var onError: ((ErrorKind) -> Unit)? = null

    // ----------------------------------------------------------------------
    // Public API — Fragment calls these in response to user input
    // ----------------------------------------------------------------------

    fun refreshStatus() {
        viewModelScope.launch { performStatus() }
    }

    fun link(username: String) {
        viewModelScope.launch { performLink(username) }
    }

    fun importGames(maxGames: Int = LichessApiClient.DEFAULT_MAX_IMPORT) {
        viewModelScope.launch { performImport(maxGames) }
    }

    fun unlink() {
        viewModelScope.launch { performUnlink() }
    }

    /**
     * Cancel any in-flight v2 import-job poll loop.
     *
     * Called by the Fragment from ``onStop()`` so that polling pauses
     * when the app is backgrounded — Doze mode would otherwise hammer
     * the server with bursts on resume.  The server-side job continues
     * independently; the next ``refreshStatus()`` call (typically fired
     * from the Fragment's ``onStart()``) reattaches via the
     * ``active_import_job_id`` field on ``/lichess/status``.
     *
     * Safe to call when no poll is in flight (no-op).  Leaves [state]
     * untouched — the UI keeps rendering the last-seen [UiState.Importing]
     * counters until refresh.
     */
    fun pausePolling() {
        importPollJob?.cancel()
        importPollJob = null
    }

    // ----------------------------------------------------------------------
    // Operation handlers
    // ----------------------------------------------------------------------

    private suspend fun performStatus() {
        val token = requireToken() ?: return
        // Any leftover poll from a prior view-binding must not race with
        // the resume path we may kick off below.
        importPollJob?.cancel()
        importPollJob = null

        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) { client.status(token) }) {
            is ApiResult.Success -> {
                val s = result.data
                if (!s.linked) {
                    transitionTo(UiState.NotLinked)
                    return
                }
                val linked = UiState.Linked(
                    username = s.externalUsername ?: "",
                    linkedAt = s.linkedAt,
                    lastImportedAt = s.lastImportedAt,
                    importedGameCount = s.importedGameCount,
                )
                transitionTo(linked)

                // v2 resume: if the server reports an in-flight import
                // for this player, fetch the job once and either rejoin
                // the progress view (running) or render the terminal
                // counters once (already finished by the time we
                // returned).  Either way, polling continues for
                // non-terminal results.
                val activeJobId = s.activeImportJobId
                if (activeJobId != null) {
                    when (val jobResult = withContext(ioDispatcher) {
                        client.getImportJob(activeJobId, token)
                    }) {
                        is ApiResult.Success -> applyJobSnapshot(
                            jobId = activeJobId,
                            status = jobResult.data.status,
                            inserted = jobResult.data.inserted,
                            skippedDuplicate = jobResult.data.skippedDuplicate,
                            skippedInvalid = jobResult.data.skippedInvalid,
                            target = jobResult.data.targetMaxGames,
                            priorLinked = linked,
                            startPolling = true,
                        )
                        // Failure modes (404 / transport / 5xx) on the
                        // resume GET leave the user in the Linked state
                        // we already transitioned to.  No retry — the
                        // user can tap Import again or pull-to-refresh.
                        else -> {}
                    }
                }
            }
            is ApiResult.HttpError -> surfaceHttpError(result.code, "status")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    private suspend fun performLink(username: String) {
        val trimmed = username.trim()
        if (!isValidUsername(trimmed)) {
            surfaceErrorKind(ErrorKind.USERNAME_INVALID)
            return
        }
        val token = requireToken() ?: return
        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) { client.link(trimmed, token) }) {
            is ApiResult.Success -> {
                val data = result.data
                transitionTo(
                    UiState.Linked(
                        username = data.externalUsername,
                        linkedAt = data.linkedAt,
                        lastImportedAt = null,
                        importedGameCount = 0,
                        calibration = data.calibration,
                    )
                )
            }
            is ApiResult.HttpError -> surfaceHttpError(result.code, "link")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    private suspend fun performImport(maxGames: Int) {
        val token = requireToken() ?: return
        val priorLinked = current as? UiState.Linked
        if (priorLinked == null) {
            surfaceErrorKind(ErrorKind.NOT_LINKED)
            return
        }
        // A stale poll from a prior import would otherwise keep flipping
        // counters under our feet; cancel before starting a new one.
        importPollJob?.cancel()
        importPollJob = null

        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) {
            client.startImport(token = token, maxGames = maxGames)
        }) {
            is ApiResult.Success -> {
                val accepted = result.data
                applyJobSnapshot(
                    jobId = accepted.jobId,
                    status = accepted.status,
                    inserted = accepted.inserted,
                    skippedDuplicate = accepted.skippedDuplicate,
                    skippedInvalid = accepted.skippedInvalid,
                    target = accepted.targetMaxGames,
                    priorLinked = priorLinked,
                    startPolling = true,
                )
            }
            is ApiResult.HttpError -> surfaceHttpError(result.code, "import")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    /**
     * Render a job-state snapshot into the UiState machine.
     *
     * Two callers: [performImport] (initial POST response) and the
     * poll loop in [startImportPoll].  Terminal statuses end the
     * polling (caller checks the return); non-terminal transitions
     * to [UiState.Importing] and conditionally schedules the next
     * poll tick.
     *
     * Returns ``true`` if the job is terminal (caller should stop
     * polling), ``false`` if polling should continue.
     */
    private fun applyJobSnapshot(
        jobId: String,
        status: String,
        inserted: Int,
        skippedDuplicate: Int,
        skippedInvalid: Int,
        target: Int,
        priorLinked: UiState.Linked,
        startPolling: Boolean,
    ): Boolean {
        when (status) {
            LichessImportJobStatus.STATUS_SUCCEEDED -> {
                // Merge counts into the prior Linked state.  The
                // ``lastImportedAt`` ISO string is NOT refreshed here
                // (the v2 payload carries Unix ms, not ISO); the next
                // refreshStatus() call will pull the canonical value
                // from the server.  This keeps the response shape
                // boundary clean.
                transitionTo(
                    priorLinked.copy(
                        importedGameCount = priorLinked.importedGameCount + inserted,
                        lastImportSummary = LichessImportResponse(
                            inserted = inserted,
                            skippedDuplicate = skippedDuplicate,
                            skippedInvalid = skippedInvalid,
                            lastImportedAt = priorLinked.lastImportedAt,
                        ),
                        calibration = null,
                    )
                )
                return true
            }
            LichessImportJobStatus.STATUS_FAILED -> {
                // Revert + surface error.  Use ``UPSTREAM`` because the
                // job failure is a server-side problem (Lichess upstream,
                // unlink-mid-import, etc.) rather than a transport issue.
                current = priorLinked
                onStateChanged?.invoke(priorLinked)
                onError?.invoke(ErrorKind.UPSTREAM)
                return true
            }
            else -> {
                transitionTo(
                    UiState.Importing(
                        jobId = jobId,
                        inserted = inserted,
                        target = target,
                        skippedDuplicate = skippedDuplicate,
                        skippedInvalid = skippedInvalid,
                        previousLinked = priorLinked,
                    )
                )
                if (startPolling) {
                    startImportPoll(jobId, priorLinked)
                }
                return false
            }
        }
    }

    private fun startImportPoll(jobId: String, priorLinked: UiState.Linked) {
        importPollJob?.cancel()
        importPollJob = viewModelScope.launch {
            while (isActive) {
                delay(POLL_INTERVAL_MS)
                val token = authRepository.getToken() ?: return@launch
                val result = withContext(ioDispatcher) { client.getImportJob(jobId, token) }
                when (result) {
                    is ApiResult.Success -> {
                        val job = result.data
                        val terminal = applyJobSnapshot(
                            jobId = jobId,
                            status = job.status,
                            inserted = job.inserted,
                            skippedDuplicate = job.skippedDuplicate,
                            skippedInvalid = job.skippedInvalid,
                            target = job.targetMaxGames,
                            priorLinked = priorLinked,
                            // Already polling — do not nest a second
                            // coroutine for the same job.
                            startPolling = false,
                        )
                        if (terminal) return@launch
                    }
                    is ApiResult.HttpError -> {
                        if (result.code == 404) {
                            // Job vanished server-side (janitor swept
                            // it, or owner check failed).  Revert
                            // silently — there's no progress to show
                            // and no error worth surfacing.
                            current = priorLinked
                            onStateChanged?.invoke(priorLinked)
                            return@launch
                        }
                        // 5xx or other — keep polling.  The next tick
                        // may succeed.  No user-visible noise.
                    }
                    is ApiResult.NetworkError, ApiResult.Timeout -> {
                        // Transient; the user may have lost connection
                        // briefly.  Keep polling.
                    }
                }
            }
        }
    }

    private suspend fun performUnlink() {
        val token = requireToken() ?: return
        // Unlink also cancels any server-side job for this player
        // (see ``unlink_account`` in llm/seca/lichess/import_service.py).
        // Stop our poll loop locally so the next tick doesn't surface
        // a transient ``failed`` flash before NotLinked lands.
        importPollJob?.cancel()
        importPollJob = null

        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) { client.unlink(token) }) {
            is ApiResult.Success -> transitionTo(UiState.NotLinked)
            is ApiResult.HttpError -> surfaceHttpError(result.code, "unlink")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    private fun requireToken(): String? {
        val token = authRepository.getToken()
        if (token == null) {
            surfaceErrorKind(ErrorKind.UNAUTHENTICATED)
            return null
        }
        return token
    }

    private fun transitionTo(next: UiState) {
        current = next
        onStateChanged?.invoke(next)
    }

    private fun surfaceErrorKind(kind: ErrorKind) {
        // Revert any in-flight Loading state so the sheet doesn't get
        // stuck on the spinner if onError is the only listener.
        val revertTarget = when (val c = current) {
            is UiState.Loading -> c.previousState
            else -> c
        }
        current = revertTarget
        onStateChanged?.invoke(revertTarget)
        onError?.invoke(kind)
    }

    private fun surfaceHttpError(code: Int, operation: String) {
        val kind = when (operation) {
            "link" -> when (code) {
                400 -> ErrorKind.USERNAME_INVALID
                401 -> ErrorKind.UNAUTHENTICATED
                404 -> ErrorKind.USERNAME_NOT_FOUND
                409 -> ErrorKind.ALREADY_LINKED_TO_OTHER_PLAYER
                502 -> ErrorKind.UPSTREAM
                503 -> ErrorKind.RATE_LIMITED
                else -> ErrorKind.UNKNOWN
            }
            "import" -> when (code) {
                400 -> ErrorKind.NOT_LINKED
                401 -> ErrorKind.UNAUTHENTICATED
                502 -> ErrorKind.UPSTREAM
                503 -> ErrorKind.RATE_LIMITED
                else -> ErrorKind.UNKNOWN
            }
            else -> when (code) {
                401 -> ErrorKind.UNAUTHENTICATED
                502 -> ErrorKind.UPSTREAM
                503 -> ErrorKind.RATE_LIMITED
                else -> ErrorKind.UNKNOWN
            }
        }
        surfaceErrorKind(kind)
    }

    companion object {
        /**
         * Same shape Lichess accepts (mirrored from the backend
         * ``_LICHESS_USERNAME_RE`` and the server-side client guard).
         * Pre-validating client-side gives instant feedback and saves
         * a round-trip for obviously-malformed input.
         */
        private val USERNAME_RE = Regex("^[A-Za-z0-9_-]{2,30}$")

        /**
         * Poll cadence for the v2 import job — 2s.  Server rate limits
         * the GET at 120/min (60/min headroom over the steady-state
         * 30/min this produces).  The Fragment is expected to gate the
         * lifecycle so polling pauses when the app backgrounds; see
         * the bottom-sheet rendering layer.  ``internal`` so tests can
         * read the value to compute deterministic time advances.
         */
        internal const val POLL_INTERVAL_MS: Long = 2_000L

        fun isValidUsername(username: String): Boolean =
            USERNAME_RE.matches(username)
    }
}
package com.cereveon.myapp

import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * Pure-JVM helper for the Lichess OAuth 2.0 authorization-code + PKCE
 * flow (RFC 7636) behind the "Sign in with Lichess" button on
 * [LoginActivity].
 *
 * Division of labour (mirrors `docs/API_CONTRACTS.md` §16a):
 *
 *  - THIS side generates the `code_verifier`, derives the S256
 *    `code_challenge`, and opens the system browser at
 *    [AUTHORIZE_ENDPOINT] with the pinned [CLIENT_ID] / [REDIRECT_URI].
 *  - Lichess redirects to `com.cereveon.myapp://lichess-auth?code&state`,
 *    which [LichessAuthRedirectActivity] forwards to [LoginActivity].
 *  - The BACKEND performs the code exchange (`POST /auth/lichess` with
 *    `code` + `code_verifier`) so Lichess access tokens never live on
 *    the device.
 *
 * [CLIENT_ID] and [REDIRECT_URI] must byte-match the server constants in
 * `llm/seca/lichess/client.py` — Lichess accepts unregistered public
 * clients, so no upstream registration exists to catch a drift; the
 * exchange just fails with `invalid_grant`.
 *
 * No `scope` parameter is sent: the sign-in needs public identity only.
 *
 * Deliberately dependency-free (no AppAuth): `java.util.Base64` (minSdk
 * 26) + `java.security` keep every function host-JVM testable — see
 * `LichessOAuthTest` for the RFC 7636 Appendix B vector pin.
 */
object LichessOAuth {

    const val AUTHORIZE_ENDPOINT = "https://lichess.org/oauth"
    const val CLIENT_ID = "com.cereveon.myapp"
    const val REDIRECT_URI = "com.cereveon.myapp://lichess-auth"

    /** Scheme + host of [REDIRECT_URI], split for intent-data matching. */
    const val REDIRECT_SCHEME = "com.cereveon.myapp"
    const val REDIRECT_HOST = "lichess-auth"

    // 64 random bytes → 86 base64url chars, comfortably inside the RFC
    // 7636 §4.1 verifier bounds (43–128) with ~512 bits of entropy.
    private const val VERIFIER_BYTES = 64

    // The `state` value only guards the redirect against CSRF/mix-up;
    // 32 bytes (43 chars) is ample.
    private const val STATE_BYTES = 32

    /** Fresh RFC 7636 code verifier (base64url, unpadded, 86 chars). */
    fun generateCodeVerifier(random: SecureRandom = SecureRandom()): String =
        randomUrlSafe(VERIFIER_BYTES, random)

    /** Fresh opaque `state` value for the authorization request. */
    fun generateState(random: SecureRandom = SecureRandom()): String =
        randomUrlSafe(STATE_BYTES, random)

    /**
     * S256 code challenge: `BASE64URL-ENCODE(SHA256(ASCII(verifier)))`
     * per RFC 7636 §4.2 — no padding.
     */
    fun codeChallengeS256(codeVerifier: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(codeVerifier.toByteArray(Charsets.US_ASCII))
        return Base64.getUrlEncoder().withoutPadding().encodeToString(digest)
    }

    /**
     * Full authorization URL for the system browser.
     *
     * Built with plain string concatenation + [URLEncoder] (not
     * `android.net.Uri`) so the function stays host-JVM testable.  Every
     * value is URL-encoded defensively even though the generated ones
     * are already URL-safe base64.
     */
    fun buildAuthorizeUrl(codeChallenge: String, state: String): String =
        AUTHORIZE_ENDPOINT +
            "?response_type=code" +
            "&client_id=${encode(CLIENT_ID)}" +
            "&redirect_uri=${encode(REDIRECT_URI)}" +
            "&code_challenge_method=S256" +
            "&code_challenge=${encode(codeChallenge)}" +
            "&state=${encode(state)}"

    private fun randomUrlSafe(byteCount: Int, random: SecureRandom): String {
        val bytes = ByteArray(byteCount)
        random.nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8")
}
package com.cereveon.myapp

import kotlinx.serialization.encodeToString

/**
 * Client for POST /live/move (server.py).
 *
 * Returns a [LiveMoveResponse] containing a per-move coaching hint from the
 * backend live coaching pipeline.  The hint always references the engine
 * evaluation band, game phase, and move quality.
 *
 * The server route is gated by `Depends(get_current_player)` (see
 * `llm/server.py::live_move`), so a valid `Authorization: Bearer <jwt>`
 * header is required on every call.  X-Api-Key is accepted by the
 * upstream proxy / shared dependency chain but does not by itself
 * satisfy `/live/move` — without a Bearer the route returns
 * 401 "Missing token" and no Mode-1 hint is ever produced.
 * Implementations are safe to call from any coroutine context.
 */
interface LiveMoveClient {

    /**
     * Fetch a coaching hint for the move just played.
     *
     * @param fen        Board position after the move in FEN notation.
     * @param uci        The move just played in UCI notation (e.g. "e2e4").
     * @param playerId   Player identifier (reserved for future enrichment).
     * @param fenBefore  Board position BEFORE the move, so the server can grade
     *                   move quality from the eval swing.  Null (default) omits
     *                   it; the server then leaves move quality "unknown".
     * @return [ApiResult.Success] with a [LiveMoveResponse] on HTTP 200;
     *         [ApiResult.HttpError] on non-200; [ApiResult.Timeout] on deadline
     *         exceeded; [ApiResult.NetworkError] for all other failures.
     */
    suspend fun getLiveCoaching(
        fen: String,
        uci: String,
        playerId: String = "demo",
        fenBefore: String? = null,
        /**
         * Server game id (``games.id``) for the free-tier coached-game
         * admission — see [LiveMoveRequest.gameId].  Null keeps today's
         * behaviour (server fails open, never degrades).
         */
        gameId: String? = null,
    ): ApiResult<LiveMoveResponse>
}

/**
 * Production implementation of [LiveMoveClient] backed by [HttpURLConnection].
 *
 * Each call opens its own connection; the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 * @param tokenProvider    Supplier of the JWT Bearer token for `/live/move`.
 *                         The route is `Depends(get_current_player)` on the
 *                         server (llm/server.py::live_move), so it returns
 *                         401 "Missing token" without an `Authorization:
 *                         Bearer <jwt>` header — silently breaking the
 *                         Mode-1 inline hint for every authenticated user.
 *                         Required (no default) on purpose: sister clients
 *                         default this to `null`, but `/live/move` is the
 *                         only one where a missing Bearer is a *certain*
 *                         401, so we force every callsite to make an
 *                         explicit choice and re-introduce the original
 *                         bug only by writing `tokenProvider = { null }`
 *                         on purpose.  Pass `{ null }` for the logged-out
 *                         window or in tests that don't assert on auth;
 *                         the server's 401 branch is handled cleanly via
 *                         `ApiResult.HttpError(401)` in
 *                         `ChessViewModel.dispatchHumanMoveCoach`.
 * @param tokenSink        Optional sink for the X-Auth-Token refresh header.
 *                         The `/live/move` route depends on `get_current_player`
 *                         (llm/server.py — `Depends(get_current_player)`), so the
 *                         server attaches a freshly-minted JWT to every 200
 *                         response.  Wiring [tokenSink] lets a long live-coach
 *                         session rotate the stored JWT continuously, instead of
 *                         dropping the rotation header on the floor and forcing
 *                         a re-login at the 24 h JWT exp.  See
 *                         docs/API_CONTRACTS.md §10 (`X-Auth-Token` refresh
 *                         header) and [TokenRefresh] for the helper.
 *                         Default `null` preserves existing callers; pass a
 *                         non-null sink to participate in rotation.
 */
class HttpLiveMoveClient(
    val baseUrl: String,
    val apiKey: String,
    val tokenProvider: () -> String?,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : LiveMoveClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val LIVE_MOVE_PATH = "/live/move"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun getLiveCoaching(
        fen: String,
        uci: String,
        playerId: String,
        fenBefore: String?,
        gameId: String?,
    ): ApiResult<LiveMoveResponse> = http.request(
        path = LIVE_MOVE_PATH,
        method = "POST",
        headers = buildMap {
            put("X-Api-Key", apiKey)
            tokenProvider.invoke()?.let { put("Authorization", "Bearer $it") }
        },
        body = ApiJson.encodeToString(
            LiveMoveRequest(
                fen = fen,
                uci = uci,
                playerId = playerId,
                fenBefore = fenBefore,
                gameId = gameId,
            )
        ),
        onResponse = { conn -> consumeRefreshedToken(conn, tokenSink) },
        parse = { body -> ApiJson.decodeFromString<LiveMoveResponse>(body) },
    )
}
package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Request/response models for POST /live/move (server.py).
 *
 * The endpoint requires X-Api-Key authentication.
 * Sprint 4.3.C migrated these off hand-rolled ``org.json.JSONObject``
 * parsing onto kotlinx-serialization — see [HttpLiveMoveClient].
 *
 * Schema documented in docs/API_CONTRACTS.md §5.
 */

/**
 * Request body for POST /live/move.
 *
 * [fen]        Board position after the move in FEN notation or "startpos".
 * [uci]        The move just played in UCI notation (e.g. "e2e4", "e7e8q").
 * [playerId]   Player identifier; reserved for future profile enrichment.
 * [fenBefore]  Board position BEFORE the move.  Lets the server grade move
 *              quality from the eval swing fen_before -> fen (it can't
 *              reconstruct the pre-move position from the post-move FEN — a
 *              capture/en-passant/promotion loses the captured piece).  Null
 *              (the default) is dropped from the wire by ``encodeDefaults=false``
 *              so the server falls back to move_quality "unknown", the
 *              pre-feature behaviour.
 */
@Serializable
data class LiveMoveRequest(
    val fen: String,
    val uci: String,
    @SerialName("player_id") val playerId: String = "demo",
    @SerialName("fen_before") val fenBefore: String? = null,
    // Distinct-game key for the free-tier entitlements admission
    // (API_CONTRACTS.md §4): the server meters LLM-coached GAMES per
    // day, not moves, keyed on this id.  Null (older flows / no server
    // game yet) is dropped from the wire by ``encodeDefaults=false``
    // and the server fails OPEN — the hint stays on the LLM path.
    @SerialName("game_id") val gameId: String? = null,
)

/**
 * Entitlements posture attached to POST /live/move responses
 * (API_CONTRACTS.md §4, additive 2026-07).
 *
 * [plan]       "free" / "pro".
 * [degraded]   True when this hint came from the deterministic coach
 *              because the game is over the plan's daily coached-game
 *              quota — the UI shows its upgrade/limit chip.  Engine
 *              analysis is unaffected; only the hint source changes.
 * [remaining]  Distinct coached games left today; null while metering
 *              is dormant ("not metered", distinct from 0).
 */
@Serializable
data class CoachTierDto(
    val plan: String = "free",
    val degraded: Boolean = false,
    val remaining: Int? = null,
)

/**
 * Response from POST /live/move.
 *
 * [status]       Always "ok" on success.
 * [hint]         Coaching hint referencing engine evaluation, phase, and move quality.
 * [moveQuality]  last_move_quality from the engine signal ("best", "blunder", etc.).
 * [mode]         Always "LIVE_V1" for this pipeline version.
 * [engineSignal] Structured evaluation context from the backend engine signal;
 *                null when absent or unparseable.  Matches [EngineSignalDto] from
 *                the /chat response so the same display logic can be reused.
 */
@Serializable
data class LiveMoveResponse(
    val status: String = "ok",
    val hint: String = "",
    @SerialName("move_quality") val moveQuality: String = "unknown",
    val mode: String = "LIVE_V1",
    @SerialName("engine_signal") val engineSignal: EngineSignalDto? = null,
    // Null when the server pre-dates entitlements (ignoreUnknownKeys
    // covers the reverse direction) — treated as "not metered".
    @SerialName("coach_tier") val coachTier: CoachTierDto? = null,
)
package com.cereveon.myapp

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch

/**
 * Login screen that authenticates the user against the coach backend.
 *
 * Flow:
 *  1. User enters email + password and taps Sign In or Create Account.
 *  2. [AuthApiClient.login] / [AuthApiClient.register] is called; on success
 *     the JWT is stored in [AuthRepository] (backed by [EncryptedSharedPreferences]).
 *  3. Routing:
 *     - Login success → [HomeActivity] (or [OnboardingActivity] if a prior
 *       registration was abandoned before the calibration step completed).
 *     - Registration success → [OnboardingActivity] for skill calibration
 *       (handoff #2), then on Continue → [HomeActivity].
 *
 * "Sign in with Lichess" (OAuth PKCE — `docs/API_CONTRACTS.md` §16a):
 *  1. Tap → [startLichessSignIn] mints a PKCE verifier + `state`, persists
 *     them in app-private prefs (they must survive process death while the
 *     browser is foregrounded), and opens the system browser at the
 *     Lichess authorize URL ([LichessOAuth.buildAuthorizeUrl]).
 *  2. Lichess redirects to `com.cereveon.myapp://lichess-auth`;
 *     [LichessAuthRedirectActivity] forwards the URI here (onNewIntent
 *     when this instance is still alive, onCreate data after death).
 *  3. [handleLichessRedirect] verifies `state` against the persisted
 *     value, then [performLichessLogin] posts the one-time code + verifier
 *     to the backend, which does the code exchange server-side and
 *     returns the same JWT shape as password login → [launchPostAuth].
 *
 * Token expiry: [AuthRepository.isLoggedIn] checks the `exp` claim; if the
 * stored token is already expired when the app starts, [MainActivity] /
 * [HomeActivity] redirect back here automatically.
 */
class LoginActivity : AppCompatActivity() {

    companion object {
        /**
         * App-private prefs holding the in-flight OAuth attempt.  Plain
         * (not encrypted) SharedPreferences is deliberate: the verifier
         * is single-use, worthless without the matching one-time code
         * Lichess issues to OUR redirect, and cleared the moment the
         * redirect resolves — while surviving process death during the
         * browser round-trip, which is the property that matters.
         */
        private const val LICHESS_OAUTH_PREFS = "lichess_oauth"
        private const val KEY_PENDING_VERIFIER = "pending_code_verifier"
        private const val KEY_PENDING_STATE = "pending_state"
    }

    private lateinit var etEmail: TextInputEditText
    private lateinit var etPassword: TextInputEditText
    private lateinit var btnLogin: Button
    private lateinit var btnRegister: Button
    private lateinit var btnLichess: Button
    private lateinit var tvError: TextView
    private lateinit var progressBar: ProgressBar

    private val authApiClient: AuthApiClient by lazy {
        HttpAuthApiClient(baseUrl = BuildConfig.COACH_API_BASE)
    }

    private val authRepository: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // If already logged in, go straight to the game — avoid showing the
        // login form when the user re-opens the app with a valid session.
        if (authRepository.isLoggedIn()) {
            launchPostAuth()
            return
        }

        setContentView(R.layout.activity_login)

        etEmail = findViewById(R.id.etEmail)
        etPassword = findViewById(R.id.etPassword)
        btnLogin = findViewById(R.id.btnLogin)
        btnRegister = findViewById(R.id.btnRegister)
        btnLichess = findViewById(R.id.btnLichess)
        tvError = findViewById(R.id.tvError)
        progressBar = findViewById(R.id.progressBar)

        btnLogin.setOnClickListener {
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()

            if (email.isEmpty() || password.isEmpty()) {
                showError("Please enter your email and password.")
                return@setOnClickListener
            }

            performLogin(email, password)
        }

        btnRegister.setOnClickListener {
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()

            if (email.isEmpty() || password.isEmpty()) {
                showError("Please enter an email and password to create an account.")
                return@setOnClickListener
            }

            performRegister(email, password)
        }

        btnLichess.setOnClickListener { startLichessSignIn() }

        // Cold-start delivery of an OAuth redirect (LichessAuthRedirectActivity
        // relaunched us after process death while the browser was up).
        handleLichessRedirect(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // Warm delivery: the redirect trampoline reached the existing
        // instance via CLEAR_TOP | SINGLE_TOP.
        handleLichessRedirect(intent)
    }

    // ---------------------------------------------------------------------------
    // Login logic
    // ---------------------------------------------------------------------------

    private fun performLogin(email: String, password: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.login(email, password)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    launchPostAuth()
                }

                is ApiResult.HttpError -> {
                    val message =
                        if (result.code == 401) {
                            "Invalid email or password."
                        } else {
                            "Server error (${result.code}). Please try again."
                        }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    private fun performRegister(email: String, password: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        btnRegister.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.register(email, password)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    launchOnboarding()
                }

                is ApiResult.HttpError -> {
                    val message = when (result.code) {
                        409 -> "An account with this email already exists."
                        else -> "Registration failed (${result.code}). Please try again."
                    }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Sign in with Lichess (OAuth authorization-code + PKCE)
    // ---------------------------------------------------------------------------

    /**
     * Mint fresh PKCE material, persist it, and hand the user to the
     * system browser for the Lichess consent screen.
     *
     * The verifier + state are written with [android.content.SharedPreferences.Editor.commit]
     * (not `apply`) because the browser takes over the foreground
     * immediately — an async write racing process death would strand the
     * redirect with no verifier to exchange.
     */
    private fun startLichessSignIn() {
        val verifier = LichessOAuth.generateCodeVerifier()
        val state = LichessOAuth.generateState()
        getSharedPreferences(LICHESS_OAUTH_PREFS, MODE_PRIVATE)
            .edit()
            .putString(KEY_PENDING_VERIFIER, verifier)
            .putString(KEY_PENDING_STATE, state)
            .commit()

        val url = LichessOAuth.buildAuthorizeUrl(
            codeChallenge = LichessOAuth.codeChallengeS256(verifier),
            state = state,
        )
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: ActivityNotFoundException) {
            clearPendingLichessAuth()
            showError("No browser available to open Lichess.")
        }
    }

    /**
     * Consume a Lichess OAuth redirect if (and only if) this intent
     * carries one and a sign-in attempt is actually pending.
     *
     * Silent no-ops (by design):
     *  - intents without our `com.cereveon.myapp://lichess-auth` data —
     *    every normal launcher start lands here;
     *  - redirects with no pending verifier — a replayed / stale intent
     *    (e.g. re-delivery on configuration change after the attempt
     *    already resolved) has nothing to exchange, and any hostile app
     *    firing forged redirect intents at us learns nothing.
     */
    private fun handleLichessRedirect(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme != LichessOAuth.REDIRECT_SCHEME || data.host != LichessOAuth.REDIRECT_HOST) {
            return
        }

        val prefs = getSharedPreferences(LICHESS_OAUTH_PREFS, MODE_PRIVATE)
        val pendingVerifier = prefs.getString(KEY_PENDING_VERIFIER, null)
        val pendingState = prefs.getString(KEY_PENDING_STATE, null)
        if (pendingVerifier == null || pendingState == null) {
            return
        }

        // The user backed out on the consent screen (?error=access_denied)
        // or Lichess reported another authorize-stage failure.
        if (data.getQueryParameter("error") != null) {
            clearPendingLichessAuth()
            showError("Lichess sign-in was cancelled.")
            return
        }

        val code = data.getQueryParameter("code")
        val state = data.getQueryParameter("state")
        if (code.isNullOrEmpty() || state != pendingState) {
            // State mismatch = this redirect does not belong to the flow WE
            // started (CSRF / injected intent).  Burn the pending attempt.
            clearPendingLichessAuth()
            showError("Lichess sign-in failed. Please try again.")
            return
        }

        clearPendingLichessAuth()
        performLichessLogin(code, pendingVerifier)
    }

    private fun performLichessLogin(code: String, codeVerifier: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        btnRegister.isEnabled = false
        btnLichess.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.loginWithLichess(code, codeVerifier)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    launchPostAuth()
                }

                is ApiResult.HttpError -> {
                    val message = when (result.code) {
                        // The one-time code was rejected (expired / replayed /
                        // verifier mismatch) — restarting the flow mints a
                        // fresh one.
                        401 -> "Lichess sign-in failed. Please try again."
                        503 -> "Lichess is busy right now. Please try again shortly."
                        else -> "Server error (${result.code}). Please try again."
                    }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    private fun clearPendingLichessAuth() {
        getSharedPreferences(LICHESS_OAUTH_PREFS, MODE_PRIVATE)
            .edit()
            .remove(KEY_PENDING_VERIFIER)
            .remove(KEY_PENDING_STATE)
            .apply()
    }

    private fun showError(message: String) {
        progressBar.visibility = View.GONE
        btnLogin.isEnabled = true
        btnRegister.isEnabled = true
        btnLichess.isEnabled = true
        tvError.text = message
        tvError.visibility = View.VISIBLE
    }

    /**
     * Decide where an authenticated user lands.  Newly-registered users that
     * abandoned the calibration flow (or any future case where onboarding is
     * incomplete) get routed through [OnboardingActivity] first; everyone else
     * goes to [HomeActivity], the post-auth landing.
     */
    private fun launchPostAuth() {
        if (OnboardingActivity.isCompleted(this)) {
            launchHome()
        } else {
            launchOnboarding()
        }
    }

    private fun launchHome() {
        startActivity(
            Intent(this, HomeActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }

    private fun launchOnboarding() {
        // Enter at step 1/3 (Welcome).  Step 2/3 (calibration) and
        // step 3/3 (completion) are reached by the Begin / Continue
        // buttons on each screen; HomeActivity is the post-onboarding
        // landing.
        startActivity(
            Intent(this, OnboardingWelcomeActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }
}
package com.cereveon.myapp

import android.app.AlertDialog
import android.app.Dialog
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.util.Log
import android.view.GestureDetector
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.animation.AlphaAnimation
import android.view.animation.Animation
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.viewModels
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.appcompat.app.AppCompatActivity
import androidx.drawerlayout.widget.DrawerLayout
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

class MainActivity : AppCompatActivity() {

    private val viewModel: ChessViewModel by viewModels()

    private lateinit var chessBoard: ChessBoardView
    private lateinit var drawerLayout: DrawerLayout
    private lateinit var coachText: TextView
    private lateinit var coachDock: LinearLayout
    private lateinit var statusPulse: View
    private lateinit var scoreRow: LinearLayout
    private lateinit var txtEngineScore: TextView
    private lateinit var txtMistakeCategory: TextView

    // ── Game session state ───────────────────────────────────────────────────
    private lateinit var gameApiClient: GameApiClient
    private lateinit var coachApiClient: CoachApiClient
    private lateinit var authApiClient: AuthApiClient
    private lateinit var feedbackApiClient: FeedbackApiClient
    private lateinit var authRepo: AuthRepository
    private lateinit var txtWeaknessTags: TextView
    private lateinit var txtNextTrainingChip: TextView
    private lateinit var txtUpgradeChip: TextView
    private var currentPlayerId: String = "demo"
    private val moveClassifications = mutableListOf<MistakeClassification>()

    /**
     * Cached result from the most recent /game/finish call.
     * Provides [PlayerProfileDto] (rating + confidence) and weakness categories for
     * the next chat session opened via [openChat].  Null before the first game ends.
     */
    private var lastGameFinishResponse: GameFinishResponse? = null

    /**
     * Backend safe-mode gate (see [SecaSafetyGate]).  Built in [onCreate]
     * after the API client, refreshed at cold-start and on every
     * [onResume].  Null only between [onCreate] entry and the API
     * client construction; every reference site below uses `?.` so
     * tests with a null Activity surface don't NPE.
     */
    private var secaSafetyGate: SecaSafetyGate? = null

    /**
     * Persistent Snackbar shown while [secaSafetyGate] is not in the
     * Safe state.  The gate's [SecaSafetyState.Unsafe.reason] is
     * surfaced as the message; tapping "Retry" calls [refreshSafetyGate].
     * Held as a field so we can [Snackbar.dismiss] it as soon as the
     * state flips to Safe — Material's Snackbar doesn't auto-dismiss
     * indefinite ones.
     */
    private var safetySnackbar: Snackbar? = null

    /**
     * Game id captured from the most recent /game/start success.
     * Forwarded on /game/finish so the backend marks the matching
     * `games` row complete (result + finished_at columns) instead of
     * leaving it orphaned in NULL purgatory.  Survives across the
     * Resume restore path: [tryRestoreInProgressGame] re-loads it from
     * [PREF_LAST_GAME_SERVER_ID] so a resumed game finishes against
     * the original row.
     */
    private var currentServerGameId: String? = null

    // ── Game-review (replay) state ───────────────────────────────────────────
    // Set when a finished game is opened from history for replay + live
    // coaching.  reviewPositions are per-ply FENs from GET /game/{id}/positions;
    // ◀/▶ step through them on the (passive) board.  Empty == normal play.
    private var reviewPositions: List<String> = emptyList()
    private var reviewMoves: List<String> = emptyList()
    private var reviewPly: Int = 0
    // The side the player was on in the game under review ("black" for
    // imported Lichess games played as Black; null = white/live). Read by
    // the coach chat so its "you" framing follows the player's seat.
    private var reviewPlayerColor: String? = null
    // The finished game under review: its history event id (the review
    // API key) and provenance.  The "Coach review" button shows only for
    // source == "lichess" — in-app games have their own finish surfaces.
    private var reviewEventId: String? = null
    private var reviewSource: String? = null
    private lateinit var reviewNavBar: View
    private lateinit var btnReviewPrev: Button
    private lateinit var btnReviewNext: Button
    private lateinit var btnCoachReview: Button
    private lateinit var txtReviewMove: TextView
    private lateinit var reviewApiClient: ReviewApiClient

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Redirect unauthenticated users to the login screen before showing
        // the board. EncryptedTokenStorage is lazily initialised; no Keystore
        // operation occurs if the token is already in the prefs cache.
        authRepo = AuthRepository(EncryptedTokenStorage(this))
        if (!authRepo.isLoggedIn()) {
            startActivity(
                Intent(this, LoginActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
            return
        }

        currentPlayerId = (authRepo.authState() as? AuthState.Authenticated)?.playerId ?: "demo"
        gameApiClient =
            HttpGameApiClient(
                baseUrl = BuildConfig.COACH_API_BASE,
                apiKey = BuildConfig.COACH_API_KEY,
                tokenProvider = { authRepo.getToken() },
                tokenSink = { newToken -> authRepo.saveToken(newToken) },
            )

        // Coach client for the read-only per-game chat shown in game history
        // (expand a past game to see its coaching thread).  Same auth wiring
        // as gameApiClient; default read timeout — history is a quick GET,
        // not the long-poll /chat/stream that needs CHAT_READ_TIMEOUT_MS.
        coachApiClient =
            HttpCoachApiClient(
                baseUrl = BuildConfig.COACH_API_BASE,
                apiKey = BuildConfig.COACH_API_KEY,
                tokenProvider = { authRepo.getToken() },
                tokenSink = { newToken -> authRepo.saveToken(newToken) },
            )

        // Post-game review client (imported Lichess games).  Token is
        // passed per call by GameReviewBottomSheet via its tokenProvider;
        // rotation headers land in the same sink as the other clients.
        reviewApiClient =
            HttpReviewApiClient(
                baseUrl = BuildConfig.COACH_API_BASE,
                tokenSink = { newToken -> authRepo.saveToken(newToken) },
            )

        // Wire the engine-strength dial: the server's /player/progress
        // returns a rating-derived opponentElo, which ChessViewModel
        // reads via PlayerProfileCache and maps to a 0–100 strength
        // level passed to ChessNative.getBestMoveWithStrength.  Without
        // this wiring the engine plays at full strength regardless of
        // the user's calibration — see test_adaptive_engine_wiring.py
        // for the contract on both ends.
        //
        // Fire-and-forget warm: the AI move dispatch reads the cache
        // non-blockingly via cachedOpponentEloOrNull(), so populating
        // it in the background here ensures the very first AI move
        // already sees a calibrated strength level instead of falling
        // back to 100.  Failures are absorbed inside warm().
        viewModel.playerProfileCache = PlayerProfileCache(gameApiClient).also { cache ->
            lifecycleScope.launch { cache.warm() }
        }
        authApiClient = HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            // Wire X-Auth-Token rotation: every successful authenticated
            // response (me, updateMe, changePassword) hands back a fresh
            // 24h JWT; this sink saves it so the next call picks up the
            // rotated token.  Without this, the JWT would expire after
            // 24h and bounce the user to login.
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )

        // Send-feedback client for the drawer's form (FeedbackFlows).
        // Same auth wiring as the other authenticated clients.
        feedbackApiClient = HttpFeedbackApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
            tokenProvider = { authRepo.getToken() },
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )

        // If a previous /game/finish failed offline (timeout / 5xx /
        // network), the payload was persisted; try again now that we
        // (probably) have connectivity.  Fire-and-forget — see the
        // method's kdoc for the keep-vs-drop policy.
        retryPendingFinishOnColdStart()

        // Verify SECA safe_mode before any coaching call leaves the
        // app.  The gate stays in [SecaSafetyState.Unknown] until the
        // first refresh resolves; ChessViewModel's per-move coach
        // dispatch checks `gate.isSafe()` and skips `/live/move` until
        // the state clears, and [openChat] refuses similarly.  See
        // README > "Trust Boundaries" and SecaSafetyGate kdoc.
        secaSafetyGate = HttpSecaSafetyGate(gameApiClient).also { gate ->
            viewModel.secaSafetyGate = gate
            observeSafetyGate(gate)
            refreshSafetyGate(gate)
        }

        setContentView(R.layout.activity_main)

        Log.d("AI_TEST", "MainActivity started")

        // -------- FIND VIEWS --------
        chessBoard = findViewById(R.id.chessBoard)
        chessBoard.boardStyle = SettingsBottomSheet.readBoardStyle(this)
        drawerLayout = findViewById(R.id.drawerLayout)
        coachText = findViewById(R.id.txtCoach)
        coachDock = findViewById(R.id.txtCoachContainer)
        statusPulse = findViewById(R.id.statusPulse)
        scoreRow = findViewById(R.id.scoreRow)
        txtEngineScore = findViewById(R.id.txtEngineScore)
        txtMistakeCategory = findViewById(R.id.txtMistakeCategory)

        txtWeaknessTags = findViewById(R.id.txtWeaknessTags)
        txtNextTrainingChip = findViewById(R.id.txtNextTrainingChip)
        txtUpgradeChip = findViewById(R.id.txtUpgradeChip)
        txtUpgradeChip.setOnClickListener {
            startActivity(Intent(this, PaywallActivity::class.java))
        }
        reviewNavBar = findViewById(R.id.reviewNavBar)
        btnReviewPrev = findViewById(R.id.btnReviewPrev)
        btnReviewNext = findViewById(R.id.btnReviewNext)
        btnCoachReview = findViewById(R.id.btnCoachReview)
        txtReviewMove = findViewById(R.id.txtReviewMove)
        btnReviewPrev.setOnClickListener { stepReview(-1) }
        btnReviewNext.setOnClickListener { stepReview(+1) }
        btnCoachReview.setOnClickListener { openCoachReviewSheet() }
        val btnExitToHome = findViewById<Button>(R.id.btnExitToHome)
        val btnReset = findViewById<Button>(R.id.btnReset)
        val btnUndo = findViewById<Button>(R.id.btnUndo)
        val btnChat = findViewById<Button>(R.id.btnChat)
        val btnGameHistory = findViewById<Button>(R.id.btnGameHistory)

        // START PULSE ANIMATION
        startPulseAnimation()

        // Theme runs edge-to-edge (transparent statusBarColor; the
        // navigationBarColor sits behind the gesture area on
        // Android 13+).  Without an inset listener the action bar
        // at the bottom would render under the system nav, making
        // "Ask the coach" / "?" untappable on devices that overlap
        // the bottom of the activity with the gesture pill.
        applyBottomSystemBarInset(findViewById(R.id.atriumActionBar))

        // 🛡️ SAFETY CHECK
        if (!ChessNative.isLibraryLoaded) {
            Toast.makeText(this, "Native engine failed to load!", Toast.LENGTH_LONG).show()
            coachText.text = "❌ Engine Error"
        } else {
            Log.d("AI_TEST", "Engine loaded. Ready to play.")
        }

        // Atrium chapter header — initialise once, then refresh after
        // every applied move so the kicker reads "MOVE N" instead of
        // the static "Chapter · Move".  The header title stays
        // generic ("Position") until a coach-supplied theme lands;
        // the design's "The Pin" / "The Squeeze" copy comes from the
        // server-side coach analysis layer that has not been wired
        // through to the client yet.
        updateChapterHeader()

        // 3️⃣ Wire move callback
        chessBoard.onMovePlayed = { fr, fc, tr, tc ->
            if (ChessNative.isLibraryLoaded) {
                viewModel.onHumanMove(
                    fr, fc, tr, tc,
                    applyHumanMove = {
                        chessBoard.applyMove(fr, fc, tr, tc).also {
                            updateChapterHeader()
                            persistInProgressSnapshot()
                        }
                    },
                    exportFEN = {
                        chessBoard.exportFEN()
                    },
                    applyAIMove = { afr, afc, atr, atc, apromo ->
                        chessBoard.applyAIMove(afr, afc, atr, atc, apromo).also {
                            updateChapterHeader()
                            persistInProgressSnapshot()
                        }
                    },
                    consumeGameOver = { chessBoard.consumePendingGameOver() },
                )
            } else {
                Toast.makeText(this, "Engine not available", Toast.LENGTH_SHORT).show()
            }
        }

        // -------- SIDEBAR BUTTONS --------
        // Exit to Home — finishes MainActivity; the standard back-stack
        // pops to HomeActivity (which started us via startActivity).  We
        // deliberately persist the in-progress snapshot before finishing
        // so the user can resume from HomeActivity's Resume card.
        btnExitToHome.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            if (::chessBoard.isInitialized && viewModel.moveCount > 0) {
                persistInProgressSnapshot()
            }
            finish()
        }

        // Reset wipes the in-progress game irreversibly, so the tap is
        // gated behind an explicit "Are you sure" confirmation.  The
        // drawer closes first so the dialog reads against the board, not
        // the menu; the destructive body lives in performResetGame() and
        // runs ONLY from the dialog's positive button (pinned by
        // GamePanelActionsSourcePinTest).
        btnReset.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            AlertDialog.Builder(this)
                .setTitle(R.string.reset_confirm_title)
                .setMessage(R.string.reset_confirm_message)
                .setPositiveButton(R.string.reset_confirm_positive) { _, _ ->
                    performResetGame()
                }
                .setNegativeButton(R.string.reset_confirm_negative, null)
                .show()
        }

        btnUndo.setOnClickListener {
            chessBoard.undoBoth()
            viewModel.reset()
            drawerLayout.closeDrawer(GravityCompat.END)
        }

        btnChat.setOnClickListener {
            openChat()
        }

        // Atrium action bar — primary "Ask the coach" + 44dp "?" both
        // route into the same chat flow as the drawer's btnChat.  Wired
        // here as findViewById so the activity's lateinit field set
        // does not need to grow; tests that don't exercise these IDs
        // are unaffected.
        findViewById<Button>(R.id.btnAskCoach)?.setOnClickListener { openChat() }
        findViewById<Button>(R.id.btnHelp)?.setOnClickListener { openChat() }

        btnGameHistory.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            val sheet = GameHistoryBottomSheet()
            sheet.gameApiClient = gameApiClient
            sheet.show(supportFragmentManager, "GameHistoryBottomSheet")
        }

        val btnProgressDashboard = findViewById<android.widget.Button>(R.id.btnProgressDashboard)
        btnProgressDashboard.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            val sheet = ProgressDashboardBottomSheet()
            sheet.gameApiClient = gameApiClient
            sheet.show(supportFragmentManager, "ProgressDashboardBottomSheet")
        }

        // Atrium Settings sheet — preferences (coach voice, board
        // style, sound, notifications, account chevrons).  Account
        // section routes through AccountFlows, shared with
        // HomeActivity's avatar entry so the two hosts can't drift.
        // Change password lives ONLY in the sheet's Account rows; Sign
        // out additionally has a standalone drawer button again
        // (btnSignOut below — reinstated by product request 2026-07-10),
        // but both surfaces route through the same
        // AccountFlows.performLogout so they cannot drift.
        findViewById<Button>(R.id.btnSettings)?.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            val sheet = SettingsBottomSheet()
            sheet.onChangePasswordTapped = {
                AccountFlows.showChangePasswordDialog(this, authRepo, authApiClient)
            }
            sheet.onSignOutTapped = {
                AccountFlows.performLogout(this, authRepo, authApiClient)
            }
            sheet.onConnectLichessTapped = {
                LichessConnectBottomSheet()
                    .show(supportFragmentManager, LichessConnectBottomSheet.TAG)
            }
            sheet.show(supportFragmentManager, "SettingsBottomSheet")
        }

        // Send feedback — drawer form that POSTs to /feedback
        // (docs/API_CONTRACTS.md §38).  Fire-and-forget: FeedbackFlows
        // owns the dialog, validation, and outcome toasts.
        findViewById<Button>(R.id.btnSendFeedback)?.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            FeedbackFlows.showSendFeedbackDialog(this, feedbackApiClient)
        }

        // Sign out — standalone drawer button (reinstated by product
        // request 2026-07-10).  Routes through the SAME shared
        // AccountFlows.performLogout as the Settings sheet's Account row
        // above, so the two surfaces cannot drift: best-effort server
        // logout, local token clear, hard route to LoginActivity.
        findViewById<Button>(R.id.btnSignOut)?.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            AccountFlows.performLogout(this, authRepo, authApiClient)
        }

        // Cached curriculum chip if available — the rating header was
        // retired from the drawer when the user-visible Elo display was
        // removed.  PREF_RATING is still read elsewhere (silent adaptive
        // opponent matching) but no longer drives any UI on this screen.
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val cachedTopic = prefs.getString(PREF_CURRICULUM_TOPIC, null)
        val cachedExType = prefs.getString(PREF_CURRICULUM_EXERCISE_TYPE, null)
        if (cachedTopic != null) {
            txtNextTrainingChip.text = formatCurriculumChip(cachedTopic, cachedExType)
            txtNextTrainingChip.visibility = View.VISIBLE
        }

        // Sync full profile from server at cold-start (rating + skill_vector for weakness tags).
        val authToken = authRepo.getToken()
        if (authToken != null) {
            lifecycleScope.launch {
                when (val r = authApiClient.me(authToken)) {
                    is ApiResult.Success -> {
                        // Server is authoritative.  PR #175 retired the
                        // cold-start reconcile path that used to compare
                        // the local PREF_PLAYER_RATING_ESTIMATE (set
                        // during onboarding or in Settings) against the
                        // server rating and re-PATCH if they differed.
                        // That logic CLOBBERED game-driven rating
                        // updates on every cold-start: after a loss the
                        // server's rating dropped, but the stale local
                        // estimate hadn't, so the reconcile pushed the
                        // pre-game rating back to the server.  Caught
                        // 2026-05-18 — the user saw rating RISING after
                        // losses on the 'Your trends' dashboard.
                        //
                        // The onboarding and settings paths still PATCH
                        // their values directly at the moment the user
                        // sets them; on success they clear these PREFs
                        // (see OnboardingActivity.firePatchAuthMe /
                        // SettingsBottomSheet.firePatchAuthMe).  No
                        // auto-retry on cold-start.
                        val server = r.data
                        // Wipe the stale onboarding-time estimate too,
                        // so older installs (where the PATCH cleanup
                        // hadn't yet been added) don't carry the
                        // pre-PR-#175 dirty value forward.  Rating +
                        // confidence are still cached because adaptive
                        // opponent matching reads them silently — only
                        // the drawer's "Rating: ..." label was retired
                        // when the UI hid the user-facing Elo number.
                        // training_xp populates the new Home Level/XP
                        // kicker via PREF_TRAINING_XP.
                        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                            .putFloat(PREF_RATING, server.rating)
                            .putFloat(PREF_CONFIDENCE, server.confidence)
                            .putInt(PREF_TRAINING_XP, server.trainingXp)
                            .remove(PREF_PLAYER_RATING_ESTIMATE)
                            .remove(PREF_PLAYER_CONFIDENCE_LOCAL)
                            .apply()
                        val tags = formatWeaknessTags(server.skillVector)
                        if (tags.isNotEmpty()) {
                            txtWeaknessTags.text = tags
                            txtWeaknessTags.visibility = View.VISIBLE
                        }
                    }
                    is ApiResult.HttpError -> Log.d("AUTH", "me() HTTP ${r.code}")
                    is ApiResult.NetworkError -> Log.d("AUTH", "me() network error", r.cause)
                    ApiResult.Timeout -> Log.d("AUTH", "me() timed out")
                }
            }
        }

        // -------- ROBUST GESTURE FOR THE WHOLE DOCK --------
        val swipeDetector = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean = true

            override fun onFling(e1: MotionEvent?, e2: MotionEvent, vX: Float, vY: Float): Boolean {
                if (e1 != null && (e1.y - e2.y > 50)) {
                    coachDock.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                    openChat()
                    return true
                }
                return false
            }

            override fun onSingleTapUp(e: MotionEvent): Boolean {
                coachDock.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
                openChat()
                return true
            }
        })

        coachDock.isClickable = true
        coachDock.setOnTouchListener { v, event ->
            if (swipeDetector.onTouchEvent(event)) return@setOnTouchListener true
            if (event.action == MotionEvent.ACTION_UP) v.performClick()
            true
        }

        chessBoard.coachListener = { comment -> coachText.text = comment }
        chessBoard.promotionListener = { r, c -> showPromotionDialog(r, c) }

        viewModel.onGameOver = { result ->
            // PGN Result header (White="Player", Black="Engine") so the server
            // can derive the winner's last move for the history list. Without
            // it the PGN carried "*" and every game's winner move was blank.
            val pgnResult =
                when (result) {
                    GameResult.WHITE_WINS -> "1-0"
                    GameResult.BLACK_WINS -> "0-1"
                    GameResult.DRAW -> "1/2-1/2"
                }
            val pgn = viewModel.exportPGN(pgnResult)
            val resultStr =
                when (result) {
                    GameResult.WHITE_WINS -> "win"
                    GameResult.BLACK_WINS -> "loss"
                    GameResult.DRAW -> "draw"
                }
            val accuracy = computeAccuracy()
            val weaknesses = computeWeaknesses(moveClassifications)
            // Atrium hero card needs the result enum + move count — capture
            // them BEFORE moveClassifications is cleared / a new game starts,
            // so the summary sheet sees the values from the just-finished game.
            val finalResult = result
            val finalMoveCount = chessBoard.moveCount
            // FIX: capture the live server game id BEFORE clearInProgressSnapshot()
            // nulls currentServerGameId. /game/finish MUST carry it so the server
            // links this GameEvent to its coaching-chat thread (app_game_id).
            // Previously the clear ran first, so finish sent game_id=null and
            // every finished game's chat was unreachable in history.
            val finishedGameId = currentServerGameId
            moveClassifications.clear()
            // Game's done — clear the in-progress flag so HomeActivity
            // doesn't show a stale Resume card on the next visit.
            clearInProgressSnapshot()
            val finishReq = GameFinishRequest(
                pgn = pgn,
                result = resultStr,
                accuracy = accuracy,
                weaknesses = weaknesses,
                playerId = currentPlayerId,
                gameId = finishedGameId,
            )
            lifecycleScope.launch {
                when (val r = gameApiClient.finishGame(finishReq)) {
                    is ApiResult.Success -> {
                        lastGameFinishResponse = r.data
                        showCoachingResult(r.data, finalResult, finalMoveCount)
                        // Server bumped the rating + (likely) shifted
                        // opponent_elo.  Drop the cached profile and
                        // warm a fresh entry in the background so the
                        // next AI move sees the updated strength
                        // level without paying for `/player/progress`
                        // on the AI dispatch path.
                        viewModel.playerProfileCache?.let { cache ->
                            cache.invalidate()
                            lifecycleScope.launch { cache.warm() }
                        }
                    }
                    is ApiResult.HttpError -> {
                        if (r.code == 401) {
                            // Persist BEFORE bouncing to login so the game
                            // survives re-auth: the post-login cold-start
                            // retry (retryPendingFinishOnColdStart) replays
                            // it.  If a DIFFERENT account signs in, the
                            // server's player_id ownership check 403s and
                            // the retry classifies DROP — no cross-account
                            // attribution.
                            persistPendingFinish(finishReq)
                            handleSessionExpired()
                        } else if (PendingGameFinish.isTransient(r)) {
                            // 5xx — server-side incident.  Persist + retry
                            // on next cold-start instead of dropping the
                            // game's PGN + analysis on the floor.
                            persistPendingFinish(finishReq)
                            Log.w("GAME", "finishGame HTTP ${r.code} — saved for retry")
                        } else {
                            // 4xx (other than 401) — server actively
                            // rejected the payload, retry would just
                            // fail again.  Log and drop.
                            Log.w("GAME", "finishGame HTTP ${r.code} — dropping (non-retryable)")
                        }
                    }
                    is ApiResult.NetworkError -> {
                        persistPendingFinish(finishReq)
                        Log.w("GAME", "finishGame network error — saved for retry", r.cause)
                    }
                    ApiResult.Timeout -> {
                        persistPendingFinish(finishReq)
                        Log.w("GAME", "finishGame timed out — saved for retry")
                    }
                }
            }
        }

        // Resume vs. new-game branch.  HomeActivity sets EXTRA_RESUME
        // when the user taps the Resume card; we then load the saved
        // position into the board + ViewModel and skip
        // startNewGameSession() (which would bump the game number and
        // wipe the snapshot).  When the prefs don't actually have a
        // resumable position we fall through to the new-game path so
        // a stale extra (e.g. from a back-navigation) doesn't strand
        // the user on an empty board.
        val resumed = if (intent?.getBooleanExtra(EXTRA_RESUME, false) == true) {
            tryRestoreInProgressGame()
        } else false

        if (!resumed) {
            // Only a genuine "New game" launch (no sheet to open) may be
            // hard-blocked by the paywall.  HomeActivity routes Past games
            // through MainActivity with EXTRA_OPEN_SHEET; that is
            // NAVIGATION, not a request to play, so it must never bounce
            // the user to the paywall — the game gate is about PLAYING.
            startNewGameSession(
                allowPaywallOnLimit = intent?.getStringExtra(EXTRA_OPEN_SHEET) == null,
            )
        } else {
            // Already in-progress server-side from before the resume —
            // refresh the chapter header so it reads "Move N" instead
            // of "Opening", and leave the game number / snapshot
            // intact so the next move advances them naturally.
            updateChapterHeader()
        }

        // Wire real Stockfish evaluation: after each AI move, ChessViewModel calls
        // POST /engine/eval and optionally POST /live/move, then emits the result here.
        viewModel.engineEvalClient = HttpEngineEvalClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
        )
        viewModel.liveCoachClient = HttpLiveMoveClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
            // POST /live/move depends on get_current_player on the server
            // (llm/server.py — `Depends(get_current_player)`), so a JWT
            // Bearer header is mandatory.  Without it the server returns
            // 401 "Missing token" and the Mode-1 inline coach hint
            // silently never lands.  Pass the same authRepo-backed
            // provider used by HttpGameApiClient / HttpCoachApiClient.
            tokenProvider = { authRepo.getToken() },
            // Every 200 response carries an X-Auth-Token refresh header.
            // Wire the sink so long live-coach sessions rotate the JWT
            // instead of expiring at the 24 h exp and bouncing the user
            // to login.
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        // Free-tier coached-game admission: every /live/move call carries the
        // current server game id so the entitlements layer meters GAMES, not
        // moves (API_CONTRACTS.md §4).  Null before /game/start lands — the
        // server fails open for it.
        viewModel.serverGameIdProvider = { currentServerGameId }

        viewModel.onQuickCoachUpdate = { update ->
            // Track for end-of-game accuracy computation — only on human-move updates
            // to avoid double-counting (one human-move coach update + one AI-score update per turn)
            if (update.isHumanMoveCoachUpdate) {
                moveClassifications.add(update.classification)
                // Entitlements limit chip: only human-move updates carry the
                // coach_tier verdict, and it holds for the WHOLE game (the
                // admission is per game_id), so set — don't clear — outside
                // this branch.  Cleared on reset/new game via startNewGameUi.
                txtUpgradeChip.visibility =
                    if (update.coachDegraded) View.VISIBLE else View.GONE
            }

            // Show engine score badge; degrade gracefully when engine is unavailable
            txtEngineScore.text = if (update.engineAvailable) {
                update.scoreText
            } else {
                "⚠ Eval N/A"
            }

            // Show mistake category badge with severity colour.
            // Token reads (atrium_severity_*) so bright mode swaps in
            // paper-legible variants via values-notnight/colors.xml.
            txtMistakeCategory.text = update.classification.label()
            val categoryColor = when (update.classification) {
                MistakeClassification.BLUNDER    ->
                    ContextCompat.getColor(this, R.color.atrium_severity_blunder)
                MistakeClassification.MISTAKE    ->
                    ContextCompat.getColor(this, R.color.atrium_severity_mistake)
                MistakeClassification.INACCURACY ->
                    ContextCompat.getColor(this, R.color.atrium_severity_inaccuracy)
                MistakeClassification.GOOD       ->
                    ContextCompat.getColor(this, R.color.atrium_severity_good)
            }
            txtMistakeCategory.setTextColor(categoryColor)
            scoreRow.visibility = View.VISIBLE

            // Show explanation or fallback when position is solid
            coachText.text = update.explanation
                ?: "Solid move — tap for deeper analysis"
        }

        // HomeActivity routes "Past games" taps here with
        // EXTRA_OPEN_SHEET so the drawer's btnGameHistory wiring stays
        // the single source of truth for that flow (the performClick()
        // path also closes the already-closed drawer harmlessly).  The
        // other Home surfaces (Puzzles / You) host their sheets
        // directly over HomeActivity and never route through here.
        when (intent?.getStringExtra(EXTRA_OPEN_SHEET)) {
            OPEN_SHEET_HISTORY -> btnGameHistory.performClick()
        }
    }

    companion object {
        const val PREFS_NAME = "chesscoach_prefs"
        const val PREF_RATING = "last_rating"
        const val PREF_CONFIDENCE = "last_confidence"
        // Training XP counter cached from /auth/me.  Drives the Home
        // screen's Level/XP kicker that replaced the user-visible Elo
        // rating.  Rating + confidence are still cached above because
        // they continue to power adaptive opponent matching internally
        // — only the user-facing display switched.
        const val PREF_TRAINING_XP = "last_training_xp"
        const val PREF_CURRICULUM_TOPIC = "curriculum_topic"
        const val PREF_CURRICULUM_DIFFICULTY = "curriculum_difficulty"
        const val PREF_CURRICULUM_EXERCISE_TYPE = "curriculum_exercise_type"

        // Latest coach decision — populated by GameSummaryBottomSheet on
        // every /game/finish and consumed by ProgressDashboardBottomSheet
        // to surface "what the coach is currently telling the player"
        // beyond the transient post-game sheet.  Cleared on logout via
        // the same SharedPreferences scrub the rating keys go through.
        const val PREF_LAST_COACH_ACTION_TYPE  = "last_coach_action_type"
        const val PREF_LAST_COACH_WEAKNESS     = "last_coach_weakness"
        const val PREF_LAST_COACH_REASON       = "last_coach_reason"
        const val PREF_LAST_COACH_TITLE        = "last_coach_title"
        const val PREF_LAST_COACH_DESCRIPTION  = "last_coach_description"

        // In-progress snapshot keys — read by HomeActivity to populate
        // the Resume card.  See bumpGameNumber / persistInProgressSnapshot
        // / clearInProgressSnapshot below for the lifecycle.
        const val PREF_LAST_GAME_NUMBER       = "last_game_number"
        const val PREF_LAST_GAME_MOVE_COUNT   = "last_game_move_count"
        const val PREF_LAST_GAME_TIMESTAMP    = "last_game_timestamp"
        const val PREF_LAST_GAME_IN_PROGRESS  = "last_game_in_progress"

        // Resume payload — populated alongside the snapshot above so
        // the EXTRA_RESUME branch in onCreate can actually restore the
        // position rather than just relaunching with a fresh session.
        const val PREF_LAST_GAME_FEN          = "last_game_fen"
        const val PREF_LAST_GAME_UCI_HISTORY  = "last_game_uci_history"

        // Server-side game id from the last /game/start response.
        // Reused on Resume so the eventual /game/finish closes the same
        // `games` row instead of orphaning it.
        const val PREF_LAST_GAME_SERVER_ID    = "last_game_server_id"

        // Set by HomeActivity on the Resume card tap; read in onCreate
        // to skip startNewGameSession() and apply the saved FEN /
        // UCI list instead.
        const val EXTRA_RESUME              = "resume"

        /**
         * Parse the comma-separated UCI list persisted by
         * [persistInProgressSnapshot] back into the list shape
         * [ChessViewModel.restoreMoveHistory] expects.  Filters out
         * empty tokens defensively (a stale "" prefs value used to
         * yield a 1-element list containing "").
         */
        fun parseUciHistory(stored: String?): List<String> =
            stored
                ?.split(',')
                ?.map { it.trim() }
                ?.filter { it.isNotEmpty() }
                ?: emptyList()

        // Onboarding-time calibration persisted by OnboardingActivity.
        // We re-declare the names here as constants because the
        // recovery path in this file's onCreate reads them, and the
        // Kotlin compiler refuses cross-companion-object constant
        // imports inside the activity body without explicit aliasing.
        const val PREF_PLAYER_RATING_ESTIMATE  = OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE
        const val PREF_PLAYER_CONFIDENCE_LOCAL = OnboardingActivity.PREF_PLAYER_CONFIDENCE

        // Tolerance for the cold-start reconcile path: a server rating
        // within 0.5 of the local estimate is "close enough" — common
        // case is exact equality after the OnboardingActivity PATCH
        // succeeded; the epsilon guards against IEEE 754 round-trip
        // noise from the float→JSON→float conversion.
        const val RATING_RECONCILE_EPSILON = 0.5f

        /**
         * Maximum time openChat waits for the SECA gate to settle out
         * of [SecaSafetyState.Unknown] before deciding whether to
         * present the chat sheet.  Covers the common cold-start case
         * where /seca/status is round-tripping; long enough for a
         * mobile network round-trip, short enough that a stuck call
         * doesn't feel hung.
         */
        const val SECA_OPEN_CHAT_AWAIT_MS: Long = 1500L

        // Intent extras used by HomeActivity to ask MainActivity to
        // open a specific bottom sheet on startup.  String constants
        // (rather than an enum) keep the Intent contract trivially
        // serialisable and let HomeActivity pass null for "no sheet".
        // Past games is the only remaining sheet route — Puzzles / You
        // sheets are hosted directly over HomeActivity.
        const val EXTRA_OPEN_SHEET  = "open_sheet"
        const val OPEN_SHEET_HISTORY = "history"

        /**
         * Format top [maxTags] skill-vector entries as weakness tag labels.
         *
         * Entries are sorted by descending weakness score. Tags with a score ≥ 0.5
         * are marked "↑" (high weakness); those below 0.5 are marked "↓".
         * Returns an empty string when [skillVector] is empty.
         */
        fun formatWeaknessTags(skillVector: Map<String, Float>, maxTags: Int = 3): String {
            val sorted = skillVector.entries.sortedByDescending { it.value }.take(maxTags)
            if (sorted.isEmpty()) return ""
            return sorted.joinToString(" · ") { (k, v) ->
                val arrow = if (v >= 0.5f) "↑" else "↓"
                "$arrow ${k.replace('_', ' ')}"
            }
        }

        /**
         * Format the cached curriculum recommendation as a training chip label.
         *
         * Example: "↳ DRILL: endgame technique"
         */
        fun formatCurriculumChip(topic: String, exerciseType: String?): String {
            val type = exerciseType?.uppercase() ?: "TRAIN"
            return "↳ $type: ${topic.replace('_', ' ')}"
        }

        /**
         * Compute weakness rates from the accumulated move classifications.
         *
         * Returned map keys match the backend SECA schema:
         *  - "blunder_rate"    — fraction of moves classified as BLUNDER
         *  - "mistake_rate"    — fraction classified as MISTAKE
         *  - "inaccuracy_rate" — fraction classified as INACCURACY
         *
         * Returns emptyMap() when [classifications] is empty (avoids division
         * by zero and matches the previous safe fallback).
         */
        fun computeWeaknesses(classifications: List<MistakeClassification>): Map<String, Float> {
            val total = classifications.size.toFloat()
            if (total == 0f) return emptyMap()
            return mapOf(
                "blunder_rate"    to classifications.count { it == MistakeClassification.BLUNDER }    / total,
                "mistake_rate"    to classifications.count { it == MistakeClassification.MISTAKE }    / total,
                "inaccuracy_rate" to classifications.count { it == MistakeClassification.INACCURACY } / total,
            )
        }
    }

    private fun startPulseAnimation() {
        val pulse = AlphaAnimation(1.0f, 0.3f).apply {
            duration = 1000
            repeatMode = Animation.REVERSE
            repeatCount = Animation.INFINITE
        }
        statusPulse.startAnimation(pulse)
    }

    /**
     * Adds the system-bar bottom inset onto [view]'s padding so a
     * bottom-anchored container clears the gesture / 3-button nav
     * bar on edge-to-edge devices.
     *
     * Captures the original paddingBottom on the first invocation so
     * the inset is added on top of the layout's static padding rather
     * than replacing it — without that, the action-bar's 16dp visual
     * breathing room would collapse to zero when the system bar inset
     * happens to be 0 (e.g. landscape with hidden nav bar).
     */
    private fun applyBottomSystemBarInset(view: View) {
        val basePaddingBottom = view.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(bottom = basePaddingBottom + bars.bottom)
            insets
        }
    }

    private fun openChat() {
        if (supportFragmentManager.isStateSaved) return
        // README: confirm safe_mode=true before sending coaching requests.
        // Chat is the most coaching-heavy surface in the app; refuse
        // until the gate is in [SecaSafetyState.Safe].  The persistent
        // safety Snackbar already explains why and offers Retry.
        //
        // Three-way branch on the gate state so the cold-start window
        // (state=Unknown until the first /seca/status response lands)
        // doesn't immediately fail.  Used to bail with a toast on
        // anything-but-Safe — including the Unknown window — which
        // forced the user to retry several times in the second or two
        // it took /seca/status to round-trip.
        val gate = secaSafetyGate
        when (val state = gate?.state?.value) {
            null, SecaSafetyState.Safe -> presentChatSheet()
            SecaSafetyState.Unknown -> awaitGateThenOpenChat(gate!!)
            is SecaSafetyState.Unsafe ->
                Toast.makeText(
                    this,
                    getString(R.string.seca_safety_chat_blocked),
                    Toast.LENGTH_LONG,
                ).show()
        }
    }

    /**
     * Suspend up to [SECA_OPEN_CHAT_AWAIT_MS] for the first
     * `/seca/status` response to land, then re-decide.  Confirmed
     * Unsafe still blocks chat — only the cold-start "we don't know
     * yet" window changes from immediate-bail to wait-then-decide.
     *
     * If the gate stays Unknown past the timeout (network is so slow
     * the first refresh hasn't completed), we treat it the same as
     * Unsafe: surface the blocked-toast so the user knows to wait /
     * tap Retry on the persistent safety Snackbar.
     */
    private fun awaitGateThenOpenChat(gate: SecaSafetyGate) {
        lifecycleScope.launch {
            val resolved = withTimeoutOrNull(SECA_OPEN_CHAT_AWAIT_MS) {
                gate.state.first { it !is SecaSafetyState.Unknown }
            }
            when (resolved) {
                SecaSafetyState.Safe -> presentChatSheet()
                is SecaSafetyState.Unsafe, SecaSafetyState.Unknown, null ->
                    Toast.makeText(
                        this@MainActivity,
                        getString(R.string.seca_safety_chat_blocked),
                        Toast.LENGTH_LONG,
                    ).show()
            }
        }
    }

    /**
     * Caller-side preconditions: the SECA gate has cleared and the
     * activity is in a state safe to commit a fragment transaction.
     * Split out of [openChat] so both the synchronous Safe branch and
     * the deferred await-then-open branch share the sheet construction.
     */
    private fun presentChatSheet() {
        if (supportFragmentManager.isStateSaved) return
        if (drawerLayout.isDrawerOpen(GravityCompat.END)) {
            drawerLayout.closeDrawer(GravityCompat.END)
        }

        val boardSnapshot = chessBoard.exportFEN()
        val currentMoveCount = viewModel.moveCount

        // Build player context: prefer live game result, fall back to cached prefs.
        val profile: PlayerProfileDto? = lastGameFinishResponse?.let {
            PlayerProfileDto(rating = it.newRating, confidence = it.confidence)
        } ?: run {
            val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            val cachedRating = prefs.getFloat(PREF_RATING, -1f)
            if (cachedRating >= 0f) {
                PlayerProfileDto(
                    rating = cachedRating,
                    confidence = prefs.getFloat(PREF_CONFIDENCE, 0f).coerceAtLeast(0f),
                )
            } else null
        }
        val mistakes = lastGameFinishResponse?.coachAction?.weakness?.let { listOf(it) }

        ChatBottomSheet
            .newInstance(boardSnapshot, profile, mistakes, currentMoveCount)
            .show(supportFragmentManager, "ChatBottomSheet")
    }

    /**
     * Live coach context for an OPEN [ChatBottomSheet]. The sheet captures a
     * position snapshot when it opens, but the chat is now a non-modal panel
     * over the live board — the user can play moves while it's up. The sheet
     * re-pulls these at send time so the coach discusses the CURRENT position,
     * not the one from when the panel opened. Source matches the open-time
     * snapshot in [presentChatSheet]: board FEN + ViewModel move count.
     */
    fun currentBoardFen(): String? =
        if (::chessBoard.isInitialized) chessBoard.exportFEN() else null

    fun currentMoveCount(): Int =
        if (::chessBoard.isInitialized) viewModel.moveCount else 0

    /**
     * The player's most recent move (UCI) so the chat coach can describe it in
     * plain English ("your f-pawn"); null before the human's first move.
     */
    fun currentLastMove(): String? = viewModel.lastHumanMoveUci()

    /**
     * Current server game id (from /game/start, or restored on resume), so the
     * coach chat can scope its history per game. Null when no active server
     * game (e.g. offline, or before /game/start) → chat stays player-global.
     */
    fun currentGameId(): String? = currentServerGameId

    /**
     * The side the player is on for the coach chat's "you" framing:
     * "black" while reviewing an imported game played as Black, null
     * otherwise (live games are always White; null keeps the field off
     * the wire and the server anchors White).
     */
    fun currentPlayerColor(): String? = reviewPlayerColor

    /**
     * Called when the backend returns HTTP 401 during an active game session.
     * Shows a non-disruptive dialog instead of silently breaking the game flow.
     * The user can choose to re-authenticate or dismiss and continue offline.
     */
    private fun handleSessionExpired() {
        if (isFinishing || isDestroyed) return
        AlertDialog.Builder(this)
            .setTitle("Session expired")
            .setMessage("Your session has expired. Log in again to save your game progress.")
            .setPositiveButton("Log in") { _, _ ->
                authRepo.clearToken()
                startActivity(
                    Intent(this, LoginActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
                )
                finish()
            }
            .setNegativeButton("Dismiss", null)
            .show()
    }

    /**
     * Open a finished game from history for replay + live coaching: fetch its
     * per-ply positions, load them onto the (passive) main board, and reveal
     * the ◀/▶ nav.  The live coach chat (Coach button) then reasons about the
     * position currently shown — exactly like during a game — scoped to this
     * game's thread (currentServerGameId).
     */
    fun openFinishedGameReview(eventId: String, gameId: String?, source: String? = null) {
        lifecycleScope.launch {
            when (val r = gameApiClient.getGamePositions(eventId)) {
                is ApiResult.Success -> {
                    // Remembered for the "Coach review" surface: the event id
                    // keys POST/GET /game/{event_id}/review, and the button
                    // shows only for imported Lichess games.
                    reviewEventId = eventId
                    reviewSource = source
                    loadFinishedGameForReview(
                        r.data.positions, r.data.moves, gameId, r.data.playerColor
                    )
                }
                else ->
                    Toast.makeText(
                        this@MainActivity,
                        "Couldn't load that game for review.",
                        Toast.LENGTH_SHORT,
                    ).show()
            }
        }
    }

    private fun loadFinishedGameForReview(
        positions: List<String>,
        moves: List<String>,
        gameId: String?,
        playerColor: String?,
    ) {
        if (positions.isEmpty()) return
        reviewPositions = positions
        reviewMoves = moves
        reviewPly = positions.size - 1
        // Orient the board to the side the player was on, so their pieces
        // sit at the bottom under the "You" label.  Imported Lichess games
        // carry "white"/"black"; in-app games (always white) and legacy
        // rows send null -> no flip.
        chessBoard.flipped = playerColor.equals("black", ignoreCase = true)
        // Remember the seat for the coach chat: its "you" framing must follow
        // the player's side, or every pronoun is inverted on Black games.
        reviewPlayerColor = if (chessBoard.flipped) "black" else null
        // The coach chat reads currentGameId() + the live board FEN, so set the
        // game thread and make the board a passive replay surface (no play taps).
        currentServerGameId = gameId?.takeIf { it.isNotBlank() }
        chessBoard.isInteractive = false
        reviewNavBar.visibility = View.VISIBLE
        // AI review is an imported-Lichess surface (in-app games keep
        // their existing finish summary + mistake replay).
        btnCoachReview.visibility =
            if (reviewSource.equals("lichess", ignoreCase = true)) View.VISIBLE
            else View.GONE
        renderReviewPly()
    }

    private fun stepReview(delta: Int) {
        if (reviewPositions.isEmpty()) return
        reviewPly = (reviewPly + delta).coerceIn(0, reviewPositions.size - 1)
        renderReviewPly()
    }

    /**
     * Jump the replay board to [ply] — the review sheet's "Explore"
     * action (moment cards + graph markers).  Position indexing matches
     * GET /game/{id}/positions: index i = board AFTER ply i.
     */
    fun jumpReviewToPly(ply: Int) {
        if (reviewPositions.isEmpty()) return
        reviewPly = ply.coerceIn(0, reviewPositions.size - 1)
        renderReviewPly()
    }

    /** Open the post-game AI review sheet for the game under replay. */
    private fun openCoachReviewSheet() {
        val eventId = reviewEventId ?: return
        GameReviewBottomSheet().apply {
            reviewApiClient = this@MainActivity.reviewApiClient
            tokenProvider = { authRepo.getToken() }
            this.eventId = eventId
            onExplorePly = { ply -> jumpReviewToPly(ply) }
        }.show(supportFragmentManager, "game_review")
    }

    private fun renderReviewPly() {
        if (reviewPositions.isEmpty()) return
        chessBoard.setFEN(reviewPositions[reviewPly])
        val san = if (reviewPly in 1..reviewMoves.size) reviewMoves[reviewPly - 1] else "start"
        txtReviewMove.text = "move $reviewPly / ${reviewPositions.size - 1}  ·  $san"
        btnReviewPrev.isEnabled = reviewPly > 0
        btnReviewNext.isEnabled = reviewPly < reviewPositions.size - 1
    }

    /** Leave replay mode and restore the normal interactive play board. */
    private fun exitReviewMode() {
        val wasReviewing = reviewPositions.isNotEmpty()
        reviewPositions = emptyList()
        reviewMoves = emptyList()
        reviewPly = 0
        reviewPlayerColor = null
        reviewEventId = null
        reviewSource = null
        // Restore the default White-at-bottom orientation for live play.
        chessBoard.flipped = false
        if (wasReviewing) chessBoard.isInteractive = true
        if (::reviewNavBar.isInitialized) reviewNavBar.visibility = View.GONE
        if (::btnCoachReview.isInitialized) btnCoachReview.visibility = View.GONE
    }

    /**
     * The destructive body of the drawer's "Reset game" action: wipe the
     * board + per-game UI state and start a fresh server game session.
     *
     * Reachable ONLY from the confirmation dialog's positive button in
     * btnReset's click listener (pinned by GamePanelActionsSourcePinTest)
     * — never bind this directly to a tap.  Extracted verbatim from the
     * pre-confirmation listener body (2026-07-10); the drawer close moved
     * up into the listener so the dialog shows over the board.
     */
    private fun performResetGame() {
        if (ChessNative.isLibraryLoaded) {
            viewModel.reset()
            chessBoard.resetBoard()
        }
        moveClassifications.clear()
        coachText.text = "♟ New game. Control the center!"
        scoreRow.visibility = View.GONE
        txtEngineScore.text = ""
        txtMistakeCategory.text = ""
        // A fresh game gets a fresh admission verdict; hide the limit
        // chip until /live/move says otherwise for the new game_id.
        txtUpgradeChip.visibility = View.GONE
        updateChapterHeader()
        startNewGameSession()
    }

    /**
     * Starts a new server game session.
     *
     * On a free-tier daily-limit 402 the board is LOCKED (no play) and the
     * upgrade chip is shown, but the activity is never finished — the drawer
     * and any requested sheet stay usable, so only PLAYING is blocked.
     *
     * @param allowPaywallOnLimit when true (a genuine "New game" / "Reset"
     *   intent), the 402 additionally pops the paywall.  When false (the
     *   launch only wanted to open a sheet — Past games / Coach / You /
     *   Lessons), the board still locks but the paywall is not popped, so
     *   navigation is never interrupted.
     */
    private fun startNewGameSession(allowPaywallOnLimit: Boolean = true) {
        exitReviewMode()
        bumpGameNumber()
        currentServerGameId = null
        lifecycleScope.launch {
            when (val r = gameApiClient.startGame(currentPlayerId)) {
                is ApiResult.Success -> {
                    currentServerGameId = r.data.gameId
                    getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                        .putString(PREF_LAST_GAME_SERVER_ID, r.data.gameId)
                        .apply()
                    // A real game was granted — ensure the board is playable
                    // and any prior daily-limit lock is cleared.
                    chessBoard.isInteractive = true
                    txtUpgradeChip.visibility = View.GONE
                    Log.d("GAME", "Session started: ${r.data.gameId}")
                }
                is ApiResult.HttpError -> {
                    if (r.code == 402 && GameLimitNotice.fromBody(r.body) != null) {
                        // Free-tier daily game limit reached.  The chess board
                        // is LOCAL-first, so merely refusing the server game
                        // would leave it playable — and /live/move with a null
                        // game_id fails open with coaching, so the user could
                        // keep playing unmetered.  LOCK the board so no new
                        // game can actually be played until tomorrow/upgrade.
                        // We do NOT finish() — the drawer (Settings / Game
                        // history / Exit to Home) and any requested sheet stay
                        // fully usable, so only PLAYING is blocked, never
                        // navigation.  A genuine play intent (New game / Reset)
                        // also pops the paywall; a nav launch just shows the
                        // upgrade chip behind its sheet.
                        Log.d("GAME", "daily game limit — locking board (paywall=$allowPaywallOnLimit)")
                        chessBoard.isInteractive = false
                        txtUpgradeChip.visibility = View.VISIBLE
                        coachText.text =
                            "Daily game reached. Upgrade for unlimited games, " +
                                "or come back tomorrow."
                        if (allowPaywallOnLimit) {
                            startActivity(Intent(this@MainActivity, PaywallActivity::class.java))
                        }
                    } else {
                        Log.w("GAME", "startGame HTTP ${r.code}")
                    }
                }
                is ApiResult.NetworkError -> Log.w("GAME", "startGame network error", r.cause)
                ApiResult.Timeout -> Log.w("GAME", "startGame timed out")
            }
        }
    }

    /**
     * HomeActivity Resume tap → reload the saved board state + UCI
     * history into [chessBoard] and [viewModel], so the user picks up
     * the position they left.
     *
     * Returns true on successful restore, false if the snapshot is
     * missing / blank / inconsistent (e.g. the user uninstalled and
     * reinstalled, or the prefs got corrupted).  A false return tells
     * the caller to fall through to the standard new-game path so the
     * user never lands on an empty board.
     *
     * Server-side note: we do NOT issue a new /game/start when
     * resuming.  The pre-resume server session may have timed out
     * (the snapshot has a 6h TTL — see HomeActivity), in which case
     * the next /game/finish will create a fresh row.  This is fine
     * for the local "keep playing" UX; perfect server-side resumption
     * is out of scope here.
     */
    private fun tryRestoreInProgressGame(): Boolean {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        if (!prefs.getBoolean(PREF_LAST_GAME_IN_PROGRESS, false)) return false
        val fen = prefs.getString(PREF_LAST_GAME_FEN, null)?.takeIf { it.isNotBlank() }
            ?: return false
        val uciList = parseUciHistory(prefs.getString(PREF_LAST_GAME_UCI_HISTORY, null))
        if (uciList.isEmpty()) return false  // no real progress to resume

        chessBoard.setFEN(fen)
        viewModel.restoreMoveHistory(uciList)
        // Reuse the original /game/start id so the next /game/finish
        // closes the same `games` row server-side instead of orphaning
        // it.  Null is fine here — older snapshots won't have it; the
        // finish call will simply omit the field and the backend will
        // skip the repo.finish_game write (still creates the GameEvent).
        currentServerGameId = prefs.getString(PREF_LAST_GAME_SERVER_ID, null)
            ?.takeIf { it.isNotBlank() }
        Log.d(
            "RESUME",
            "Restored ${uciList.size}-move position from snapshot " +
                "(serverGameId=${currentServerGameId ?: "<none>"})",
        )
        return true
    }

    /**
     * Persistence hooks for [HomeActivity]'s Resume card.  We persist
     * the bare minimum needed to render a meaningful "you have an
     * unfinished game" tile: which game we're on, how many half-moves
     * have been played, the wall-clock of the last update, and an
     * in-progress flag the Home screen reads to decide whether to show
     * the card at all.
     *
     * No board state is persisted yet — true position restore on
     * Resume tap is a separate feature.  For now the Resume card just
     * relaunches MainActivity, which kicks off a fresh session.
     */
    private fun bumpGameNumber() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val current = prefs.getInt(PREF_LAST_GAME_NUMBER, 0)
        prefs.edit()
            .putInt(PREF_LAST_GAME_NUMBER, current + 1)
            .putInt(PREF_LAST_GAME_MOVE_COUNT, 0)
            .putLong(PREF_LAST_GAME_TIMESTAMP, System.currentTimeMillis())
            .putBoolean(PREF_LAST_GAME_IN_PROGRESS, true)
            // Clear any prior position snapshot so the next Resume tap
            // can't restore a position from the *previous* game.  The
            // server game id is also cleared — startNewGameSession will
            // write the new one once /game/start returns.
            .remove(PREF_LAST_GAME_FEN)
            .remove(PREF_LAST_GAME_UCI_HISTORY)
            .remove(PREF_LAST_GAME_SERVER_ID)
            .apply()
    }

    private fun persistInProgressSnapshot() {
        val fen = chessBoard.exportFEN()
        val uciHistory = viewModel.exportUciHistory()
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
            .putInt(PREF_LAST_GAME_MOVE_COUNT, viewModel.moveCount)
            .putLong(PREF_LAST_GAME_TIMESTAMP, System.currentTimeMillis())
            .putBoolean(PREF_LAST_GAME_IN_PROGRESS, true)
            // Resume payload — board FEN + UCI list — used by the
            // EXTRA_RESUME branch in onCreate to actually restore the
            // position.  Without these the Resume card would only be
            // a "you have an unfinished game" indicator that starts a
            // new game on tap.
            .putString(PREF_LAST_GAME_FEN, fen)
            .putString(PREF_LAST_GAME_UCI_HISTORY, uciHistory)
            .apply()

        // Push the checkpoint server-side too so a device swap /
        // reinstall can pull this state via HomeActivity's cold-start
        // GET /game/active.  Best-effort: log + drop on failure (the
        // local snapshot still works).  No game_id → no checkpoint
        // (the very first move before /game/start returns).
        val gameId = currentServerGameId ?: return
        lifecycleScope.launch {
            when (val r = gameApiClient.checkpointGame(gameId, fen, uciHistory)) {
                is ApiResult.Success -> { /* silent — checkpoint per move would be too noisy */ }
                is ApiResult.HttpError -> Log.d("CHECKPOINT", "HTTP ${r.code}")
                is ApiResult.NetworkError -> Log.d("CHECKPOINT", "network error", r.cause)
                ApiResult.Timeout -> Log.d("CHECKPOINT", "timed out")
            }
        }
    }

    private fun clearInProgressSnapshot() {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
            .putBoolean(PREF_LAST_GAME_IN_PROGRESS, false)
            .remove(PREF_LAST_GAME_FEN)
            .remove(PREF_LAST_GAME_UCI_HISTORY)
            .remove(PREF_LAST_GAME_SERVER_ID)
            .apply()
        currentServerGameId = null
    }

    /**
     * Persist the failed /game/finish payload so the next cold-start
     * can retry it.  Called from the transient-error branches in
     * onGameOver (timeout, network, 5xx) — without this hook those
     * branches used to drop the entire PGN + classification analysis
     * silently, which is the kind of data loss users notice.
     *
     * Slot is one-deep; a second pending payload overwrites the first.
     * See PendingGameFinish kdoc for the rationale.
     */
    private fun persistPendingFinish(req: GameFinishRequest) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
            .putString(PendingGameFinish.PREF_PENDING_FINISH_PAYLOAD, PendingGameFinish.toJson(req))
            .apply()
        runOnUiThread {
            Toast.makeText(
                this,
                "Saved offline — we'll sync your game next time",
                Toast.LENGTH_LONG,
            ).show()
        }
    }

    /**
     * Try to send any pending /game/finish payload from a previous
     * session that failed offline.  Called once from onCreate;
     * HomeActivity also calls the same shared helper so the retry
     * fires on either the post-auth landing OR direct relaunch into
     * MainActivity.  The helper's process-singleton guard prevents
     * the two call sites from double-firing on the same cold-start.
     */
    private fun retryPendingFinishOnColdStart() {
        PendingGameFinish.retryFromPrefs(
            prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE),
            client = gameApiClient,
            scope = lifecycleScope,
            onSuccess = {
                Toast.makeText(
                    this@MainActivity,
                    "Synced your offline game",
                    Toast.LENGTH_SHORT,
                ).show()
            },
            onSessionExpired = { handleSessionExpired() },
        )
    }

    private fun computeAccuracy(): Float {
        if (moveClassifications.isEmpty()) return 0.5f
        val score =
            moveClassifications.sumOf { c ->
                when (c) {
                    MistakeClassification.GOOD -> 1.0
                    MistakeClassification.INACCURACY -> 0.75
                    MistakeClassification.MISTAKE -> 0.5
                    MistakeClassification.BLUNDER -> 0.0
                }
            }
        return (score / moveClassifications.size).toFloat()
    }

    /**
     * Refresh the Atrium chapter header at the top of the in-game
     * coaching screen.
     *
     * Kicker reads "Move N" once at least one half-move has been
     * played, "Opening" before that.  Title stays a generic
     * "Position" until the coach analysis layer supplies a theme
     * (the design's "The Pin" / "The Squeeze" / etc.) — that hook
     * is handled by the server-side coach pipeline that has not been
     * wired through to the client yet.
     */
    private fun updateChapterHeader() {
        val header = findViewById<AtriumChapterHeaderView>(R.id.atriumChapterHeader) ?: return
        // Take the max of the two move-count sources:
        //   - chessBoard.moveCount tracks the visual undo stack
        //     (chessBoard.history); after btnUndo / undoBoth pops
        //     entries, this is the post-undo count.
        //   - viewModel.moveCount tracks the UCI list used for PGN
        //     export.  After a HomeActivity Resume tap restores the
        //     position via setFEN, the board's internal history is
        //     empty (no piece-by-piece replay) but viewModel was
        //     populated by restoreMoveHistory, so without the max
        //     the kicker would read "Opening" right after restore.
        // The max gives us the right answer on both edges.
        val moves = maxOf(chessBoard.moveCount, viewModel.moveCount)
        header.kicker = if (moves > 0) "Move $moves" else "Opening"
        header.title = "Position"
    }

    private fun showCoachingResult(
        response: GameFinishResponse,
        result: GameResult? = null,
        moveCount: Int = 0,
    ) {
        coachText.text = response.coachContent.title

        // (Drawer rating header retired alongside the rest of the
        // user-visible Elo surfaces.  ``response.newRating`` is still
        // consumed downstream — GameSummaryBottomSheet writes it back
        // to PREF_RATING so adaptive opponent matching keeps working.)

        if (supportFragmentManager.isStateSaved) return
        val sheet = GameSummaryBottomSheet.newInstance(
            response, currentPlayerId, result, moveCount,
        )
        sheet.gameApiClient = gameApiClient
        sheet.show(supportFragmentManager, "GameSummaryBottomSheet")
    }

    private fun showPromotionDialog(r: Int, c: Int) {
        val dialog = Dialog(this)
        dialog.setContentView(R.layout.dialog_promotion)
        dialog.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        dialog.setCancelable(false)

        fun onSelected(piece: Char) {
            chessBoard.promotePawn(r, c, piece)
            viewModel.onPromotionFinished(
                exportFEN = { chessBoard.exportFEN() },
                applyAIMove = { afr, afc, atr, atc, apromo ->
                    chessBoard.applyAIMove(afr, afc, atr, atc, apromo)
                },
                consumeGameOver = { chessBoard.consumePendingGameOver() },
            )
            dialog.dismiss()
        }

        dialog.findViewById<Button>(R.id.btnQueen).setOnClickListener { onSelected('Q') }
        dialog.findViewById<Button>(R.id.btnRook).setOnClickListener { onSelected('R') }
        dialog.findViewById<Button>(R.id.btnBishop).setOnClickListener { onSelected('B') }
        dialog.findViewById<Button>(R.id.btnKnight).setOnClickListener { onSelected('N') }
        dialog.show()
    }

    override fun onResume() {
        super.onResume()
        // Re-apply the board variant in case the user changed Settings →
        // Board style while the bottom sheet was open over MainActivity;
        // the sheet only persists, the consumer reads it back here.
        if (::chessBoard.isInitialized) {
            chessBoard.boardStyle = SettingsBottomSheet.readBoardStyle(this)
        }
        // Re-check SECA safe-mode on every resume so a backend that
        // drifts to unsafe mid-session is caught the next time the
        // activity comes to the foreground.  Cheap (single open
        // GET) and cancelled by lifecycle if the user immediately
        // navigates away.
        secaSafetyGate?.let { refreshSafetyGate(it) }
    }

    /**
     * Subscribe the activity to [gate.state] so the persistent safety
     * Snackbar appears whenever the state is not [SecaSafetyState.Safe]
     * and dismisses as soon as the state clears.  Uses
     * [repeatOnLifecycle(STARTED)] so the collector pauses while the
     * activity is in the background — no coroutine leak across
     * configuration changes.
     */
    private fun observeSafetyGate(gate: SecaSafetyGate) {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                gate.state.collect { state ->
                    when (state) {
                        SecaSafetyState.Safe -> {
                            safetySnackbar?.dismiss()
                            safetySnackbar = null
                        }
                        SecaSafetyState.Unknown -> {
                            // Cold-start window before the first refresh
                            // resolves; show a soft "checking" message.
                            showSafetySnackbar(
                                getString(R.string.seca_safety_checking),
                                gate,
                            )
                        }
                        is SecaSafetyState.Unsafe -> {
                            showSafetySnackbar(
                                getString(R.string.seca_safety_unsafe, state.reason),
                                gate,
                            )
                        }
                    }
                }
            }
        }
    }

    private fun showSafetySnackbar(message: String, gate: SecaSafetyGate) {
        val anchor = drawerLayout
        val existing = safetySnackbar
        if (existing != null && existing.isShown) {
            existing.setText(message)
            return
        }
        val snackbar = Snackbar.make(anchor, message, Snackbar.LENGTH_INDEFINITE)
            .setAction(R.string.seca_safety_retry) { refreshSafetyGate(gate) }
        safetySnackbar = snackbar
        snackbar.show()
    }

    /**
     * Fire-and-forget refresh.  The Snackbar reflects the result
     * automatically through the [observeSafetyGate] flow collector.
     */
    private fun refreshSafetyGate(gate: SecaSafetyGate) {
        lifecycleScope.launch { gate.refresh() }
    }
}
package com.cereveon.myapp

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Mistake-replay bottom sheet (Phase 3).
 *
 * Launched from [GameSummaryBottomSheet] when the /game/finish response
 * carries a non-null ``biggest_mistake`` block.  Shows the position
 * the user was looking at when they made their worst centipawn-loss
 * move + the move they actually played, and lets them try a stronger
 * alternative on an interactive [ChessBoardView].
 *
 * Flow per attempt
 * ----------------
 *  1.  User taps & drags a piece on [ChessBoardView] → fires
 *      ``onMovePlayed(fr, fc, tr, tc)``.
 *  2.  Board is locked (``isInteractive=false``) and the status text
 *      flips to "Checking...".
 *  3.  Activity calls ``POST /training/verify-replay`` with the FEN
 *      and the UCI of the attempted move.  Server runs Stockfish.
 *  4a. ``isCorrect=true`` → activity calls ``POST /training/solve``
 *      with ``source_type=mistake_replay`` and the ``source_ref``
 *      that came down the wire on the original ``biggest_mistake``.
 *      Server credits +10 XP; activity toasts "+10 XP", updates
 *      ``PREF_TRAINING_XP`` so the Home kicker bumps on next paint,
 *      and dismisses.
 *  4b. ``isCorrect=false`` → status flips to "Not quite, try again"
 *      (amber), the board's FEN is reset to the mistake position,
 *      and ``isInteractive`` is re-enabled.  No XP penalty; the
 *      user can retry indefinitely.
 *
 * Trust boundary
 * --------------
 * The server's verify endpoint is the trust anchor — a modded client
 * could in principle skip /training/verify-replay and post directly
 * to /training/solve, but the dedup constraint
 * ``(player, source_type, source_ref)`` means each mistake can be
 * credited at most once anyway.  Phase 4+ can tighten by requiring
 * a server-issued nonce that proves the client went through
 * /training/verify-replay.
 *
 * Args
 * ----
 * Carried as bundle extras.  See [newInstance] for the canonical
 * construction path used by [GameSummaryBottomSheet].
 */
class MistakeReplayBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host activity before [show]; required for the verify + solve calls. */
    var gameApiClient: GameApiClient? = null

    private lateinit var board: ChessBoardView
    private lateinit var statusView: TextView

    private var fen: String = ""
    private var sourceRef: String = ""

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_mistake_replay, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val args = requireArguments()
        fen = args.getString(ARG_FEN, "")
        val playedMove = args.getString(ARG_PLAYED_MOVE, "")
        val moveNumber = args.getInt(ARG_MOVE_NUMBER, 0)
        val evalLossCp = args.getInt(ARG_EVAL_LOSS_CP, 0)
        sourceRef = args.getString(ARG_SOURCE_REF, "")

        view.findViewById<TextView>(R.id.mistakeReplayKicker).text =
            formatKicker(moveNumber)
        view.findViewById<TextView>(R.id.mistakeReplayPlayedMove).text =
            formatPlayedMoveLine(playedMove, evalLossCp)

        board = view.findViewById(R.id.mistakeReplayBoard)
        statusView = view.findViewById(R.id.mistakeReplayStatus)

        // Seed the board to the mistake position.  ``setFEN`` resets
        // last-move highlights and the selection state so the sheet
        // opens to a clean "your turn" presentation.
        board.setFEN(fen)
        board.isInteractive = true

        board.onMovePlayed = { fr, fc, tr, tc ->
            handleAttempt(fr, fc, tr, tc)
        }

        view.findViewById<Button>(R.id.mistakeReplayCloseButton).setOnClickListener {
            dismiss()
        }
    }

    private fun handleAttempt(fr: Int, fc: Int, tr: Int, tc: Int) {
        // Lock the board the moment the move lands so the user can't
        // fire a second attempt while the first is in flight.  The
        // wrong-move recovery path resets ``isInteractive=true``;
        // the correct-move path dismisses the sheet.
        board.isInteractive = false

        // Apply the move visually so the user sees their attempt
        // play out on the board before the verify round-trip lands.
        // If the move is illegal the board's own legality check
        // refuses it (no-op), so we never round-trip an impossible
        // UCI string to the server.
        val moveResult = board.applyMove(fr, fc, tr, tc)
        if (moveResult == MoveResult.FAILED) {
            // Illegal in this position (e.g. moves into check).
            // Reset interactivity; status stays at the default.
            board.isInteractive = true
            return
        }

        val moveUci = rowColToUci(fr, fc) + rowColToUci(tr, tc)
        setStatus("Checking...", ATRIUM_DIM_COLOR_RES)

        val client = gameApiClient ?: run {
            setStatus("Couldn't reach the engine.", ATRIUM_AMBER_COLOR_RES)
            board.setFEN(fen)
            board.isInteractive = true
            return
        }

        lifecycleScope.launch {
            when (val verify = client.verifyReplayMove(fen, moveUci)) {
                is ApiResult.Success -> {
                    if (verify.data.isCorrect) {
                        creditXpAndDismiss(client)
                    } else {
                        setStatus("Not quite, try again.", ATRIUM_AMBER_COLOR_RES)
                        board.setFEN(fen)
                        board.isInteractive = true
                    }
                }
                is ApiResult.HttpError -> {
                    setStatus(
                        if (verify.code == 503) "Engine busy. Try again."
                        else "Move couldn't be verified.",
                        ATRIUM_AMBER_COLOR_RES,
                    )
                    board.setFEN(fen)
                    board.isInteractive = true
                }
                is ApiResult.NetworkError, ApiResult.Timeout -> {
                    setStatus("Offline. Try again later.", ATRIUM_AMBER_COLOR_RES)
                    board.setFEN(fen)
                    board.isInteractive = true
                }
            }
        }
    }

    private suspend fun creditXpAndDismiss(client: GameApiClient) {
        when (val solve = client.submitTrainingSolve(
            sourceType = SOURCE_TYPE_MISTAKE_REPLAY,
            sourceRef = sourceRef.ifBlank { null },
        )) {
            is ApiResult.Success -> {
                val awarded = solve.data.xpAwarded
                val total = solve.data.trainingXp
                // Update the cache so the Home Level/XP kicker
                // re-renders to the new total on next paint without
                // waiting for the next /auth/me cold-start.
                requireContext()
                    .getSharedPreferences(
                        MainActivity.PREFS_NAME,
                        Context.MODE_PRIVATE,
                    )
                    .edit()
                    .putInt(MainActivity.PREF_TRAINING_XP, total)
                    .apply()

                val toastText =
                    if (awarded > 0) "+$awarded XP"
                    else "Already solved · $total XP"
                Toast.makeText(requireContext(), toastText, Toast.LENGTH_SHORT).show()
                dismiss()
            }
            else -> {
                // Verify succeeded but the solve persist failed —
                // the user nailed the move but we couldn't bank the
                // XP.  Show a soft message + leave the sheet open
                // so they don't lose the "I solved it" moment.
                setStatus("Solved, but couldn't save. Try again.", ATRIUM_AMBER_COLOR_RES)
                board.setFEN(fen)
                board.isInteractive = true
            }
        }
    }

    private fun setStatus(text: String, colorRes: Int) {
        statusView.text = text
        statusView.setTextColor(ContextCompat.getColor(requireContext(), colorRes))
    }

    companion object {
        private const val ARG_FEN = "fen"
        private const val ARG_PLAYED_MOVE = "played_move"
        private const val ARG_MOVE_NUMBER = "move_number"
        private const val ARG_EVAL_LOSS_CP = "eval_loss_cp"
        private const val ARG_SOURCE_REF = "source_ref"

        /** Source-type string accepted by POST /training/solve for mistake-replay solves. */
        const val SOURCE_TYPE_MISTAKE_REPLAY: String = "mistake_replay"

        // Status colour tokens — referenced via resource ids so a
        // theme change picks them up without touching this file.
        private val ATRIUM_DIM_COLOR_RES = R.color.atrium_dim
        private val ATRIUM_AMBER_COLOR_RES = R.color.atrium_accent_amber

        fun newInstance(mistake: BiggestMistakeDto): MistakeReplayBottomSheet =
            MistakeReplayBottomSheet().apply {
                arguments = Bundle().apply {
                    putString(ARG_FEN, mistake.fen)
                    putString(ARG_PLAYED_MOVE, mistake.playedMove)
                    putInt(ARG_MOVE_NUMBER, mistake.moveNumber)
                    putInt(ARG_EVAL_LOSS_CP, mistake.evalLossCp)
                    putString(ARG_SOURCE_REF, mistake.sourceRef)
                }
            }

        /**
         * Convert ChessBoardView's (row, col) coords (row 0 = rank 8,
         * col 0 = file a) to a 2-char UCI square like ``e2``.  Pure
         * helper so unit tests can pin the conversion without
         * standing up a view.
         */
        fun rowColToUci(row: Int, col: Int): String {
            val file = ('a' + col)
            val rank = (8 - row).toString()
            return "$file$rank"
        }

        /** "Mistake · Move 14" kicker copy. */
        fun formatKicker(moveNumber: Int): String =
            "Mistake · Move $moveNumber"

        /**
         * "You played e2e4 — eval dropped by 240 cp." subline.  Pure
         * helper so the wording is testable and the activity-only
         * findViewById noise stays out of the unit test surface.
         */
        fun formatPlayedMoveLine(playedMoveUci: String, evalLossCp: Int): String =
            "You played $playedMoveUci — eval dropped by $evalLossCp cp."
    }
}
package com.cereveon.myapp

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.slider.Slider
import kotlin.math.roundToInt
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Onboarding · Skill calibration (handoff #2).
 *
 * Shown ONCE after registration so the adaptation layer can dispatch
 * a first opponent at the right level.  LoginActivity routes any
 * authenticated user here until [PREF_ONBOARDING_COMPLETED] is true,
 * so this screen has no "real" Back path — see the Back button
 * comment in [onCreate] for the skip-with-defaults semantics.
 *
 * Persistence: SharedPreferences "chesscoach_prefs" (the same store
 * MainActivity / SettingsBottomSheet use):
 *   onboarding_completed: Boolean — true once Continue is tapped
 *   player_rating_estimate: Float  — slider value, e.g. 1720f
 *   player_confidence: Float       — sure=0.85, guessing=0.5, rusty=0.25
 *
 * Server sync: [persistAndContinue] also fires a best-effort PATCH
 * /auth/me with the new values so the backend's adaptation layer sees
 * the calibration before the first game.  If the call fails (offline,
 * server down) the values stay in SharedPreferences and MainActivity's
 * cold-start sync reconciles by re-PATCHing — see
 * MainActivity.PREF_PLAYER_RATING_ESTIMATE / RATING_RECONCILE_EPSILON.
 */
class OnboardingActivity : AppCompatActivity() {

    private lateinit var slider: Slider
    private lateinit var ratingValue: TextView
    private lateinit var firstOpponent: TextView

    private val confidenceDots = mutableMapOf<String, View>()
    private var selectedConfidence: String = DEFAULT_CONFIDENCE

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding)

        slider        = findViewById(R.id.sliderRating)
        ratingValue   = findViewById(R.id.txtRatingValue)
        firstOpponent = findViewById(R.id.txtFirstOpponent)

        // ── Slider — live-update the hero value + first-opponent preview.
        slider.value = DEFAULT_RATING
        ratingValue.text = formatRating(DEFAULT_RATING)
        firstOpponent.text = formatFirstOpponent(DEFAULT_RATING)
        slider.addOnChangeListener { _, value, _ ->
            ratingValue.text = formatRating(value)
            firstOpponent.text = formatFirstOpponent(value)
        }

        // ── Confidence radio.
        confidenceDots["sure"]     = findViewById(R.id.confSureDot)
        confidenceDots["guessing"] = findViewById(R.id.confGuessingDot)
        confidenceDots["rusty"]    = findViewById(R.id.confRustyDot)
        applyConfidenceState(DEFAULT_CONFIDENCE)

        bindConfidenceRow(R.id.confSure)
        bindConfidenceRow(R.id.confGuessing)
        bindConfidenceRow(R.id.confRusty)

        // ── Footer.
        // "Back" here means "skip calibration": the user is already
        // authenticated (registration succeeded) and LoginActivity will
        // bounce them right back here until [PREF_ONBOARDING_COMPLETED]
        // is true.  So pressing Back persists DEFAULT_RATING + the
        // current confidence and continues to MainActivity, where the
        // rating can later be adjusted from the drawer.
        findViewById<Button>(R.id.btnOnboardingBack).setOnClickListener {
            slider.value = DEFAULT_RATING
            persistAndContinue()
        }

        findViewById<Button>(R.id.btnOnboardingContinue).setOnClickListener {
            persistAndContinue()
        }
    }

    private fun bindConfidenceRow(rowId: Int) {
        val row = findViewById<LinearLayout>(rowId)
        val value = row.tag as String
        row.setOnClickListener {
            selectedConfidence = value
            applyConfidenceState(value)
        }
    }

    private fun applyConfidenceState(selected: String) {
        val filled = ContextCompat.getDrawable(this, R.drawable.atrium_radio_selected)
        val hollow = ContextCompat.getDrawable(this, R.drawable.atrium_radio_unselected)
        confidenceDots.forEach { (key, dot) ->
            dot.background = if (key == selected) filled else hollow
        }
        selectedConfidence = selected
    }

    private fun persistAndContinue() {
        val rating = slider.value
        val confidence = confidenceFromKey(selectedConfidence)
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
            .putBoolean(PREF_ONBOARDING_COMPLETED, true)
            .putFloat(PREF_PLAYER_RATING_ESTIMATE, rating)
            .putFloat(PREF_PLAYER_CONFIDENCE, confidence)
            // Mirror the rating into the existing PREF_RATING key so
            // MainActivity's drawer header (and the Home screen's "I —
            // New game" sub) pick it up immediately without waiting
            // for the next /auth/me sync.
            .putFloat(MainActivity.PREF_RATING, rating)
            .apply()

        // Best-effort PATCH /auth/me so the server's adaptation layer
        // sees the calibration before the first game.  If the network
        // call fails (offline, server down) the values stay in
        // SharedPreferences and MainActivity's cold-start sync
        // reconciles by comparing the cached PREF_PLAYER_RATING_ESTIMATE
        // against the server-returned rating and re-PATCHing.
        firePatchAuthMe(rating, confidence)

        // Step 3/3 reads back what we just saved so the user sees the
        // calibration confirmed before the first game; that screen
        // owns the final "go to Home" navigation.
        startActivity(Intent(this, OnboardingCompleteActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }

    private fun firePatchAuthMe(rating: Float, confidence: Float) {
        val authRepo = AuthRepository(EncryptedTokenStorage(this))
        val token = authRepo.getToken() ?: return
        val client: AuthApiClient = HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            // Same X-Auth-Token rotation as MainActivity — see kdoc there.
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        // Fire on the activity's lifecycleScope rather than
        // GlobalScope: if the user backgrounds the app immediately
        // after Continue, the launch is cancelled and we don't leak
        // a coroutine.  PR #175 retired the cold-start reconcile path
        // that used to retry this PATCH from PREF_PLAYER_RATING_ESTIMATE
        // (the retry kept clobbering game-driven rating updates).  On
        // failure the user keeps the server's default 1200 rating; one
        // game or a Settings adjustment recovers without lingering
        // stale state.
        lifecycleScope.launch {
            when (val r = client.updateMe(token, rating = rating, confidence = confidence)) {
                is ApiResult.Success -> {
                    Log.d(
                        "ONBOARDING",
                        "PATCH /auth/me OK (rating=${r.data.rating}, confidence=${r.data.confidence})",
                    )
                    // Clear the onboarding-time estimate now that the
                    // server has accepted it.  Without this clear, the
                    // PREF would linger and (pre-PR-#175) get re-PATCHed
                    // on every cold-start, clobbering game-driven
                    // updates.  Post-PR-#175 the cold-start reconcile
                    // is gone, but the PREF still has no further role,
                    // so we wipe it for hygiene.
                    getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
                        .remove(PREF_PLAYER_RATING_ESTIMATE)
                        .remove(PREF_PLAYER_CONFIDENCE)
                        .apply()
                }
                is ApiResult.HttpError -> Log.w("ONBOARDING", "PATCH /auth/me HTTP ${r.code}")
                is ApiResult.NetworkError -> Log.w("ONBOARDING", "PATCH /auth/me network error", r.cause)
                ApiResult.Timeout -> Log.w("ONBOARDING", "PATCH /auth/me timed out")
            }
        }
    }

    companion object {
        const val PREFS_NAME = MainActivity.PREFS_NAME

        const val PREF_ONBOARDING_COMPLETED    = "onboarding_completed"
        const val PREF_PLAYER_RATING_ESTIMATE  = "player_rating_estimate"
        const val PREF_PLAYER_CONFIDENCE       = "player_confidence"

        const val DEFAULT_RATING: Float = 1500f
        const val DEFAULT_CONFIDENCE: String = "guessing"

        /** Whether this account has been through the calibration flow. */
        fun isCompleted(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_ONBOARDING_COMPLETED, false)

        /** Format a slider value as a bare integer rating string. */
        fun formatRating(value: Float): String = value.roundToInt().toString()

        /**
         * Compute the first-opponent preview row text for [rating].
         * Per the handoff: opponent rating is biased ~40 below the
         * player's estimate so the first match is gentle but
         * adaptive will close the gap quickly.
         */
        fun formatFirstOpponent(rating: Float): String {
            val opponent = (rating - 40f).coerceAtLeast(800f).roundToInt()
            return "~$opponent · adaptive"
        }

        /**
         * Map a confidence radio key to a 0.0–1.0 value used by the
         * adaptation layer.  The values match the handoff intent —
         * "Sure of it" implies near-true rating, "Rusty" implies
         * a wide uncertainty band so adaptive moves faster.
         */
        fun confidenceFromKey(key: String): Float = when (key.lowercase()) {
            "sure"     -> 0.85f
            "guessing" -> 0.50f
            "rusty"    -> 0.25f
            else       -> 0.50f
        }
    }
}
package com.cereveon.myapp

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * Cereveon · Atrium · Onboarding · Completion (handoff step 3 of 3).
 *
 * Reads back the calibration the user just supplied so they see what
 * was recorded before the first game.  Reached from
 * [OnboardingActivity.persistAndContinue]; tapping
 * "Play your first game" routes to [HomeActivity], which is the same
 * post-auth landing every other path lands on.
 *
 * No Back path — by the time the user hits this screen they've
 * already saved the calibration to prefs; the only forward action
 * is "go play".
 */
class OnboardingCompleteActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding_complete)

        val prefs = getSharedPreferences(OnboardingActivity.PREFS_NAME, Context.MODE_PRIVATE)
        val rating = prefs.getFloat(
            OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
            OnboardingActivity.DEFAULT_RATING,
        )
        val confidence = prefs.getFloat(
            OnboardingActivity.PREF_PLAYER_CONFIDENCE,
            OnboardingActivity.confidenceFromKey(OnboardingActivity.DEFAULT_CONFIDENCE),
        )

        findViewById<TextView>(R.id.onboardingCompleteRating).text =
            OnboardingActivity.formatRating(rating)
        findViewById<TextView>(R.id.onboardingCompleteConfidence).text =
            formatConfidenceLabel(confidence)
        findViewById<TextView>(R.id.onboardingCompleteOpponent).text =
            OnboardingActivity.formatFirstOpponent(rating)

        findViewById<Button>(R.id.btnOnboardingCompleteStart).setOnClickListener {
            startActivity(
                Intent(this, HomeActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
        }
    }

    companion object {
        /**
         * Map a 0–1 confidence weight back to a human-readable label
         * matching [OnboardingActivity]'s 3-row radio.  Inverse of
         * [OnboardingActivity.confidenceFromKey] up to a small
         * tolerance so float round-trip noise from the
         * SharedPreferences write/read doesn't bucket a clear "Sure
         * of it" choice into "Guessing".
         *
         * The buckets:
         *   ≥ 0.70 → "Sure of it"   (sure-key value 0.85)
         *   ≥ 0.40 → "Guessing"     (guessing-key value 0.50)
         *    <0.40 → "Rusty"        (rusty-key value 0.25)
         */
        fun formatConfidenceLabel(confidence: Float): String = when {
            confidence >= 0.70f -> "Sure of it"
            confidence >= 0.40f -> "Guessing"
            else                -> "Rusty"
        }
    }
}
package com.cereveon.myapp

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * Cereveon · Atrium · Onboarding · Welcome (handoff step 1 of 3).
 *
 * Pure informational screen — sets up the app's value proposition
 * before [OnboardingActivity] (step 2/3) asks the user to calibrate
 * their skill.  Tapping Begin advances to the calibration screen;
 * there's no Back path (the user is already authenticated and
 * [LoginActivity.launchPostAuth] would just bounce them back here
 * until [OnboardingActivity.PREF_ONBOARDING_COMPLETED] is true).
 *
 * Bullets are inflated dynamically from [DEFAULT_HOOKS] using the
 * same item_paywall_bullet primitive the paywall ✦-bullet list uses
 * — keeps the typographic rhyme between marketing-style screens
 * consistent without duplicating the row layout.
 */
class OnboardingWelcomeActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding_welcome)

        renderBullets(findViewById(R.id.onboardingWelcomeBullets))

        findViewById<Button>(R.id.btnOnboardingWelcomeBegin).setOnClickListener {
            startActivity(
                Intent(this, OnboardingActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
        }
    }

    private fun renderBullets(container: LinearLayout) {
        container.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (text in DEFAULT_HOOKS) {
            val row = inflater.inflate(R.layout.item_paywall_bullet, container, false)
            row.findViewById<TextView>(R.id.paywallBulletText).text = text
            container.addView(row)
        }
    }

    companion object {
        /**
         * What the user gets — three short Cormorant-italic lines.
         * Order matters: most concrete benefit first, the
         * "personalised" framing last so the eye lands on it before
         * tapping Begin.
         */
        val DEFAULT_HOOKS: List<String> = listOf(
            "Adaptive opponents at your level",
            "Coach chat grounded in your games",
            "A study that grows with you",
        )
    }
}
package com.cereveon.myapp

import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.PurchasesUpdatedListener
import com.android.billingclient.api.QueryProductDetailsParams
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Paywall (handoff screen #11).
 *
 * Reached from SettingsBottomSheet → "Upgrade · Premium" chevron row.
 *
 * Purchase flow (Play Billing → server verify → Pro)
 * --------------------------------------------------
 *  1. Begin → connect [billingClient], query the selected plan's
 *     subscription product ([PLAY_PRODUCT_IDS]), launch the Play
 *     purchase sheet.
 *  2. [purchasesUpdatedListener] receives the PURCHASED result and
 *     posts the purchase token to POST /billing/google/verify — the
 *     SERVER is the entitlement authority; a local purchase result is
 *     never trusted on its own (docs/API_CONTRACTS.md §36).
 *  3. Only on a verified `plan == "pro"` ([verifyOutcome]): acknowledge
 *     the purchase with Play, cache the plan locally
 *     ([PREF_PLAYER_PLAN]), and finish into the Pro state.
 *  4. Any verify failure keeps the paywall open AND leaves the purchase
 *     unacknowledged — Play auto-refunds unacknowledged purchases, so a
 *     dead server can never silently keep the user's money.  Reopening
 *     the paywall retries pending verification via the purchases-updated
 *     listener on the next purchase attempt.
 *
 * The static plan catalogue ([DEFAULT_PLANS] / [DEFAULT_FEATURES] /
 * [recommendedPlanKey]) is unchanged from the scaffold pass — display
 * pricing stays design-driven for now; the Play product catalogue only
 * decides what is PURCHASED, keyed by [PLAY_PRODUCT_IDS].
 */
class PaywallActivity : AppCompatActivity() {

    private var selectedPlanKey: String = "yearly"
    private lateinit var monthlyTile: FrameLayout
    private lateinit var yearlyTile: FrameLayout
    private lateinit var monthlyPrice: TextView
    private lateinit var yearlyPrice: TextView
    private lateinit var monthlySub: TextView
    private lateinit var yearlySub: TextView

    private val authRepo: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    private val billingApi: BillingApiClient by lazy {
        HttpBillingApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
            tokenProvider = { authRepo.getToken() },
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
    }

    /**
     * Play Billing results arrive here — including purchases completed
     * in a previous session that Play redelivers on reconnect, which is
     * what retries a purchase whose server verify failed last time.
     */
    private val purchasesUpdatedListener = PurchasesUpdatedListener { result, purchases ->
        when {
            result.responseCode == BillingClient.BillingResponseCode.OK && purchases != null ->
                purchases.forEach(::handlePurchase)
            result.responseCode == BillingClient.BillingResponseCode.USER_CANCELED ->
                Unit // deliberate dismissal — no toast noise
            else ->
                toastOnUi("Purchase did not complete (code ${result.responseCode})")
        }
    }

    private lateinit var billingClient: BillingClient

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_paywall)

        billingClient = BillingClient.newBuilder(this)
            .setListener(purchasesUpdatedListener)
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build()
            )
            .build()

        // Theme runs edge-to-edge; without this listener the bottom
        // "Subscribe" / "Maybe later" footer would render
        // under the system gesture / nav bar.
        val footer = findViewById<LinearLayout>(R.id.paywallFooter)
        val footerBasePaddingBottom = footer.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(footer) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(bottom = footerBasePaddingBottom + bars.bottom)
            insets
        }

        renderFeatureBullets(findViewById(R.id.paywallFeatures))

        monthlyTile  = findViewById(R.id.paywallPlanMonthly)
        yearlyTile   = findViewById(R.id.paywallPlanYearly)
        monthlyPrice = findViewById(R.id.paywallPlanMonthlyPrice)
        yearlyPrice  = findViewById(R.id.paywallPlanYearlyPrice)
        monthlySub   = findViewById(R.id.paywallPlanMonthlySub)
        yearlySub    = findViewById(R.id.paywallPlanYearlySub)

        // DEFAULT_PLANS is the single source for the tile copy — the XML
        // values are pre-bind placeholders.  What gets BILLED is the Play
        // Console product behind PLAY_PRODUCT_IDS; these labels must be
        // kept in lock-step with the prices configured there (pinned by
        // PaywallActivityTest's launch-pricing test).
        DEFAULT_PLANS.firstOrNull { it.key == "monthly" }?.let {
            monthlyPrice.text = it.price
            monthlySub.text = it.sub
        }
        DEFAULT_PLANS.firstOrNull { it.key == "yearly" }?.let {
            yearlyPrice.text = it.price
            yearlySub.text = it.sub
        }

        monthlyTile.setOnClickListener { selectPlan("monthly") }
        yearlyTile.setOnClickListener { selectPlan("yearly") }
        // Initial state matches the design — yearly active by default.
        selectPlan(selectedPlanKey)

        findViewById<Button>(R.id.btnPaywallBegin).setOnClickListener {
            startPurchase(productIdFor(selectedPlanKey))
        }
        findViewById<TextView>(R.id.btnPaywallMaybeLater).setOnClickListener {
            finish()
        }
    }

    override fun onDestroy() {
        if (this::billingClient.isInitialized) {
            billingClient.endConnection()
        }
        super.onDestroy()
    }

    // ── Play Billing flow ────────────────────────────────────────────

    private fun startPurchase(productId: String) {
        if (billingClient.isReady) {
            queryAndLaunch(productId)
            return
        }
        billingClient.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                    queryAndLaunch(productId)
                } else {
                    // No Play services / not signed in / emulator without
                    // Play — the paywall stays open and harmless.
                    toastOnUi("Google Play billing unavailable (code ${result.responseCode})")
                }
            }

            override fun onBillingServiceDisconnected() {
                // Next Begin tap reconnects; no retry loop needed here.
            }
        })
    }

    private fun queryAndLaunch(productId: String) {
        val params = QueryProductDetailsParams.newBuilder()
            .setProductList(
                listOf(
                    QueryProductDetailsParams.Product.newBuilder()
                        .setProductId(productId)
                        .setProductType(BillingClient.ProductType.SUBS)
                        .build()
                )
            )
            .build()
        billingClient.queryProductDetailsAsync(params) { result, productDetailsList ->
            val details = productDetailsList.firstOrNull()
            if (result.responseCode != BillingClient.BillingResponseCode.OK || details == null) {
                toastOnUi("Plan not available right now — try again shortly")
                return@queryProductDetailsAsync
            }
            // Subscriptions always carry at least one offer; the base
            // plan's token is the first entry when no targeted offer
            // applies.  A missing token means the Play product is
            // misconfigured (not a client bug) — fail soft.
            val offerToken = details.subscriptionOfferDetails?.firstOrNull()?.offerToken
            if (offerToken == null) {
                toastOnUi("Plan not available right now — try again shortly")
                return@queryProductDetailsAsync
            }
            val flowParams = BillingFlowParams.newBuilder()
                .setProductDetailsParamsList(
                    listOf(
                        BillingFlowParams.ProductDetailsParams.newBuilder()
                            .setProductDetails(details)
                            .setOfferToken(offerToken)
                            .build()
                    )
                )
                .build()
            runOnUiThread { billingClient.launchBillingFlow(this, flowParams) }
        }
    }

    private fun handlePurchase(purchase: Purchase) {
        if (purchase.purchaseState != Purchase.PurchaseState.PURCHASED) {
            // PENDING (e.g. cash top-up pending) — Play redelivers via the
            // listener once it completes; nothing to verify yet.
            return
        }
        val productId = purchase.products.firstOrNull() ?: return
        lifecycleScope.launch {
            val result = billingApi.verifyGooglePurchase(purchase.purchaseToken, productId)
            when (verifyOutcome(result)) {
                VerifyOutcome.PRO_ACTIVATED -> {
                    acknowledgeIfNeeded(purchase)
                    cachePlan("pro")
                    toastOnUi("Premium active — welcome aboard")
                    finish()
                }
                VerifyOutcome.KEEP_PAYWALL -> {
                    // Purchase stays UNACKNOWLEDGED on purpose: if the
                    // server stays unreachable, Play refunds it — the
                    // user is never charged for an entitlement the
                    // server never granted.
                    Log.w(TAG, "verify failed for $productId: $result")
                    toastOnUi(
                        "Could not confirm the purchase with the coach server — " +
                            "it will be retried, you won't be double-charged"
                    )
                }
            }
        }
    }

    private fun acknowledgeIfNeeded(purchase: Purchase) {
        if (purchase.isAcknowledged) return
        val params = AcknowledgePurchaseParams.newBuilder()
            .setPurchaseToken(purchase.purchaseToken)
            .build()
        billingClient.acknowledgePurchase(params) { result ->
            // Best-effort: the SERVER verdict granted the plan; a failed
            // acknowledge just means Play redelivers the purchase and we
            // re-acknowledge on the next listener pass.
            if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                Log.w(TAG, "acknowledgePurchase failed (code ${result.responseCode})")
            }
        }
    }

    private fun cachePlan(plan: String) {
        getSharedPreferences(MainActivity.PREFS_NAME, MODE_PRIVATE).edit()
            .putString(PREF_PLAYER_PLAN, plan)
            .apply()
    }

    private fun toastOnUi(message: String) {
        runOnUiThread { Toast.makeText(this, message, Toast.LENGTH_SHORT).show() }
    }

    // ── Static UI scaffolding (unchanged from the scaffold pass) ─────

    private fun renderFeatureBullets(container: LinearLayout) {
        container.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (text in DEFAULT_FEATURES) {
            val row = inflater.inflate(R.layout.item_paywall_bullet, container, false)
            row.findViewById<TextView>(R.id.paywallBulletText).text = text
            container.addView(row)
        }
    }

    private fun selectPlan(key: String) {
        selectedPlanKey = key
        val isMonthly = key == "monthly"

        monthlyTile.background = ContextCompat.getDrawable(
            this,
            if (isMonthly) R.drawable.atrium_paywall_plan_active
            else R.drawable.atrium_paywall_plan_dormant,
        )
        yearlyTile.background = ContextCompat.getDrawable(
            this,
            if (isMonthly) R.drawable.atrium_paywall_plan_dormant
            else R.drawable.atrium_paywall_plan_active,
        )
        monthlyPrice.setTextColor(
            ContextCompat.getColor(
                this,
                if (isMonthly) R.color.atrium_accent_cyan else R.color.atrium_ink,
            ),
        )
        yearlyPrice.setTextColor(
            ContextCompat.getColor(
                this,
                if (isMonthly) R.color.atrium_ink else R.color.atrium_accent_cyan,
            ),
        )
    }

    /** One subscription plan tile in the paywall's 2-column grid. */
    data class Plan(
        val key: String,
        val title: String,
        val price: String,
        val sub: String,
        val isRecommended: Boolean,
    )

    /** Terminal decision after a server verify — see [verifyOutcome]. */
    enum class VerifyOutcome { PRO_ACTIVATED, KEEP_PAYWALL }

    companion object {
        private const val TAG = "PaywallActivity"

        /**
         * SharedPreferences key (in [MainActivity.PREFS_NAME]) caching
         * the last server-confirmed plan ("free" / "pro").  Written by
         * this activity after a verified purchase; read by the limit /
         * upgrade UI (client-reaction follow-up).  A UI cache only —
         * the server re-decides entitlement on every metered call.
         */
        const val PREF_PLAYER_PLAN = "player_plan"

        /**
         * Paywall plan key → Play Console product id.  Must stay in
         * lock-step with the server's `KNOWN_PRODUCTS` in
         * `llm/seca/billing/router.py` (both products grant plan "pro")
         * and with the products configured in the Play Console.
         */
        val PLAY_PRODUCT_IDS: Map<String, String> = mapOf(
            "monthly" to "pro_monthly",
            "yearly" to "pro_yearly",
        )

        /**
         * The Play product to purchase for a paywall plan key.  Unknown
         * keys fall back to the monthly product — defensive only; the
         * activity's click listeners can only produce catalogue keys
         * (pinned by [PaywallActivityTest]).
         */
        fun productIdFor(planKey: String): String =
            PLAY_PRODUCT_IDS[planKey] ?: PLAY_PRODUCT_IDS.getValue("monthly")

        /**
         * Decide the paywall's terminal state from a verify response.
         * ONLY an [ApiResult.Success] whose body says `plan == "pro"`
         * activates — every error (402 not entitled, 502/503 upstream,
         * network, timeout) and every non-pro body keeps the paywall
         * open and the purchase unacknowledged.  Static + framework-free
         * so the host-JVM test suite pins the transition table.
         */
        fun verifyOutcome(result: ApiResult<BillingVerifyResponse>): VerifyOutcome =
            if (result is ApiResult.Success && result.data.plan == "pro") {
                VerifyOutcome.PRO_ACTIVATED
            } else {
                VerifyOutcome.KEEP_PAYWALL
            }

        /**
         * Canonical plan-tile copy, bound to the tiles in [onCreate].
         * Lifted to the companion so unit tests can verify the shape
         * without launching the activity; the "yearly" entry is marked
         * recommended (drives the initial active-tile selection).
         *
         * LAUNCH PRICING (2026-07): €9.99/month; yearly €71.99 (= €6 a
         * month, ~40% off).  Chosen against the MEASURED unit costs —
         * a fully-coached game ≈ $0.0033 in DeepSeek tokens, so a
         * heavy Pro user costs well under €1/month (≥95% gross margin
         * after ~20% VAT + Play's 15% fee).  These labels are DISPLAY
         * copy: what gets billed is the Play Console product behind
         * [PLAY_PRODUCT_IDS] — change both together, and let Play's
         * per-country price templates localise the actual charge.
         */
        val DEFAULT_PLANS: List<Plan> = listOf(
            Plan(
                key = "monthly",
                title = "Monthly",
                price = "€9.99",
                sub = "per month",
                isRecommended = false,
            ),
            Plan(
                key = "yearly",
                title = "Yearly",
                price = "€71.99",
                sub = "€6 / month",
                isRecommended = true,
            ),
        )

        /**
         * Bullet copy for the feature list.  4 items per the design;
         * order matters (the most concrete benefit comes first).
         *
         * Every bullet must describe a benefit the live entitlements
         * actually grant (llm/seca/entitlements/service.py): unlimited
         * games (free: 1/day), full coach hints (free: degraded past
         * the daily coached-game limit), and 30 chat questions/day
         * (free: 3).  The old "Full curriculum · 12 chapters" and
         * "Opening repertoire drills" bullets were retired with their
         * UI surfaces (PR #379 removed the Lessons and Openings
         * screens).
         */
        val DEFAULT_FEATURES: List<String> = listOf(
            "Unlimited adaptive games",
            "Full coach hints in every game",
            "Coach chat · grounded in your games",
            "30 coach questions a day",
        )

        /**
         * Recommended plan key used by the activity's initial tile
         * selection.  Defaults to "yearly" (matching the design)
         * unless every plan's `isRecommended` is false, in which
         * case we fall back to the first plan.
         */
        fun recommendedPlanKey(plans: List<Plan> = DEFAULT_PLANS): String =
            plans.firstOrNull { it.isRecommended }?.key
                ?: plans.firstOrNull()?.key
                ?: "yearly"
    }
}
package com.cereveon.myapp

import android.content.SharedPreferences
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString

/**
 * Helpers for the offline /game/finish retry path.
 *
 * Lifecycle
 * ---------
 * When MainActivity.onGameOver hits a transient failure on
 * /game/finish — timeout, network error, or 5xx — the request payload
 * is JSON-serialised via [toJson] and persisted to
 * [PREF_PENDING_FINISH_PAYLOAD] in SharedPreferences.  On the next
 * MainActivity cold-start [fromJson] rehydrates it and the activity
 * tries the call again.  Success clears the slot; a still-transient
 * failure (including 429 — see [isTransient]) leaves it for the next
 * attempt; any other 4xx response (except 401, which is handled
 * separately) clears the slot since the call would just fail again.
 *
 * This closes a real silent-data-loss bug: an entire game's PGN +
 * weakness analysis used to be dropped on the floor when the network
 * hiccupped at exactly the wrong moment, and the user had no way to
 * recover the work.
 *
 * The slot is one-deep on purpose — chess games take 10–30 minutes,
 * so multiple pending finishes is a vanishingly rare edge.  A second
 * pending finish overwrites the first; the older one is gone.  Future
 * improvement: a queue, or fire-and-forget retry that doesn't block
 * the next finish attempt.
 */
object PendingGameFinish {

    const val PREF_PENDING_FINISH_PAYLOAD = "pending_game_finish_payload"

    /**
     * Should this [ApiResult] failure be retried later, or is it
     * permanent (4xx, success)?  We retry only on signals that
     * suggest "the request never reached a healthy server":
     *   - Timeout: server might be slow / unreachable, retry
     *   - NetworkError: connection refused / DNS / etc., retry
     *   - HttpError 5xx: server-side incident, retry
     *   - HttpError 429: rate-limited, NOT a payload rejection — the
     *     same payload succeeds once the window resets.  /game/finish
     *     is limited to 10/min server-side, so a burst of quick games
     *     (or a proxy-collapsed rate bucket) must not cost the game.
     *
     * Other 4xx (401 is handled by handleSessionExpired upstream)
     * indicates a payload the server actively rejected — retrying
     * with the same payload would just fail again, so we don't.
     */
    fun isTransient(result: ApiResult<*>): Boolean = when (result) {
        is ApiResult.Timeout       -> true
        is ApiResult.NetworkError  -> true
        is ApiResult.HttpError     -> result.code >= 500 || result.code == 429
        is ApiResult.Success       -> false
    }

    /**
     * Serialise a GameFinishRequest into a JSON blob suitable for
     * SharedPreferences.  Schema mirrors the wire format the
     * HttpGameApiClient sends so the round-trip is loss-free.  Since
     * Sprint 4.3.C, [GameFinishRequest] itself is @Serializable so
     * the on-disk format is identical to what the HTTP client posts.
     * Null ``player_id`` / ``game_id`` are dropped by the shared
     * [ApiJson] config (``encodeDefaults = false``).
     */
    fun toJson(req: GameFinishRequest): String =
        ApiJson.encodeToString(req)

    /**
     * Inverse of [toJson] — returns null when the blob is malformed
     * (corrupted prefs, partial write, schema drift across upgrades)
     * so the caller can drop the slot and continue rather than crash.
     * Missing optional fields (player_id, game_id) round-trip to null.
     * An empty-string optional from a legacy payload is normalised
     * back to null to preserve the pre-Sprint-4.3.C behaviour.
     */
    fun fromJson(json: String): GameFinishRequest? = try {
        val req = ApiJson.decodeFromString<GameFinishRequest>(json)
        // Legacy parser dropped empty optionals to null via ``takeIf``;
        // keep that invariant so a fromJson(toJson(req)) round-trip
        // still maps "" → null for callers that key off nullability.
        req.copy(
            playerId = req.playerId?.takeIf { it.isNotEmpty() },
            gameId = req.gameId?.takeIf { it.isNotEmpty() },
        )
    } catch (_: Exception) {
        null
    }

    /**
     * What to do with the prefs slot after a retry attempt completes.
     * Pulled out as a pure mapping over [ApiResult] so it can be unit
     * tested on the host JVM without spinning up SharedPreferences.
     */
    enum class RetryAction {
        /** /game/finish succeeded — drop the slot. */
        DONE,
        /** Non-transient failure (e.g. 4xx) — drop the slot since
         *  retrying with the same payload would just fail again. */
        DROP,
        /** Transient failure (timeout / network / 5xx) — keep the
         *  slot for the next cold-start. */
        RESTORE,
        /** 401 — keep the slot, route the caller to its login flow.
         *  The next successful auth + cold-start will retry. */
        SESSION_EXPIRED,
    }

    /**
     * Classify the outcome of the retry's /game/finish call.  Mirrors
     * [isTransient] but adds the 401-vs-other-4xx distinction since
     * retry semantics differ:
     *   - 401 means "auth lapsed", which is recoverable on next login;
     *     keep the payload.
     *   - 429 is transient per [isTransient]; keep the payload.
     *   - Other 4xx means "server rejected this payload"; drop it.
     */
    fun classifyRetryResult(result: ApiResult<*>): RetryAction = when (result) {
        is ApiResult.Success      -> RetryAction.DONE
        is ApiResult.HttpError    -> when {
            result.code == 401 -> RetryAction.SESSION_EXPIRED
            isTransient(result) -> RetryAction.RESTORE
            else                -> RetryAction.DROP
        }
        is ApiResult.NetworkError -> RetryAction.RESTORE
        is ApiResult.Timeout      -> RetryAction.RESTORE
    }

    /**
     * Drop the pending finish payload from [prefs].  Idempotent —
     * a no-op when no payload is present.  Returns true iff a
     * payload was actually removed (so callers can decide whether
     * to show a "Discarded" toast vs. nothing).
     *
     * Used by HomeActivity's long-press-on-sync-indicator → confirm
     * affordance; gives the user an escape hatch when a stuck pending
     * finish keeps failing (e.g. the underlying game has a malformed
     * PGN the server perma-rejects but isTransient classifies as 5xx,
     * or the user just no longer cares about the lost game).
     */
    fun discardFromPrefs(prefs: SharedPreferences): Boolean {
        if (!prefs.contains(PREF_PENDING_FINISH_PAYLOAD)) return false
        prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
        return true
    }

    /**
     * Process-singleton guard against double-fire when both
     * MainActivity and HomeActivity try to retry on the same
     * cold-start.  Without this, the user's offline game would
     * post twice and create two GameEvent rows server-side.
     *
     * Compare-and-set: a second concurrent caller sees the flag
     * already set and returns early.  Cleared in the launch's
     * `finally` so a thrown / cancelled coroutine doesn't pin the
     * flag forever.
     */
    private val retryInFlight = AtomicBoolean(false)

    /**
     * Read the pending payload from [prefs], fire [client]'s
     * /game/finish, and update the slot per [classifyRetryResult].
     *
     * Designed to be safe to call from multiple activity cold-starts
     * back-to-back — the [retryInFlight] flag prevents double-firing.
     * Both [onSuccess] and [onSessionExpired] are invoked on the
     * coroutine's resumed dispatcher (typically Main, since the
     * caller passes [androidx.lifecycle.lifecycleScope]).
     */
    fun retryFromPrefs(
        prefs: SharedPreferences,
        client: GameApiClient,
        scope: CoroutineScope,
        onSuccess: () -> Unit = {},
        onSessionExpired: () -> Unit = {},
    ) {
        if (!retryInFlight.compareAndSet(false, true)) {
            Log.d("GAME", "Pending finish retry skipped — another retry is in flight")
            return
        }
        val raw = prefs.getString(PREF_PENDING_FINISH_PAYLOAD, null)
        if (raw == null) {
            retryInFlight.set(false)
            return
        }
        val req = fromJson(raw)
        if (req == null) {
            // Corrupted blob — drop it so we don't keep tripping over it.
            prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
            retryInFlight.set(false)
            Log.w("GAME", "Dropping malformed pending finish payload")
            return
        }
        scope.launch {
            try {
                when (classifyRetryResult(client.finishGame(req))) {
                    RetryAction.DONE -> {
                        prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
                        onSuccess()
                        Log.d("GAME", "Pending finish synced successfully")
                    }
                    RetryAction.DROP -> {
                        prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
                        Log.w("GAME", "Pending finish non-retryable; dropping")
                    }
                    RetryAction.RESTORE -> {
                        Log.d("GAME", "Pending finish still transient; keeping for next try")
                    }
                    RetryAction.SESSION_EXPIRED -> {
                        Log.d("GAME", "Pending finish hit 401; keeping for post-login retry")
                        onSessionExpired()
                    }
                }
            } finally {
                retryInFlight.set(false)
            }
        }
    }
}
package com.cereveon.myapp

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * In-memory cache for the authenticated player's progress snapshot.
 *
 * The cache exists so the per-move strength dial (`/move` request)
 * doesn't hit `/player/progress` on every move — the snapshot only
 * really changes between games.  Two invalidation paths:
 *
 *  - **Explicit**: [invalidate] is called from
 *    `MainActivity` after every successful `/game/finish` so the next
 *    AI move sees the rating bump the server applied.
 *  - **Implicit (TTL)**: the cached entry expires after [ttlMillis]
 *    (default 5 minutes).  This bounds the staleness window for
 *    out-of-band changes the explicit path can't see — admin edits to
 *    the player's rating, a sibling account on another device finishing
 *    a game, a manual `/auth/me` push that updated the cached profile.
 *    Mid-session drift is rare but not impossible; without a TTL the
 *    only recovery is killing and reopening the app.
 *
 * Five minutes is a comfortable default: long enough that the typical
 * within-game read is a cache hit, short enough that drift recovers
 * automatically before the next reasonable game starts.
 *
 * The clock is injectable for tests via [nowMs] so we can exercise
 * expiry deterministically without manipulating wall-clock time.
 */
class PlayerProfileCache(
    private val apiClient: GameApiClient,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS,
    private val nowMs: () -> Long = System::currentTimeMillis,
) {

    private data class CachedEntry(
        val dto: ProgressCurrentDto,
        val expiresAtMillis: Long,
    )

    private val mutex = Mutex()

    @Volatile private var cached: CachedEntry? = null

    suspend fun getOpponentElo(): Int {
        // Fast path — read the volatile entry once and validate its
        // freshness against the injected clock.  Two reads of `cached`
        // can in principle return different snapshots if a writer
        // races; that's harmless because both are valid points in
        // time and the slow path under the mutex resolves any tie.
        cached?.let { entry ->
            if (entry.expiresAtMillis > nowMs()) {
                return entry.dto.opponentElo
            }
        }

        return mutex.withLock {
            // Re-check under the lock — a concurrent waiter may have
            // already refreshed by the time we acquired it.
            cached?.let { entry ->
                if (entry.expiresAtMillis > nowMs()) {
                    return@withLock entry.dto.opponentElo
                }
            }
            val result = apiClient.getPlayerProgress()
            if (result is ApiResult.Success) {
                val current = result.data.current
                cached = CachedEntry(
                    dto = current,
                    expiresAtMillis = nowMs() + ttlMillis,
                )
                current.opponentElo
            } else {
                error("getPlayerProgress failed: $result")
            }
        }
    }

    /**
     * Non-suspending peek at the cached opponent ELO.  Returns null
     * when the cache is empty or the cached entry has expired.
     *
     * Exists so the AI-move dispatch path ([ChessViewModel.requestAIMove])
     * never blocks on `/player/progress`.  When the backend is slow
     * (or unreachable — see SECA "Backend safety unverified" condition)
     * the suspending [getOpponentElo] would otherwise wait up to the
     * read timeout (~15s) on every move, which leaves `turn = AI` and
     * makes the board appear frozen until the request finally fails.
     *
     * Callers that read this should fall back to a deterministic
     * default (e.g. strength 100) when null is returned, and rely on
     * a background [warm] coroutine to populate the cache out-of-band.
     */
    fun cachedOpponentEloOrNull(): Int? =
        cached?.takeIf { it.expiresAtMillis > nowMs() }?.dto?.opponentElo

    /**
     * Best-effort cache populate.  Suitable for fire-and-forget use
     * from `lifecycleScope.launch { cache.warm() }` at cold-start and
     * after [invalidate] (e.g. post-`/game/finish`).  Failures are
     * swallowed — the AI path tolerates a null cache via
     * [cachedOpponentEloOrNull].
     */
    suspend fun warm() {
        try {
            getOpponentElo()
        } catch (_: Exception) {
            // The backend is unhealthy; the AI path falls back to
            // strength 100 in the meantime.  No need to surface the
            // failure here — the SECA snackbar already explains.
        }
    }

    fun invalidate() {
        cached = null
    }

    companion object {
        /** Default time-to-live for cached progress: 5 minutes. */
        const val DEFAULT_TTL_MILLIS: Long = 5L * 60L * 1000L
    }
}
package com.cereveon.myapp

import android.content.Context
import android.graphics.Typeface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.max

/**
 * Full-screen bottom sheet showing the player's profile — the "You"
 * surface: human progress up top, then the coaching dashboard.
 *
 * Sections (post-Elo-removal):
 *  1. Human-progress header — "Level N" + "X XP" hero (cached training
 *     XP, same source as the Home kicker) and a "Recent games ·
 *     N played · M won" stat row from the /player/progress history.
 *  2. Weakness profile — [WeaknessBarChartView] of category scores from the world model.
 *  3. "How the coach sees you" — world-model fields in plain language.
 *     (OPPONENT ELO row suppressed — would otherwise leak the
 *     player's own hidden rating since opponent = rating - ~40.)
 *  4. Coach's plan — most recent decision from SharedPreferences.
 *  5. Training focus — prioritised recommendations from HistoricalAnalysisPipeline.
 *
 * Retired surfaces
 * ----------------
 * The Elo hero cell and confidence row were retired when the
 * user-visible Elo display was hidden, and are now repurposed in
 * place (same view IDs) as the human-progress header above.  The
 * "Rating trend" sparkline section stays fully retired — its views
 * remain in the layout with ``visibility="gone"`` and nothing flips
 * them visible any more (the old ``populateSparkline`` could resurrect
 * the sparkline and re-leak the hidden rating trend); the slot is
 * reserved for a future XP-progress visualisation.
 *
 * Data is fetched from GET /player/progress (Bearer auth).
 * Inject [gameApiClient] before calling [show].
 */
class ProgressDashboardBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the hosting activity before [show] is called. */
    var gameApiClient: GameApiClient? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_progress_dashboard, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val heroLevelBlock      = view.findViewById<LinearLayout>(R.id.heroLevelBlock)
        val txtRating           = view.findViewById<TextView>(R.id.txtRating)
        val txtHeroXp           = view.findViewById<TextView>(R.id.txtHeroXp)
        val statGamesRow        = view.findViewById<LinearLayout>(R.id.statGamesRow)
        val statGamesDivider    = view.findViewById<View>(R.id.statGamesDivider)
        val txtConfidence       = view.findViewById<TextView>(R.id.txtConfidence)
        val weaknessChart       = view.findViewById<WeaknessBarChartView>(R.id.weaknessChart)
        val worldModelContainer = view.findViewById<LinearLayout>(R.id.worldModelContainer)
        val recommendationsList = view.findViewById<LinearLayout>(R.id.recommendationsList)
        val txtNoRecs           = view.findViewById<TextView>(R.id.txtNoRecommendations)
        val txtError            = view.findViewById<TextView>(R.id.txtDashboardError)

        // Human-progress hero — Level / XP from the same SharedPreferences
        // cache that backs the Home kicker (written on every /auth/me
        // round-trip).  Rendered synchronously so the header shows before,
        // and regardless of, the /player/progress fetch below.  Hidden on
        // a fresh install until the first /auth/me lands, matching the
        // Home kicker's behaviour.
        val cachedXp = requireContext()
            .getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
            .getInt(MainActivity.PREF_TRAINING_XP, -1)
        if (cachedXp >= 0) {
            txtRating.text = formatHeroLevel(cachedXp)
            txtHeroXp.text = formatHeroXp(cachedXp)
            heroLevelBlock.visibility = View.VISIBLE
        }

        // ── Coach's plan section (read-only from SharedPreferences) ─────────
        // Populated by GameSummaryBottomSheet on every /game/finish.  The
        // section renders only when there's actually a coach decision to
        // show — first-run / freshly-logged-in players see the existing
        // dashboard sections without an empty coach card stub.
        populateCoachPlanFromPrefs(view)

        val client = gameApiClient ?: run {
            txtError.visibility = View.VISIBLE
            return
        }

        lifecycleScope.launch {
            when (val result = client.getPlayerProgress()) {
                is ApiResult.Success -> {
                    val data = result.data
                    populateGamesRow(statGamesRow, statGamesDivider, txtConfidence, data.history)
                    populateWeaknessChart(weaknessChart, data)
                    populateWorldModel(worldModelContainer, data.current)
                    populateRecommendations(recommendationsList, txtNoRecs, data.analysis)
                }
                else -> {
                    txtError.visibility = View.VISIBLE
                }
            }
        }
    }

    // ── Sections ─────────────────────────────────────────────────────────────

    /**
     * Read the most-recent coach decision from SharedPreferences and
     * surface it as a card above "Training focus".  When there's no
     * decision yet (fresh account / never finished a game), the
     * kicker + card + divider stay hidden and the dashboard reads
     * exactly as it did pre-PR-#172.
     *
     * Source keys are populated by ``GameSummaryBottomSheet``'s
     * persist block — see the matching ``putString`` calls there.
     * The contract is one-way: dashboard never writes, only reads.
     * Logout-time cleanup of these keys is owned by the same
     * SharedPreferences scrub that nukes ``last_rating`` etc.
     */
    private fun populateCoachPlanFromPrefs(view: View) {
        val prefs = requireContext().getSharedPreferences(
            MainActivity.PREFS_NAME,
            Context.MODE_PRIVATE,
        )
        val actionType  = prefs.getString(MainActivity.PREF_LAST_COACH_ACTION_TYPE, null).orEmpty()
        val weakness    = prefs.getString(MainActivity.PREF_LAST_COACH_WEAKNESS, null).orEmpty()
        val reason      = prefs.getString(MainActivity.PREF_LAST_COACH_REASON, null).orEmpty()
        val title       = prefs.getString(MainActivity.PREF_LAST_COACH_TITLE, null).orEmpty()
        val description = prefs.getString(MainActivity.PREF_LAST_COACH_DESCRIPTION, null).orEmpty()

        // Show the section iff we have ANY non-trivial coach content.
        // ``NONE/No trigger`` with empty title+description+weakness is
        // the "controller didn't fire" idle state — no value showing it
        // to the user.
        val hasContent =
            (title.isNotBlank() || description.isNotBlank()) ||
                (actionType.isNotBlank() && actionType != "NONE") ||
                weakness.isNotBlank()

        val kicker  = view.findViewById<TextView>(R.id.txtCoachPlanKicker)
        val card    = view.findViewById<AtriumCardView>(R.id.coachPlanCard)
        val divider = view.findViewById<View>(R.id.coachPlanDivider)

        if (!hasContent) {
            kicker.visibility  = View.GONE
            card.visibility    = View.GONE
            divider.visibility = View.GONE
            return
        }

        val txtAction      = view.findViewById<TextView>(R.id.txtCoachPlanAction)
        val txtWeakness    = view.findViewById<TextView>(R.id.txtCoachPlanWeakness)
        val txtTitle       = view.findViewById<TextView>(R.id.txtCoachPlanTitle)
        val txtDescription = view.findViewById<TextView>(R.id.txtCoachPlanDescription)
        val txtReason      = view.findViewById<TextView>(R.id.txtCoachPlanReason)

        txtAction.text = actionVerdictLabel(actionType)

        if (weakness.isNotBlank()) {
            txtWeakness.text = "FOCUS · ${weakness.uppercase()}"
            txtWeakness.visibility = View.VISIBLE
        } else {
            txtWeakness.visibility = View.GONE
        }

        if (title.isNotBlank()) {
            txtTitle.text = title
            txtTitle.visibility = View.VISIBLE
        } else {
            txtTitle.visibility = View.GONE
        }

        if (description.isNotBlank()) {
            txtDescription.text = description
            txtDescription.visibility = View.VISIBLE
        } else {
            txtDescription.visibility = View.GONE
        }

        if (reason.isNotBlank()) {
            txtReason.text = reason
            txtReason.visibility = View.VISIBLE
        } else {
            txtReason.visibility = View.GONE
        }

        kicker.visibility  = View.VISIBLE
        card.visibility    = View.VISIBLE
        divider.visibility = View.VISIBLE
    }

    /**
     * Map raw ``CoachAction.type`` strings to readable card kickers.
     * Mirrors ``GameSummaryBottomSheet.actionBadgeLabel`` so the same
     * action surfaces with the same label across both screens.
     */
    private fun actionVerdictLabel(actionType: String): String = when (actionType.uppercase()) {
        "DRILL"       -> "DRILL"
        "PUZZLE"      -> "PUZZLE"
        "REFLECT"     -> "REFLECT"
        "PLAN_UPDATE" -> "PLAN UPDATE"
        "CELEBRATE"   -> "CELEBRATE"
        "NONE", ""    -> "COACH"
        else          -> "COACH"
    }

    /**
     * "Recent games · N played · M won" stat row.  Sourced from the
     * /player/progress history window (newest-first, server-capped at
     * 20 rows) — no endpoint returns lifetime totals, so the label
     * says "Recent" honestly rather than implying an all-time count.
     * The divider under the row flips visible with it so a fetch
     * failure never leaves an orphan hairline above WEAKNESS PROFILE.
     */
    private fun populateGamesRow(
        row: View,
        divider: View,
        txtGames: TextView,
        history: List<ProgressHistoryItem>,
    ) {
        txtGames.text = formatGamesSummary(history)
        row.visibility = View.VISIBLE
        divider.visibility = View.VISIBLE
    }

    private fun populateWeaknessChart(
        chart: WeaknessBarChartView,
        data: PlayerProgressResponse,
    ) {
        // Build entries from category_scores; annotate with recommendation priority.
        val priorityMap = data.analysis.recommendations.associate { it.category to it.priority }

        val labelFor = mapOf(
            "tactical_vision"      to "Tactics",
            "opening_preparation"  to "Opening",
            "endgame_technique"    to "Endgame",
            "positional_play"      to "Position",
        )

        val entries = data.analysis.categoryScores
            .entries
            .sortedByDescending { it.value }
            .map { (cat, score) ->
                WeaknessBarChartView.Entry(
                    label    = labelFor[cat] ?: cat,
                    value    = score,
                    priority = priorityMap[cat] ?: "",
                )
            }

        // Fall back to raw skill_vector when no pipeline data available.
        val finalEntries = if (entries.isEmpty()) {
            data.current.skillVector.entries
                .sortedByDescending { it.value }
                .map { (k, v) -> WeaknessBarChartView.Entry(label = k, value = v) }
        } else {
            entries
        }

        chart.setEntries(finalEntries)
    }

    private fun populateWorldModel(
        container: LinearLayout,
        current: ProgressCurrentDto,
    ) {
        val tierLabel = when (current.tier) {
            "beginner"     -> "Beginner — keep it simple"
            "intermediate" -> "Intermediate — building concepts"
            "advanced"     -> "Advanced — deep analysis"
            else           -> current.tier
        }

        val styleLabel = when (current.teachingStyle) {
            "simple"       -> "Simple explanations, 1 concept at a time"
            "intermediate" -> "Balanced depth, some variations shown"
            "advanced"     -> "Full analysis, all variations"
            else           -> current.teachingStyle
        }

        // OPPONENT ELO was removed when the user-visible Elo rating was
        // hidden from the UI — exposing the matched-opponent rating
        // here would leak the player's own (now-hidden) rating since
        // the matcher derives the opponent from rating - ~40.  The
        // adaptive-difficulty selection itself still happens
        // internally; it is just no longer displayed.
        val rows = listOf(
            "TIER"          to tierLabel,
            "COACH STYLE"   to styleLabel,
            "DEPTH"         to "%.0f%%".format(current.explanationDepth * 100),
            "COMPLEXITY"    to "%.0f%%".format(current.conceptComplexity * 100),
        )

        rows.forEach { (label, value) ->
            container.addView(buildWorldModelRow(label, value))
        }
    }

    private fun populateRecommendations(
        list: LinearLayout,
        txtNone: TextView,
        analysis: ProgressAnalysisDto,
    ) {
        if (analysis.recommendations.isEmpty()) {
            txtNone.visibility = View.VISIBLE
            return
        }
        analysis.recommendations.forEach { rec ->
            list.addView(buildRecommendationRow(rec))
        }
    }

    // ── Row builders ─────────────────────────────────────────────────────────

    private fun buildWorldModelRow(label: String, value: String): View {
        // Atrium re-skin: mono dim kicker on the left, mono ink value
        // on the right.  Mono stays for telemetry rows per Atrium's
        // "Numerics: JetBrains Mono" role; the colour shift to the
        // dim/ink tokens lines up with the rest of the dashboard.
        val ctx = requireContext()
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 8, 0, 8)

            addView(TextView(ctx).apply {
                text = label
                textSize = 11f
                setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_dim))
                typeface = Typeface.MONOSPACE
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            })

            addView(TextView(ctx).apply {
                text = value
                textSize = 12f
                setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_ink))
                typeface = Typeface.MONOSPACE
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 2f)
            })
        }
    }

    private fun buildRecommendationRow(rec: ProgressRecommendation): View {
        // Atrium two-tone signal — mirrors WeaknessBarChartView.priorityColor
        // so high-severity recommendations read amber (warning role) and
        // low-severity read cyan (improving / player-side).  Token reads
        // so bright mode flips them via values-notnight/colors.xml.
        val ctx = requireContext()
        val priorityColor = when (rec.priority) {
            "high"   -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_amber)
            "medium" -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_amber_cc)
            else     -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_cyan)
        }

        val categoryLabel = rec.category
            .replace("_", " ")
            .split(" ")
            .joinToString(" ") { it.replaceFirstChar(Char::uppercase) }

        // Atrium colours: priority kicker in the two-tone signal
        // (amber for high/medium, cyan for low), category in ink,
        // rationale in muted ink.  Mono kept for kickers; category
        // and rationale stay mono here for compact-list density —
        // promoting them to Cormorant italic would inflate row
        // height and crowd the small-screen bottom sheet.
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 10, 0, 10)

            addView(LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL

                addView(TextView(ctx).apply {
                    text = rec.priority.uppercase()
                    textSize = 9f
                    setTextColor(priorityColor)
                    typeface = Typeface.MONOSPACE
                    setPadding(0, 0, 12, 0)
                })

                addView(TextView(ctx).apply {
                    text = categoryLabel
                    textSize = 13f
                    setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_ink))
                    typeface = Typeface.MONOSPACE
                })
            })

            addView(TextView(ctx).apply {
                text = rec.rationale
                textSize = 11f
                setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_muted))
                typeface = Typeface.MONOSPACE
                setPadding(0, 4, 0, 0)
            })
        }
    }

    companion object {

        // ── Pure display helpers — testable without Android framework ─────────

        /**
         * "Level N" hero line.  Reuses [HomeActivity.XP_PER_LEVEL] and
         * clamps like [HomeActivity.formatXpKicker] (negatives / fresh
         * accounts read "Level 1") so the profile hero and the Home
         * kicker can never disagree on the level curve.
         */
        fun formatHeroLevel(xp: Int): String {
            val safeXp = max(0, xp)
            return "Level ${max(1, safeXp / HomeActivity.XP_PER_LEVEL + 1)}"
        }

        /** "340 XP" kicker under the hero level; clamps negatives to 0. */
        fun formatHeroXp(xp: Int): String = "${max(0, xp)} XP"

        /**
         * "N played · M won" from the /player/progress history window.
         * Only "win" rows count as won — draws and losses contribute to
         * the played count alone.
         */
        fun formatGamesSummary(history: List<ProgressHistoryItem>): String {
            val won = history.count { it.result.equals("win", ignoreCase = true) }
            return "${history.size} played · $won won"
        }
    }
}
package com.cereveon.myapp

import android.app.Dialog
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import androidx.fragment.app.DialogFragment

class PromotionDialogFragment(
    private val onSelect: (Char) -> Unit
) : DialogFragment() {

    override fun onCreateDialog(savedInstanceState: Bundle?): Dialog {
        val dialog = Dialog(requireContext())
        dialog.setCancelable(false)

        val layout = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(24, 24, 24, 24)
            // Surface token (was a near-identical literal #0B0F14) so
            // bright mode flips it via values-notnight/colors.xml.
            setBackgroundColor(
                androidx.core.content.ContextCompat.getColor(
                    requireContext(), R.color.atrium_bg_surface,
                ),
            )
        }

        fun add(label: String, piece: Char) {
            val btn = Button(requireContext()).apply {
                text = label
                textSize = 20f
                minWidth = 120
                setOnClickListener {
                    onSelect(piece)
                    dismiss()
                }
            }
            layout.addView(btn)
        }

        add("Queen ♕", 'Q')
        add("Rook ♖", 'R')
        add("Bishop ♗", 'B')
        add("Knight ♘", 'N')   // ✅ now clearly visible

        dialog.setContentView(layout)
        return dialog
    }
}
package com.cereveon.myapp

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Puzzle trainer bottom sheet (Puzzles tab).
 *
 * Endless practice-puzzle stream, decoupled from the per-mistake study
 * plan.  Each puzzle comes from ``GET /puzzles/next`` — live-fetched
 * from Lichess at the player's rating-derived difficulty, with a
 * server-side fallback to the curated corpus (one wire shape either
 * way; [PuzzleNextDto.source] carries the attribution).
 *
 * Flow per puzzle
 * ---------------
 *  1.  [fetchNextPuzzle] loads a position; the board flips when Black
 *      is to move so the solver's colour sits at the bottom, and the
 *      status row announces the side ("White to move").
 *  2.  A move attempt round-trips ``POST /training/verify-replay`` —
 *      the LOCAL engine judges it; the Lichess solution move is never
 *      the oracle (same trust anchor as the drill sheets).
 *  3a. Correct → ``POST /training/solve`` with
 *      ``source_type="standard_puzzle"`` and
 *      ``source_ref=<puzzle_id>`` credits +10 XP (deduped per puzzle
 *      by the server's unique triple), the XP cache refreshes so Home
 *      re-renders, and the board locks on the solved position until
 *      "Next puzzle" advances.
 *  3b. Wrong → "Not quite, try again." — position resets, no penalty,
 *      unlimited retries (matches [TodaysDrillBottomSheet]).
 *
 * "Next puzzle" doubles as a skip: tapping it before a solve just
 * fetches a fresh position (no XP, no penalty).
 */
class PuzzleTrainerBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host before [show]; required for every call. */
    var gameApiClient: GameApiClient? = null

    private lateinit var board: ChessBoardView
    private lateinit var kickerView: TextView
    private lateinit var statusView: TextView
    private lateinit var nextButton: Button

    private var puzzle: PuzzleNextDto? = null

    /** True while a /puzzles/next fetch is in flight — debounces the
     *  Next button so a double-tap can't burn two fetches. */
    private var fetching: Boolean = false

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_puzzle_trainer, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        kickerView = view.findViewById(R.id.puzzleTrainerKicker)
        statusView = view.findViewById(R.id.puzzleTrainerStatus)
        board = view.findViewById(R.id.puzzleTrainerBoard)
        nextButton = view.findViewById(R.id.puzzleTrainerNextButton)

        board.isInteractive = false
        board.onMovePlayed = { fr, fc, tr, tc -> handleAttempt(fr, fc, tr, tc) }

        nextButton.setOnClickListener { fetchNextPuzzle() }
        view.findViewById<Button>(R.id.puzzleTrainerCloseButton).setOnClickListener {
            dismiss()
        }

        fetchNextPuzzle()
    }

    /**
     * Load the next puzzle.  On failure the status row carries the
     * error and the Next button stays live as the retry affordance —
     * the sheet never dead-ends silently.
     */
    private fun fetchNextPuzzle() {
        if (fetching) return
        val client = gameApiClient ?: run {
            setStatus("Couldn't load a puzzle. Try again.", ATRIUM_AMBER_COLOR_RES)
            return
        }
        fetching = true
        board.isInteractive = false
        setStatus("Finding a puzzle...", ATRIUM_DIM_COLOR_RES)

        lifecycleScope.launch {
            val result = try {
                client.getNextPuzzle()
            } catch (_: Exception) {
                null
            } finally {
                fetching = false
            }
            val next = (result as? ApiResult.Success)?.data
            if (next == null || next.fen.isBlank()) {
                setStatus("Couldn't load a puzzle. Try again.", ATRIUM_AMBER_COLOR_RES)
                return@launch
            }
            puzzle = next
            kickerView.text = formatKicker(next)
            board.setFEN(next.fen)
            // Solver's colour at the bottom — a random puzzle can put
            // the user on either side.
            board.flipped = isBlackToMove(next.fen)
            board.isInteractive = true
            setStatus(sideToMoveLabel(next.fen), ATRIUM_DIM_COLOR_RES)
        }
    }

    private fun handleAttempt(fr: Int, fc: Int, tr: Int, tc: Int) {
        val current = puzzle ?: return
        // Lock the board the moment the move lands so the user can't
        // fire a second attempt while the first is in flight.  Wrong-
        // move recovery re-enables; a solve leaves it locked until
        // "Next puzzle" advances.
        board.isInteractive = false

        val moveResult = board.applyMove(fr, fc, tr, tc)
        if (moveResult == MoveResult.FAILED) {
            // Illegal in this position (e.g. moves into check).
            board.isInteractive = true
            return
        }

        val moveUci = MistakeReplayBottomSheet.rowColToUci(fr, fc) +
            MistakeReplayBottomSheet.rowColToUci(tr, tc)
        setStatus("Checking...", ATRIUM_DIM_COLOR_RES)

        val client = gameApiClient ?: run {
            setStatus("Couldn't reach the engine.", ATRIUM_AMBER_COLOR_RES)
            resetPosition(current)
            return
        }

        lifecycleScope.launch {
            when (val verify = client.verifyReplayMove(current.fen, moveUci)) {
                is ApiResult.Success -> {
                    if (verify.data.isCorrect) {
                        creditXp(client, current)
                    } else {
                        setStatus("Not quite, try again.", ATRIUM_AMBER_COLOR_RES)
                        resetPosition(current)
                    }
                }
                is ApiResult.HttpError -> {
                    setStatus(
                        if (verify.code == 503) "Engine busy. Try again."
                        else "Move couldn't be verified.",
                        ATRIUM_AMBER_COLOR_RES,
                    )
                    resetPosition(current)
                }
                is ApiResult.NetworkError, ApiResult.Timeout -> {
                    setStatus("Offline. Try again later.", ATRIUM_AMBER_COLOR_RES)
                    resetPosition(current)
                }
            }
        }
    }

    private suspend fun creditXp(client: GameApiClient, current: PuzzleNextDto) {
        when (val solve = client.submitTrainingSolve(
            sourceType = SOURCE_TYPE_STANDARD_PUZZLE,
            sourceRef = current.puzzleId.ifBlank { null },
        )) {
            is ApiResult.Success -> {
                val awarded = solve.data.xpAwarded
                val total = solve.data.trainingXp
                requireContext()
                    .getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                    .edit()
                    .putInt(MainActivity.PREF_TRAINING_XP, total)
                    .apply()

                val toastText =
                    if (awarded > 0) "+$awarded XP"
                    else "Already solved · $total XP"
                Toast.makeText(requireContext(), toastText, Toast.LENGTH_SHORT).show()
                // Board stays locked on the solved position — "Next
                // puzzle" is the explicit advance, so the user keeps
                // the solved moment instead of the board yanking away.
                setStatus(
                    if (awarded > 0) "Solved · +$awarded XP" else "Solved",
                    ATRIUM_CYAN_COLOR_RES,
                )
            }
            else -> {
                // Verify succeeded but the solve persist failed —
                // leave the puzzle live so the user doesn't lose the
                // "I solved it" moment; replaying the move retries.
                setStatus("Solved, but couldn't save. Try again.", ATRIUM_AMBER_COLOR_RES)
                resetPosition(current)
            }
        }
    }

    /** Reset the board to the puzzle position and re-enable input. */
    private fun resetPosition(current: PuzzleNextDto) {
        board.setFEN(current.fen)
        board.isInteractive = true
    }

    private fun setStatus(text: String, colorRes: Int) {
        statusView.text = text
        statusView.setTextColor(ContextCompat.getColor(requireContext(), colorRes))
    }

    companion object {
        /** ``TrainingCompletion.source_type`` for standalone puzzles —
         *  mirrors ``llm.seca.training.models.SOURCE_TYPE_STANDARD_PUZZLE``. */
        const val SOURCE_TYPE_STANDARD_PUZZLE = "standard_puzzle"

        private val ATRIUM_DIM_COLOR_RES = R.color.atrium_dim
        private val ATRIUM_AMBER_COLOR_RES = R.color.atrium_accent_amber
        private val ATRIUM_CYAN_COLOR_RES = R.color.atrium_accent_cyan

        /**
         * "Puzzle · <theme> · <difficulty> · via Lichess" kicker, with
         * empty segments dropped:
         *
         *  * theme — corpus tag rendered via
         *    [TodaysDrillBottomSheet.prettyTheme]; the Lichess feed's
         *    ``"mix"`` (and ``"generic"``) collapse to nothing.
         *  * difficulty — capitalised band, omitted when the server
         *    sent none.
         *  * "via Lichess" — only for live-fetched puzzles; corpus
         *    picks carry no attribution.
         *
         * Pure function — unit-testable without a fragment.
         */
        fun formatKicker(puzzle: PuzzleNextDto): String {
            val parts = mutableListOf("Puzzle")
            val theme = puzzle.theme.trim().lowercase()
            if (theme != "mix") {
                val pretty = TodaysDrillBottomSheet.prettyTheme(theme)
                if (pretty.isNotEmpty()) parts.add(pretty)
            }
            val difficulty = puzzle.difficulty.trim().lowercase()
            if (difficulty.isNotEmpty()) {
                parts.add(difficulty.replaceFirstChar(Char::uppercaseChar))
            }
            if (puzzle.source.trim().lowercase() == "lichess") {
                parts.add("via Lichess")
            }
            return parts.joinToString(" · ")
        }

        /**
         * True when the FEN's side-to-move field is Black — drives the
         * board flip so the solver's pieces sit at the bottom.  A
         * malformed FEN defaults to White (unflipped), matching the
         * board's own lenient parsing.  Pure function.
         */
        fun isBlackToMove(fen: String): Boolean =
            fen.trim().split(" ").getOrNull(1)?.lowercase() == "b"

        /** "White to move" / "Black to move" status line.  Pure function. */
        fun sideToMoveLabel(fen: String): String =
            if (isBlackToMove(fen)) "Black to move" else "White to move"
    }
}
package com.cereveon.myapp

import kotlin.math.abs

/**
 * Pure-Kotlin logic for the Quick Coach dock.
 *
 * All computation is deterministic:
 *  - Material balance from board state (piece-count heuristic)
 *  - Score formatted as "+1.5", "Equal", "-2.0"
 *  - Capture classification: captured piece value → severity tier
 *  - One-line explanation derived from classification tier
 *
 * No model inference, no RL, no backend calls.
 */
object QuickCoachLogic {

    private val PIECE_VALUE = mapOf(
        'p' to 1, 'n' to 3, 'b' to 3, 'r' to 5, 'q' to 9
    )

    /**
     * Compute material balance (white minus black, in pawn units).
     * Positive = white advantage; negative = black advantage.
     */
    fun materialBalance(board: Array<CharArray>): Float {
        var white = 0f
        var black = 0f
        for (row in board) {
            for (ch in row) {
                val value = PIECE_VALUE[ch.lowercaseChar()]?.toFloat() ?: continue
                if (ch.isUpperCase()) white += value else black += value
            }
        }
        return white - black
    }

    /**
     * Format a material balance float as "+1.5", "Equal", or "-2.0".
     * Values within ±0.05 are considered equal.
     */
    fun formatScore(balance: Float): String = when {
        abs(balance) < 0.05f -> "Equal"
        balance > 0f         -> "+%.1f".format(balance)
        else                 -> "%.1f".format(balance)
    }

    /**
     * Classify the human's last move based on what the AI captured.
     * '.' or any unmapped char → GOOD (AI took nothing).
     */
    fun classifyCapture(capturedPiece: Char): MistakeClassification {
        return when (PIECE_VALUE[capturedPiece.lowercaseChar()] ?: 0) {
            9    -> MistakeClassification.BLUNDER     // queen hung
            5    -> MistakeClassification.MISTAKE     // rook hung
            3    -> MistakeClassification.MISTAKE     // bishop or knight hung
            1    -> MistakeClassification.INACCURACY  // pawn dropped
            else -> MistakeClassification.GOOD
        }
    }

    /**
     * Derive a one-line coaching explanation from the classification.
     * Returns null for GOOD moves — the dock shows a generic fallback instead.
     *
     * Shown only when the server hint is unavailable (transport failure),
     * so it must still read as the coach speaking — warm, not telegraphic
     * (de-robotified 2026-07-09) — and must never mention the engine: the
     * server's output validators enforce that rule for backend text, but
     * client-local strings are on their own ("engine capitalised" leaked
     * the framing the product hides everywhere else).
     */
    fun deriveExplanation(classification: MistakeClassification): String? = when (classification) {
        MistakeClassification.BLUNDER    -> "That one hurt — a piece was left undefended and your opponent took it."
        MistakeClassification.MISTAKE    -> "Some material slipped away there — make sure your pieces are protected before pushing forward."
        MistakeClassification.INACCURACY -> "A pawn went loose on that move — keep an eye on your undefended pawns."
        MistakeClassification.GOOD       -> null
    }

    /**
     * Build a [QuickCoachUpdate] from the AI's captured piece and the
     * board state after the AI's move has been applied.
     *
     * Uses the local material balance heuristic for the score.
     * [bestMove] is null because no engine call is made here.
     */
    fun buildUpdate(capturedPiece: Char, board: Array<CharArray>): QuickCoachUpdate {
        val classification = classifyCapture(capturedPiece)
        val balance = materialBalance(board)
        return QuickCoachUpdate(
            scoreText = formatScore(balance),
            classification = classification,
            explanation = deriveExplanation(classification),
        )
    }

    /**
     * Format a centipawn score from the engine as a human-readable string.
     *
     * The engine returns centipawns from White's perspective (100 cp = 1 pawn).
     * Positive → White advantage; negative → Black advantage.
     *
     * | Input        | Output   |
     * |--------------|----------|
     * | null         | "?"      |
     * | abs(cp) < 5  | "Equal"  |
     * | cp = +152    | "+1.52"  |
     * | cp = -80     | "-0.80"  |
     */
    fun formatCentipawns(score: Int?): String = when {
        score == null        -> "?"
        score in -4..4      -> "Equal"
        score > 0           -> "+%.2f".format(score / 100.0)
        else                -> "%.2f".format(score / 100.0)
    }

    /**
     * Build a [QuickCoachUpdate] using the real engine centipawn score
     * instead of the local material balance heuristic.
     *
     * Use this path when [EngineEvalClient.evaluate] has returned successfully.
     *
     * @param capturedPiece    Piece char captured by the AI (or '.' for none).
     * @param engineScore      Centipawn score from [EngineEvalResponse.score]; null if unavailable.
     * @param bestMove         UCI string from [EngineEvalResponse.bestMove]; null if unavailable.
     * @param liveHint         Coaching hint from POST /live/move; overrides the static
     *                         [deriveExplanation] when non-null.
     * @param engineAvailable  False when the eval request failed; propagated to the update
     *                         so the UI can display a degraded-mode indicator.
     */
    fun buildUpdateFromEngine(
        capturedPiece: Char,
        engineScore: Int?,
        bestMove: String? = null,
        liveHint: String? = null,
        engineAvailable: Boolean = true,
        classificationOverride: MistakeClassification? = null,
        engineSignal: EngineSignalDto? = null,
        isHumanMoveCoachUpdate: Boolean = false,
        coachDegraded: Boolean = false,
    ): QuickCoachUpdate {
        val classification = classificationOverride ?: classifyCapture(capturedPiece)
        return QuickCoachUpdate(
            scoreText = formatCentipawns(engineScore),
            classification = classification,
            explanation = liveHint ?: deriveExplanation(classification),
            bestMove = bestMove,
            engineAvailable = engineAvailable,
            engineSignal = engineSignal,
            isHumanMoveCoachUpdate = isHumanMoveCoachUpdate,
            coachDegraded = coachDegraded,
        )
    }

    /**
     * Map the backend's move_quality string to a [MistakeClassification].
     *
     * The backend returns one of: "GOOD", "INACCURACY", "MISTAKE", "BLUNDER"
     * (or legacy "best" / "ok" treated as GOOD).
     * Any unrecognised string falls back to GOOD (fail-safe).
     */
    fun fromBackendString(s: String): MistakeClassification = when (s.uppercase()) {
        "BLUNDER"    -> MistakeClassification.BLUNDER
        "MISTAKE"    -> MistakeClassification.MISTAKE
        "INACCURACY" -> MistakeClassification.INACCURACY
        else         -> MistakeClassification.GOOD
    }
}
package com.cereveon.myapp

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
package com.cereveon.myapp

import kotlinx.coroutines.delay

/**
 * Retries [block] up to [maxAttempts] times with exponential backoff + jitter.
 *
 * Only retries on transient transport failures ([ApiResult.Timeout] and
 * [ApiResult.NetworkError]). Definitive server responses ([ApiResult.Success]
 * and [ApiResult.HttpError]) are returned immediately — retrying a 4xx/5xx
 * would not produce a different outcome and wastes battery on mobile.
 *
 * @param maxAttempts    Total number of attempts including the first.
 * @param initialDelayMs Pause before the second attempt in milliseconds.
 * @param maxDelayMs     Upper cap on the delay between attempts.
 */
suspend fun <T> withRetry(
    maxAttempts: Int = 3,
    initialDelayMs: Long = 500L,
    maxDelayMs: Long = 5_000L,
    block: suspend () -> ApiResult<T>,
): ApiResult<T> {
    var delayMs = initialDelayMs
    repeat(maxAttempts - 1) {
        val result = block()
        // Non-local return from the inline lambda — exits withRetry immediately.
        if (result !is ApiResult.Timeout && result !is ApiResult.NetworkError) return result
        delay(delayMs + (0L..100L).random())          // jitter avoids thundering-herd
        delayMs = (delayMs * 2).coerceAtMost(maxDelayMs)
    }
    return block()
}
package com.cereveon.myapp

import java.net.HttpURLConnection

/**
 * Client for the post-game AI review endpoints
 * (docs/API_CONTRACTS.md §39/§39a).
 *
 * Returns [ApiResult] on every call — callers never see raw
 * exceptions.  Both endpoints require Bearer auth and forward the
 * ``X-Auth-Token`` rotation header to [tokenSink] on success, same as
 * [HttpLichessApiClient].
 *
 * Default implementations return [ApiResult.HttpError(501)] so test
 * fakes can override only the methods they exercise.
 */
interface ReviewApiClient {

    /**
     * POST /game/{event_id}/review — start, coalesce, or retry the
     * review job.  202 while worker work is in flight, 200 when a
     * complete row already answers; both parse to the same
     * [GameReviewResponse] and the caller just polls [getReview]
     * until [GameReviewResponse.isTerminal].
     *
     * Never 402s: past the free cap the review still completes with
     * `llm.outcome == "skipped_entitlement"` + the quota snapshot.
     *
     * @return HttpError(400) for ineligible games (in-app source /
     *         too short), (403) another player's game, (404) unknown
     *         event, (429) rate limit.
     */
    suspend fun startReview(eventId: String, token: String): ApiResult<GameReviewResponse> =
        ApiResult.HttpError(501)

    /**
     * GET /game/{event_id}/review — poll the row.  404 while no review
     * exists at the current analysis version (the UI shows the "Get
     * coach review" state).
     */
    suspend fun getReview(eventId: String, token: String): ApiResult<GameReviewResponse> =
        ApiResult.HttpError(501)
}

/**
 * Production [ReviewApiClient] backed by [BaseHttpClient].
 *
 * @param baseUrl   Scheme + host + optional port, no trailing slash.
 * @param tokenSink Sink for the ``X-Auth-Token`` refresh header; null
 *                  disables rotation (test fakes).
 */
class HttpReviewApiClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : ReviewApiClient {

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    private fun bearerHeader(token: String): Map<String, String> =
        mapOf("Authorization" to "Bearer $token")

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    // eventId is a server-issued UUID from /game/history (RFC 4122
    // grammar), safe to concatenate without URL-encoding — same
    // reasoning as HttpLichessApiClient.getImportJob.
    private fun reviewPath(eventId: String) = "/game/$eventId/review"

    override suspend fun startReview(
        eventId: String,
        token: String,
    ): ApiResult<GameReviewResponse> = http.request(
        path = reviewPath(eventId),
        method = "POST",
        headers = bearerHeader(token),
        // 202 = job dispatched, 200 = an existing complete row answered;
        // both carry the same body and the poll loop treats them alike.
        successCodes = setOf(
            HttpURLConnection.HTTP_OK,
            HttpURLConnection.HTTP_ACCEPTED,
        ),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameReviewResponse>(body) },
    )

    override suspend fun getReview(
        eventId: String,
        token: String,
    ): ApiResult<GameReviewResponse> = http.request(
        path = reviewPath(eventId),
        method = "GET",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameReviewResponse>(body) },
    )
}
package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed models for the post-game AI review endpoints
 * (POST/GET /game/{event_id}/review) — docs/API_CONTRACTS.md §39/§39a.
 *
 * One response shape serves both endpoints; the three payload sections
 * ([GameReviewResponse.engine], [.moments], [.llm]) are null until
 * their pipeline stage lands, which is exactly the client's wave
 * boundary: render each section the poll tick it becomes non-null.
 *
 * Trust-boundary note (mirrored from llm/seca/review): the eval series
 * arrives BANDED — five player-relative steps, never centipawns — so
 * this client cannot render numeric evals even by accident.  The five
 * strings map 1:1 onto [EvalBandView.Band].
 */

/** Review job/result row.  `status` drives the 2s poll loop. */
@Serializable
data class GameReviewResponse(
    @SerialName("review_id") val reviewId: String,
    @SerialName("event_id") val eventId: String,
    val status: String,
    @SerialName("analysis_version") val analysisVersion: Int = 1,
    @SerialName("review_mode") val reviewMode: String? = null,
    val engine: ReviewEngine? = null,
    val moments: List<ReviewMoment>? = null,
    val llm: ReviewLlm? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    val entitlement: ReviewEntitlement? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("completed_at") val completedAt: String? = null,
) {
    companion object {
        const val STATUS_QUEUED = "queued"
        const val STATUS_RUNNING = "running"
        const val STATUS_ENGINE_DONE = "engine_done"
        const val STATUS_COMPLETE = "complete"
        const val STATUS_FAILED = "failed"
    }

    /** True when polling should stop (nothing further will change). */
    val isTerminal: Boolean
        get() = status == STATUS_COMPLETE || status == STATUS_FAILED
}

/**
 * Wave-2 engine truth.  [bands] has one entry per board position
 * (index 0 = start, index i = after ply i — the same indexing as
 * GET /game/{event_id}/positions, so it zips with the replay list).
 */
@Serializable
data class ReviewEngine(
    val bands: List<String> = emptyList(),
    val accuracy: Float = 0f,
    val counts: ReviewCounts = ReviewCounts(),
    @SerialName("moves_analyzed") val movesAnalyzed: Int = 0,
    @SerialName("player_color") val playerColor: String? = null,
    val plies: Int = 0,
    val meta: ReviewMeta = ReviewMeta(),
)

@Serializable
data class ReviewCounts(
    val blunders: Int = 0,
    val mistakes: Int = 0,
    val inaccuracies: Int = 0,
)

/** PGN-header echo for the review header line; fields absent server-side stay null. */
@Serializable
data class ReviewMeta(
    val white: String? = null,
    val black: String? = null,
    @SerialName("white_elo") val whiteElo: String? = null,
    @SerialName("black_elo") val blackElo: String? = null,
    @SerialName("time_control") val timeControl: String? = null,
    val opening: String? = null,
    val eco: String? = null,
    val date: String? = null,
    val termination: String? = null,
)

/**
 * One critical moment.  [ply] indexes the replay positions list
 * (position AFTER the move at `positions[ply]`, the faced position at
 * `positions[ply - 1]`), which is how "Explore" jumps the main board.
 */
@Serializable
data class ReviewMoment(
    val ply: Int,
    @SerialName("move_number") val moveNumber: Int,
    val san: String,
    @SerialName("moment_type") val momentType: String,
    val phase: String? = null,
    @SerialName("band_before") val bandBefore: String? = null,
    @SerialName("band_after") val bandAfter: String? = null,
    @SerialName("fen_before") val fenBefore: String? = null,
    @SerialName("fen_after") val fenAfter: String? = null,
    @SerialName("clock_remaining_s") val clockRemainingS: Int? = null,
) {
    companion object {
        const val TYPE_BLUNDER = "blunder"
        const val TYPE_MISSED_WIN = "missed_win"
        const val TYPE_MISTAKE = "mistake"
        const val TYPE_PUNISHED_MISTAKE = "punished_mistake"
        const val TYPE_STRATEGIC = "strategic"
    }
}

/** Wave-3 coach texts.  `outcome` semantics per §39. */
@Serializable
data class ReviewLlm(
    val moments: List<ReviewLlmMoment> = emptyList(),
    val verdict: ReviewLlmVerdict? = null,
    val outcome: String = OUTCOME_FULL,
) {
    companion object {
        const val OUTCOME_FULL = "full"
        const val OUTCOME_FALLBACK = "fallback"
        const val OUTCOME_SKIPPED_ENTITLEMENT = "skipped_entitlement"
    }
}

@Serializable
data class ReviewLlmMoment(
    val ply: Int,
    val text: String,
    val source: String = "llm",
)

@Serializable
data class ReviewLlmVerdict(
    val text: String,
    val source: String = "llm",
)

/**
 * Quota snapshot for the upgrade CTA ("2 coach reviews left this
 * month").  All-null limits mean entitlements are not enforced (dev)
 * — treat as unlimited.
 */
@Serializable
data class ReviewEntitlement(
    val metric: String? = null,
    val allowed: Boolean = true,
    val plan: String? = null,
    val limit: Int? = null,
    val used: Int? = null,
    val remaining: Int? = null,
) {
    companion object {
        /** Monthly ceiling (free 3 / pro 50). */
        const val METRIC_MONTHLY = "import_analysis"

        /** Pro daily smoothing cap (10/day) — resets at midnight UTC. */
        const val METRIC_DAILY = "import_analysis_daily"

        const val PLAN_PRO = "pro"
    }
}
package com.cereveon.myapp

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
package com.cereveon.myapp

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Discriminated state for the backend's SECA safe-mode flag.
 *
 *  - [Unknown]  No status check has completed yet (cold-start before
 *               the first refresh, or all previous refreshes failed
 *               before any successful response).  Treated as "not yet
 *               cleared for coaching" — the gate fails closed.
 *  - [Safe]     The most recent `GET /seca/status` returned
 *               `safe_mode: true`.  Coaching requests permitted.
 *  - [Unsafe]   Either the backend reported `safe_mode: false`, or the
 *               status check itself failed (HTTP error / timeout /
 *               network unreachable).  Coaching requests refused; the
 *               UI surfaces [reason] so the user can see why.
 */
sealed class SecaSafetyState {
    object Unknown : SecaSafetyState()
    object Safe : SecaSafetyState()
    data class Unsafe(val reason: String) : SecaSafetyState()
}

/**
 * Cold-start (and on-resume) gate that decides whether the Android
 * client may send coaching requests to the backend.
 *
 * The README contract says: the client must confirm `safe_mode: true`
 * via `GET /seca/status` before sending coaching requests.  This
 * interface is the local representation of that contract.
 *
 * Why a fail-closed default
 * -------------------------
 * The state starts at [SecaSafetyState.Unknown] and stays there until
 * the first successful refresh.  [isSafe] returns false in that
 * window, so a coaching call that races the cold-start check sees the
 * "not yet cleared" state and is refused — never the optimistic
 * "assume safe until proven otherwise" path that would let an unsafe
 * backend slip a single coaching request through during the initial
 * round-trip.
 *
 * Why network errors map to Unsafe
 * --------------------------------
 * If `/seca/status` is unreachable, the client cannot prove the
 * backend is safe.  The conservative reading of "before sending
 * coaching requests, confirm safe_mode" is "no proof, no coaching".
 * The user sees the failure reason and can retry.  Reverse
 * interpretation ("network broken, assume safe") would mask a
 * deliberately disabled `/seca/status` endpoint — exactly the threat
 * we're guarding against.
 */
interface SecaSafetyGate {
    val state: StateFlow<SecaSafetyState>

    /** Re-fetch `GET /seca/status` and update [state]. Idempotent. */
    suspend fun refresh()

    /** True when [state] is [SecaSafetyState.Safe]. */
    fun isSafe(): Boolean = state.value is SecaSafetyState.Safe
}

/**
 * Default gate backed by a [GameApiClient].  Holds a [MutableStateFlow]
 * so observers in the activity can render the banner reactively
 * without polling.
 */
class HttpSecaSafetyGate(
    private val client: GameApiClient,
) : SecaSafetyGate {

    private val _state = MutableStateFlow<SecaSafetyState>(SecaSafetyState.Unknown)
    override val state: StateFlow<SecaSafetyState> = _state.asStateFlow()

    override suspend fun refresh() {
        _state.value =
            when (val r = client.getSecaStatus()) {
                is ApiResult.Success ->
                    if (r.data.safeModeEnabled) {
                        SecaSafetyState.Safe
                    } else {
                        SecaSafetyState.Unsafe(
                            "Backend reports safe_mode=false (adaptive learning may be active)",
                        )
                    }
                is ApiResult.HttpError ->
                    SecaSafetyState.Unsafe("Status check failed (HTTP ${r.code})")
                is ApiResult.Timeout ->
                    SecaSafetyState.Unsafe("Status check timed out")
                is ApiResult.NetworkError ->
                    SecaSafetyState.Unsafe("Status check unreachable")
            }
    }
}
package com.cereveon.myapp

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatDelegate
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.slider.Slider
import kotlin.math.roundToInt
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Settings (handoff screen #10).
 *
 * Sections (each separated by an Atrium hairline rule):
 *   1.  Coach voice  — radio (formal / conversational / terse)
 *   2.  Board style  — radio (flat / engraved / wireframe)
 *   3.  Appearance   — Bright mode switch
 *   4.  Sound        — switch
 *   5.  Notifications — switch
 *   6.  Profile      — chevron row: Skill rating (opens edit dialog)
 *   7.  Premium      — chevron row: Upgrade
 *   8.  Account      — chevron rows: Change password, Sign out
 *
 * Persistence: [PREFS_NAME] SharedPreferences (the same store
 * MainActivity uses for the rating cache and curriculum chip).
 *
 * **Consumer wiring status:**
 *   - Coach voice — persisted and read by `chat_pipeline.generate_chat_reply`
 *     via the `coach_voice` field on `/coach/chat`; CoachApiClient
 *     forwards [readCoachVoice] on every request.
 *   - Board style — persisted and read by [MainActivity.onCreate] /
 *     [MainActivity.onResume]; assigns [ChessBoardView.boardStyle] which
 *     branches the per-square render in `onDraw`.
 *   - Bright mode — persisted and applied immediately via
 *     [androidx.appcompat.app.AppCompatDelegate]; re-applied at every
 *     cold start by [CereveonApplication.onCreate] through
 *     [readBrightModeEnabled] + [nightModeFor], so the palette is
 *     app-controlled and the system light/dark setting has no effect.
 *   - Sound / notifications persist, but no audio system or
 *     notification channel exists yet to consume them.
 *
 * The settings UI is the right place to put these toggles ahead of the
 * features that read them, so users see one consistent surface.  The
 * downstream readers can opt-in via [readCoachVoice], [readBoardStyle],
 * [readSoundEnabled], [readNotificationsEnabled].
 */
class SettingsBottomSheet : BottomSheetDialogFragment() {

    /**
     * Optional callbacks the host activity can wire to handle
     * Account-section taps.  Both default to no-ops; MainActivity
     * sets them to forward to its existing change-password dialog
     * and logout flow.
     */
    var onChangePasswordTapped: (() -> Unit)? = null
    var onSignOutTapped: (() -> Unit)? = null

    /**
     * Optional callback the host activity wires to surface the
     * Lichess Connect bottom sheet when the "Lichess" row in the
     * Integrations section is tapped.  Defaults to no-op; the host
     * is responsible for showing [LichessConnectBottomSheet] (or any
     * future replacement surface).
     */
    var onConnectLichessTapped: (() -> Unit)? = null

    private val voiceDots = mutableMapOf<String, View>()
    private val boardDots = mutableMapOf<String, View>()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_settings, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val prefs = requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        // ── Coach voice radio ────────────────────────────────────────
        voiceDots["formal"]         = view.findViewById(R.id.voiceFormalDot)
        voiceDots["conversational"] = view.findViewById(R.id.voiceConversationalDot)
        voiceDots["terse"]          = view.findViewById(R.id.voiceTerseDot)
        applyRadioState(voiceDots, prefs.getString(PREF_COACH_VOICE, DEFAULT_COACH_VOICE)!!)

        bindRow(view, R.id.voiceFormal,         voiceDots, PREF_COACH_VOICE)
        bindRow(view, R.id.voiceConversational, voiceDots, PREF_COACH_VOICE)
        bindRow(view, R.id.voiceTerse,          voiceDots, PREF_COACH_VOICE)

        // ── Board style radio ────────────────────────────────────────
        boardDots["flat"]      = view.findViewById(R.id.boardFlatDot)
        boardDots["engraved"]  = view.findViewById(R.id.boardEngravedDot)
        boardDots["wireframe"] = view.findViewById(R.id.boardWireframeDot)
        applyRadioState(boardDots, prefs.getString(PREF_BOARD_STYLE, DEFAULT_BOARD_STYLE)!!)

        bindRow(view, R.id.boardFlat,      boardDots, PREF_BOARD_STYLE)
        bindRow(view, R.id.boardEngraved,  boardDots, PREF_BOARD_STYLE)
        bindRow(view, R.id.boardWireframe, boardDots, PREF_BOARD_STYLE)

        // ── Bright mode switch ───────────────────────────────────────
        val bright = view.findViewById<SwitchCompat>(R.id.switchBrightMode)
        bright.isChecked = prefs.getBoolean(PREF_BRIGHT_MODE, false)
        bright.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_BRIGHT_MODE, checked).apply()
            // Dismiss BEFORE flipping the night mode: the mode change
            // recreates the host activity, and a framework-restored
            // sheet would come back with null Account callbacks (the
            // hosts wire them at show-time, not at restore-time) —
            // same reason the chevron rows use dismiss-then-launch.
            dismiss()
            AppCompatDelegate.setDefaultNightMode(nightModeFor(checked))
        }
        view.findViewById<View>(R.id.rowBrightMode).setOnClickListener { bright.toggle() }

        // ── Sound switch ─────────────────────────────────────────────
        val sound = view.findViewById<SwitchCompat>(R.id.switchSound)
        sound.isChecked = prefs.getBoolean(PREF_SOUND_ENABLED, true)
        sound.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_SOUND_ENABLED, checked).apply()
        }
        view.findViewById<View>(R.id.rowSound).setOnClickListener { sound.toggle() }

        // ── Notifications switch ─────────────────────────────────────
        val notif = view.findViewById<SwitchCompat>(R.id.switchNotifications)
        notif.isChecked = prefs.getBoolean(PREF_NOTIFICATIONS_ENABLED, true)
        notif.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_NOTIFICATIONS_ENABLED, checked).apply()
        }
        view.findViewById<View>(R.id.rowNotifications).setOnClickListener { notif.toggle() }

        // ── Profile · Skill rating chevron row ──────────────────────
        // Retired when the user-visible Elo display was hidden from the
        // UI; the layout views are still in the tree with
        // ``visibility="gone"`` (see bottom_sheet_settings.xml).  The
        // showEditRatingDialog / persistRating / firePatchAuthMe helpers
        // below are now unreachable from the UI but remain in place
        // until the next phase replaces the calibration affordance.

        // ── Integrations · Lichess chevron row ──────────────────────
        view.findViewById<View>(R.id.rowConnectLichess).setOnClickListener {
            // Dismiss so the Lichess sheet slides over a settled host
            // background, not over the Settings sheet's fading scrim
            // (matches the rowUpgrade dismiss-then-launch idiom below).
            dismiss()
            onConnectLichessTapped?.invoke()
        }

        // ── Premium chevron row ──────────────────────────────────────
        view.findViewById<View>(R.id.rowUpgrade).setOnClickListener {
            // Dismiss the sheet first so the paywall slides in over a
            // settled MainActivity background, not over a half-faded
            // bottom-sheet scrim.
            dismiss()
            startActivity(Intent(requireContext(), PaywallActivity::class.java))
        }

        // ── Account chevron rows ─────────────────────────────────────
        view.findViewById<View>(R.id.rowChangePassword).setOnClickListener {
            dismiss()
            onChangePasswordTapped?.invoke()
        }
        view.findViewById<View>(R.id.rowSignOut).setOnClickListener {
            dismiss()
            onSignOutTapped?.invoke()
        }
    }

    /**
     * Show the rating-edit AlertDialog, prefilled with the user's
     * current calibrated rating.  On Save:
     *   - Persist the new value to PREF_PLAYER_RATING_ESTIMATE +
     *     mirror into PREF_RATING (the drawer header reads it)
     *   - Update the row's trailing label
     *   - Fire PATCH /auth/me on lifecycleScope (best-effort; the
     *     same recovery path MainActivity already runs at cold-start
     *     reconciles if this fails offline)
     *
     * Cancel is a no-op.  No "are you sure" — the slider already
     * shows the new value before tap, and an accidental change is
     * one tap away from a corrective edit.
     */
    private fun showEditRatingDialog(
        prefs: android.content.SharedPreferences,
        rowLabel: TextView,
    ) {
        val ctx = requireContext()
        val view = LayoutInflater.from(ctx).inflate(R.layout.dialog_edit_rating, null, false)
        val slider = view.findViewById<Slider>(R.id.dialogRatingSlider)
        val valueLabel = view.findViewById<TextView>(R.id.dialogRatingValue)

        val current = prefs.getFloat(
            OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
            prefs.getFloat(MainActivity.PREF_RATING, OnboardingActivity.DEFAULT_RATING),
        ).coerceIn(SLIDER_MIN, SLIDER_MAX)

        slider.value = current
        valueLabel.text = OnboardingActivity.formatRating(current)
        slider.addOnChangeListener { _, value, _ ->
            valueLabel.text = OnboardingActivity.formatRating(value)
        }

        AlertDialog.Builder(ctx)
            .setTitle("Adjust your rating")
            .setView(view)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Save") { _, _ ->
                val newRating = slider.value
                persistRating(prefs, newRating)
                rowLabel.text = formatRatingLabel(prefs)
                firePatchAuthMe(newRating)
            }
            .show()
    }

    private fun persistRating(prefs: android.content.SharedPreferences, rating: Float) {
        prefs.edit()
            .putFloat(OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE, rating)
            // Mirror into the drawer-header key so MainActivity picks
            // it up immediately without waiting for the next /auth/me
            // round-trip.
            .putFloat(MainActivity.PREF_RATING, rating)
            .apply()
    }

    private fun firePatchAuthMe(rating: Float) {
        val ctx = requireContext()
        val authRepo = AuthRepository(EncryptedTokenStorage(ctx))
        val token = authRepo.getToken() ?: return
        val client: AuthApiClient = HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        // Fire on the fragment's lifecycleScope; if the user dismisses
        // the sheet before the call returns, the launch is cancelled
        // cleanly.  Confidence omitted: the user only adjusted the
        // rating, not the certainty around it.
        lifecycleScope.launch {
            when (val r = client.updateMe(token, rating = rating, confidence = null)) {
                is ApiResult.Success -> {
                    Toast.makeText(
                        ctx, "Rating saved · ${rating.roundToInt()}", Toast.LENGTH_SHORT,
                    ).show()
                    // PR #175: clear the onboarding-time PREF after a
                    // successful PATCH.  Without this the value would
                    // linger and (pre-PR-#175 cold-start reconcile) get
                    // re-PATCHed on every cold-start, clobbering
                    // game-driven rating updates.  The cold-start
                    // reconcile path is retired in PR #175; we still
                    // clear the PREF for hygiene so a future regression
                    // can't reintroduce the same shape.
                    ctx.getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                        .edit()
                        .remove(OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE)
                        .remove(OnboardingActivity.PREF_PLAYER_CONFIDENCE)
                        .apply()
                }
                is ApiResult.HttpError -> {
                    Log.w("SETTINGS", "PATCH /auth/me HTTP ${r.code}")
                    if (r.code != 401) {
                        // 401 → MainActivity's session-expired flow handles it
                        Toast.makeText(
                            ctx,
                            "Saved locally · server sync will retry",
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                }
                is ApiResult.NetworkError -> {
                    Log.w("SETTINGS", "PATCH /auth/me network error", r.cause)
                    Toast.makeText(
                        ctx, "Saved locally · server sync will retry",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
                ApiResult.Timeout -> {
                    Log.w("SETTINGS", "PATCH /auth/me timed out")
                    Toast.makeText(
                        ctx, "Saved locally · server sync will retry",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            }
        }
    }

    /**
     * Wire a radio-row click: write [prefKey] = row.tag and update
     * the visual selection so only the tapped dot is filled.
     */
    private fun bindRow(
        root: View,
        rowId: Int,
        dots: Map<String, View>,
        prefKey: String,
    ) {
        val row = root.findViewById<LinearLayout>(rowId)
        val value = row.tag as String
        row.setOnClickListener {
            requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putString(prefKey, value).apply()
            applyRadioState(dots, value)
        }
    }

    /** Set the dot drawable for each entry in [dots]: filled if its key matches [selected]. */
    private fun applyRadioState(dots: Map<String, View>, selected: String) {
        val ctx = requireContext()
        val filled = ContextCompat.getDrawable(ctx, R.drawable.atrium_radio_selected)
        val hollow = ContextCompat.getDrawable(ctx, R.drawable.atrium_radio_unselected)
        dots.forEach { (key, dot) ->
            dot.background = if (key == selected) filled else hollow
        }
    }

    companion object {
        // Same SharedPreferences store MainActivity uses for rating
        // cache + curriculum chip.  One prefs file keeps the app's
        // user-state surface coherent.
        const val PREFS_NAME = "chesscoach_prefs"

        const val PREF_COACH_VOICE = "setting_coach_voice"
        const val DEFAULT_COACH_VOICE = "conversational"

        const val PREF_BOARD_STYLE = "setting_board_style"
        const val DEFAULT_BOARD_STYLE = "flat"

        const val PREF_SOUND_ENABLED = "setting_sound_enabled"
        const val PREF_NOTIFICATIONS_ENABLED = "setting_notifications_enabled"

        // Bright (light) mode is opt-in; absent key = dark, the Atrium
        // default posture.  The palette is selected ONLY by this pref
        // (via forced AppCompatDelegate modes) — never by the system
        // light/dark setting.
        const val PREF_BRIGHT_MODE = "setting_bright_mode"

        // Slider bounds for the rating-edit dialog.  Match the
        // OnboardingActivity slider so a re-edit feels like the
        // same affordance the user saw at calibration.
        const val SLIDER_MIN = 800f
        const val SLIDER_MAX = 2600f

        /**
         * Format the current rating for the row's trailing label.
         * Prefers the local PREF_PLAYER_RATING_ESTIMATE (set by
         * Onboarding + this dialog), falls back to PREF_RATING
         * (the drawer-header cache, possibly synced from /auth/me),
         * and finally to the slider's neutral midpoint default
         * so the row never reads "—" once a user is logged in.
         */
        fun formatRatingLabel(prefs: android.content.SharedPreferences): String {
            val rating = prefs.getFloat(
                OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
                prefs.getFloat(MainActivity.PREF_RATING, OnboardingActivity.DEFAULT_RATING),
            )
            return OnboardingActivity.formatRating(rating)
        }

        // ── Reader helpers — call these from downstream features ──

        fun readCoachVoice(ctx: Context): String =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(PREF_COACH_VOICE, DEFAULT_COACH_VOICE)!!

        fun readBoardStyle(ctx: Context): String =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(PREF_BOARD_STYLE, DEFAULT_BOARD_STYLE)!!

        fun readSoundEnabled(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_SOUND_ENABLED, true)

        fun readNotificationsEnabled(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_NOTIFICATIONS_ENABLED, true)

        fun readBrightModeEnabled(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_BRIGHT_MODE, false)

        /**
         * Map the bright-mode preference onto a FORCED AppCompat night
         * mode.  Never returns MODE_NIGHT_FOLLOW_SYSTEM: Atrium's
         * palette is app-controlled by design, so dark stays the
         * default posture and the system toggle has no effect.
         * [CereveonApplication.onCreate] applies this at process start;
         * the settings switch applies it live.
         */
        fun nightModeFor(brightEnabled: Boolean): Int =
            if (brightEnabled) {
                AppCompatDelegate.MODE_NIGHT_NO
            } else {
                AppCompatDelegate.MODE_NIGHT_YES
            }
    }
}
package com.cereveon.myapp

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.google.android.material.bottomsheet.BottomSheetDialogFragment

/**
 * Cereveon · Atrium · Study-plan week overview (study-plan phase 2b).
 *
 * Renders the whole weekly curriculum at a glance — the aggregate
 * dominant-weakness focus ([CoachPlanResponse.anchorCategory]), the LLM
 * coach verdict, and the three spaced-repetition days (offset 0 / 3 / 7)
 * each marked **Today / Done / Locked** — with a primary CTA that
 * launches the existing [TodaysDrillBottomSheet] for the currently-due
 * puzzle ([CoachPlanResponse.todayPuzzle]).
 *
 * Pure renderer
 * -------------
 * HomeActivity passes the already-fetched [CoachPlanResponse] as a JSON
 * string, so this sheet does **no network I/O**.  The "Start today's
 * drill" CTA dismisses this sheet and shows the drill, so only one sheet
 * is visible at a time — there is no stacked-dialog lifecycle / stale
 * refresh problem.  After the drill advances the plan (it calls
 * `completePlanPuzzle` itself), the user lands back on Home, whose
 * `onResume` re-polls `/coach/plan/today`; reopening this overview
 * therefore always renders fresh state.
 *
 * A secondary "Practice puzzles" CTA opens the standalone
 * [PuzzleTrainerBottomSheet] (endless Lichess-fed stream) with the same
 * dismiss-then-show pattern — plan drills and free practice share the
 * Puzzles tab without stacking sheets.
 *
 * Args
 * ----
 * Single bundle extra [ARG_PLAN_JSON] — the serialized
 * [CoachPlanResponse].  See [newInstance].
 */
class StudyPlanOverviewBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host before [show]; forwarded to the drill sheet. */
    var gameApiClient: GameApiClient? = null

    private var plan: CoachPlanResponse? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_study_plan_overview, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val planJson = requireArguments().getString(ARG_PLAN_JSON, "")
        val decoded = try {
            // Explicit-serializer form needs no kotlinx.serialization import
            // (the reified extension would), keeping this decode-only file's
            // import list clean for ktlint.
            ApiJson.decodeFromString(CoachPlanResponse.serializer(), planJson)
        } catch (_: Exception) {
            // Should never happen — HomeActivity encodes a valid response
            // before opening us.  If it somehow does, fail closed rather
            // than render a half-empty sheet.
            dismiss()
            return
        }
        plan = decoded

        view.findViewById<TextView>(R.id.overviewFocus).text = formatFocus(decoded)

        val verdictView = view.findViewById<TextView>(R.id.overviewVerdict)
        if (decoded.verdict.isNotBlank()) {
            verdictView.text = decoded.verdict
            verdictView.visibility = View.VISIBLE
        } else {
            verdictView.visibility = View.GONE
        }

        view.findViewById<TextView>(R.id.overviewProgress).text =
            formatProgress(decoded.days, decoded.totalDays)

        bindDayRows(view, decoded.days)
        bindCta(view, decoded)

        // Secondary entry into the standalone puzzle trainer — the
        // endless Lichess-fed practice stream.  Same dismiss-then-show
        // pattern as the drill CTA so only one sheet is up at a time.
        view.findViewById<Button>(R.id.overviewPracticeButton).setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            val sheet = PuzzleTrainerBottomSheet()
            sheet.gameApiClient = gameApiClient
            val fm = parentFragmentManager
            dismiss()
            sheet.show(fm, "PuzzleTrainerBottomSheet")
        }

        view.findViewById<Button>(R.id.overviewCloseButton).setOnClickListener { dismiss() }
    }

    private fun bindDayRows(view: View, days: List<PlanDayDto>) {
        val rows = listOf(
            Triple(R.id.overviewDay1Row, R.id.overviewDay1Label, R.id.overviewDay1Status),
            Triple(R.id.overviewDay2Row, R.id.overviewDay2Label, R.id.overviewDay2Status),
            Triple(R.id.overviewDay3Row, R.id.overviewDay3Label, R.id.overviewDay3Status),
        )
        rows.forEachIndexed { index, (rowId, labelId, statusId) ->
            val row = view.findViewById<LinearLayout>(rowId)
            val day = days.getOrNull(index)
            if (day == null) {
                // Legacy server response with no days[] — hide the empty slot.
                row.visibility = View.GONE
                return@forEachIndexed
            }
            row.visibility = View.VISIBLE
            view.findViewById<TextView>(labelId).text =
                formatDayLabel(dayNumber(day.dayOffset), day.sourceType)
            val status = view.findViewById<TextView>(statusId)
            status.text = statusText(day)
            status.setTextColor(ContextCompat.getColor(requireContext(), statusColorRes(day)))
        }
    }

    private fun bindCta(view: View, response: CoachPlanResponse) {
        val cta = view.findViewById<Button>(R.id.overviewStartButton)
        val puzzle = response.todayPuzzle
        if (puzzle == null) {
            // Nothing due right now — either the week is complete or the
            // next day hasn't unlocked yet.  Hide the CTA; the day rows
            // already communicate the state.
            cta.visibility = View.GONE
            return
        }
        cta.visibility = View.VISIBLE
        cta.text = formatCtaLabel(dayNumber(puzzle.dayOffset))
        cta.setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            val sheet = TodaysDrillBottomSheet.newInstance(
                planId = response.planId,
                dayOffset = puzzle.dayOffset,
                totalDays = response.totalDays,
                theme = response.theme,
                verdict = response.verdict,
                fen = puzzle.fen,
                expectedMoveUci = puzzle.expectedMoveUci,
            )
            sheet.gameApiClient = gameApiClient
            // Capture the FM before dismissing — show the drill on the
            // same (activity) manager so only one sheet is up at a time.
            val fm = parentFragmentManager
            dismiss()
            sheet.show(fm, "TodaysDrillBottomSheet")
        }
    }

    companion object {
        private const val ARG_PLAN_JSON = "plan_json"

        private val STATUS_TODAY_COLOR = R.color.atrium_accent_cyan
        private val STATUS_DONE_COLOR = R.color.atrium_muted
        private val STATUS_LOCKED_COLOR = R.color.atrium_dim

        fun newInstance(planJson: String): StudyPlanOverviewBottomSheet =
            StudyPlanOverviewBottomSheet().apply {
                arguments = Bundle().apply { putString(ARG_PLAN_JSON, planJson) }
            }

        /**
         * The big focus title — the player's aggregate weakness mapped to
         * a friendly noun ([formatCategory]).  Falls back to the day-0
         * mistake's own theme, then to a neutral default, so the title is
         * never blank.  Pure — unit-testable without a view.
         */
        fun formatFocus(response: CoachPlanResponse): String {
            val byCategory = formatCategory(response.anchorCategory)
            if (byCategory.isNotEmpty()) return byCategory
            val byTheme = TodaysDrillBottomSheet.prettyTheme(response.theme)
            if (byTheme.isNotEmpty()) return byTheme
            return "This week"
        }

        /**
         * Map an aggregate [CoachPlanResponse.anchorCategory] (one of the
         * four MistakeCategory values) to a friendly focus noun.  Returns
         * "" for null / generic / unknown so the caller can fall back.
         */
        fun formatCategory(category: String?): String = when (category?.trim()?.lowercase()) {
            "tactical_vision" -> "Tactics"
            "endgame_technique" -> "Endgames"
            "opening_preparation" -> "Openings"
            "positional_play" -> "Strategy"
            else -> ""
        }

        /** 1-based day in the plan (offset 0→1, 3→2, 7→3). */
        fun dayNumber(dayOffset: Int): Int = when (dayOffset) {
            0 -> 1
            3 -> 2
            7 -> 3
            else -> 1
        }

        /**
         * Row label: "Day N · Replay your mistake" for the original day-0
         * position, "Day N · Practice" for the library practice puzzles.
         */
        fun formatDayLabel(dayNumber: Int, sourceType: String): String {
            val kind =
                if (sourceType.trim().lowercase() == "original") "Replay your mistake"
                else "Practice"
            return "Day $dayNumber · $kind"
        }

        /** Status word for one day: Done / Today / Locked. */
        fun statusText(day: PlanDayDto): String = when {
            day.completed -> "Done"
            day.isDue -> "Today"
            else -> "Locked"
        }

        /** Status colour resource: cyan = today (actionable), muted = done, dim = locked. */
        fun statusColorRes(day: PlanDayDto): Int = when {
            day.completed -> STATUS_DONE_COLOR
            day.isDue -> STATUS_TODAY_COLOR
            else -> STATUS_LOCKED_COLOR
        }

        /**
         * "Day N of M" progress, or "Week complete" once every day is
         * solved.  N is the count of completed days + 1 (the day you're
         * up to), capped at M.  Pure — unit-testable.
         */
        fun formatProgress(days: List<PlanDayDto>, totalDays: Int): String {
            val completed = days.count { it.completed }
            if (totalDays > 0 && completed >= totalDays) return "Week complete"
            return "Day ${completed + 1} of $totalDays"
        }

        /** Primary CTA label for the currently-due day. */
        fun formatCtaLabel(dayNumber: Int): String = "Start day $dayNumber"
    }
}
package com.cereveon.myapp

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Today's drill bottom sheet (study-plan phase 4).
 *
 * Launched from [HomeActivity]'s ``TodaysDrillCard`` when
 * /coach/plan/today returns a non-null ``today_puzzle``.  Drills the
 * user on the currently-due puzzle in their per-mistake study plan
 * (day 0 = the original mistake position; days 3 and 7 = theme-matched
 * library variants).
 *
 * Flow per attempt
 * ----------------
 *  1.  User taps & drags a piece on [ChessBoardView] → fires
 *      ``onMovePlayed(fr, fc, tr, tc)``.
 *  2.  Board is locked (``isInteractive=false``) and the status text
 *      flips to "Checking...".
 *  3.  Activity calls ``POST /training/verify-replay`` with the FEN
 *      and the UCI of the attempted move.  Server runs Stockfish.
 *  4a. ``isCorrect=true`` → activity calls ``POST /training/solve``
 *      with ``source_type=mistake_replay`` and
 *      ``source_ref=plan_<plan_id>:day_<day_offset>``.  Server
 *      credits +10 XP; activity toasts "+10 XP", updates
 *      ``PREF_TRAINING_XP`` so Home re-renders, and dismisses.
 *  4b. ``isCorrect=false`` → status flips to "Not quite, try again"
 *      (amber), the board's FEN is reset to the puzzle position,
 *      and ``isInteractive`` is re-enabled.  No XP penalty; the user
 *      can retry indefinitely (matches the [MistakeReplayBottomSheet]
 *      UX).
 *
 * The verify + solve flow is intentionally identical to
 * [MistakeReplayBottomSheet] — they both terminate at
 * ``/training/solve`` with the same ``source_type="mistake_replay"``
 * so the ``(player, source_type, source_ref)`` dedup triple keeps each
 * individual day-N puzzle credit-once.  Only the source_ref shape
 * differs:
 *
 * * Mistake replay (post-game sheet):
 *     ``event_<event_id>:move_<move_number>``
 * * Today's drill (this sheet):
 *     ``plan_<plan_id>:day_<day_offset>``
 *
 * Args
 * ----
 * Carried as bundle extras.  See [newInstance] for the canonical
 * construction path used by [HomeActivity.fetchAndPopulateTodaysDrill].
 */
class TodaysDrillBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host activity before [show]; required for the verify + solve calls. */
    var gameApiClient: GameApiClient? = null

    private lateinit var board: ChessBoardView
    private lateinit var statusView: TextView

    private var fen: String = ""
    private var sourceRef: String = ""

    // Plan coordinates for the completion call after a verified solve —
    // advances the study plan (day 0 -> 3 -> 7) so the week progresses.
    private var planId: String = ""
    private var dayOffset: Int = 0

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_todays_drill, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val args = requireArguments()
        fen = args.getString(ARG_FEN, "")
        planId = args.getString(ARG_PLAN_ID, "")
        dayOffset = args.getInt(ARG_DAY_OFFSET, 0)
        val totalDays = args.getInt(ARG_TOTAL_DAYS, 3)
        val theme = args.getString(ARG_THEME, "generic")
        val verdict = args.getString(ARG_VERDICT, "")
        sourceRef = formatSourceRef(planId = planId, dayOffset = dayOffset)

        view.findViewById<TextView>(R.id.todaysDrillKicker).text =
            formatKicker(dayOffset = dayOffset, totalDays = totalDays, theme = theme)

        val verdictView = view.findViewById<TextView>(R.id.todaysDrillVerdict)
        if (verdict.isNotBlank()) {
            verdictView.text = verdict
            verdictView.visibility = View.VISIBLE
        } else {
            // Verdict empty (LLM unreachable / validator-rejected) —
            // hide the line cleanly rather than render an empty
            // paragraph between the title and the board.
            verdictView.visibility = View.GONE
        }

        board = view.findViewById(R.id.todaysDrillBoard)
        statusView = view.findViewById(R.id.todaysDrillStatus)

        board.setFEN(fen)
        board.isInteractive = true
        board.onMovePlayed = { fr, fc, tr, tc -> handleAttempt(fr, fc, tr, tc) }

        view.findViewById<Button>(R.id.todaysDrillCloseButton).setOnClickListener {
            dismiss()
        }
    }

    private fun handleAttempt(fr: Int, fc: Int, tr: Int, tc: Int) {
        // Lock the board the moment the move lands so the user can't
        // fire a second attempt while the first is in flight.  Wrong-
        // move recovery re-enables ``isInteractive``; correct-move
        // path dismisses the sheet.
        board.isInteractive = false

        val moveResult = board.applyMove(fr, fc, tr, tc)
        if (moveResult == MoveResult.FAILED) {
            // Illegal in this position (e.g. moves into check).
            board.isInteractive = true
            return
        }

        val moveUci = MistakeReplayBottomSheet.rowColToUci(fr, fc) +
            MistakeReplayBottomSheet.rowColToUci(tr, tc)
        setStatus("Checking...", ATRIUM_DIM_COLOR_RES)

        val client = gameApiClient ?: run {
            setStatus("Couldn't reach the engine.", ATRIUM_AMBER_COLOR_RES)
            board.setFEN(fen)
            board.isInteractive = true
            return
        }

        lifecycleScope.launch {
            when (val verify = client.verifyReplayMove(fen, moveUci)) {
                is ApiResult.Success -> {
                    if (verify.data.isCorrect) {
                        creditXpAndDismiss(client)
                    } else {
                        setStatus("Not quite, try again.", ATRIUM_AMBER_COLOR_RES)
                        board.setFEN(fen)
                        board.isInteractive = true
                    }
                }
                is ApiResult.HttpError -> {
                    setStatus(
                        if (verify.code == 503) "Engine busy. Try again."
                        else "Move couldn't be verified.",
                        ATRIUM_AMBER_COLOR_RES,
                    )
                    board.setFEN(fen)
                    board.isInteractive = true
                }
                is ApiResult.NetworkError, ApiResult.Timeout -> {
                    setStatus("Offline. Try again later.", ATRIUM_AMBER_COLOR_RES)
                    board.setFEN(fen)
                    board.isInteractive = true
                }
            }
        }
    }

    private suspend fun creditXpAndDismiss(client: GameApiClient) {
        when (val solve = client.submitTrainingSolve(
            sourceType = MistakeReplayBottomSheet.SOURCE_TYPE_MISTAKE_REPLAY,
            sourceRef = sourceRef.ifBlank { null },
        )) {
            is ApiResult.Success -> {
                val awarded = solve.data.xpAwarded
                val total = solve.data.trainingXp
                requireContext()
                    .getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                    .edit()
                    .putInt(MainActivity.PREF_TRAINING_XP, total)
                    .apply()

                // Advance the study plan: mark this day's puzzle solved
                // so the week progresses (day 0 -> 3 -> 7) and completes.
                // Best-effort — XP is already credited above; if this call
                // fails the day simply resurfaces on the next
                // /coach/plan/today fetch (the endpoint is idempotent), so
                // we don't block the success UX on it.
                if (planId.isNotBlank()) {
                    client.completePlanPuzzle(planId = planId, dayOffset = dayOffset)
                }

                val toastText =
                    if (awarded > 0) "+$awarded XP"
                    else "Already solved · $total XP"
                Toast.makeText(requireContext(), toastText, Toast.LENGTH_SHORT).show()
                dismiss()
            }
            else -> {
                // Verify succeeded but the solve persist failed —
                // leave the sheet open so the user doesn't lose the
                // "I solved it" moment.
                setStatus("Solved, but couldn't save. Try again.", ATRIUM_AMBER_COLOR_RES)
                board.setFEN(fen)
                board.isInteractive = true
            }
        }
    }

    private fun setStatus(text: String, colorRes: Int) {
        statusView.text = text
        statusView.setTextColor(ContextCompat.getColor(requireContext(), colorRes))
    }

    companion object {
        private const val ARG_PLAN_ID = "plan_id"
        private const val ARG_DAY_OFFSET = "day_offset"
        private const val ARG_TOTAL_DAYS = "total_days"
        private const val ARG_THEME = "theme"
        private const val ARG_VERDICT = "verdict"
        private const val ARG_FEN = "fen"
        private const val ARG_EXPECTED_MOVE_UCI = "expected_move_uci"

        private val ATRIUM_DIM_COLOR_RES = R.color.atrium_dim
        private val ATRIUM_AMBER_COLOR_RES = R.color.atrium_accent_amber

        @Suppress("LongParameterList")
        fun newInstance(
            planId: String,
            dayOffset: Int,
            totalDays: Int,
            theme: String,
            verdict: String,
            fen: String,
            expectedMoveUci: String,
        ): TodaysDrillBottomSheet = TodaysDrillBottomSheet().apply {
            arguments = Bundle().apply {
                putString(ARG_PLAN_ID, planId)
                putInt(ARG_DAY_OFFSET, dayOffset)
                putInt(ARG_TOTAL_DAYS, totalDays)
                putString(ARG_THEME, theme)
                putString(ARG_VERDICT, verdict)
                putString(ARG_FEN, fen)
                putString(ARG_EXPECTED_MOVE_UCI, expectedMoveUci)
            }
        }

        /**
         * Compose the ``source_ref`` value that flows into
         * /training/solve on a verified-correct attempt.  Shape:
         * ``plan_<plan_id>:day_<day_offset>``.  The
         * ``(player, source_type, source_ref)`` dedup triple on the
         * solve table means each day-N puzzle is credit-once across
         * retries — exactly the semantics the existing mistake-replay
         * surface uses (with a different prefix).
         *
         * Pure helper so unit tests pin the exact format the server
         * sees without standing up the fragment.
         */
        fun formatSourceRef(planId: String, dayOffset: Int): String =
            "plan_${planId}:day_${dayOffset}"

        /**
         * "Day N of 3 · <theme>" kicker rendered above the title.
         * Day 0 displays as "Day 1" because the user thinks in
         * 1-indexed terms; the ``dayOffset`` field on the wire is
         * just the spaced-repetition step (0 / 3 / 7).
         *
         * For ``theme="generic"`` (or empty), the kicker collapses to
         * "Day N of M" without the trailing themed segment.
         */
        fun formatKicker(dayOffset: Int, totalDays: Int, theme: String): String {
            val displayDay = when (dayOffset) {
                0 -> 1
                3 -> 2
                7 -> 3
                else -> 1
            }
            val themeLabel = prettyTheme(theme)
            return if (themeLabel.isEmpty()) {
                "Day $displayDay of $totalDays"
            } else {
                "Day $displayDay of $totalDays · $themeLabel"
            }
        }

        /**
         * Map a server-side theme tag (snake_case) to a sentence-case
         * label, returning the empty string when the theme is
         * ``"generic"`` (treated as "no specific theme to surface").
         */
        fun prettyTheme(theme: String): String {
            val tag = theme.trim().lowercase()
            if (tag.isEmpty() || tag == "generic") return ""
            val parts = tag.split('_')
            return parts.first().replaceFirstChar(Char::uppercaseChar) +
                parts.drop(1).joinToString("") { " $it" }
        }
    }
}
package com.cereveon.myapp

import java.net.HttpURLConnection

/**
 * Header the backend uses to hand back a freshly-minted JWT on every
 * successful authenticated response.  See [get_current_player] in
 * llm/seca/auth/router.py.
 *
 * The contract: an active client rotates the JWT it stores after
 * each authenticated call, so a stolen JWT is only valid until the
 * next legitimate call from the real owner happens — and the JWT
 * exp itself is bounded at 24 h regardless.
 */
internal const val X_AUTH_TOKEN_HEADER = "X-Auth-Token"

/**
 * Read [X_AUTH_TOKEN_HEADER] from a completed [HttpURLConnection]
 * response and pass any non-blank value to [sink].  No-op when:
 *   - [sink] is null (clients that don't participate in refresh)
 *   - the header is absent (unauthenticated routes / older server)
 *   - the value is blank (defensive)
 *
 * Header lookup is case-insensitive — Java's HttpURLConnection
 * normalises header names to whatever case the server returned.
 *
 * Designed to be called only after a successful response (HTTP 200);
 * server-side guards already prevent the header from being attached
 * to 4xx/5xx, so calling this on every code path would be safe but
 * a waste of one method invocation per failure.
 */
internal fun consumeRefreshedToken(conn: HttpURLConnection, sink: ((String) -> Unit)?) {
    if (sink == null) return
    val refreshed = conn.getHeaderField(X_AUTH_TOKEN_HEADER) ?: return
    if (refreshed.isBlank()) return
    sink(refreshed)
}
package com.cereveon.myapp

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Abstraction over the JWT persistence layer.
 *
 * Defining storage as an interface allows [AuthRepository] to be tested with
 * a pure in-memory fake on the JVM, without requiring a real Android Keystore
 * or filesystem. Production code wires in [EncryptedTokenStorage].
 */
interface TokenStorage {
    /** Persist [token] to durable storage, replacing any previously stored value. */
    fun save(token: String)

    /** Return the stored token, or null if none has been saved or it was cleared. */
    fun load(): String?

    /** Remove the stored token. Subsequent [load] calls return null. */
    fun clear()
}

/**
 * Production [TokenStorage] backed by [EncryptedSharedPreferences].
 *
 * The token is encrypted at rest using AES-256-GCM via the Android Keystore,
 * and the shared-preferences key is encrypted with AES-256-SIV.
 *
 * The [EncryptedSharedPreferences] instance is created lazily on the first
 * access; subsequent accesses are instant. This class is safe to construct
 * from the main thread because Keystore operations are deferred.
 *
 * Requires API level 23+; the app's minSdk is 26, so this is always safe.
 *
 * @param context   Any [Context]; the application context is used internally.
 * @param prefsName Shared-preferences file name. Override in tests if needed.
 */
class EncryptedTokenStorage(
    context: Context,
    prefsName: String = "auth_prefs",
) : TokenStorage {

    private val prefs by lazy {
        val masterKey =
            MasterKey.Builder(context.applicationContext)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
        EncryptedSharedPreferences.create(
            context.applicationContext,
            prefsName,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun save(token: String) {
        prefs.edit().putString(KEY_TOKEN, token).apply()
    }

    override fun load(): String? = prefs.getString(KEY_TOKEN, null)

    override fun clear() {
        prefs.edit().remove(KEY_TOKEN).apply()
    }

    private companion object {
        const val KEY_TOKEN = "jwt_token"
    }
}
package com.cereveon.myapp

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat

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
    // muted contrast preserves the "official document" calm).  Token
    // reads so the chart follows the active palette (bright mode
    // flips them via values-notnight/colors.xml).
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_muted)
        textSize = 36f
        typeface = android.graphics.Typeface.MONOSPACE
    }

    private val valuePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_muted)
        textSize = 32f
        typeface = android.graphics.Typeface.MONOSPACE
    }

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    // Atrium hairline — same primitive used for dividers.
    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.atrium_hairline)
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
    private fun priorityColor(priority: String, value: Float): Int {
        val amber = ContextCompat.getColor(context, R.color.atrium_accent_amber)
        val amberMuted = ContextCompat.getColor(context, R.color.atrium_accent_amber_cc)
        val cyan = ContextCompat.getColor(context, R.color.atrium_accent_cyan)
        return when (priority) {
            "high"   -> amber
            "medium" -> amberMuted
            "low"    -> cyan
            else     -> when {
                value > 0.12f -> amber
                value > 0.06f -> amberMuted
                else          -> cyan
            }
        }
    }
}
