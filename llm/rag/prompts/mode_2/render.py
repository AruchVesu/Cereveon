import json
from pathlib import Path


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def render_mode_2_prompt(
    *,
    system_prompt: str,
    engine_signal: dict,
    rag_docs: list[dict],
    fen: str,
    user_query: str,
    rag_context=None,
) -> str:
    rag_blocks = []

    for i, doc in enumerate(rag_docs, start=1):
        content = doc["content"]["description"]
        rag_blocks.append(f"[{i}] {content}")

    rag_text = "\n\n".join(rag_blocks) if rag_blocks else "(no retrieved context)"

    safe_fen = "".join(c if c >= "\x20" else " " for c in fen)

    prompt = f"""{system_prompt}

────────────────────────────
ENGINE SIGNAL (STRUCTURED)
────────────────────────────
{json.dumps(engine_signal, indent=2)}

────────────────────────────
RETRIEVED CONTEXT (REFERENCE)
────────────────────────────
{rag_text}

────────────────────────────
POSITION
────────────────────────────
FEN: {safe_fen}

────────────────────────────
USER REQUEST
────────────────────────────
<user_query>
{user_query}
</user_query>
""".strip()
    if rag_context:
        prompt += f"\nContext:\n{rag_context}"
    return prompt
