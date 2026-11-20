import json
import threading
from datetime import datetime, timezone

from config import LOG_DIR

_lock = threading.Lock()


def log_event(event: str, **fields) -> None:
    now = datetime.now(timezone.utc)
    record = {"ts": now.isoformat(timespec="seconds"), "event": event, **fields}
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _lock, open(LOG_DIR / f"events-{now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"Could not write event log: {exc}")
