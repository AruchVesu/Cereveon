# seca/closed_loop/curriculum_adjust.py
def adjust_curriculum(action, error, scale=0.1):
    """
    If improvement is weaker than predicted,
    push curriculum toward stronger intervention.
    """
    magnitude = error.norm().item()

    if magnitude < 0.05:
        return action  # prediction accurate

    # increase training intensity
    return action + scale * error
