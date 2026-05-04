from llm.seca.runtime.safe_mode import assert_safe

assert_safe()


class SafeWorldModel:
    def predict_next(self, state, action=None):
        return state
