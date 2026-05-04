# seca/player/player_model.py
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class PlayerModel:
    player_id: str

    # --- rating ---
    rating: int = 800

    # --- skill vector (0–1 scale) ---
    skills: Dict[str, float] = field(
        default_factory=lambda: {
            "tactics": 0.3,
            "strategy": 0.3,
            "endgame": 0.2,
            "calculation": 0.3,
        }
    )

    # --- psychology ---
    tilt_sensitivity: float = 0.3  # how fast tilt appears
    current_tilt: float = 0.0  # runtime emotional state

    # --- learning behaviour ---
    preferred_depth: int = 2  # explanation depth (1–4)
    learning_speed: float = 0.5  # adaptation rate

    # --- confidence estimation ---
    confidence: float = 0.5

    # --- statistics ---
    games_played: int = 0
    mistakes_recent: int = 0

    # --- last explanation context ---
    last_move_quality_before: str | None = None
