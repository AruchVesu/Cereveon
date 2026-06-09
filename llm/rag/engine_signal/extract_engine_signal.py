import logging
import re

import chess

logger = logging.getLogger(__name__)

# Pattern an ESV flag string must match to reach the prompt.  Bounded
# to lowercase ASCII letters / digits / underscore / colon / hyphen,
# 1-64 chars.  This is a *shape* filter, not a closed-vocabulary check:
# the canonical vocabulary for flags emitted by
# ``llm.seca.engines.stockfish.board_features`` lives in
# ``llm.rag.engine_signal.flag_vocabulary`` and is enforced by the
# board-feature unit tests, not at the trust boundary.  The pattern
# here defends against upstream bugs (a stray empty string, a free-form
# sentence, an int, control characters) reaching the LLM prompt.
_FLAG_SHAPE = re.compile(r"^[a-z0-9_:\-]{1,64}$")

_PIECE_CP = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
}

_VALID_EVAL_TYPES = frozenset(("cp", "mate"))

# All move-quality labels Stockfish or internal code may produce.
# Any value outside this set is normalized to "unknown" with a warning.
_KNOWN_MOVE_QUALITIES = frozenset(
    ("unknown", "ok", "best", "excellent", "good", "inaccuracy", "mistake", "blunder")
)

# Code points that terminate or visually break a log line.  Stripped by
# _safe_log() so an attacker controlling Stockfish JSON or an HTTP-supplied
# FEN cannot inject forged log lines (CWE-117).  Includes the obscure
# Unicode line separators that some loggers respect even though repr()
# normally escapes ASCII control characters.
_LOG_INJECTION_CHARS = ("\r", "\n", "\x85", " ", " ")


# Compiled character class equivalent to ``[<every char in _LOG_INJECTION_CHARS>]``.
# Built from the tuple so the source of truth stays one place — adding a
# new code point to the tuple automatically widens the regex.
_LOG_INJECTION_RE = re.compile("[" + "".join(_LOG_INJECTION_CHARS) + "]")


def _safe_log(value: object, max_len: int = 80) -> str:
    """Sanitize an untrusted value for safe inclusion in a log line.

    Mitigates CWE-117 log injection: values reaching this module come
    from a Stockfish JSON payload (or an untrusted FEN supplied via
    HTTP) — an attacker who controls those bytes could otherwise embed
    CR/LF and forge a fake log entry.  ``repr()`` already escapes
    standard ASCII control characters, but Unicode line separators
    (NEL U+0085, U+2028, U+2029) can slip past it depending on the
    encoder; we strip every line-terminating code point with a regex
    pass, then truncate so a giant payload cannot bloat the log file.

    Implementation note: ``re.sub`` is deliberate.  CodeQL's
    ``py/log-injection`` taint tracker recognises ``re.sub(pattern,
    "", str)`` as a sanitiser but does NOT recognise the equivalent
    loop-of-``str.replace`` shape (CodeQL alerts #225–#228, fixed
    2026-05-13).
    """
    return _LOG_INJECTION_RE.sub("", repr(value))[:max_len]


def side_from_fen(fen: str | None) -> str | None:
    if not fen:
        return None
    try:
        return "white" if fen.split()[1] == "w" else "black"
    except Exception:
        return None


def _fen_material_cp(board: chess.Board) -> int:
    return sum(
        _PIECE_CP.get(pt, 0) * (
            len(board.pieces(pt, chess.WHITE)) - len(board.pieces(pt, chess.BLACK))
        )
        for pt in _PIECE_CP
    )


def _fen_phase(board: chess.Board) -> str:
    total = len(board.piece_map())
    has_queens = bool(
        board.pieces(chess.QUEEN, chess.WHITE) | board.pieces(chess.QUEEN, chess.BLACK)
    )
    if board.fullmove_number <= 8 and total >= 28:
        return "opening"
    if total <= 14 or (not has_queens and total <= 20):
        return "endgame"
    return "middlegame"


def _filter_flags(raw: object, label: str) -> list[str]:
    """Sanitize a flag list before it reaches the prompt.

    The ESV is the trust boundary in front of the LLM.  Anything
    flowing through here is rendered verbatim into the Mode-2 prompt,
    so a stray sentence or control-character payload would be
    user-visible at best and prompt-injection bait at worst.  The
    filter drops entries that:

    - are not strings,
    - are empty,
    - exceed 64 characters,
    - contain anything outside ``[a-z0-9_:-]``.

    It does NOT enforce a closed vocabulary — the canonical labels
    emitted by ``llm.seca.engines.stockfish.board_features`` are
    documented in ``llm.rag.engine_signal.flag_vocabulary`` and pinned
    by board-feature unit tests; legacy flag strings used by fixtures
    and rule-based retrieval ("forced_mate", "mate_threat",
    "better_development", etc.) keep flowing through unchanged.
    """
    if not isinstance(raw, list):
        if raw is not None and raw != []:
            logger.warning(
                "%s is not a list (%s); ignoring", label, _safe_log(type(raw).__name__)
            )
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            logger.warning(
                "%s entry %s is not a string; dropping",
                label,
                _safe_log(item),
            )
            continue
        if not _FLAG_SHAPE.match(item):
            logger.warning(
                "%s entry %s rejected by shape filter; dropping",
                label,
                _safe_log(item),
            )
            continue
        out.append(item)
    return out


