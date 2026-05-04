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
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.viewModels
import androidx.core.view.GravityCompat
import androidx.appcompat.app.AppCompatActivity
import androidx.drawerlayout.widget.DrawerLayout
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

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
    private lateinit var authApiClient: AuthApiClient
    private lateinit var authRepo: AuthRepository
    private lateinit var txtRatingHeader: TextView
    private lateinit var txtWeaknessTags: TextView
    private lateinit var txtNextTrainingChip: TextView
    private var currentPlayerId: String = "demo"
    private val moveClassifications = mutableListOf<MistakeClassification>()

    /**
     * Cached result from the most recent /game/finish call.
     * Provides [PlayerProfileDto] (rating + confidence) and weakness categories for
     * the next chat session opened via [openChat].  Null before the first game ends.
     */
    private var lastGameFinishResponse: GameFinishResponse? = null

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

        // Wire the engine-strength dial: the server's /player/progress
        // returns a rating-derived opponentElo, which ChessViewModel
        // reads via PlayerProfileCache and maps to a 0–100 strength
        // level passed to ChessNative.getBestMoveWithStrength.  Without
        // this wiring the engine plays at full strength regardless of
        // the user's calibration — see test_adaptive_engine_wiring.py
        // for the contract on both ends.
        viewModel.playerProfileCache = PlayerProfileCache(gameApiClient)
        authApiClient = HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            // Wire X-Auth-Token rotation: every successful authenticated
            // response (me, updateMe, changePassword) hands back a fresh
            // 24h JWT; this sink saves it so the next call picks up the
            // rotated token.  Without this, the JWT would expire after
            // 24h and bounce the user to login.
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )

        // If a previous /game/finish failed offline (timeout / 5xx /
        // network), the payload was persisted; try again now that we
        // (probably) have connectivity.  Fire-and-forget — see the
        // method's kdoc for the keep-vs-drop policy.
        retryPendingFinishOnColdStart()

        // Verify SECA safe_mode at cold-start — fire-and-forget, no UI update.
        lifecycleScope.launch {
            when (val r = gameApiClient.getSecaStatus()) {
                is ApiResult.Success -> {
                    Log.d("SECA", "seca/status: safe_mode=${r.data.safeModeEnabled}")
                    if (!r.data.safeModeEnabled) {
                        Log.w("SECA", "WARNING: backend reports safe_mode=false — bandit training may be active")
                    }
                }
                else -> Log.d("SECA", "seca/status unavailable (${r::class.simpleName})")
            }
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

        txtRatingHeader = findViewById(R.id.txtRatingHeader)
        txtWeaknessTags = findViewById(R.id.txtWeaknessTags)
        txtNextTrainingChip = findViewById(R.id.txtNextTrainingChip)
        val btnReset = findViewById<Button>(R.id.btnReset)
        val btnUndo = findViewById<Button>(R.id.btnUndo)
        val btnChat = findViewById<Button>(R.id.btnChat)
        val btnGameHistory = findViewById<Button>(R.id.btnGameHistory)
        val btnTraining = findViewById<Button>(R.id.btnTraining)
        val btnChangePassword = findViewById<Button>(R.id.btnChangePassword)
        val btnLogout = findViewById<Button>(R.id.btnLogout)

        // START PULSE ANIMATION
        startPulseAnimation()

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
                    }
                )
            } else {
                Toast.makeText(this, "Engine not available", Toast.LENGTH_SHORT).show()
            }
        }

        // -------- SIDEBAR BUTTONS --------
        btnReset.setOnClickListener {
            if (ChessNative.isLibraryLoaded) {
                viewModel.reset()
                chessBoard.resetBoard()
            }
            moveClassifications.clear()
            coachText.text = "♟ New game. Control the center!"
            scoreRow.visibility = View.GONE
            txtEngineScore.text = ""
            txtMistakeCategory.text = ""
            updateChapterHeader()
            drawerLayout.closeDrawer(GravityCompat.END)
            startNewGameSession()
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

        btnTraining.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            lifecycleScope.launch {
                when (val r = gameApiClient.getNextCurriculum(currentPlayerId)) {
                    is ApiResult.Success -> {
                        if (!supportFragmentManager.isStateSaved) {
                            TrainingSessionBottomSheet
                                .newInstance(r.data)
                                .show(supportFragmentManager, "TrainingSessionBottomSheet")
                        }
                    }
                    else -> Toast.makeText(
                        this@MainActivity,
                        "Training unavailable — try again later",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            }
        }

        // Atrium Settings sheet — preferences (coach voice, board
        // style, sound, notifications, account chevrons).  Account
        // section delegates to the existing change-password dialog
        // and logout flow rather than duplicating them.
        findViewById<Button>(R.id.btnSettings)?.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            val sheet = SettingsBottomSheet()
            sheet.onChangePasswordTapped = { showChangePasswordDialog() }
            sheet.onSignOutTapped = { performLogout() }
            sheet.show(supportFragmentManager, "SettingsBottomSheet")
        }

        btnChangePassword.setOnClickListener {
            drawerLayout.closeDrawer(GravityCompat.END)
            showChangePasswordDialog()
        }

        btnLogout.setOnClickListener {
            performLogout()
        }

        // Show persisted rating and cached curriculum chip if available.
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val storedRating = prefs.getFloat(PREF_RATING, -1f)
        if (storedRating >= 0f) {
            txtRatingHeader.text = "Rating: %.0f".format(storedRating)
        }
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
                        // Recovery path for the OnboardingActivity PATCH:
                        // if the local PREF_PLAYER_RATING_ESTIMATE was
                        // set (i.e. user completed Onboarding) but the
                        // server rating still differs, the PATCH
                        // attempted there must have failed (offline,
                        // server down).  Re-PATCH now while we have a
                        // good network round-trip in flight, then use
                        // the local values as the source of truth for
                        // this cold-start.  Without this branch the
                        // server's stale rating would clobber the
                        // calibration the user just completed.
                        val localEstimate = prefs.getFloat(PREF_PLAYER_RATING_ESTIMATE, -1f)
                        val localConfidence = prefs.getFloat(PREF_PLAYER_CONFIDENCE_LOCAL, -1f)
                        val needsReconcile = localEstimate >= 0f &&
                            kotlin.math.abs(localEstimate - r.data.rating) > RATING_RECONCILE_EPSILON
                        val reconciled = if (needsReconcile) {
                            val confArg = localConfidence.takeIf { it >= 0f }
                            when (val patch = authApiClient.updateMe(
                                token = authToken,
                                rating = localEstimate,
                                confidence = confArg,
                            )) {
                                is ApiResult.Success -> patch.data
                                else -> {
                                    Log.d("AUTH", "PATCH /auth/me reconcile failed: $patch")
                                    r.data
                                }
                            }
                        } else r.data

                        txtRatingHeader.text = "Rating: %.0f".format(reconciled.rating)
                        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                            .putFloat(PREF_RATING, reconciled.rating)
                            .putFloat(PREF_CONFIDENCE, reconciled.confidence)
                            .apply()
                        val tags = formatWeaknessTags(reconciled.skillVector)
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

        chessBoard.onGameOver = { result ->
            val pgn = viewModel.exportPGN()
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
                gameId = currentServerGameId,
            )
            lifecycleScope.launch {
                when (val r = gameApiClient.finishGame(finishReq)) {
                    is ApiResult.Success -> {
                        lastGameFinishResponse = r.data
                        showCoachingResult(r.data, finalResult, finalMoveCount)
                        // Server bumped the rating + (likely) shifted
                        // opponent_elo.  Drop the cached profile so
                        // the next AI move re-fetches and the
                        // strength dial reflects the updated level.
                        viewModel.playerProfileCache?.invalidate()
                    }
                    is ApiResult.HttpError -> {
                        if (r.code == 401) {
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
            startNewGameSession()
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
        )
        viewModel.onQuickCoachUpdate = { update ->
            // Track for end-of-game accuracy computation — only on human-move updates
            // to avoid double-counting (one human-move coach update + one AI-score update per turn)
            if (update.isHumanMoveCoachUpdate) {
                moveClassifications.add(update.classification)
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

        // HomeActivity routes "Lessons" / "Past games" / "Coach" / "You"
        // taps here with EXTRA_OPEN_SHEET so the existing sheet wiring
        // is the single source of truth for those flows.  All four
        // re-use the same buttons / openChat() the drawer uses; the
        // performClick() path also closes the (already-closed) drawer
        // harmlessly.
        when (intent?.getStringExtra(EXTRA_OPEN_SHEET)) {
            OPEN_SHEET_TRAINING -> btnTraining.performClick()
            OPEN_SHEET_HISTORY  -> btnGameHistory.performClick()
            OPEN_SHEET_PROFILE  -> btnProgressDashboard.performClick()
            OPEN_SHEET_CHAT     -> openChat()
        }
    }

    companion object {
        const val PREFS_NAME = "chesscoach_prefs"
        const val PREF_RATING = "last_rating"
        const val PREF_CONFIDENCE = "last_confidence"
        const val PREF_CURRICULUM_TOPIC = "curriculum_topic"
        const val PREF_CURRICULUM_DIFFICULTY = "curriculum_difficulty"
        const val PREF_CURRICULUM_EXERCISE_TYPE = "curriculum_exercise_type"

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

        // Intent extras used by HomeActivity to ask MainActivity to
        // open a specific bottom sheet on startup.  String constants
        // (rather than an enum) keep the Intent contract trivially
        // serialisable and let HomeActivity pass null for "no sheet".
        const val EXTRA_OPEN_SHEET     = "open_sheet"
        const val OPEN_SHEET_TRAINING  = "training"
        const val OPEN_SHEET_HISTORY   = "history"
        const val OPEN_SHEET_PROFILE   = "profile"
        const val OPEN_SHEET_CHAT      = "chat"

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

    private fun performLogout() {
        val token = authRepo.getToken()
        lifecycleScope.launch {
            if (token != null) {
                authApiClient.logout(token)   // best-effort; ignore result
            }
            authRepo.clearToken()
            startActivity(
                Intent(this@MainActivity, LoginActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
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

    private fun openChat() {
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

    private fun showChangePasswordDialog() {
        if (isFinishing || isDestroyed) return
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(64, 32, 64, 16)
        }
        val etCurrent = EditText(this).apply {
            hint = "Current password"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val etNew = EditText(this).apply {
            hint = "New password (min 8 characters)"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        layout.addView(etCurrent)
        layout.addView(etNew)

        AlertDialog.Builder(this)
            .setTitle("Change Password")
            .setView(layout)
            .setPositiveButton("Save") { _, _ ->
                val current = etCurrent.text.toString()
                val new = etNew.text.toString()
                if (current.isBlank() || new.isBlank()) {
                    Toast.makeText(this, "Fields must not be empty.", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                if (new.length < 8) {
                    Toast.makeText(this, "New password must be at least 8 characters.", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                val token = authRepo.getToken() ?: return@setPositiveButton
                lifecycleScope.launch {
                    when (authApiClient.changePassword(current, new, token)) {
                        is ApiResult.Success ->
                            Toast.makeText(this@MainActivity, "Password updated.", Toast.LENGTH_SHORT).show()
                        is ApiResult.HttpError ->
                            Toast.makeText(this@MainActivity, "Incorrect current password.", Toast.LENGTH_SHORT).show()
                        else ->
                            Toast.makeText(this@MainActivity, "Network error. Please try again.", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun startNewGameSession() {
        bumpGameNumber()
        currentServerGameId = null
        lifecycleScope.launch {
            when (val r = gameApiClient.startGame(currentPlayerId)) {
                is ApiResult.Success -> {
                    currentServerGameId = r.data.gameId
                    getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                        .putString(PREF_LAST_GAME_SERVER_ID, r.data.gameId)
                        .apply()
                    Log.d("GAME", "Session started: ${r.data.gameId}")
                }
                is ApiResult.HttpError -> Log.w("GAME", "startGame HTTP ${r.code}")
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

        // Update rating header immediately so it's visible when the drawer is open
        txtRatingHeader.text = "Rating: %.0f".format(response.newRating)

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
                applyAIMove = { afr, afc, atr, atc -> chessBoard.applyAIMove(afr, afc, atr, atc) }
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
    }
}
