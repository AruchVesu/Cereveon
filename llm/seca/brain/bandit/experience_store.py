import json
from sqlalchemy import text


class ExperienceStore:
    def __init__(self, db):
        self.db = db

    def log(self, player_id, context, action, reward):
        # Schema is owned by ``llm.seca.brain.models.BanditExperience`` and
        # created via ``Base.metadata.create_all`` at startup
        # (``llm/seca/db.py`` and ``llm/seca/auth/router.py:init_schema``).
        # Earlier revisions issued a raw ``CREATE TABLE IF NOT EXISTS ...
        # AUTOINCREMENT ...`` here, which Postgres rejects at parse time
        # (SQLite-only DDL) and which aborted the outer transaction in
        # ``finish_game`` — see the 2026-05-15 prod incident.
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
