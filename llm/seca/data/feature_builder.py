import chess


def build_game_features(game, analyzer):
    board = game.board()
    records = []

    for move in game.mainline_moves():
        before = analyzer.evaluate(board)
        board.push(move)
        after = analyzer.evaluate(board)

    records.append(
        {
            "fen": board.fen(),
            "delta_cp": after - before,
            "is_blunder": abs(after - before) > 150,
        }
    )

    return records
