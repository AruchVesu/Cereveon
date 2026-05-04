import random
from statistics import mean

from llm.rag.quality.explanation_score import score_explanation
from llm.rag.llm.config import MIN_QUALITY_SCORE

RNG_SEED = 12345
SAMPLES = 100

CAUSAL_MARKERS = ["because", "due to", "explains", "reflects", "results from"]
FORBIDDEN_SOFT = ["should", "best move", "consider"]
FLAGS = ["hanging piece", "weak square", "outpost"]
BANDS = ["decisive", "advantage", "equal"]


def make_high_quality_text(band: str, flags: list):
    # Construct a multi-line, causal, flag-covering text without advisory words
    lines = []
    lines.append(f"The evaluation indicates a {band} for White.")
    lines.append(f"This is because White has superior development and {', '.join(flags[:1])}.")
    lines.append("The position shows improved piece activity and concrete targets.")
    return "\n".join(lines)


def make_low_quality_text():
    # Single-line, vague, may include advisory wording or forbidden soft triggers
    templates = [
        "White is better.",
        "You should play aggressively.",
        "White has chances.",
        "Consider pawn breaks to improve play.",
    ]
    return random.choice(templates)


def test_fuzz_scores_monotonic_between_high_and_low_samples():
    random.seed(RNG_SEED)

    high_samples = []  # tuples of (score, text, engine_signal)
    low_samples = []

    for _ in range(SAMPLES):
        band = random.choice(BANDS)
        # pick a random subset of flags
        flags = [f for i, f in enumerate(FLAGS) if random.random() < 0.6]
        engine_signal = {
            "evaluation": {"band": band},
            "tactical_flags": flags,
            "last_move_quality": "mistake",
        }
        text = make_high_quality_text(band, flags)
        sc = score_explanation(text=text, engine_signal=engine_signal)
        assert 0 <= sc <= 10, f"Score out of bounds: {sc}"
        high_samples.append((sc, text, engine_signal))

    for _ in range(SAMPLES):
        engine_signal = {"evaluation": {"band": random.choice(BANDS)}}
        text = make_low_quality_text()
        sc = score_explanation(text=text, engine_signal=engine_signal)
        assert 0 <= sc <= 10, f"Score out of bounds: {sc}"
        low_samples.append((sc, text, engine_signal))

    high_scores = [s for s, *_ in high_samples]
    low_scores = [s for s, *_ in low_samples]

    # On average, high-quality samples should score above MIN_QUALITY_SCORE
    high_mean = mean(high_scores)
    low_mean = mean(low_scores)

    if high_mean < MIN_QUALITY_SCORE:
        # find the worst high examples to help debugging
        worst = sorted(high_samples, key=lambda t: t[0])[:5]
        details = "\n".join([f"score={s}: text={t!r} engine={e!r}" for s, t, e in worst])
        import pytest

        pytest.fail(
            f"High-quality mean {high_mean} < MIN_QUALITY_SCORE ({MIN_QUALITY_SCORE}). Worst high examples:\n{details}"
        )

    # Low-quality mean should be strictly less than high-quality mean by at least 1
    if (high_mean - low_mean) < 1.0:
        worst_high = sorted(high_samples, key=lambda t: t[0])[:3]
        best_low = sorted(low_samples, key=lambda t: -t[0])[:3]
        details_high = "\n".join([f"score={s}: text={t!r}" for s, t, _ in worst_high])
        details_low = "\n".join([f"score={s}: text={t!r}" for s, t, _ in best_low])
        import pytest

        pytest.fail(
            "Expected separation between high and low samples. "
            f"high_mean={high_mean}, low_mean={low_mean}.\nWorst high samples:\n{details_high}\nBest low samples:\n{details_low}"
        )


def test_fuzz_no_exceptions_for_random_texts():
    # Ensure that random generated texts never raise and scores are bounded
    random.seed(RNG_SEED + 1)
    for _ in range(SAMPLES * 2):
        # randomly compose a text from fragments
        fragments = [
            "White",
            "Black",
            "has advantage",
            "because",
            "should",
            "consider",
            "hanging piece",
            "decisive",
        ]
        text = " ".join(random.choices(fragments, k=random.randint(1, 10)))
        engine_signal = {
            "evaluation": {"band": random.choice(BANDS)},
            "tactical_flags": random.sample(FLAGS, k=random.randint(0, len(FLAGS))),
        }
        try:
            sc = score_explanation(text=text, engine_signal=engine_signal)
        except Exception as e:
            import pytest

            pytest.fail(f"scoring raised exception for text={text!r} engine={engine_signal!r}: {e}")

        if not (0 <= sc <= 10):
            import pytest

            pytest.fail(
                f"Score out of bounds for random text: {sc} (text={text!r} engine={engine_signal!r})"
            )
