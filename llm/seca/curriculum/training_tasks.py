from .curriculum_types import TrainingTask

TASK_LIBRARY = {
    "tactics": [
        TrainingTask("tactics", "Fork puzzles", "Solve 10 fork tactics", 3),
        TrainingTask("tactics", "Pin recognition", "Identify pins in positions", 4),
    ],
    "calculation": [
        TrainingTask("calculation", "3-move calculation", "Calculate forcing lines", 5),
    ],
    "endgame": [
        TrainingTask("endgame", "King & pawn basics", "Opposition and promotion", 2),
    ],
    "strategy": [
        TrainingTask("strategy", "Weak squares", "Identify weak color complexes", 4),
    ],
    "blunder_check": [
        TrainingTask("blunder_check", "Safety scan", "Check captures, checks, threats", 1),
    ],
    "tilt_control": [
        TrainingTask("tilt_recovery", "Breathing reset", "1-minute reset routine", 1),
    ],
}
