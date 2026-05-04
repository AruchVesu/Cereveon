# seca/henm/dataset.py
import pandas as pd
import torch
from torch.utils.data import Dataset

from .features import encode_board, encode_scalar_features


def classify_cp_loss(cp_loss: float) -> int:
    """0=best, 1=inaccuracy, 2=mistake, 3=blunder"""
    if cp_loss < 30:
        return 0
    if cp_loss < 80:
        return 1
    if cp_loss < 200:
        return 2
    return 3


class HumanErrorDataset(Dataset):
    def __init__(self, csv_path: str):
        self.df = pd.read_csv(csv_path)

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]

        board_vec = encode_board(row.fen)
        scalar_vec = encode_scalar_features(row.elo, row.tactical_complexity)

        x = torch.tensor(
            list(board_vec) + list(scalar_vec),
            dtype=torch.float32,
        )

        y = torch.tensor(classify_cp_loss(row.cp_loss), dtype=torch.long)

        return x, y
