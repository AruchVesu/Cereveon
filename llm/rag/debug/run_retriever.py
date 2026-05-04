import json
from pathlib import Path

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve

# --- Load golden case ---
ROOT = Path(__file__).resolve().parents[2]

with open(ROOT / "tests" / "golden" / "cases" / "tactical_mistake" / "case_001.json", "r") as f:
    case = json.load(f)

# --- Build ESV ---
esv = extract_engine_signal(case["stockfish_json"])

print(">>> ESV:")
print(json.dumps(esv, indent=2))

# --- Load all RAG documents ---
# For now, we load them manually as a list
from llm.rag.documents import ALL_RAG_DOCUMENTS  # see note below

# --- Run retriever ---
docs = retrieve(esv, ALL_RAG_DOCUMENTS)

ids = [doc["id"] for doc in docs]

print(">>> RETRIEVED RAG IDS:")
print(json.dumps(ids, indent=2))
