print(">>> USING MODE-2 RENDER V1 <<<")


def render_mode_2_prompt(
    *,
    system_prompt: str,
    engine_signal: dict,
    rag_docs: list,
    fen: str,
    user_query: str = "",
    rag_context=None,
) -> str:
    parts: list[str] = []

    # SYSTEM PROMPT
    parts.append("SYSTEM PROMPT:")
    parts.append(system_prompt.strip())
    parts.append("")

    # ENGINE SIGNAL
    parts.append("ENGINE SIGNAL:")
    for key in [
        "evaluation",
        "eval_delta",
        "last_move_quality",
        "tactical_flags",
        "position_flags",
        "phase",
    ]:
        if key in engine_signal:
            parts.append(f"{key}: {engine_signal[key]}")
    parts.append("")

    # RAG CONTEXT
    parts.append("RAG CONTEXT:")
    for doc in rag_docs:
        parts.append(f"- {doc['content']['description']}")
    parts.append("")

    # FEN
    parts.append("FEN:")
    parts.append(fen)
    parts.append("")

    # USER QUESTION
    if user_query:
        parts.append("USER QUESTION:")
        parts.append(user_query)

    prompt = "\n".join(parts).strip()
    if rag_context:
        prompt += f"\nContext:\n{rag_context}"
    return prompt
