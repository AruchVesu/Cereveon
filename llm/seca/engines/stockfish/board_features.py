"""Deterministic categorical feature extraction from a ``chess.Board``.

Provides the two functions the Stockfish adapter calls to enrich an
evaluation with ``tactical_flags`` and ``position_flags`` lists,
populated against the closed vocabulary in
``llm.rag.engine_signal.flag_vocabulary``.

The module is intentionally *pure* — no I/O, no random sources, no
chess-engine search.  Every output is a deterministic function of the
input board, so the same FEN always produces the same flag list
(important for the golden-snapshot tests and the trust-boundary
guarantee in ``docs/ARCHITECTURE.md``).

Architecture compliance: every emitted string is a categorical label
from the closed vocabulary.  No square coordinates, no piece-and-target
tuples, no numeric precision.  PV / depth / centipawn-score are never
read here — only the static board.
"""

from __future__ import annotations

import chess


# Piece values used for the material-imbalance band.  Standard
# Hans-Berliner-ish weights; deliberately coarse so the imbalance band
# is a category, not a number.
_PIECE_VALUES: dict[int, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
}


# ---------------------------------------------------------------------------
# Tactical flags
# ---------------------------------------------------------------------------


def _hanging_sides(board: chess.Board) -> set[chess.Color]:
    """Return the set of colors whose pieces include at least one hanger.

    A piece is considered hanging if it is attacked by at least one
    enemy piece and defended by zero friendly pieces.  Kings are
    excluded — kings have their own check semantics.  This is a coarse
    flag, not a tactical solver; deeper threats (overloaded defender,
    pin-and-fork, x-ray) are out of scope.
    """
    hanging: set[chess.Color] = set()
    for square, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        attackers = board.attackers(not piece.color, square)
        if not attackers:
            continue
        defenders = board.attackers(piece.color, square)
        if not defenders:
            hanging.add(piece.color)
    return hanging


def compute_tactical_flags(board: chess.Board) -> list[str]:
    """Return the sorted list of tactical flag labels for ``board``.

    Returned strings are members of
    ``flag_vocabulary.TACTICAL_FLAGS_VOCAB``.  The list is
    deterministic (sorted) so the prompt-renderer output is stable
    against Python's dict ordering changes.
    """
    flags: list[str] = []

    hanging = _hanging_sides(board)
    if chess.WHITE in hanging:
        flags.append("hanging_piece:white")
    if chess.BLACK in hanging:
        flags.append("hanging_piece:black")

    if board.is_check():
        if board.turn == chess.WHITE:
            flags.append("check:white_to_move")
        else:
            flags.append("check:black_to_move")

    return sorted(flags)


# ---------------------------------------------------------------------------
# Position flags — castling
# ---------------------------------------------------------------------------


def _castling_state(board: chess.Board, color: chess.Color) -> str:
    """Detect whether ``color`` has castled, and which side.

    Detection is positional, not historical: we look at the current
    king + rook placement that *would result from* a castling move.
    Without move history a king walked manually to g1 then a rook
    walked to f1 would look identical; the project considers this a
    rare edge case and accepts the false positive.
    """
    king_sq = board.king(color)
    if king_sq is None:
        return f"{('white' if color == chess.WHITE else 'black')}_uncastled"

    side = "white" if color == chess.WHITE else "black"

    if color == chess.WHITE:
        kingside_king, kingside_rook = chess.G1, chess.F1
        queenside_king, queenside_rook = chess.C1, chess.D1
    else:
        kingside_king, kingside_rook = chess.G8, chess.F8
        queenside_king, queenside_rook = chess.C8, chess.D8

    if king_sq == kingside_king:
        rook = board.piece_at(kingside_rook)
        if rook and rook.piece_type == chess.ROOK and rook.color == color:
            return f"{side}_kingside_done"

    if king_sq == queenside_king:
        rook = board.piece_at(queenside_rook)
        if rook and rook.piece_type == chess.ROOK and rook.color == color:
            return f"{side}_queenside_done"

    return f"{side}_uncastled"


# ---------------------------------------------------------------------------
# Position flags — king safety
# ---------------------------------------------------------------------------


def _king_zone(king_sq: chess.Square) -> set[chess.Square]:
    """Squares within one step of ``king_sq`` (inclusive)."""
    king_file = chess.square_file(king_sq)
    king_rank = chess.square_rank(king_sq)
    zone: set[chess.Square] = set()
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            f = king_file + df
            r = king_rank + dr
            if 0 <= f < 8 and 0 <= r < 8:
                zone.add(chess.square(f, r))
    return zone


def _pawn_shield_count(board: chess.Board, color: chess.Color, king_sq: chess.Square) -> int:
    """Count pawns of ``color`` directly in front of the king on the
    king's file and the two adjacent files, within two ranks.

    "In front" depends on the side — White's shield is on higher
    ranks; Black's is on lower.  Returns 0..3.
    """
    king_file = chess.square_file(king_sq)
    king_rank = chess.square_rank(king_sq)
    if color == chess.WHITE:
        target_ranks = (king_rank + 1, king_rank + 2)
    else:
        target_ranks = (king_rank - 1, king_rank - 2)
    count = 0
    for df in (-1, 0, 1):
        file_ = king_file + df
        if file_ < 0 or file_ > 7:
            continue
        for rank in target_ranks:
            if rank < 0 or rank > 7:
                continue
            piece = board.piece_at(chess.square(file_, rank))
            if piece and piece.piece_type == chess.PAWN and piece.color == color:
                count += 1
                break
    return count


