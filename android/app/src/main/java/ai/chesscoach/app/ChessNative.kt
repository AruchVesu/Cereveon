package ai.chesscoach.app

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
