import concurrent.futures as cf
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone

import numpy as np
from pydantic import BaseModel, Field

import gemini
from config import BASE_DIR, DATA_DIR, EMBED_DIM, EMBED_MODEL, FAST_MODEL

SOURCE = BASE_DIR / "embeddingDatabase.json"
META_CACHE = DATA_DIR / "metadata_cache.json"

CHUNK_WORDS = 260
CHUNK_OVERLAP_WORDS = 60
NEAR_DUPLICATE = 0.98


REFERENCES_HEADING = re.compile(r"^\s*(works cited|references|bibliography|citations?)\s*:?\s*$", re.I)


def clean_text(raw: str) -> str:
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n")]

    start = 0
    while start < len(lines) and len(lines[start].split()) < 10:
        start += 1
    if start == len(lines):
        start = 0
    lines = lines[start:]

    for i, ln in enumerate(lines):
        if REFERENCES_HEADING.match(ln) and i > len(lines) * 0.5:
            lines = lines[:i]
            break

    text = "\n".join(re.sub(r"[ \t ]+", " ", ln) for ln in lines)
    return re.sub(r"\n{2,}", "\n", text).strip()


def stable_id(text: str) -> str:
    return "n" + hashlib.sha1(re.sub(r"\s+", " ", text.lower()).encode()).hexdigest()[:8]


def load_and_dedupe() -> list[dict]:
    raw = json.loads(SOURCE.read_text())
    essays, seen = [], set()
    for item in raw:
        text = clean_text(item.get("text", ""))
        if len(text.split()) < 40:
            continue
        nid = stable_id(text)
        if nid in seen:
            continue
        seen.add(nid)
        essays.append({"id": nid, "text": text, "old_embedding": item.get("embedding")})

    vecs = np.asarray([e["old_embedding"] for e in essays], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    sims = vecs @ vecs.T
    drop = set()
    for i in range(len(essays)):
        for j in range(i + 1, len(essays)):
            if sims[i, j] > NEAR_DUPLICATE and i not in drop and j not in drop:
                shorter = i if len(essays[i]["text"]) < len(essays[j]["text"]) else j
                drop.add(shorter)
    kept = [e for k, e in enumerate(essays) if k not in drop]
    for e in kept:
        e.pop("old_embedding")
    print(f"Essays: {len(raw)} in source -> {len(kept)} after cleaning and removing duplicates")
    return kept


class Catalogue(BaseModel):
    title: str = Field(description="3-7 words naming the student's experience, not the assignment. No names.")
    preview: str = Field(description="One sentence, at most 30 words, starting 'A student', describing the personal experience they share.")
    concepts: list[str] = Field(description="2-6 sociological concepts or theories the essay applies, using their standard names.")
    has_personal_story: bool = Field(description="True if the student describes a specific experience from their own life or someone close to them.")
    story_strength: int = Field(description="1-5: how vivid and specific the personal story is. 1 = none, 5 = detailed, emotional anecdote.")


CATALOGUE_PROMPT = """You are cataloguing anonymized reflective essays written by students in an introductory sociology course at UNCW.
Future students will browse these essays to understand sociological concepts through their peers' lived experiences.

Read the essay and return:
- title: 3-7 words naming the student's experience (e.g. "Growing up between two cultures"), not the assignment. Never include anyone's name.
- preview: one sentence, at most 30 words, starting "A student", describing the personal experience they share. If there is no personal story, describe their argument instead. Never include names.
- concepts: 2-6 sociological concepts or theories the essay applies, using standard names (e.g. "Looking-Glass Self", "Beauty Myth", "Anomie", "Presentation of Self", "Collective Consciousness", "White Privilege", "Social Class").
- has_personal_story: does the student describe a specific experience from their own life or someone close to them?
- story_strength: 1-5, how vivid and specific that personal story is.

<essay>
{text}
</essay>"""


def catalogue_all(essays: list[dict]) -> None:
    cache = json.loads(META_CACHE.read_text()) if META_CACHE.exists() else {}
    todo = [e for e in essays if e["id"] not in cache]
    print(f"Cataloguing {len(todo)} essays with {FAST_MODEL} ({len(essays) - len(todo)} already cached)...")

    def work(essay):
        meta = gemini.generate_json(FAST_MODEL, CATALOGUE_PROMPT.format(text=essay["text"][:40000]), Catalogue)
        return essay["id"], meta.model_dump()

    done = 0
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(work, e) for e in todo]
        for fut in cf.as_completed(futures):
            try:
                nid, meta = fut.result()
                cache[nid] = meta
            except Exception as exc:
                print(f"  ! cataloguing failed: {exc}")
            done += 1
            if done % 25 == 0 or done == len(todo):
                META_CACHE.write_text(json.dumps(cache, indent=1))
                print(f"  {done}/{len(todo)}")

    missing = [e["id"] for e in essays if e["id"] not in cache]
    if missing:
        sys.exit(f"{len(missing)} essays are not catalogued yet. Run the script again to retry them.")
    for e in essays:
        m = cache[e["id"]]
        e.update(title=m["title"].strip(), preview=m["preview"].strip(), concepts=m["concepts"],
                 has_personal_story=m["has_personal_story"],
                 story_strength=max(1, min(5, int(m["story_strength"]))))


