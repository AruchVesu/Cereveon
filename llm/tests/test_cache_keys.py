from llm.cache_keys import eval_cache_key


def test_eval_cache_key_matches_equivalent_fen_and_moves():
    fen = "rnbqkb1r/pppp1ppp/5n2/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    moves = ["e2e4", "e7e5", "g1f3", "g8f6"]

    fen_key = eval_cache_key(fen=fen, movetime_ms=20)
    moves_key = eval_cache_key(moves=moves, movetime_ms=20)

    assert fen_key == moves_key


def test_eval_cache_key_separates_different_limits():
    moves = ["e2e4", "e7e5", "g1f3"]

    fast_key = eval_cache_key(moves=moves, nodes=3000)
    deeper_key = eval_cache_key(moves=moves, nodes=5000)

    assert fast_key != deeper_key
