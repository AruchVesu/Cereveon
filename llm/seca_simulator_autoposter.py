"""
SECA Simulator -> /game/finish Auto-Poster
-----------------------------------------
Closes the OMEGA-loop:
Planner -> Simulator -> API(/game/finish) -> DB -> World Model -> Planner

Run:
    python seca_simulator_autoposter.py
"""

import time
import random
import requests
from dataclasses import dataclass

# =========================
# Config
# =========================
API_URL = "http://127.0.0.1:5000"
EMAIL = "fresh@seca.ai"
PASSWORD = "test123"
GAMES_PER_BATCH = 10
SLEEP_BETWEEN_GAMES = 0.5


# =========================
# Auth
# =========================


def login() -> str:
    r = requests.post(
        f"{API_URL}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    r.raise_for_status()
    return r.json()["access_token"]


# =========================
# Synthetic game generator
# =========================

RESULTS = ["win", "loss", "draw"]
THEMES = ["opening", "tactics", "endgame", "time_management"]


def generate_pgn() -> str:
    base = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
    length = random.randint(6, 20)
    moves = base + random.choices(base, k=length - len(base))
    numbered = []

    for i in range(0, len(moves), 2):
        move_no = i // 2 + 1
        pair = moves[i : i + 2]
        numbered.append(f"{move_no}. {' '.join(pair)}")

    return " ".join(numbered)


def generate_accuracy(player_rating: float, opponent_rating: float) -> float:
    diff = player_rating - opponent_rating
    base = 0.6 + diff / 2000
    noise = random.uniform(-0.05, 0.05)
    return max(0.3, min(0.98, base + noise))


def generate_weaknesses() -> dict:
    theme = random.choice(THEMES)
    return {theme: round(random.uniform(0.3, 1.0), 2)}


# =========================
# Data model
# =========================


@dataclass
class SimulatedGame:
    pgn: str
    result: str
    accuracy: float
    weaknesses: dict


# =========================
# Simulator
# =========================


def simulate_game(player_rating: float) -> SimulatedGame:
    opponent_rating = player_rating + random.randint(-150, 150)

    result = random.choices(
        RESULTS,
        weights=[
            0.45 + (player_rating - opponent_rating) / 800,
            0.45 + (opponent_rating - player_rating) / 800,
            0.1,
        ],
    )[0]

    accuracy = generate_accuracy(player_rating, opponent_rating)
    weaknesses = generate_weaknesses()

    return SimulatedGame(
        pgn=generate_pgn(),
        result=result,
        accuracy=accuracy,
        weaknesses=weaknesses,
    )


# =========================
# Poster
# =========================


def post_game(token: str, game: SimulatedGame):
    r = requests.post(
        f"{API_URL}/game/finish",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pgn": game.pgn,
            "result": game.result,
            "accuracy": game.accuracy,
            "weaknesses": game.weaknesses,
        },
    )

    if r.status_code != 200:
        print("POST ERROR:", r.text)
        return None

    return r.json()


# =========================
# Ω-loop runner
# =========================


def run_batch():
    print("\n=== SECA OMEGA-LOOP START ===")

    token = login()
    print("Logged in.")

    player_rating = 1200.0

    for i in range(GAMES_PER_BATCH):
        game = simulate_game(player_rating)
        response = post_game(token, game)

        if response and "new_rating" in response:
            player_rating = response["new_rating"]

        print(
            f"Game {i+1}:",
            game.result,
            f"acc={game.accuracy:.2f}",
            "→ rating",
            round(player_rating, 1),
        )

        time.sleep(SLEEP_BETWEEN_GAMES)

    print("=== OMEGA-LOOP END ===\n")


# =========================
# Main
# =========================


if __name__ == "__main__":
    while True:
        run_batch()
        time.sleep(3)  # pause between batches
