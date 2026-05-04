from .coaching_policy import CoachingPolicy
from llm.seca.outcome.outcome_model import Outcome


def evolve_policy(policy: CoachingPolicy, outcome: Outcome) -> CoachingPolicy:
    if outcome.delta <= 0:
        policy.tactic_ratio = min(1.0, policy.tactic_ratio + 0.05)
        policy.session_length_min = max(10, policy.session_length_min - 5)
    return policy
