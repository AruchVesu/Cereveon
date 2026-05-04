import joblib
import pandas as pd
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.multioutput import MultiOutputRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error

DATASET_PATH = "llm/seca/brain/data/world_model_dataset.csv"
MODEL_PATH = "llm/seca/brain/world_model/world_model.pkl"


def load_dataset():
    if not Path(DATASET_PATH).exists():
        raise FileNotFoundError("Dataset not found. Run build_world_model_dataset.py first.")
    return pd.read_csv(DATASET_PATH)


def train_model(df: pd.DataFrame):
    X = df.drop(columns=["delta_rating", "delta_confidence"])
    y = df[["delta_rating", "delta_confidence"]]

    if len(df) < 2:
        X_train, y_train = X, y
        X_test, y_test = None, None
    else:
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    base_model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=3,
        learning_rate=0.05,
    )

    model = MultiOutputRegressor(base_model)
    model.fit(X_train, y_train)

    if X_test is not None and len(X_test) > 0:
        preds = model.predict(X_test)

        mae_rating = mean_absolute_error(y_test["delta_rating"], preds[:, 0])
        mae_conf = mean_absolute_error(y_test["delta_confidence"], preds[:, 1])

        print("Validation MAE:")
        print(f"   rating delta MAE: {mae_rating:.3f}")
        print(f"   confidence delta MAE: {mae_conf:.3f}")
    else:
        print("Validation skipped (dataset too small for a holdout set).")

    return model


def save_model(model):
    Path(MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    print(f"Model saved -> {MODEL_PATH}")


def main():
    df = load_dataset()
    model = train_model(df)
    save_model(model)


if __name__ == "__main__":
    main()
