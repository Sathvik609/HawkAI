import csv
import functools
import io
import json
import random
import time
from collections import Counter

from flask import Flask, Response, abort, jsonify, render_template, request, stream_with_context

import generation
from config import DATA_DIR, MAX_HISTORY_TURNS, MAX_MESSAGE_CHARS, NUM_OPTIONS
import review_store
from event_log import log_event
from retrieval import NarrativeIndex, Searcher

app = Flask(__name__)

STARTERS = ["The Beauty Myth", "Looking-Glass Self", "Anomie", "Presentation of Self",
            "Collective Consciousness", "White Privilege", "Alienation", "Stigma"]

index = NarrativeIndex(DATA_DIR)
searcher = Searcher(index)
print(f"HawkAI ready: {len(index.narratives)} narratives, {len(index.chunk_text)} passages.")


def _concept_names() -> list[str]:
    names = {}
    tags = Counter(c.strip() for n in index.narratives for c in n["concepts"])
    found = [c["concept"] for c in searcher.concepts.values() if c.get("options")]
    for name in [*STARTERS, *found, *[t for t, k in tags.most_common() if k >= 3]]:
        names.setdefault(Searcher._key(name), name)
    return [n for k, n in names.items() if k]


CONCEPT_NAMES = _concept_names()


def _payload() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Expected a JSON body.")
    return data


def _who(data: dict) -> dict:
    return {"session": str(data.get("session_id", ""))[:64], "pid": str(data.get("pid", ""))[:64],
            "mode": "gemini" if data.get("mode") == "gemini" else "hawkai"}


def _text(value, limit: int = MAX_MESSAGE_CHARS) -> str:
    return str(value or "").strip()[:limit]


def _history(data: dict) -> list[dict]:
    history = data.get("history") or []
    if not isinstance(history, list):
        return []
    clean = [{"role": "model" if h.get("role") == "model" else "user", "text": _text(h.get("text"), 20000)}
             for h in history if isinstance(h, dict)]
    return clean[-MAX_HISTORY_TURNS:]


def _narrative(data: dict) -> dict:
    narrative = index.by_id.get(str(data.get("narrative_id", "")))
    if not narrative:
        abort(404, "That narrative was not found.")
    return narrative


def _stream(pieces, on_finish, clean=None):
    def generate():
        full = []
        try:
            for piece in pieces:
                full.append(piece)
                yield json.dumps({"type": "delta", "text": piece}) + "\n"
            body, suggestions = generation.split_suggestions("".join(full))
            body, keys = generation.split_keys(body)
            body = generation.mark_keys(body, keys)
            if clean:
                body = clean(body)
            extra = on_finish(body, suggestions) or {}
            yield json.dumps({"type": "done", "text": body, "suggestions": suggestions, **extra}) + "\n"
        except Exception as exc:
            print(f"Generation failed: {exc}")
            yield json.dumps({"type": "error", "message": "HawkAI couldn't finish this answer. Try again in a moment."}) + "\n"

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.errorhandler(400)
@app.errorhandler(404)
def _client_error(err):
    return jsonify({"error": getattr(err, "description", "Bad request")}), err.code


@app.get("/")
def home():
    starters = []
    for name in STARTERS:
        hit = searcher.cached(name) or {}
        starters.append({"name": name, "definition": hit.get("definition") or "",
                         "count": len(hit.get("options") or [])})
    return render_template("index.html", num_options=NUM_OPTIONS, total=len(index.narratives), starters=starters, concept_names=CONCEPT_NAMES)


@app.get("/healthz")
def healthz():
    return {"ok": True, "narratives": len(index.narratives)}


@app.post("/api/search")
def search():
    data = _payload()
    query = _text(data.get("query"), 300)
    if not query:
        abort(400, "Type a concept to search for.")
    started = time.time()
    try:
        result = searcher.search(query)
    except Exception as exc:
        print(f"Search failed: {exc}")
        return jsonify({"error": "Search is unavailable right now. Try again in a moment."}), 502
    result = {**result, "options": [{**o, "content_note": index.notes.get(o["id"], [])} for o in result["options"]]}
    log_event("search", **_who(data), query=query, concept=result["concept"],
              options=[o["id"] for o in result["options"]], cached=result.get("cached", False),
              seconds=round(time.time() - started, 2))
    return jsonify(result)


