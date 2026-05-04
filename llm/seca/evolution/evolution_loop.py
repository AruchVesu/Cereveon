from llm.seca.policy.policy_evolver import evolve_policy


class EvolutionLoop:
    def __init__(self, policy_store, outcome_tracker):
        self.policy_store = policy_store
        self.outcome_tracker = outcome_tracker

    def step(self, user_id: str):
        policy = self.policy_store.load(user_id)
        outcome = self.outcome_tracker.latest(user_id)

        if outcome:
            policy = evolve_policy(policy, outcome)
            self.policy_store.save(user_id, policy)
