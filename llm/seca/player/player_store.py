# seca/player/player_store.py
import json
from pathlib import Path
from .player_model import PlayerModel

DATA_DIR = Path("data/players")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _path(player_id: str) -> Path:
    return DATA_DIR / f"{player_id}.json"


def load_player(player_id: str) -> PlayerModel:
    path = _path(player_id)

    if not path.exists():
        return PlayerModel(player_id=player_id)

    data = json.loads(path.read_text(encoding="utf-8"))
    return PlayerModel(**data)


def save_player(player: PlayerModel) -> None:
    path = _path(player.player_id)
    path.write_text(json.dumps(player.__dict__, indent=2), encoding="utf-8")