@app.get("/api/narrative/<nid>")
def narrative(nid):
    n = index.by_id.get(nid)
    if not n:
        abort(404, "That narrative was not found.")
    return jsonify({"id": n["id"], "title": n["title"], "concepts": n["concepts"], "text": n["text"],
                    "content_note": index.notes.get(n["id"], [])})


@app.post("/api/explain")
def explain():
    data = _payload()
    narrative = _narrative(data)
    concept = _text(data.get("concept"), 200) or "this concept"
    who = _who(data)
    log_event("select", **who, concept=concept, narrative_id=narrative["id"])
    started = time.time()

    def finish(body, suggestions):
        quote = generation.verify_quote(body, narrative["text"])
        log_event("explain", **who, concept=concept, narrative_id=narrative["id"], quote_check=quote,
                  response=body, seconds=round(time.time() - started, 2))
        return {"quote_check": quote, "quote": generation.extract_quote(body)}

    return _stream(generation.stream_explain(concept, narrative), finish)


@app.post("/api/chat")
def chat():
    data = _payload()
    who = _who(data)
    message = _text(data.get("message"))
    if not message:
        abort(400, "Type a question first.")
    history = _history(data)
    started = time.time()

    if who["mode"] == "gemini":
        pieces = generation.stream_gemini(history, message)
        context, clean = {}, None
    else:
        narrative = _narrative(data)
        concept = _text(data.get("concept"), 200) or "this concept"
        initial = _text(data.get("initial"), 20000)
        pieces = generation.stream_chat(concept, narrative, initial, history, message)
        context = {"concept": concept, "narrative_id": narrative["id"]}

        def clean(body):
            body, context["tags_removed"] = generation.check_tags(body, narrative["text"])
            return body

    def finish(body, suggestions):
        log_event("chat", **who, **context, turn=len(history) // 2 + 1, message=message, response=body,
                  seconds=round(time.time() - started, 2))

    return _stream(pieces, finish, clean)


@app.post("/api/hawkai")
def legacy_one_shot():
    data = _payload()
    concept = _text(data.get("concept"), 300)
    if not concept:
        return jsonify({"error": "No concept provided"}), 400
    result = searcher.search(concept)
    if not result["options"]:
        return jsonify({"error": result["message"]}), 404
    best = index.by_id[result["options"][0]["id"]]
    body, _ = generation.split_suggestions("".join(generation.stream_explain(result["concept"], best)))
    body, _ = generation.split_keys(body)
    return jsonify({"result": body, "score": float(result["options"][0]["relevance"] or 0) / 10})


REVIEW_FILE = DATA_DIR / "experience_chunks_sample.json"
REVIEW_MARKS = {"correct", "partly", "wrong"}


def _review_sample() -> list[dict]:
    return json.loads(REVIEW_FILE.read_text())["narratives"] if REVIEW_FILE.exists() else []


def _reviewer(value) -> str:
    name = " ".join(str(value or "").split())[:60]
    if not name:
        abort(400, description="Please enter your name first.")
    return name


@app.get("/review")
def review():
    return render_template("review.html")


@app.get("/api/review")
def review_data():
    reviewer = _reviewer(request.args.get("reviewer"))
    marks = [m for m in review_store.load(reviewer) if m["chunk"] >= 0]
    return jsonify({"narratives": _review_sample(), "marks": marks})


@app.post("/api/review/mark")
def review_mark():
    data = _payload()
    reviewer = _reviewer(data.get("reviewer"))
    sample = {n["id"]: n for n in _review_sample()}
    nid = str(data.get("narrative_id") or "")
    if nid not in sample:
        abort(400, description="Unknown narrative.")
    try:
        chunk = int(data.get("chunk"))
    except (TypeError, ValueError):
        abort(400, description="Unknown chunk.")
    if not 0 <= chunk <= len(sample[nid]["chunks"]):
        abort(400, description="Unknown chunk.")
    mark = data.get("mark") if data.get("mark") in REVIEW_MARKS else None
    review_store.save(reviewer, nid, chunk, mark, _text(data.get("note"), 4000))
    return jsonify({"ok": True})


