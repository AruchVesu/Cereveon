# scripts/build_skill_dataset.py

from seca.training.dataset.pgn_loader import load_games
from seca.training.dataset.dataset_builder import build_skill_transitions
from seca.training.dataset.export import save_dataset

PGN_PATH = "data/games.pgn"
OUT_PATH = "data/skill_dataset.npz"


def main():
    games = list(load_games(PGN_PATH))
    transitions = build_skill_transitions(games)
    save_dataset(transitions, OUT_PATH)


if __name__ == "__main__":
    main()
