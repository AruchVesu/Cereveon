from pathlib import Path
import yaml

DOCS_DIR = Path(__file__).parent

ALL_RAG_DOCUMENTS = []

for path in DOCS_DIR.glob("*.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
        ALL_RAG_DOCUMENTS.append(doc)