@app.get("/review/export.csv")
def review_export():
    sample = {n["id"]: n for n in _review_sample()}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["reviewer", "narrative_id", "narrative_title", "chunk", "type", "label", "mark", "note", "updated_at"])
    for r in sorted(review_store.load(), key=lambda r: (r["reviewer"], r["narrative_id"], r["chunk"])):
        if r["chunk"] < 0 or (not r["mark"] and not (r["note"] or "").strip()):
            continue
        n = sample.get(r["narrative_id"], {})
        c = n.get("chunks", [])[r["chunk"] - 1] if r["chunk"] else {}
        writer.writerow([r["reviewer"], r["narrative_id"], n.get("title", ""), r["chunk"] or "whole narrative",
                         c.get("kind", ""), c.get("label", ""), r["mark"] or "", r["note"] or "", r["updated_at"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=chunk_reviews.csv"})


NO_EXPERIENCE_CHUNK = -1
NO_EXPERIENCE_MARKS = {"correct", "wrong"}
NO_EXPERIENCE_GROUPS = {"sample": "Random sample", "none": "No experience", "brief": "Brief mention only"}


@functools.cache
def _no_experience() -> dict:
    chunks = json.loads((DATA_DIR / "experience_chunks.json").read_text())["narratives"]
    checks = {r["id"]: r for r in json.loads((DATA_DIR / "experience_check.json").read_text())}
    out = {}
    for nid, r in chunks.items():
        if any(c.get("kind") == "experience" for c in r["chunks"]) or nid not in checks:
            continue
        check = checks[nid]
        out[nid] = {"id": nid, "title": r["title"], "type": check.get("narrative_type", ""),
                    "group": "brief" if check["level"] == "brief" else "none",
                    "brief": "; ".join(check.get("experiences") or []) if check["level"] == "brief" else "",
                    "text": "\n\n".join(c["text"] for c in r["chunks"])}
    none = sorted((n for n in out.values() if n["group"] == "none"), key=lambda n: n["title"])
    sample = {n["id"] for n in random.Random(2026).sample(none, min(10, len(none)))}
    for n in out.values():
        n["sample"] = n["id"] in sample
    return out


@app.get("/no-experience")
def no_experience():
    return render_template("no_experience.html")


@app.get("/api/no-experience")
def no_experience_data():
    reviewer = _reviewer(request.args.get("reviewer"))
    items = sorted(_no_experience().values(), key=lambda n: n["title"])
    marks = [m for m in review_store.load(reviewer) if m["chunk"] == NO_EXPERIENCE_CHUNK]
    return jsonify({"narratives": [{k: v for k, v in n.items() if k != "text"} for n in items], "marks": marks})


@app.get("/api/no-experience/<nid>")
def no_experience_text(nid: str):
    n = _no_experience().get(nid)
    if not n:
        abort(404, description="Unknown narrative.")
    return jsonify({"id": nid, "text": n["text"]})


@app.post("/api/no-experience/mark")
def no_experience_mark():
    data = _payload()
    reviewer = _reviewer(data.get("reviewer"))
    nid = str(data.get("narrative_id") or "")
    if nid not in _no_experience():
        abort(400, description="Unknown narrative.")
    mark = data.get("mark") if data.get("mark") in NO_EXPERIENCE_MARKS else None
    review_store.save(reviewer, nid, NO_EXPERIENCE_CHUNK, mark, _text(data.get("note"), 4000))
    return jsonify({"ok": True})


@app.get("/no-experience/export.csv")
def no_experience_export():
    items = _no_experience()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["reviewer", "narrative_id", "narrative_title", "group", "has_no_experience", "note", "updated_at"])
    for r in sorted(review_store.load(), key=lambda r: (r["reviewer"], r["narrative_id"])):
        if r["chunk"] != NO_EXPERIENCE_CHUNK or (not r["mark"] and not (r["note"] or "").strip()):
            continue
        n = items.get(r["narrative_id"], {})
        answer = {"correct": "Correct, no experience", "wrong": "Wrong, it has an experience"}.get(r["mark"], "")
        writer.writerow([r["reviewer"], r["narrative_id"], n.get("title", ""), NO_EXPERIENCE_GROUPS.get(n.get("group"), ""),
                         answer, r["note"] or "", r["updated_at"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=no_experience_reviews.csv"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001, threaded=True)
