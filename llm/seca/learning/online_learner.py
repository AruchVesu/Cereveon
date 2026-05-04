from datetime import datetime
from ..skills.trainer import SkillTrainer
from ..storage.event_storage import EventStorage
from ..skills.skill_graph import SkillGraph
from ..learning.causal_engine import CausalEngine
from ..coaching.world_policy import WorldModelPolicy
from ..runtime.safe_mode import SAFE_MODE
from ..world_model.state import PlayerStateEncoder
from ..world_model.transition import SkillTransitionModel
from ..world_model.outcome import OutcomePredictor
from ..world_model.simulator import CounterfactualSimulator


class OnlineSECALearner:
    """
    Minimal continuous learner.

    Runs one training step after each finished game.
    """

    def __init__(self, storage: EventStorage, trainer: SkillTrainer):
        self.storage = storage
        self.trainer = trainer
        self.graph = SkillGraph()
        self.causal = CausalEngine(self.graph)
        self.encoder = PlayerStateEncoder(self.graph)
        dim = len(self.encoder.encode())

        self.transition = SkillTransitionModel(dim)
        self.outcome = OutcomePredictor(dim)
        self.simulator = CounterfactualSimulator(
            self.encoder,
            self.transition,
            self.outcome,
        )

        self.policy = WorldModelPolicy(self.simulator, list(self.graph.values.keys()))

    def train_step(self):
        """
        Pull latest events → update skill model.
        """
        if SAFE_MODE:
            return {"status": "safe_mode"}

        events = self.storage.get_recent_games(limit=50)

        if not events:
            return {"status": "no_data"}

        result = self.trainer.train_on_events(events)

        for e in events:
            self.causal.observe_game(
                accuracy=e.accuracy,
                weaknesses=e.weaknesses_json,
                result=1 if e.result == "win" else 0,
            )

        focus, prob = self.policy.choose_focus()

        return {
            "status": "trained",
            "next_focus": focus,
            "timestamp": datetime.utcnow().isoformat(),
            **result,
        }
