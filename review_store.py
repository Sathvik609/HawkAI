"""Marks from researchers reviewing the experience chunks. Postgres when configured, else a local JSON file."""
import json
import threading
from datetime import datetime, timezone

from config import DATABASE_URL, LOG_DIR

SCHEMA = """CREATE TABLE IF NOT EXISTS chunk_reviews (
    reviewer text NOT NULL,
    narrative_id text NOT NULL,
    chunk int NOT NULL,
    mark text,
    note text,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (reviewer, narrative_id, chunk)
)"""

FILE = LOG_DIR / "chunk_reviews.json"
_lock = threading.Lock()
_conn = None


def _db():
    global _conn
    if _conn is None or _conn.closed:
        import psycopg
        _conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
        _conn.execute(SCHEMA)
    return _conn


def _query(sql: str, params=()):
    global _conn
    for attempt in range(2):
        try:
            cur = _db().execute(sql, params)
            return cur.fetchall() if cur.description else []
        except Exception:
            _conn = None
            if attempt:
                raise


def _read_file() -> dict:
    return json.loads(FILE.read_text()) if FILE.exists() else {}


def save(reviewer: str, narrative_id: str, chunk: int, mark: str | None, note: str) -> None:
    now = datetime.now(timezone.utc)
    with _lock:
        if DATABASE_URL:
            _query("""INSERT INTO chunk_reviews (reviewer, narrative_id, chunk, mark, note, updated_at)
                      VALUES (%s, %s, %s, %s, %s, %s)
                      ON CONFLICT (reviewer, narrative_id, chunk)
                      DO UPDATE SET mark = EXCLUDED.mark, note = EXCLUDED.note, updated_at = EXCLUDED.updated_at""",
                   (reviewer, narrative_id, chunk, mark, note, now))
            return
        rows = _read_file()
        rows[f"{reviewer}|{narrative_id}|{chunk}"] = {"reviewer": reviewer, "narrative_id": narrative_id, "chunk": chunk,
                                                      "mark": mark, "note": note, "updated_at": now.isoformat(timespec="seconds")}
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(rows, indent=1, ensure_ascii=False))


def load(reviewer: str | None = None) -> list[dict]:
    with _lock:
        if DATABASE_URL:
            sql = "SELECT reviewer, narrative_id, chunk, mark, note, updated_at FROM chunk_reviews"
            rows = _query(sql + " WHERE reviewer = %s" if reviewer else sql, (reviewer,) if reviewer else ())
            return [{"reviewer": r[0], "narrative_id": r[1], "chunk": r[2], "mark": r[3], "note": r[4],
                     "updated_at": r[5].isoformat(timespec="seconds")} for r in rows]
        rows = list(_read_file().values())
    return [r for r in rows if not reviewer or r["reviewer"] == reviewer]
