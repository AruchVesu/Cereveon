package ai.chesscoach.app

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
                    applyAIMove = { afr, afc, atr, atc ->
                        chessBoard.applyAIMove(afr, afc, atr, atc).also {
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

            // Show mistake category badge with severity colour
            txtMistakeCategory.text = update.classification.label()
            val categoryColor = when (update.classification) {
                MistakeClassification.BLUNDER    -> 0xFFFF4444.toInt()
                MistakeClassification.MISTAKE    -> 0xFFFF8800.toInt()
                MistakeClassification.INACCURACY -> 0xFFFFDD00.toInt()
                MistakeClassification.GOOD       -> 0xFF00FFFF.toInt()
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
                applyAIMove = { afr, afc, atr, atc -> chessBoard.applyAIMove(afr, afc, atr, atc) },
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
