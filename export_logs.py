import csv
import json
import sys
from datetime import datetime

from config import BASE_DIR, DATABASE_URL
from event_log import connect

FIRST = ["ts", "event", "session", "pid", "mode", "query", "concept", "narrative_id", "turn", "message", "response",
         "quote_check", "options", "cached", "seconds"]


def main():
    if not DATABASE_URL:
        sys.exit("Set DATABASE_URL (or POSTGRES_URL) to the study database first.")
    since = sys.argv[1] if len(sys.argv) > 1 else "1970-01-01"
    with connect() as conn:
        rows = conn.execute("SELECT ts, event, session, pid, mode, data FROM events WHERE ts >= %s ORDER BY ts, id",
                            (since,)).fetchall()
    records = [{"ts": ts.isoformat(timespec="seconds"), "event": event, "session": session, "pid": pid, "mode": mode, **data}
               for ts, event, session, pid, mode, data in rows]

    out_dir = BASE_DIR / "exports"
    out_dir.mkdir(exist_ok=True)
    stem = out_dir / f"events-{datetime.now():%Y-%m-%d-%H%M}"
    with open(f"{stem}.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    columns = FIRST + sorted({k for r in records for k in r} - set(FIRST))
    with open(f"{stem}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in records:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()})

    print(f"Exported {len(records)} events to {stem}.jsonl and {stem}.csv")


if __name__ == "__main__":
    main()
