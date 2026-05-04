from dataclasses import dataclass
import numpy as np


@dataclass
class GovernorState:
    alpha_bandit: float = 0.1
    difficulty_step: float = 0.0
    learning_frozen: bool = False


class AutonomousTrainingGovernor:
    def __init__(
        self,
        alpha_min=0.01,
        alpha_max=0.5,
        d_max=0.3,
        k1=0.05,
        k2=0.1,
        k3=0.2,
        k4=0.1,
    ):
        self.alpha_min = alpha_min
        self.alpha_max = alpha_max
        self.d_max = d_max

        self.k1 = k1
        self.k2 = k2
        self.k3 = k3
        self.k4 = k4

        self.state = GovernorState()

    # ----------------------------------
    # Main update step
    # ----------------------------------
    def update(self, delta_rating, delta_conf, pred_error, games_7d):
        if self.state.learning_frozen:
            return self.state

        # ---- adjust bandit learning rate ----
        new_alpha = self.state.alpha_bandit + self.k1 * delta_rating - self.k2 * pred_error

        self.state.alpha_bandit = float(np.clip(new_alpha, self.alpha_min, self.alpha_max))

        # ---- adjust curriculum difficulty ----
        diff = self.k3 * delta_rating + self.k4 * delta_conf
        self.state.difficulty_step = float(np.clip(diff, -self.d_max, self.d_max))

        # ---- freeze detection ----
        if delta_rating < -50 and delta_conf < -0.1:
            self.state.learning_frozen = True

        return self.state


class MultiGovernor:

    def __init__(self, governors, weights):
        self.governors = governors
        self.weights = weights  # dict: name -> weight

    def step(self, obs):
        outputs = {name: g.update(**obs) for name, g in self.governors.items()}

        # ---- freeze dominance ----
        freeze = any(o.learning_frozen for o in outputs.values())

        # ---- weighted merge ----
        alpha = sum(self.weights[name] * outputs[name].alpha_bandit for name in outputs)

        difficulty = sum(self.weights[name] * outputs[name].difficulty_step for name in outputs)

        return {
            "alpha_bandit": alpha,
            "difficulty_step": difficulty,
            "learning_frozen": freeze,
        }


class SafetyShield:

    def __init__(self, c_min=0.3, delta_max=0.2, alpha_max=1.0):
        self.c_min = c_min
        self.delta_max = delta_max
        self.alpha_max = alpha_max

    def enforce(self, prev_state, proposal):
        safe = proposal.copy()

        # ---- confidence floor ----
        if prev_state["confidence"] < self.c_min:
            safe["learning_frozen"] = True

        # ---- difficulty smoothness ----
        diff_change = abs(proposal["difficulty_step"])
        if diff_change > self.delta_max:
            safe["difficulty_step"] = self.delta_max * (
                1 if proposal["difficulty_step"] > 0 else -1
            )

        # ---- bounded alpha ----
        safe["alpha_bandit"] = max(0.0, min(self.alpha_max, proposal["alpha_bandit"]))

        return safe


class HumanValueShield:

    def __init__(self, flow_band=150, agency_min=0.3):
        self.flow_band = flow_band
        self.agency_min = agency_min

    def enforce(self, state, proposal):
        safe = proposal.copy()

        # ---- Flow constraint ----
        skill_gap = abs(state["rating"] - proposal["target_difficulty"])
        if skill_gap > self.flow_band:
            safe["target_difficulty"] = state["rating"] + (
                self.flow_band
                if proposal["target_difficulty"] > state["rating"]
                else -self.flow_band
            )

        # ---- Agency constraint ----
        if proposal["choice_entropy"] < self.agency_min:
            safe["force_multiple_options"] = True

        return safe
