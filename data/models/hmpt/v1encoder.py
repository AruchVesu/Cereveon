import torch
import torch.nn as nn
import chess

# =============================
# Board Encoder
# =============================


class BoardEncoder(nn.Module):
    """
    Converts a chess.Board into a tensor of shape:
    (12, 8, 8)

    6 piece types × 2 colors.
    """

    def __init__(self):
        super().__init__()

    @staticmethod
    def encode_board(board: chess.Board) -> torch.Tensor:
        planes = torch.zeros(12, 8, 8, dtype=torch.float32)

        piece_map = board.piece_map()

        for square, piece in piece_map.items():
            row = 7 - chess.square_rank(square)
            col = chess.square_file(square)

            piece_type = piece.piece_type - 1  # 0–5
            color_offset = 0 if piece.color == chess.WHITE else 6

            planes[piece_type + color_offset, row, col] = 1.0

        return planes

    def forward(self, board: chess.Board) -> torch.Tensor:
        return self.encode_board(board).unsqueeze(0)  # add batch dim


# =============================
# HMPT v1 Neural Policy Network
# =============================


class HMPTPolicyNet(nn.Module):
    """
    Simple CNN policy head:
    Input:  (B, 12, 8, 8)
    Output: logits over 4672 possible moves (AlphaZero-style upper bound)
    """

    NUM_MOVES = 4672

    def __init__(self):
        super().__init__()

        self.conv_stack = nn.Sequential(
            nn.Conv2d(12, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv2d(128, 128, kernel_size=3, padding=1),
            nn.ReLU(),
        )

        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 8 * 8, 512),
            nn.ReLU(),
            nn.Linear(512, self.NUM_MOVES),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv_stack(x)
        return self.head(x)


# =============================
# Move Mapping Utilities
# =============================


class MoveIndexer:
    """
    Maps chess.Move <-> index in [0, 4672).

    NOTE: Simplified placeholder mapping for HMPT v1.
    Real AlphaZero-style encoding comes in HMPT v2.
    """

    def __init__(self):
        self.move_to_idx = {}
        self.idx_to_move = {}

    def legal_indices(self, board: chess.Board):
        indices = []
        for i, move in enumerate(board.legal_moves):
            self.move_to_idx[move.uci()] = i
            self.idx_to_move[i] = move
            indices.append(i)
        return indices

    def idx_to_chess_move(self, idx: int) -> chess.Move:
        return self.idx_to_move[idx]


# =============================
# HMPT Inference Wrapper
# =============================


class HMPTModel:
    """
    Full inference wrapper combining:
    - encoder
    - neural policy
    - legal move masking
    """

    def __init__(self, device: str = "cpu"):
        self.device = torch.device(device)

        self.encoder = BoardEncoder()
        self.net = HMPTPolicyNet().to(self.device)
        self.indexer = MoveIndexer()

        self.net.eval()

    @torch.no_grad()
    def sample_move(self, board: chess.Board) -> chess.Move:
        x = self.encoder(board).to(self.device)
        logits = self.net(x)[0]

        legal_indices = self.indexer.legal_indices(board)

        mask = torch.full_like(logits, float("-inf"))
        mask[legal_indices] = logits[legal_indices]

        probs = torch.softmax(mask, dim=0)
        idx = torch.multinomial(probs, 1).item()

        return self.indexer.idx_to_chess_move(idx)


# =============================
# Loader Function (SECA API)
# =============================


def load_hmpt(device: str = "cpu") -> HMPTModel:
    """
    Public entry point used by SECA engines.
    """
    return HMPTModel(device=device)
