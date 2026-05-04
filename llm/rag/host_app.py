from llm.rag.deploy.embedded import explain_position

# Example input coming from your app
payload = {
    "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/5N2/PPP2PPP/RNBQKB1R b KQkq - 2 3",
    "engine_json": {
        "evaluation": {"type": "cp", "value": -180},
        "eval_delta": -150,
        "errors": {"last_move_quality": "mistake"},
        "tactical_flags": ["hanging_piece"],
    },
    "case_type": "tactical_mistake",
}

# Call your embedded module
explanation = explain_position(payload)

print(explanation)
