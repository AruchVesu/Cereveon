import json
from sqlalchemy import text


class ExperienceStore:
    def __init__(self, db):
        self.db = db

    def log(self, player_id, context, action, reward):
        self.db.execute(text("""
                CREATE TABLE IF NOT EXISTS bandit_experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT,
                    context_json TEXT,
                    action TEXT,
                    reward REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
        self.db.execute(
            text("""
                INSERT INTO bandit_experiences
                (player_id, context_json, action, reward)
                VALUES (:p, :c, :a, :r)
            """),
            {
                "p": str(player_id),
                "c": json.dumps(context.tolist()),
                "a": action,
                "r": float(reward),
            },
        )
        self.db.commit()
