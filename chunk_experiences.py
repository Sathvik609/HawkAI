"""Split narratives into chunks, each labeled with the student experience it describes."""
import argparse
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from pydantic import BaseModel

import gemini
from build_index import PAGE_HEADER
from config import DATA_DIR, GEN_MODEL


class Chunk(BaseModel):
    first: int
    last: int
    kind: Literal["experience", "reflection"]
    label: str
    what_happened: str
    feelings: str
    people: str
    life_area: str


class Chunks(BaseModel):
    chunks: list[Chunk]


PROMPT = """You are helping sociology researchers at UNCW. Below is a narrative written by a UNCW student, split into numbered sentences. Paragraph breaks are marked with a blank line.

Split the whole narrative into chunks. Each chunk is a run of consecutive sentences.
- An "experience" chunk is one specific thing the student lived through or saw happen in their own life: one event, situation or period, with the people and setting involved. Start a new chunk when the student moves to a different event, time or relationship.
- A "reflection" chunk is anything else: the introduction, explaining a sociological concept, general claims about society, or the conclusion. This course itself (class discussions, readings, videos shown in class, writing this essay) and reactions to a book, film or the news are reflection, not experience. Thinking through a reading, theory or thought experiment is reflection even when the student describes their own feelings or opinions about it. So is work or career background on its own (having a job, career plans, a family member's job history) unless something happened there. Join neighbouring reflection sentences into one chunk.

Each experience chunk must make sense when read on its own, without the chunks around it:
- Keep the whole event together, from the sentence where the student starts telling it to the sentence where they finish it.
- End a chunk only at the end of a thought, never right after a sentence that announces something still to come.

Every sentence must belong to exactly one chunk, in order, with no gaps or overlaps. The first chunk starts at sentence 1 and the last chunk ends at sentence {count}.

For each chunk give:
- first, last: its first and last sentence numbers
- kind: "experience" or "reflection"
- label: a short plain title for what happened (under 10 words), or what the reflection is about
- what_happened: one or two plain sentences on what the student experienced. For a reflection chunk, what the student is explaining
- feelings: the feelings the student says they had, in a few words, or "not stated". Do not guess feelings the student does not express
- people: who is involved (for example "mother, younger brother"), or "none"
- life_area: one of family, school, work, friends, dating, sports, religion, health, community, online, other

Only use what the narrative says. Never invent details. Call the writer "the student".

Narrative:

{numbered}"""


