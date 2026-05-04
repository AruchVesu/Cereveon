import os
import time
import pytest

if not os.getenv("LLM_MODEL"):
    pytest.skip(
        "LLM_MODEL not set; skipping real-LLM smoke test",
        allow_module_level=True,
    )

from llm.rag.deploy.embedded import explain_position


@pytest.mark.smoke
def test_real_llm_smoke():
    if not os.getenv("LLM_MODEL"):
        pytest.skip("LLM_MODEL not set")

    payload = {
        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/5N2/PPP2PPP/RNBQKB1R b KQkq - 2 3",
        "engine_json": {
            "evaluation": {"type": "cp", "value": -180},
            "eval_delta": -150,
            "errors": {"last_move_quality": "mistake"},
            "tactical_flags": ["hanging_piece"],
        },
    }

    start = time.time()
    result = explain_position(payload)
    duration = time.time() - start

    assert "explanation" in result
    assert isinstance(result["explanation"], str)
    assert len(result["explanation"]) > 30
    assert duration < 15  # seconds
