import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from .dataset import HumanErrorDataset
from .model import HumanErrorNet


def train(csv_path: str, epochs: int = 5, batch_size: int = 256):
    dataset = HumanErrorDataset(csv_path)

    sample_x, _ = dataset[0]
    model = HumanErrorNet(input_dim=len(sample_x))

    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = torch.nn.CrossEntropyLoss()

    model.train()

    for epoch in range(epochs):
        total_loss = 0

        for x, y in tqdm(loader, desc=f"epoch {epoch+1}"):
            logits = model(x)
            loss = loss_fn(logits, y)

            opt.zero_grad()
            loss.backward()
            opt.step()

            total_loss += loss.item()

        print(f"epoch {epoch+1} loss={total_loss/len(loader):.4f}")

    torch.save(model.state_dict(), "henm.pt")
    print("Model saved → henm.pt")