def paragraphs(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip() and not PAGE_HEADER.fullmatch(ln.strip())]
    lens = sorted(len(ln) for ln in lines)
    median = lens[len(lens) // 2] if lens else 0
    if median > 150:
        return lines
    out, cur = [], ""
    for line in lines:
        cur = line if not cur else cur + line if re.search(r"[A-Za-z]-$", cur) else f"{cur} {line}"
        if re.search(r"[.!?][\"”’)]?$", line) and len(line) < median * 0.8:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


SENTENCE_END = re.compile(r"(?<=[.!?])[\"”’)]*\s+(?=[\"“‘(]?[A-Z0-9])")


def sentences(text: str) -> list[list[str]]:
    result = []
    for para in paragraphs(text):
        parts, start = [], 0
        for m in SENTENCE_END.finditer(para):
            parts.append(para[start:m.end()].strip())
            start = m.end()
        parts.append(para[start:].strip())
        result.append([p for p in parts if p])
    return result


BACK_REF = re.compile(r"\W*(this|these|that|those|such)\b"
                      r"|.*\b(this|these) (idea|statistic|theor\w*|concept|part|approach|point|example)s?\b", re.I)
PRONOUN_START = re.compile(r"\W*(it|they|this|these)\b", re.I)


def pull_setup_sentence(chunks: list[Chunk], flat: list[str]) -> None:
    for prev, c in zip(chunks, chunks[1:]):
        if c.kind != "experience" or prev.kind != "reflection" or not BACK_REF.match(flat[c.first - 1]):
            continue
        for _ in range(2):
            if prev.last == prev.first:
                break
            prev.last -= 1
            c.first -= 1
            if not PRONOUN_START.match(flat[c.first - 1]):
                break


def valid(chunks: list[Chunk], count: int) -> bool:
    expected = 1
    for c in chunks:
        if c.first != expected or c.last < c.first:
            return False
        expected = c.last + 1
    return expected == count + 1


RUNS = 3


def one_run(prompt: str, flat: list[str], nid: str) -> list[Chunk]:
    for _ in range(3):
        result = gemini.generate_json(GEN_MODEL, prompt, Chunks, budget=1024).chunks
        if valid(result, len(flat)):
            pull_setup_sentence(result, flat)
            return result
    raise ValueError(f"{nid}: chunks did not cover the narrative")


def most_agreed(runs: list[list[Chunk]], count: int) -> list[Chunk]:
    def experience(run):
        return [any(c.kind == "experience" and c.first <= i <= c.last for c in run) for i in range(1, count + 1)]

    def edges(run):
        return {c.first for c in run}

    flags = [experience(r) for r in runs]
    majority = [sum(f[i] for f in flags) * 2 > len(runs) for i in range(count)]

    def distance(k):
        kind_miss = sum(a != b for a, b in zip(flags[k], majority))
        edge_miss = sum(len(edges(runs[k]) ^ edges(r)) for r in runs)
        return kind_miss * 100 + edge_miss

    return runs[min(range(len(runs)), key=distance)]


def chunk(narrative: dict) -> dict:
    paras = sentences(narrative["text"])
    flat, numbered, para_of = [], [], []
    for p, para in enumerate(paras):
        lines = []
        for s in para:
            flat.append(s)
            para_of.append(p)
            lines.append(f"[{len(flat)}] {s}")
        numbered.append("\n".join(lines))
    prompt = PROMPT.format(count=len(flat), numbered="\n\n".join(numbered))
    runs = [one_run(prompt, flat, narrative["id"]) for _ in range(RUNS)]
    result = most_agreed(runs, len(flat))
    out = []
    for n, c in enumerate(result, 1):
        text, last_para = "", None
        for i in range(c.first - 1, c.last):
            text += ("" if last_para is None else "\n\n" if para_of[i] != last_para else " ") + flat[i]
            last_para = para_of[i]
        out.append({"chunk": n, "sentences": [c.first, c.last], "text": text, **c.model_dump(exclude={"first", "last"})})
    return {"id": narrative["id"], "title": narrative["title"], "words": narrative.get("words"), "chunks": out}


ALL_FILE = DATA_DIR / "experience_chunks.json"
SAMPLE_FILE = DATA_DIR / "experience_chunks_sample.json"


def chunk_all(workers: int) -> None:
    narratives = json.loads((DATA_DIR / "narratives.json").read_text())
    done = json.loads(ALL_FILE.read_text())["narratives"] if ALL_FILE.exists() else {}
    current = {n["id"] for n in narratives}
    done = {k: v for k, v in done.items() if k in current}
    todo = [n for n in narratives if n["id"] not in done]
    print(f"Chunking {len(todo)} narratives ({len(done)} already done, {RUNS} runs each)...")

    def save():
        ALL_FILE.write_text(json.dumps({"model": GEN_MODEL, "runs": RUNS, "narratives": done}, ensure_ascii=False))

    failed = 0
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(chunk, n): n["id"] for n in todo}
        for k, fut in enumerate(as_completed(futures), 1):
            try:
                done[futures[fut]] = fut.result()
            except Exception as exc:
                failed += 1
                print(f"  ! {futures[fut]}: {exc}")
            if k % 10 == 0 or k == len(todo):
                save()
                print(f"  {k}/{len(todo)}", flush=True)
    save()
    print(f"Done: {len(done)}/{len(narratives)} chunked, {failed} failed" + (" (run again to retry)" if failed else ""))


def draw_sample(count: int, seed: int) -> None:
    done = json.loads(ALL_FILE.read_text())["narratives"]
    checks = {c["id"]: c for c in json.loads((DATA_DIR / "experience_check.json").read_text())}
    pool = sorted(i for i in done if checks.get(i, {}).get("level") == "clear")
    picked = random.Random(seed).sample(pool, count)
    results = []
    for nid in picked:
        c = checks[nid]
        results.append({**done[nid], "experience_level": c["level"], "narrative_type": c.get("narrative_type"),
                        "experience_evidence": c.get("evidence")})
    SAMPLE_FILE.write_text(json.dumps({"seed": seed, "model": GEN_MODEL, "clear_only": True, "pool": len(pool),
                                       "narratives": results}, indent=2, ensure_ascii=False))
    for r in results:
        kinds = [c["kind"] for c in r["chunks"]]
        print(f"{r['id']}  {r['title'][:50]:50}  {kinds.count('experience')} experiences, {kinds.count('reflection')} reflections")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="chunk every narrative into data/experience_chunks.json")
    ap.add_argument("--sample", action="store_true", help="draw the review sample from data/experience_chunks.json")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    if args.all:
        chunk_all(args.workers)
    if args.sample:
        draw_sample(args.count, args.seed)
    if not (args.all or args.sample):
        ap.print_help()


if __name__ == "__main__":
    main()
