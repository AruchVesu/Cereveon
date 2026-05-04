try:
    from llm.seca.auth.models import Base, Player, Session
    from llm.seca.events.models import GameEvent
    from llm.seca.brain.training.models import TrainingDecision, TrainingOutcome
except ModuleNotFoundError:
    from seca.auth.models import Base, Player, Session
    from seca.events.models import GameEvent
    from seca.brain.training.models import TrainingDecision, TrainingOutcome

__all__ = [
    "Base",
    "Player",
    "Session",
    "GameEvent",
    "TrainingDecision",
    "TrainingOutcome",
]
