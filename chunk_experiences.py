"""Split a random sample of narratives into chunks, each labeled with the student experience it describes."""
import argparse
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

import gemini
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
- A "reflection" chunk is anything else: the introduction, explaining a sociological concept, general claims about society, or the conclusion. This course itself (class discussions, readings, videos shown in class, writing this essay) and reactions to a book, film or the news are reflection, not experience. So is work or career background on its own (having a job, career plans, a family member's job history) unless something happened there. Join neighbouring reflection sentences into one chunk.

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
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
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


def valid(chunks: list[Chunk], count: int) -> bool:
    expected = 1
    for c in chunks:
        if c.first != expected or c.last < c.first:
            return False
        expected = c.last + 1
    return expected == count + 1


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
    for _ in range(3):
        result = gemini.generate_json(GEN_MODEL, prompt, Chunks, budget=4096).chunks
        if valid(result, len(flat)):
            break
    else:
        raise ValueError(f"{narrative['id']}: chunks did not cover the narrative")
    out = []
    for n, c in enumerate(result, 1):
        text, last_para = "", None
        for i in range(c.first - 1, c.last):
            text += ("" if last_para is None else "\n\n" if para_of[i] != last_para else " ") + flat[i]
            last_para = para_of[i]
        out.append({"chunk": n, "sentences": [c.first, c.last], "text": text, **c.model_dump(exclude={"first", "last"})})
    return {"id": narrative["id"], "title": narrative["title"], "words": narrative.get("words"), "chunks": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=str(DATA_DIR / "experience_chunks_sample.json"))
    ap.add_argument("--clear-only", action="store_true", help="sample only narratives with a clear student experience")
    args = ap.parse_args()
    narratives = json.loads((DATA_DIR / "narratives.json").read_text())
    if args.clear_only:
        checks = json.loads((DATA_DIR / "experience_check.json").read_text())
        clear = {c["id"] for c in checks if c["level"] == "clear"}
        narratives = [n for n in narratives if n["id"] in clear]
    sample = random.Random(args.seed).sample(narratives, args.count)
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(chunk, sample))
    with open(args.out, "w") as f:
        json.dump({"seed": args.seed, "model": GEN_MODEL, "clear_only": args.clear_only, "pool": len(narratives), "narratives": results}, f, indent=2, ensure_ascii=False)
    for r in results:
        kinds = [c["kind"] for c in r["chunks"]]
        print(f"{r['id']}  {r['title'][:50]:50}  {kinds.count('experience')} experiences, {kinds.count('reflection')} reflections")


if __name__ == "__main__":
    main()
