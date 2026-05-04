package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class EloToStrengthTest {

    @Test fun minEloGivesZero() = assertEquals(0, EloToStrength.map(600))

    @Test fun maxEloGivesHundred() = assertEquals(100, EloToStrength.map(2400))

    @Test fun midpointEloGivesFifty() = assertEquals(50, EloToStrength.map(1500))

    @Test fun belowMinIsClamped() = assertEquals(0, EloToStrength.map(0))

    @Test fun aboveMaxIsClamped() = assertEquals(100, EloToStrength.map(3000))

    @Test fun isMonotonic() {
        val elos = listOf(600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400)
        val strengths = elos.map { EloToStrength.map(it) }
        for (i in 1 until strengths.size) {
            assertTrue(
                "strength must not decrease: index $i (${strengths[i - 1]} → ${strengths[i]})",
                strengths[i] >= strengths[i - 1],
            )
        }
    }
}
