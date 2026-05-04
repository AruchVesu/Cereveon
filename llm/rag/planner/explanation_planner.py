from llm.rag.planner.plan_selector import select_plan_type
from llm.rag.planner.templates import PLAN_TEMPLATES


def build_explanation_plan(engine_signal: dict) -> dict:
    plan_type = select_plan_type(engine_signal)
    template = PLAN_TEMPLATES[plan_type]

    return {
        "type": plan_type,
        "sections": template["sections"],
    }
