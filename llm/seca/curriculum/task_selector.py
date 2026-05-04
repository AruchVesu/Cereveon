from llm.seca.curriculum.types import TrainingTask, Weakness


def choose_task(weakness: Weakness, rating: float) -> TrainingTask:
    """
    Map weakness → concrete training action.
    """

    # difficulty scaling
    difficulty = min(1.0, max(0.1, weakness.severity + (rating - 1200) / 2000))

    # format selection
    if weakness.name == "tactics":
        fmt = "puzzle"
        gain = 0.8
    elif weakness.name == "endgame":
        fmt = "drill"
        gain = 0.6
    elif weakness.name == "strategy":
        fmt = "explanation"
        gain = 0.5
    else:
        fmt = "game"
        gain = 0.4

    return TrainingTask(
        topic=weakness.name,
        difficulty=difficulty,
        format=fmt,
        expected_gain=gain * weakness.severity,
    )
