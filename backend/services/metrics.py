import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
METRICS_LOG = LOG_DIR / "metrics.jsonl"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(event: str, payload: Dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ts": _utc_now_iso(), "event": event}
    record.update(payload or {})
    with METRICS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
