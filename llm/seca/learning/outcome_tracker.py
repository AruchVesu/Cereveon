# seca/learning/outcome_tracker.py

from dataclasses import dataclass
from typing import Dict, List
import uuid
import time

# ---------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------


@dataclass
class ExplanationEvent:
    explanation_id: str
    player_id: str
    fen: str
    engine_signal: Dict
    tone: str
    timestamp: float


@dataclass
class OutcomeMetrics:
    explanation_id: str
    moves_analyzed: int
    avg_cpl: float
    blunder_rate: float
    tactic_success: bool
    confidence_delta: float


# ---------------------------------------------------------
# TRACKER
# ---------------------------------------------------------


class ExplanationOutcomeTracker:
    """
    Links explanations → future performance.
    Core feedback loop for SECA learning.
    """

    def __init__(self):
        self.events: Dict[str, ExplanationEvent] = {}
        self.outcomes: Dict[str, OutcomeMetrics] = {}

    # -----------------------------------------------------
    # RECORD EXPLANATION
    # -----------------------------------------------------

    def record_explanation(
        self,
        player_id: str,
        fen: str,
        engine_signal: Dict,
        tone: str,
    ) -> str:
        explanation_id = str(uuid.uuid4())

        event = ExplanationEvent(
            explanation_id=explanation_id,
            player_id=player_id,
            fen=fen,
            engine_signal=engine_signal,
            tone=tone,
            timestamp=time.time(),
        )

        self.events[explanation_id] = event
        return explanation_id

    # -----------------------------------------------------
    # RECORD OUTCOME
    # -----------------------------------------------------

    def record_outcome(
        self,
        explanation_id: str,
        moves_analyzed: int,
        avg_cpl: float,
        blunder_rate: float,
        tactic_success: bool,
        confidence_delta: float,
    ):
        if explanation_id not in self.events:
            raise ValueError("Unknown explanation_id")

        metrics = OutcomeMetrics(
            explanation_id=explanation_id,
            moves_analyzed=moves_analyzed,
            avg_cpl=avg_cpl,
            blunder_rate=blunder_rate,
            tactic_success=tactic_success,
            confidence_delta=confidence_delta,
        )

        self.outcomes[explanation_id] = metrics

    # -----------------------------------------------------
    # LEARNING SIGNAL
    # -----------------------------------------------------

    def compute_learning_score(self, explanation_id: str) -> float:
        """
        Converts outcome metrics into a scalar reward signal.
        Range ≈ [-1, +1]
        """

        if explanation_id not in self.outcomes:
            return 0.0

        m = self.outcomes[explanation_id]

        score = 0.0

        # lower CPL → better
        score += max(0.0, 1.0 - m.avg_cpl / 100)

        # fewer blunders → better
        score += 1.0 - m.blunder_rate

        # tactic success bonus
        if m.tactic_success:
            score += 0.5

        # psychological confidence
        score += m.confidence_delta

        # Max possible score: CPL(1.0) + blunder(1.0) + tactic(0.5) + confidence(≤1.0) = 3.5
        # Divide by 3.5 so that all-perfect performance maps to ~1.0, then clip to [-1, 1].
        return max(-1.0, min(1.0, score / 3.5))

    # -----------------------------------------------------
    # AGGREGATE PLAYER IMPACT
    # -----------------------------------------------------

    def player_learning_signal(self, player_id: str) -> float:
        scores: List[float] = []

        for eid, event in self.events.items():
            if event.player_id != player_id:
                continue

            if eid in self.outcomes:
                scores.append(self.compute_learning_score(eid))

        if not scores:
            return 0.0

        return sum(scores) / len(scores)
