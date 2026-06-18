package ai.chesscoach.app

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
     * Optional seed prompt injected by [TrainingSessionBottomSheet].
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
                        }
                    }
                }
            } finally {
                streamFinished = true
            }
            reveal.join()

            // Ensure the complete reply is shown (fallback if the stream produced
            // nothing; also covers an abort that shortened the text), then persist.
            val displayReply = accumulated.takeIf { it.isNotBlank() } ?: FALLBACK_REPLY
            renderStreamUpdate(displayReply)
            sessionStore.addMessage("assistant", displayReply)

            isStreaming = false
            sendBtn.isEnabled = true
            typingDots.visibility = View.GONE
        }
    }
}
