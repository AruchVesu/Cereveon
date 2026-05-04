import logging

import chess

logger = logging.getLogger(__name__)

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


def _safe_log(value: object, max_len: int = 80) -> str:
    """Sanitize an untrusted value for safe inclusion in a log line.

    Mitigates CWE-117 log injection: values reaching this module come
    from a Stockfish JSON payload (or an untrusted FEN supplied via
    HTTP) — an attacker who controls those bytes could otherwise embed
    CR/LF and forge a fake log entry.  ``repr()`` already escapes
    standard ASCII control characters, but Unicode line separators
    (NEL U+0085, U+2028, U+2029) can slip past it depending on the
    encoder; we strip every line-terminating code point, then truncate
    so a giant payload cannot bloat the log file.

    The explicit replace chain is also what makes the sanitiser visible
    to CodeQL's ``py/log-injection`` taint tracker — bare ``repr`` is
    not recognised as a sanitiser even though it is one.
    """
    s = repr(value)
    for ch in _LOG_INJECTION_CHARS:
        s = s.replace(ch, "")
    return s[:max_len]


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
            "tactical_flags": stockfish_json.get("tactical_flags", []),
            "position_flags": stockfish_json.get("position_flags", []),
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
        "tactical_flags": stockfish_json.get("tactical_flags", []),
        "position_flags": stockfish_json.get("position_flags", []),
        "phase": stockfish_json.get("phase", "middlegame"),
    }
