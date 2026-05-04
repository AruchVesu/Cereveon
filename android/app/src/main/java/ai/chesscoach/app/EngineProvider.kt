package ai.chesscoach.app

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
