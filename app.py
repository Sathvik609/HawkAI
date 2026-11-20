import json
import time
from collections import Counter

from flask import Flask, Response, abort, jsonify, render_template, request, stream_with_context

import generation
from config import DATA_DIR, MAX_HISTORY_TURNS, MAX_MESSAGE_CHARS, NUM_OPTIONS
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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001, threaded=True)
