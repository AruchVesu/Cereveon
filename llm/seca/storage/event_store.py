from typing import List, Dict, Any


class EventStore:
    """
    Persistent event storage interface for SECA.

    Current version:
    - in-memory fallback
    - later replace with DB / Redis / Kafka
    """

    def __init__(self):
        self._events: List[Dict[str, Any]] = []

    # ---------------------------------------------------------

    def append(self, event: Dict[str, Any]) -> None:
        """Store new event."""
        self._events.append(event)

    # ---------------------------------------------------------

    def list_events(self) -> List[Dict[str, Any]]:
        """Return all stored events."""
        return list(self._events)

    # ---------------------------------------------------------

    def clear(self) -> None:
        """Remove all events (testing only)."""
        self._events.clear()
