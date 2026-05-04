import json
import numpy as np
from sqlalchemy import create_engine, text

from .global_bandit import GlobalLinUCB

DB = "sqlite:///data/seca.db"


def train():
    engine = create_engine(DB)

    bandit = GlobalLinUCB(n_features=6, alpha=1.2)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bandit_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id TEXT,
                context_json TEXT,
                action TEXT,
                reward REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()
        rows = conn.execute(text("""
            SELECT context_json, action, reward
            FROM bandit_experiences
            ORDER BY rowid ASC
        """)).fetchall()

    for ctx_json, action, reward in rows:
        context = np.array(json.loads(ctx_json), dtype=float)
        bandit.update(action, context, reward)

    print(f"Trained on {len(rows)} experiences")
    return bandit


if __name__ == "__main__":
    train()
