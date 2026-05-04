package ai.chesscoach.app

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
    /** Emits a structured [QuickCoachUpdate] after each AI move. */
    var quickCoachListener: ((QuickCoachUpdate) -> Unit)? = null
    /** Fires when checkmate or stalemate is detected. */
    var onGameOver: ((GameResult) -> Unit)? = null

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
            checkAndNotifyGameOver()
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
    fun applyAIMove(fr: Int, fc: Int, tr: Int, tc: Int): Char {
        if (fr !in 0..7 || fc !in 0..7 || tr !in 0..7 || tc !in 0..7) {
            Log.e("CHESS_BOARD", "AI Move out of bounds: $fr,$fc -> $tr,$tc")
            return '.'
        }

        if (!isLegal(fr, fc, tr, tc)) {
            Log.e("CHESS_BOARD", "AI ATTEMPTED ILLEGAL MOVE: $fr,$fc -> $tr,$tc")
            return '.'
        }

        val capturedPiece = board[tr][tc]
        executeMove(fr, fc, tr, tc)
        whiteToMove = !whiteToMove
        checkAndNotifyGameOver()
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
        return "$rows ${if (whiteToMove) "w" else "b"}"
    }

    fun promotePawn(r: Int, c: Int, to: Char) {
        board[r][c] = if (board[r][c].isUpperCase()) to.uppercaseChar() else to.lowercaseChar()
        whiteToMove = !whiteToMove
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

    private fun checkAndNotifyGameOver() {
        if (hasAnyLegalMove()) return
        gameOver = true
        val inCheck = isInCheck(whiteToMove)
        val result = when {
            inCheck && whiteToMove -> GameResult.BLACK_WINS
            inCheck && !whiteToMove -> GameResult.WHITE_WINS
            else -> GameResult.DRAW
        }
        onGameOver?.invoke(result)
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

    private fun isLegalGeometry(p: Char, sr: Int, sc: Int, tr: Int, tc: Int): Boolean {
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
            'k' -> (dr <= 1 && dc <= 1) || (dr == 0 && dc == 2 && canCastle(p, sr, sc, tr, tc))
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
        if (isInCheck(white)) return false
        if (white && whiteKingMoved) return false
        if (!white && blackKingMoved) return false
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
                    if (isLegalGeometry(p, row, col, r, c)) return true
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
        if (piece.lowercaseChar() == 'p' && (tr == 0 || tr == 7)) promotionListener?.invoke(tr, tc)
        invalidate()
    }

    private fun updateFlags(p: Char, r: Int, c: Int) {
        if (p == 'K') whiteKingMoved = true; if (p == 'k') blackKingMoved = true
        if (p == 'R') { if (r == 7 && c == 0) whiteRookAMoved = true; if (r == 7 && c == 7) whiteRookHMoved = true }
        if (p == 'r') { if (r == 0 && c == 0) blackRookAMoved = true; if (r == 0 && c == 7) blackRookHMoved = true }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!isInteractive || gameOver || event.action != MotionEvent.ACTION_DOWN) return true
        val col = (event.x / (width / 8f)).toInt(); val row = (event.y / (width / 8f)).toInt()
        if (row !in 0..7 || col !in 0..7) return true
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
                val l = c * squareSize
                val t = r * squareSize
                val rr = (c + 1) * squareSize
                val bb = (r + 1) * squareSize
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
                if (c == 0) {
                    val rank = (8 - r).toString()
                    canvas.drawText(rank, 8f, r * squareSize + coordinatePaint.textSize, coordinatePaint)
                }
                if (r == 7) {
                    val file = ('a' + c).toString()
                    canvas.drawText(file, (c + 1) * squareSize - coordinatePaint.measureText(file) - 8f, 8 * squareSize - 8f, coordinatePaint)
                }
                val p = board[r][c]
                if (p != '.') {
                    val paint = if (p.isUpperCase()) piecePaintWhite else piecePaintBlack
                    canvas.drawText(pieceToUnicode(p), c * squareSize + squareSize / 2, r * squareSize + squareSize * 0.82f, paint)
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
            val cx = c * squareSize + squareSize / 2f
            val cy = r * squareSize + squareSize / 2f
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
        
        val startX = arrow.sc * squareSize + squareSize / 2
        val startY = arrow.sr * squareSize + squareSize / 2
        val endX = arrow.tc * squareSize + squareSize / 2
        val endY = arrow.tr * squareSize + squareSize / 2
        
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
