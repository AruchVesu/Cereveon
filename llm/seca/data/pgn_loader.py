import chess.pgn


def load_games(path):
    games = []
    with open(path, "r", encoding="utf-8") as f:
        while True:
        game = chess.pgn.read_game(f)
        if game is None:
            break
        games.append(game)
    return games
