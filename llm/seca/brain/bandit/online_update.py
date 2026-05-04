from .contextual_bandit import ContextualBandit
from .actions import ACTIONS

bandit = ContextualBandit()


def update_after_game(context, action_index, reward):
    """
    context: player state vector
    action_index: which coaching intervention was used
    reward: rating_delta or learning signal
    """
    bandit.update(context, action_index, reward)
    bandit.save()
