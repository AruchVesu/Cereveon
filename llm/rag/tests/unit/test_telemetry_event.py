from llm.rag.telemetry.event import Mode2TelemetryEvent


def test_telemetry_event_shape():
    event = Mode2TelemetryEvent(
        success=True,
        retry_used=False,
        latency_ms=123,
        validator_failures=[],
        output_length=200,
        case_type="tactical_mistake",
        confidence="high",
        model="test",
    )

    d = event.to_dict()
    assert "latency_ms" in d
    assert d["success"] is True
