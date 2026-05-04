import os
import urllib.request
import zipfile
from pathlib import Path

URL = "https://stockfishchess.org/files/stockfish_16_win_x64_avx2.zip"
ENGINE_DIR = Path("engines")
ENGINE_PATH = ENGINE_DIR / "stockfish.exe"


def main():
    if ENGINE_PATH.exists():
        print("Stockfish already installed.")
        return

    ENGINE_DIR.mkdir(exist_ok=True)

    zip_path = ENGINE_DIR / "stockfish.zip"
    print("Downloading Stockfish...")
    urllib.request.urlretrieve(URL, zip_path)

    print("Extracting...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(ENGINE_DIR)

    zip_path.unlink()

    # find exe inside extracted folder
    for root, _, files in os.walk(ENGINE_DIR):
        if "stockfish.exe" in files:
            src = Path(root) / "stockfish.exe"
            src.rename(ENGINE_PATH)
            break

    print("Stockfish installed at:", ENGINE_PATH)


if __name__ == "__main__":
    main()
