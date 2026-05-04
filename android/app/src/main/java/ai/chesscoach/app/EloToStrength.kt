package ai.chesscoach.app

object EloToStrength {
    private const val ELO_MIN = 600
    private const val ELO_MAX = 2400

    fun map(opponentElo: Int): Int =
        ((opponentElo - ELO_MIN) * 100 / (ELO_MAX - ELO_MIN)).coerceIn(0, 100)
}