def _king_attacker_count(board: chess.Board, color: chess.Color, king_sq: chess.Square) -> int:
    zone = _king_zone(king_sq)
    return sum(1 for sq in zone if board.attackers(not color, sq))


def _king_safety_band(board: chess.Board, color: chess.Color) -> str:
    """Return the king-safety band label for ``color``.

    Bands are coarse: ``exposed`` (king on a central file or 3rd+ rank
    with no shield, OR multiple enemy attackers near the king),
    ``safe`` (back rank, pawn shield intact, no nearby attackers),
    ``loose`` (everything else).
    """
    side = "white" if color == chess.WHITE else "black"
    king_sq = board.king(color)
    if king_sq is None:
        return f"king_safety:{side}_exposed"

    file_ = chess.square_file(king_sq)
    rank = chess.square_rank(king_sq)
    back_rank = 0 if color == chess.WHITE else 7
    on_back_two = abs(rank - back_rank) <= 1
    on_wing = file_ <= 2 or file_ >= 5

    shield = _pawn_shield_count(board, color, king_sq)
    attackers = _king_attacker_count(board, color, king_sq)

    if on_back_two and on_wing and shield >= 2 and attackers == 0:
        return f"king_safety:{side}_safe"
    if (not on_back_two) or shield == 0 or attackers >= 2:
        return f"king_safety:{side}_exposed"
    return f"king_safety:{side}_loose"


# ---------------------------------------------------------------------------
# Position flags — pawn structure
# ---------------------------------------------------------------------------


def _pawn_files(board: chess.Board, color: chess.Color) -> dict[int, list[int]]:
    """Map file index → sorted list of ranks where ``color`` has a pawn."""
    files: dict[int, list[int]] = {f: [] for f in range(8)}
    for square in board.pieces(chess.PAWN, color):
        files[chess.square_file(square)].append(chess.square_rank(square))
    for f in files:
        files[f].sort()
    return files


def _pawn_structure_flags(board: chess.Board, color: chess.Color) -> list[str]:
    side = "white" if color == chess.WHITE else "black"
    own = _pawn_files(board, color)
    opp = _pawn_files(board, not color)

    has_doubled = any(len(ranks) >= 2 for ranks in own.values())
    has_isolated = any(
        ranks
        and not own.get(f - 1)
        and not own.get(f + 1)
        for f, ranks in own.items()
    )
    has_passed = False
    for f, ranks in own.items():
        if not ranks:
            continue
        forward_rank = ranks[-1] if color == chess.WHITE else ranks[0]
        blocked = False
        for adj in (f - 1, f, f + 1):
            if adj < 0 or adj > 7:
                continue
            for opp_rank in opp.get(adj, []):
                if color == chess.WHITE and opp_rank > forward_rank:
                    blocked = True
                    break
                if color == chess.BLACK and opp_rank < forward_rank:
                    blocked = True
                    break
            if blocked:
                break
        if not blocked:
            has_passed = True

    flags: list[str] = []
    if has_doubled:
        flags.append(f"pawn_structure:{side}_doubled")
    if has_isolated:
        flags.append(f"pawn_structure:{side}_isolated")
    if has_passed:
        flags.append(f"pawn_structure:{side}_passed")
    return flags


# ---------------------------------------------------------------------------
# Position flags — material imbalance
# ---------------------------------------------------------------------------


def _material_band(board: chess.Board) -> str:
    diff = 0
    for piece_type, value in _PIECE_VALUES.items():
        diff += value * (
            len(board.pieces(piece_type, chess.WHITE))
            - len(board.pieces(piece_type, chess.BLACK))
        )
    magnitude = abs(diff)
    if magnitude < 50:
        return "material:even"
    if magnitude < 250:
        suffix = "white_up_pawn" if diff > 0 else "black_up_pawn"
    elif magnitude < 450:
        suffix = "white_up_piece" if diff > 0 else "black_up_piece"
    else:
        suffix = "white_up_major" if diff > 0 else "black_up_major"
    return f"material:{suffix}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_position_flags(board: chess.Board) -> list[str]:
    """Return the sorted list of position flag labels for ``board``.

    Returned strings are members of
    ``flag_vocabulary.POSITION_FLAGS_VOCAB``.  The list is
    deterministic (sorted).
    """
    flags: list[str] = []

    for color in (chess.WHITE, chess.BLACK):
        flags.append(f"castling:{_castling_state(board, color)}")
        flags.append(_king_safety_band(board, color))
        flags.extend(_pawn_structure_flags(board, color))

    flags.append(_material_band(board))

    return sorted(flags)


__all__ = (
    "compute_position_flags",
    "compute_tactical_flags",
)
