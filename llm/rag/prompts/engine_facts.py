"""Plain-English, player-perspective rendering of the deterministic engine
signal, for grounding the chat coach.

The chat coach used to hallucinate tactics because its engine signal carried no
board facts (the chat path passes an empty ``stockfish_json``).  These helpers
turn the closed-vocabulary flags from ``board_features`` / ``flag_vocabulary``
into sentences the coach can ground on, and describe what the player's last move
attacks — all from the player's (White's) perspective and all coordinate-free
(no ``<file><rank>`` token), so they never trip the no-notation output rule.
"""

from __future__ import annotations

import chess

from llm.rag.prompts.move_phrase import _PAWN_DESC, _PIECE_NAME

# Closed-vocab flag -> player-perspective sentence (player is always White, so
# white == "you", black == "your opponent").  Mirrors flag_vocabulary exactly.
_FLAG_FACT: dict[str, str] = {
    "hanging_piece:white": "You have an undefended piece under attack.",
    "hanging_piece:black": "Your opponent has an undefended piece under attack.",
    "hanging_piece": "There is an undefended piece under attack.",
    "check:white_to_move": "Your king is in check.",
    "check:black_to_move": "Your opponent's king is in check.",
    "king_safety:white_exposed": "Your king is exposed.",
    "king_safety:white_loose": "Your king is a little loose.",
    "king_safety:white_safe": "Your king is safe.",
    "king_safety:black_exposed": "Your opponent's king is exposed.",
    "king_safety:black_loose": "Your opponent's king is a little loose.",
    "king_safety:black_safe": "Your opponent's king is safe.",
    "pawn_structure:white_doubled": "You have doubled pawns.",
    "pawn_structure:white_isolated": "You have an isolated pawn.",
    "pawn_structure:white_passed": "You have a passed pawn.",
    "pawn_structure:black_doubled": "Your opponent has doubled pawns.",
    "pawn_structure:black_isolated": "Your opponent has an isolated pawn.",
    "pawn_structure:black_passed": "Your opponent has a passed pawn.",
    "castling:white_uncastled": "You haven't castled yet.",
    "castling:white_kingside_done": "You have castled kingside.",
    "castling:white_queenside_done": "You have castled queenside.",
    "castling:black_uncastled": "Your opponent hasn't castled yet.",
    "castling:black_kingside_done": "Your opponent has castled kingside.",
    "castling:black_queenside_done": "Your opponent has castled queenside.",
    "material:even": "Material is even.",
    "material:white_up_pawn": "You are up a pawn.",
    "material:white_up_piece": "You are up a piece.",
    "material:white_up_major": "You are up a rook or queen's worth of material.",
    "material:black_up_pawn": "Your opponent is up a pawn.",
    "material:black_up_piece": "Your opponent is up a piece.",
    "material:black_up_major": "Your opponent is up a rook or queen's worth of material.",
    "space_advantage:white": "You have a space advantage.",
    "space_advantage:black": "Your opponent has more space.",
    "space_advantage": "One side has a space advantage.",
}


def render_engine_facts(engine_signal: dict) -> list[str]:
    """Plain-English, player-perspective facts from the engine signal's flags.

    Order-stable and de-duplicated.  Unknown labels are skipped (the closed
    vocabulary should never produce one).
    """
    facts: list[str] = []
    seen: set[str] = set()
    flags = list(engine_signal.get("tactical_flags") or []) + list(
        engine_signal.get("position_flags") or []
    )
    for flag in flags:
        sentence = _FLAG_FACT.get(flag)
        if sentence and sentence not in seen:
            seen.add(sentence)
            facts.append(sentence)
    return facts


def _target_name(piece: chess.Piece, square: chess.Square) -> str:
    if piece.piece_type == chess.PAWN:
        return f"the {_PAWN_DESC[chess.square_name(square)[0]]}"
    if piece.piece_type == chess.QUEEN:
        return "the queen"
    return f"a {_PIECE_NAME[piece.piece_type]}"


def describe_threats(fen_after: str, uci: str) -> str:
    """One-ply description of what the player's just-moved piece now attacks.

    Coordinate-free.  Names attacked enemy pieces and whether the moved piece
    bears on the squares around the opponent's king.  Returns "" for a quiet
    move (no enemy piece attacked, not near the king) or unparseable input.
    Only immediate attacks — deep tactics (forks, mate-in-N) are out of scope.
    """
    if not fen_after or not uci or len(uci) < 4:
        return ""
    try:
        board = chess.Board() if fen_after.strip() == "startpos" else chess.Board(fen_after)
        move = chess.Move.from_uci(uci)
    except (ValueError, IndexError):
        return ""

    piece = board.piece_at(move.to_square)
    if piece is None:
        return ""
    enemy = not piece.color
    attacked = board.attacks(move.to_square)

    targets: list[str] = []
    seen: set[str] = set()
    for sq in attacked:
        target = board.piece_at(sq)
        if target is not None and target.color == enemy and target.piece_type != chess.KING:
            name = _target_name(target, sq)
            if name not in seen:
                seen.add(name)
                targets.append(name)

    king_sq = board.king(enemy)
    near_king = king_sq is not None and any(
        chess.square_distance(sq, king_sq) <= 1 for sq in attacked
    )

    if not targets and not near_king:
        return ""

    mover = "pawn" if piece.piece_type == chess.PAWN else _PIECE_NAME[piece.piece_type]
    parts: list[str] = []
    if targets:
        parts.append("attacks " + ", ".join(targets))
    if near_king:
        parts.append("bears on the squares around the opponent's king")
    return f"your {mover} now " + " and ".join(parts) + "."
