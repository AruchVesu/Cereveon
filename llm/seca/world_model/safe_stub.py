from llm.seca.runtime.safe_mode import assert_safe

assert_safe()


class SafeWorldModel:
    def predict_next(self, state, action=None):  # pylint: disable=unused-argument
        # ``action`` is part of the world-model contract (matches the
        # surface dormant adaptive code paths expect) but the SAFE_MODE
        # stub doesn't use it — predicting "no change" is the safe
        # behaviour while online learning is disabled.
        return state