SENTENCE_SPLIT = re.compile(r"(?<=[.!?”\"])\s+|\n+")


def chunk_text(text: str) -> list[str]:
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(text) if s and s.strip()]
    chunks, current, count = [], [], 0
    for sent in sentences:
        current.append(sent)
        count += len(sent.split())
        if count >= CHUNK_WORDS:
            chunks.append(" ".join(current))
            overlap, words = [], 0
            for s in reversed(current):
                if words >= CHUNK_OVERLAP_WORDS:
                    break
                overlap.insert(0, s)
                words += len(s.split())
            current, count = overlap, words
    if current and (not chunks or count > CHUNK_OVERLAP_WORDS):
        chunks.append(" ".join(current))
    return chunks


def card_text(e: dict) -> str:
    return f"{e['title']}. {e['preview']} Concepts: {', '.join(e['concepts'])}."


def build_chunks(essays: list[dict]) -> list[dict]:
    chunks = []
    for e in essays:
        chunks.append({"narrative_id": e["id"], "kind": "card", "text": card_text(e)})
        for c in chunk_text(e["text"]):
            chunks.append({"narrative_id": e["id"], "kind": "text", "text": c})
    return chunks


def main():
    DATA_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    essays = load_and_dedupe()
    catalogue_all(essays)
    chunks = build_chunks(essays)
    print(f"Embedding {len(chunks)} passages with {EMBED_MODEL} ({EMBED_DIM} dims)...")
    vectors = gemini.embed([c["text"] for c in chunks], "RETRIEVAL_DOCUMENT", EMBED_MODEL, EMBED_DIM)

    narratives = [{"id": e["id"], "title": e["title"], "preview": e["preview"], "concepts": e["concepts"],
                   "has_personal_story": e["has_personal_story"], "story_strength": e["story_strength"],
                   "words": len(e["text"].split()), "text": e["text"]} for e in essays]
    (DATA_DIR / "narratives.json").write_text(json.dumps(narratives, indent=1, ensure_ascii=False))
    (DATA_DIR / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False))
    np.save(DATA_DIR / "chunk_embeddings.npy", vectors)
    (DATA_DIR / "index_meta.json").write_text(json.dumps({
        "embed_model": EMBED_MODEL, "embed_dim": EMBED_DIM, "narratives": len(narratives),
        "chunks": len(chunks), "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=1))
    print(f"Done in {time.time() - t0:.0f}s: {len(narratives)} narratives, {len(chunks)} passages -> {DATA_DIR}")


if __name__ == "__main__":
    main()
