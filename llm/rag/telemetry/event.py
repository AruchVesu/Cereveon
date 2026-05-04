from dataclasses import dataclass, asdict
from typing import List


@dataclass
class Mode2TelemetryEvent:
    success: bool
    retry_used: bool
    latency_ms: int
    validator_failures: List[str]
    output_length: int
    case_type: str
    confidence: str
    model: str

    def to_dict(self):
        return asdict(self)
