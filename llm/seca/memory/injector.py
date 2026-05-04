from llm.rag.memory.influence import build_memory_block


def inject_memory(prompt: str, memory) -> str:
    block = build_memory_block(memory)
    if not block.strip():
        return prompt
    return prompt + "\n\n" + block
