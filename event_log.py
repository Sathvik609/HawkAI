import json
import threading
from datetime import datetime, timezone

from config import DATABASE_URL, LOG_DIR

_lock = threading.Lock()
_conn = None

SCHEMA = """CREATE TABLE IF NOT EXISTS events (
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL,
    event text NOT NULL,
    session text,
    pid text,
    mode text,
    data jsonb NOT NULL
)"""

COLUMNS = ("session", "pid", "mode")


def connect():
    import psycopg
    conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
    conn.execute(SCHEMA)
    return conn


def _insert(now: datetime, event: str, fields: dict) -> None:
    global _conn
    from psycopg.types.json import Jsonb
    row = (now, event, *(fields.get(c) for c in COLUMNS), Jsonb({k: v for k, v in fields.items() if k not in COLUMNS}))
    for attempt in range(2):
        try:
            if _conn is None or _conn.closed:
                _conn = connect()
            _conn.execute("INSERT INTO events (ts, event, session, pid, mode, data) VALUES (%s, %s, %s, %s, %s, %s)", row)
            return
        except Exception:
            _conn = None
            if attempt:
                raise


def _append_file(now: datetime, record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / f"events-{now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_event(event: str, **fields) -> None:
    now = datetime.now(timezone.utc)
    record = {"ts": now.isoformat(timespec="seconds"), "event": event, **fields}
    with _lock:
        if DATABASE_URL:
            try:
                _insert(now, event, fields)
                return
            except Exception as exc:
                print(f"Could not save event to the database: {exc}")
        try:
            _append_file(now, record)
        except OSError as exc:
            print(f"Could not write event log: {exc}")
        if DATABASE_URL:
            print("UNSAVED_EVENT " + json.dumps(record, ensure_ascii=False))
