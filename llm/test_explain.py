if __name__ == "__main__":
    from llm.rag.deploy.embedded import explain_position

    payload = {
        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/5N2/PPP2PPP/RNBQKB1R b KQkq - 2 3",
        "engine_json": {
            "evaluation": {"type": "cp", "value": -180},
            "eval_delta": -150,
            "errors": {"last_move_quality": "mistake"},
            "tactical_flags": ["hanging_piece"],
        },
    }

    result = explain_position(payload)
    print(result["explanation"])
