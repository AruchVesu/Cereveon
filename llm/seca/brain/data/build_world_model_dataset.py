import json
import pandas as pd
from sqlalchemy import create_engine, text

from llm.seca.learning.player_embedding import (
    PlayerEmbeddingEncoder,
    zeros_embedding,
)

DATABASE_URL = "sqlite:///data/seca.db"
OUTPUT_PATH = "llm/seca/brain/data/world_model_dataset.csv"

DEFAULT_RATING = 1200.0
DEFAULT_CONFIDENCE = 0.5


def ensure_schema(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    tables = {r[0] for r in rows}
    if "game_events" not in tables:
        raise RuntimeError(
            "Missing table 'game_events' in data/seca.db. "
            "Run the API once to create tables and record games."
        )


def load_events(engine):
    query = """
    SELECT
        ge.player_id,
        ge.accuracy,
        ge.weaknesses_json,
        ge.result,
        ge.created_at
    FROM game_events ge
    ORDER BY ge.created_at ASC
    """
    return pd.read_sql(text(query), engine)


def expand_weaknesses(df: pd.DataFrame) -> pd.DataFrame:
    weaknesses = df["weaknesses_json"].fillna("{}").apply(json.loads)

    all_keys = set()
    for w in weaknesses:
        all_keys.update(w.keys())

    for key in sorted(all_keys):
        df[f"weak_{key}"] = weaknesses.apply(lambda x: x.get(key, 0.0))

    return df.drop(columns=["weaknesses_json"])


def apply_skill_update(rating: float, confidence: float, result: str, accuracy: float):
    if result == "win":
        delta = 12
    elif result == "loss":
        delta = -12
    else:
        delta = 2

    delta += (accuracy - 0.5) * 10

    rating_after = max(100.0, rating + delta)
    confidence_after = min(1.0, max(0.0, confidence + (accuracy - 0.5) * 0.1))
    return rating_after, confidence_after


def add_rating_confidence(df: pd.DataFrame) -> pd.DataFrame:
    ratings = {}
    confidences = {}

    rating_before = []
    rating_after = []
    confidence_before = []
    confidence_after = []

    for row in df.itertuples(index=False):
        player_id = row.player_id
        r_before = ratings.get(player_id, DEFAULT_RATING)
        c_before = confidences.get(player_id, DEFAULT_CONFIDENCE)

        r_after, c_after = apply_skill_update(r_before, c_before, row.result, row.accuracy)

        rating_before.append(r_before)
        rating_after.append(r_after)
        confidence_before.append(c_before)
        confidence_after.append(c_after)

        ratings[player_id] = r_after
        confidences[player_id] = c_after

    df["rating_before"] = rating_before
    df["rating_after"] = rating_after
    df["confidence_before"] = confidence_before
    df["confidence_after"] = confidence_after

    return df


def add_player_embeddings(df: pd.DataFrame) -> pd.DataFrame:
    encoder = PlayerEmbeddingEncoder()
    embeddings = {}
    cols = [[] for _ in range(16)]

    for row in df.itertuples(index=False):
        player_id = row.player_id
        z_prev = embeddings.get(player_id, zeros_embedding())
        weaknesses = json.loads(row.weaknesses_json or "{}")

        z_new = encoder.encode(
            rating=row.rating_before,
            confidence=row.confidence_before,
            accuracy=row.accuracy,
            weaknesses=weaknesses,
            z_prev=z_prev,
        )

        for i in range(16):
            cols[i].append(float(z_new[i]))

        embeddings[player_id] = z_new

    for i in range(16):
        df[f"z_{i}"] = cols[i]

    return df


def compute_targets(df: pd.DataFrame) -> pd.DataFrame:
    df["delta_rating"] = df["rating_after"] - df["rating_before"]
    df["delta_confidence"] = df["confidence_after"] - df["confidence_before"]
    return df


def build_dataset():
    engine = create_engine(DATABASE_URL)

    ensure_schema(engine)
    df = load_events(engine)
    if df.empty:
        print("No game events found. Add games before building a dataset.")
        return

    df = add_rating_confidence(df)
    df = add_player_embeddings(df)
    df = expand_weaknesses(df)
    df = compute_targets(df)

    feature_cols = (
        [
            "rating_before",
            "confidence_before",
            "accuracy",
        ]
        + [c for c in df.columns if c.startswith("weak_")]
        + [f"z_{i}" for i in range(16)]
    )

    target_cols = ["delta_rating", "delta_confidence"]

    dataset = df[feature_cols + target_cols]

    dataset.to_csv(OUTPUT_PATH, index=False)
    print(f"Dataset saved -> {OUTPUT_PATH}")
    print(f"Rows: {len(dataset)}")


if __name__ == "__main__":
    build_dataset()
