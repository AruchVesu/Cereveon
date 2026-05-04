import numpy as np


class SimpleCausalDataset:
    """
    Stores (X, treatment, outcome) tuples for causal estimation.
    """

    def __init__(self):
        self.X = []
        self.t = []
        self.y = []

    # --------------------------------------------------------------

    def add(self, features, treatment, outcome):
        self.X.append(np.asarray(features, dtype=float))
        self.t.append(float(treatment))
        self.y.append(float(outcome))

    # --------------------------------------------------------------

    def to_arrays(self):
        if not self.X:
            return None, None, None

        return (
            np.vstack(self.X),
            np.asarray(self.t),
            np.asarray(self.y),
        )


# ==================================================================
# Extremely simple ATE estimator (placeholder for real causal model)
# ==================================================================


class MeanDifferenceATE:
    def fit(self, X, t, y):
        treated = y[t > 0.5]
        control = y[t <= 0.5]

        if len(treated) == 0 or len(control) == 0:
            self._ate = 0.0
        else:
            self._ate = treated.mean() - control.mean()

    # --------------------------------------------------------------

    def ate(self, X):
        return getattr(self, "_ate", 0.0)


# ==================================================================
# Public engine used by SECA
# ==================================================================


class CausalTrainingImpactEngine:
    """
    Collects explanation outcomes and estimates causal learning impact.
    """

    def __init__(self):
        self.dataset = SimpleCausalDataset()
        self.estimator = MeanDifferenceATE()

    # --------------------------------------------------------------

    def observe(self, player_id, explanation_id, features, treatment, outcome):
        self.dataset.add(features, treatment, outcome)

    # --------------------------------------------------------------

    def fit(self):
        arrays = self.dataset.to_arrays()
        if arrays[0] is None:
            return

        X, t, y = arrays
        self.estimator.fit(X, t, y)
