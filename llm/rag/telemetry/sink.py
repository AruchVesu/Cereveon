import json
import os
from llm.rag.telemetry.event import Mode2TelemetryEvent

TELEMETRY_ENABLED = os.getenv("LLM_TELEMETRY", "1") == "1"


def emit(event: Mode2TelemetryEvent):
    if not TELEMETRY_ENABLED:
        return

    print("[TELEMETRY]", json.dumps(event.to_dict(), ensure_ascii=False))
