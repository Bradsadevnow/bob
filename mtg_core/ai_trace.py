# mtg_core/ai_trace.py
import json
from datetime import datetime
from pathlib import Path

TRACE_PATH = Path("./runtime/ai_trace.jsonl")
TRACE_PATH.parent.mkdir(exist_ok=True)

def log_ai_event(event_type: str, payload: dict) -> None:
    record = {
        "ts": datetime.utcnow().isoformat(),
        "event": event_type,
        "payload": payload,
    }

    with TRACE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
