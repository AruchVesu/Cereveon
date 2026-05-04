"""
Dynamic adaptation mode — session-scoped, deterministic, no RL.

When enabled for a player the engine's target ELO shifts each move
based on the quality label reported by extract_engine_signal().  This
is used for skill-assessment sessions (e.g. first play) so the engine
converges on the player's actual level without requiring prior history.

Rules
-----
- No neural updates, no bandit training, no gradient descent.
- The quality delta table is fixed and immutable at runtime.
- ELO is always clamped to [ELO_MIN, ELO_MAX].
- State is ephemeral (in-memory, cleared on server restart).
- Each player's state is independent (no cross-player influence).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

ELO_MIN: int = 600
ELO_MAX: int = 2400
ELO_ASSESSMENT_START: int = 1200  # neutral starting point when no base supplied

# Fixed, immutable quality → ELO delta table.
# Values are intentionally asymmetric: blunders punish more than excellent
# moves reward so the estimate converges toward true skill rather than
# inflating toward the ceiling.
_QUALITY_DELTA: dict[str, int] = {
    "best":       +40,
    "excellent":  +25,
    "good":       +10,
    "inaccuracy": -10,
    "mistake":    -25,
    "blunder":    -40,
}


@dataclass
class DynamicAdaptationState:
    """Snapshot of one player's dynamic adaptation session."""

    enabled: bool = False
    current_elo: int = ELO_ASSESSMENT_START
    move_count: int = 0


class DynamicModeRegistry:
    """Thread-safe in-memory registry of per-player dynamic adaptation states.

    Typical lifecycle
    -----------------
    1. Android calls POST /adaptation/mode {"enabled": true} →
       set_mode(player_id, enabled=True, base_elo=<player's current ELO>)
    2. For every move the human plays, /live/move calls
       record_move_quality(player_id, quality_label).
    3. /move reads get_elo(player_id) and overrides target_elo when not None.
    4. Android calls POST /adaptation/mode {"enabled": false} to end the session.
    """

    def __init__(self) -> None:
        self._states: dict[str, DynamicAdaptationState] = {}
        self._lock = threading.Lock()

    def set_mode(
        self,
        player_id: str,
        *,
        enabled: bool,
        base_elo: int | None = None,
    ) -> DynamicAdaptationState:
        """Enable or disable dynamic mode for *player_id*.

        When enabling, *base_elo* sets the starting ELO for this session.
        If *base_elo* is None the current stored ELO is kept (or the
        assessment start value for a new player).
        When disabling, the move counter resets so a subsequent enable
        starts a fresh assessment.
        """
        with self._lock:
            existing = self._states.get(player_id, DynamicAdaptationState())
            if enabled:
                elo = base_elo if base_elo is not None else existing.current_elo
                elo = max(ELO_MIN, min(ELO_MAX, elo))
                new_state = DynamicAdaptationState(
                    enabled=True,
                    current_elo=elo,
                    move_count=existing.move_count,
                )
            else:
                new_state = DynamicAdaptationState(
                    enabled=False,
                    current_elo=existing.current_elo,
                    move_count=0,
                )
            self._states[player_id] = new_state
            return new_state

    def get_state(self, player_id: str) -> DynamicAdaptationState:
        """Return current state, or a default disabled state for unknown players."""
        with self._lock:
            return self._states.get(player_id, DynamicAdaptationState())

    def get_elo(self, player_id: str) -> int | None:
        """Return current dynamic ELO if mode is enabled, else None."""
        state = self.get_state(player_id)
        return state.current_elo if state.enabled else None

    def record_move_quality(self, player_id: str, quality: str) -> DynamicAdaptationState:
        """Apply deterministic ELO delta for *quality* label.

        No-op when dynamic mode is disabled.
        Unknown quality labels produce a delta of 0 (no change).
        """
        with self._lock:
            state = self._states.get(player_id, DynamicAdaptationState())
            if not state.enabled:
                return state
            delta = _QUALITY_DELTA.get(quality, 0)
            new_elo = max(ELO_MIN, min(ELO_MAX, state.current_elo + delta))
            new_state = DynamicAdaptationState(
                enabled=True,
                current_elo=new_elo,
                move_count=state.move_count + 1,
            )
            self._states[player_id] = new_state
            return new_state
