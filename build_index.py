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


PAGE_HEADER = re.compile(r"\d{1,3}|[A-Z][A-Za-z'’-]+( [A-Z][A-Za-z'’-]+)? \d{1,3}")


def strip_page_headers(text: str) -> str:
    return "\n".join(ln for ln in text.split("\n") if not PAGE_HEADER.fullmatch(ln.strip()))


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
        text = strip_page_headers(text)
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


PIECE_WORDS = 250
EXPERIENCE_MAX_WORDS = 500


def split_run(sents: list[str], paras: list[int], target: int) -> list[list[int]]:
    words = [len(s.split()) for s in sents]
    total = sum(words)
    parts = max(1, round(total / target))
    if parts == 1:
        return [list(range(len(sents)))]
    goal = total / parts

    def heading(j):
        return words[j] < 10 and not re.search(r"[.!?][\"”’)]*$", sents[j])

    cuts, run = [], 0
    for i in range(len(sents) - 1):
        run += words[i]
        if len(cuts) < parts - 1 and run >= goal * (len(cuts) + 1) - words[i] / 2:
            ok = [j for j in range(max(0, i - 3), min(len(sents) - 1, i + 4)) if not heading(j)]
            near = [j for j in ok if paras[j] != paras[j + 1]] or ok or [i]
            cut = min(near, key=lambda j: abs(j - i))
            if not cuts or cut > cuts[-1]:
                cuts.append(cut)
    bounds = [-1, *cuts, len(sents) - 1]
    return [list(range(a + 1, b + 1)) for a, b in zip(bounds, bounds[1:]) if b > a]


def join_sentences(sents: list[str], paras: list[int], idx: list[int]) -> str:
    text = ""
    for k, i in enumerate(idx):
        text += ("" if k == 0 else "\n\n" if paras[i] != paras[idx[k - 1]] else " ") + sents[i]
    return text


def build_chunks(essays: list[dict]) -> list[dict]:
    from chunk_experiences import ALL_FILE, sentences
    semantic = json.loads(ALL_FILE.read_text())["narratives"] if ALL_FILE.exists() else {}
    chunks, missing = [], 0
    for e in essays:
        chunks.append({"narrative_id": e["id"], "kind": "card", "text": card_text(e)})
        groups = sentences(e["text"])
        sents = [s for g in groups for s in g]
        paras = [p for p, g in enumerate(groups) for _ in g]
        plan = semantic.get(e["id"], {}).get("chunks")
        if not plan or plan[-1]["sentences"][1] != len(sents):
            missing += 1
            plan = [{"kind": "reflection", "label": "", "what_happened": "", "sentences": [1, len(sents)]}]
        for c in plan:
            a, b = c["sentences"]
            idx = list(range(a - 1, b))
            words = sum(len(sents[i].split()) for i in idx)
            if c["kind"] == "experience" and words <= EXPERIENCE_MAX_WORDS:
                pieces = [idx]
            else:
                target = EXPERIENCE_MAX_WORDS if c["kind"] == "experience" else PIECE_WORDS
                pieces = [[idx[j] for j in part] for part in split_run([sents[i] for i in idx], [paras[i] for i in idx], target)]
            for part in pieces:
                chunks.append({"narrative_id": e["id"], "kind": c["kind"], "label": c["label"],
                               "what_happened": c["what_happened"] if c["kind"] == "experience" else "",
                               "sentences": [part[0] + 1, part[-1] + 1],
                               "text": join_sentences(sents, paras, part)})
    if missing:
        print(f"  ! {missing} narratives had no experience chunks yet and were split by length only")
    return chunks


def card_text(e: dict) -> str:
    return f"{e['title']}. {e['preview']} Concepts: {', '.join(e['concepts'])}."


def embed_text(c: dict) -> str:
    if c["kind"] == "card":
        return c["text"]
    head = ". ".join(x for x in (c.get("label"), c.get("what_happened")) if x)
    return f"{head}\n\n{c['text']}" if head else c["text"]


def main():
    DATA_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    essays = load_and_dedupe()
    catalogue_all(essays)
    chunks = build_chunks(essays)
    print(f"Embedding {len(chunks)} passages with {EMBED_MODEL} ({EMBED_DIM} dims)...")
    vectors = gemini.embed([embed_text(c) for c in chunks], "RETRIEVAL_DOCUMENT", EMBED_MODEL, EMBED_DIM)

    narratives = [{"id": e["id"], "title": e["title"], "preview": e["preview"], "concepts": e["concepts"],
                   "has_personal_story": e["has_personal_story"], "story_strength": e["story_strength"],
                   "words": len(e["text"].split()), "text": e["text"]} for e in essays]
    grades = DATA_DIR / "experience_check.json"
    if grades.exists():
        from experience_check import apply_grades
        apply_grades(narratives, grades)
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
