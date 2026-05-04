from __future__ import annotations
import sqlite3
import json
from pathlib import Path
from typing import Any, Dict, List
from types import SimpleNamespace


class EventStorage:
    """
    Persistent SECA event store (SQLite).

    Stores:
    - explanations
    - outcomes
    - skill updates
    """

    def __init__(self, db_path: str = "data/seca.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    # ------------------------------------------------------------------

    def _create_tables(self):
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            payload TEXT
        )
        """)

        self.conn.commit()

    # ------------------------------------------------------------------

    def log_event(self, event_type: str, payload: Dict[str, Any]):
        cur = self.conn.cursor()

        cur.execute(
            "INSERT INTO events (type, payload) VALUES (?, ?)",
            (event_type, json.dumps(payload)),
        )

        self.conn.commit()

    # ------------------------------------------------------------------

    def fetch_events(self, event_type: str | None = None) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        if event_type:
            cur.execute("SELECT payload FROM events WHERE type = ?", (event_type,))
        else:
            cur.execute("SELECT payload FROM events")

        rows = cur.fetchall()

        return [json.loads(r[0]) for r in rows]

    # ------------------------------------------------------------------

    def get_recent_games(self, limit: int = 50):
        """
        Compatibility helper for OnlineSECALearner.
        Expects events stored with type "game_finished".
        """
        cur = self.conn.cursor()
        cur.execute(
            "SELECT payload FROM events WHERE type = ? ORDER BY id DESC LIMIT ?",
            ("game_finished", limit),
        )
        rows = cur.fetchall()

        events = []
        for (payload,) in rows:
            data = json.loads(payload)
            weaknesses = data.get("weaknesses", {}) or {}
            events.append(
                SimpleNamespace(
                    accuracy=float(data.get("accuracy", 0.0)),
                    weaknesses_json=json.dumps(weaknesses),
                    result=data.get("result", "draw"),
                )
            )

        return events
