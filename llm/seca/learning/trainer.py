import asyncio

from llm.seca.learning.outcome_tracker import ExplanationOutcomeTracker
from llm.seca.learning.causal_impact import CausalTrainingImpactEngine
from llm.seca.runtime.safe_mode import SAFE_MODE


class SECALearner:
    """
    Background learning loop for SECA.

    Periodically:
    - collects explanation outcomes
    - updates causal training impact model
    """

    def __init__(self):
        self.tracker = ExplanationOutcomeTracker()
        self.causal = CausalTrainingImpactEngine()
        self.running = False

    # ------------------------------------------------------------------
    # Async background loop
    # ------------------------------------------------------------------

    async def run(self, interval_seconds: int = 60):
        """Start periodic learning loop."""
        self.running = True

        while self.running:
            try:
                self._train_step()
            except Exception as e:
                print(">>> Learner error:", e)

            await asyncio.sleep(interval_seconds)

    # ------------------------------------------------------------------
    # Single training step
    # ------------------------------------------------------------------

    def _train_step(self):
        """
        Pull new outcomes → update causal model.
        """

        if SAFE_MODE:
            return

        samples = self.tracker.consume_new_samples()

        if not samples:
            return

        for s in samples:
            self.causal.observe(
                player_id=s.player_id,
                explanation_id=s.explanation_id,
                features=s.features,
                treatment=s.treatment,
                outcome=s.learning_score,
            )

        # Fit causal estimator
        self.causal.fit()

        # Debug output
        try:
            ate = self.causal.estimator.ate(self.causal.dataset.to_arrays()[0])
            print(">>> Causal model updated. ATE:", ate)
        except Exception:
            print(">>> Causal model updated (ATE unavailable)")