def _enrich_from_fen(stockfish_json: dict, fen: str | None) -> dict:
    """Fill hollow engine signal fields from FEN when Stockfish data is absent."""
    if fen is None:
        return stockfish_json
    has_eval = bool(stockfish_json.get("evaluation"))
    has_phase = bool(stockfish_json.get("phase"))
    if has_eval and has_phase:
        return stockfish_json
    try:
        board = chess.Board(fen)
    except Exception:
        return stockfish_json
    enriched = dict(stockfish_json)
    if not has_eval:
        enriched["evaluation"] = {"type": "cp", "value": _fen_material_cp(board)}
    if not has_phase:
        enriched["phase"] = _fen_phase(board)
    return enriched


def extract_engine_signal(
    stockfish_json: dict | None,
    *,
    fen: str | None = None,
) -> dict:
    _had_eval = bool(stockfish_json and stockfish_json.get("evaluation"))
    stockfish_json = _enrich_from_fen(stockfish_json or {}, fen)
    if not _had_eval:
        logger.debug(
            "No Stockfish evaluation data; applied FEN material fallback (fen=%s)",
            _safe_log(fen),
        )

    evaluation = stockfish_json.get("evaluation", {})
    eval_type = evaluation.get("type", "cp")
    if eval_type not in _VALID_EVAL_TYPES:
        logger.warning(
            "Unknown eval_type %s from Stockfish; treating as 'cp'",
            _safe_log(eval_type),
        )
        eval_type = "cp"

    _raw_value = evaluation.get("value", 0)
    try:
        value = int(_raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Non-numeric eval value %s from Stockfish; using 0",
            _safe_log(_raw_value),
        )
        value = 0

    # Extract errors dict safely — Stockfish may send a non-dict value
    errors = stockfish_json.get("errors", {})
    if not isinstance(errors, dict):
        logger.warning(
            "'errors' field is not a dict (%s); ignoring move quality",
            type(errors).__name__,
        )
        errors = {}
    last_move_quality = errors.get("last_move_quality", "unknown")
    if last_move_quality not in _KNOWN_MOVE_QUALITIES:
        logger.warning(
            "Unknown last_move_quality %s from Stockfish; normalizing to 'unknown'",
            _safe_log(last_move_quality),
        )
        last_move_quality = "unknown"

    # -------------------------
    # MATE (TERMINAL STATE)
    # -------------------------
    if eval_type == "mate":
        # ``value`` is the signed mate distance from White's perspective
        # (pool.py emits ``white_score.mate()``): positive → White delivers
        # the mate, negative → Black does.  Use the SAME White-relative sign
        # convention as the cp branch below.
        #
        # The previous ``side_from_fen(fen)`` was a latent inversion: the
        # live-move pipeline extracts this signal from the FEN *after* the
        # player's move, so the side to move is the OPPONENT.  A forced mate
        # FOR the player (White, value > 0) was attributed to ``side="black"``
        # and the player-perspective framing (render._frame_player_perspective)
        # then told the winning player "you are about to be mated" — the
        # in-app report that surfaced this: a clear win where the coach's last
        # Mode-1 messages said the player was losing.
        if value > 0:
            side = "white"
        elif value < 0:
            side = "black"
        else:
            # value == 0 should not occur for a real mate (the pool emits cp
            # for non-terminal positions); fall back to side-neutral rather
            # than assert a winner on degenerate data.
            side = side_from_fen(fen)
            if side not in ("white", "black"):
                side = "unknown"

        delta = stockfish_json.get("eval_delta", 0)
        if delta >= 50:
            eval_delta = "increase"
        elif delta <= -50:
            eval_delta = "decrease"
        else:
            eval_delta = "stable"

        return {
            "evaluation": {
                "type": "mate",
                "band": "decisive_advantage",
                "side": side,
            },
            "eval_delta": eval_delta,
            "last_move_quality": last_move_quality,
            "tactical_flags": _filter_flags(
                stockfish_json.get("tactical_flags", []), "tactical_flags"
            ),
            "position_flags": _filter_flags(
                stockfish_json.get("position_flags", []), "position_flags"
            ),
            "phase": stockfish_json.get("phase", "middlegame"),
        }

    # -------------------------
    # CP (NON-TERMINAL STATE)
    # -------------------------
    cp = abs(value)
    if cp <= 20:
        band = "equal"
    elif cp <= 60:
        band = "small_advantage"
    elif cp <= 120:
        band = "clear_advantage"
    else:
        band = "decisive_advantage"

    # Schema contract: value is centipawns from White's perspective.
    # Positive  → White is ahead  → white has the advantage.
    # Negative  → Black is ahead  → black has the advantage.
    # Zero      → equal; attribute to black by convention (band="equal" is primary).
    side = "white" if value > 0 else "black"

    delta = stockfish_json.get("eval_delta", 0)
    if delta >= 50:
        eval_delta = "increase"
    elif delta <= -50:
        eval_delta = "decrease"
    else:
        eval_delta = "stable"

    return {
        "evaluation": {
            "type": "cp",
            "band": band,
            "side": side,
        },
        "eval_delta": eval_delta,
        "last_move_quality": last_move_quality,
        "tactical_flags": _filter_flags(
            stockfish_json.get("tactical_flags", []), "tactical_flags"
        ),
        "position_flags": _filter_flags(
            stockfish_json.get("position_flags", []), "position_flags"
        ),
        "phase": stockfish_json.get("phase", "middlegame"),
    }
