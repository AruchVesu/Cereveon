import torch
from torch.utils.data import Dataset


class SkillDynamicsDataset(Dataset):
    def __init__(self, samples: list[dict]):
        self.samples = samples

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]

        return (
            torch.tensor(s["skill_before"], dtype=torch.float32),
            torch.tensor(s["training_action"], dtype=torch.float32),
            torch.tensor(s["skill_after"], dtype=torch.float32),
        )
