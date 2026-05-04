from llm.rag.planner.explanation_planner import build_explanation_plan


def inject_plan(prompt: str, engine_signal: dict) -> str:
    plan = build_explanation_plan(engine_signal)

    plan_text = "\n".join(f"- {s}" for s in plan["sections"])

    block = f"""
EXPLANATION STRUCTURE (MANDATORY):

Follow this teaching structure exactly:

{plan_text}

Do not skip sections.
Do not merge sections.
Explain clearly and concisely.
"""

    return prompt + "\n\n" + block
