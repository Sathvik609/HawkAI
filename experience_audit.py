"""Second, recall-focused pass: list every passage where the student describes their own life."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

import gemini
from config import DATA_DIR, GEN_MODEL
from generation import _match, _normalise


class Passage(BaseModel):
    quote: str
    whose: Literal["the student", "someone close to the student"]
    specific: bool
    about: str


class Passages(BaseModel):
    passages: list[Passage]


PROMPT = """Below is a narrative written by a UNCW student for a sociology course.

List every passage where the student tells about their OWN life: something that happened to them, or something they saw happen to family, friends, classmates or coworkers. Include short mentions too. Do not include: retelling a book, film, article or case study; hypothetical or "imagine" examples; general statements about people or society; the student's opinions.

For each passage give:
- quote: one sentence from the passage, copied exactly, word for word
- whose: "the student" or "someone close to the student"
- specific: true if it describes a particular event, situation or period (who, where, what happened). false if it is only a general feeling or habit ("social media makes me feel insecure")
- about: under 10 words on what happened

Return an empty list if there are none.

Narrative:

{text}"""

FIRST_PERSON = re.compile(
    r"\b(when I|I was|I had|I remember|I grew up|growing up|my (mom|mother|dad|father|parents|brother|sister|family|friend|friends|"
    r"grand\w+|aunt|uncle|cousin|boss|coach|teacher|boyfriend|girlfriend|roommate)|in (middle|high) school|"
    r"my (freshman|senior|junior|sophomore) year|at my (job|work|school))\b", re.I)


def personal_sentences(text: str) -> int:
    return sum(bool(FIRST_PERSON.search(s)) for s in re.split(r"(?<=[.!?])\s+", text))


def audit(n: dict) -> dict:
    found = gemini.generate_json(GEN_MODEL, PROMPT.format(text=n["text"]), Passages, budget=1024).passages
    source = _normalise(n["text"])
    passages = [{**p.model_dump(), "verified": _match(_normalise(p.quote), source) != "unverified"} for p in found]
    return {"id": n["id"], "personal_sentences": personal_sentences(n["text"]), "passages": passages}


def main():
    narratives = json.loads((DATA_DIR / "narratives.json").read_text())
    partial = DATA_DIR / "experience_audit.partial.jsonl"
    done = {}
    if partial.exists():
        for line in partial.read_text().splitlines():
            row = json.loads(line)
            done[row["id"]] = row
    todo = [n for n in narratives if n["id"] not in done]
    print(f"{len(done)} cached, {len(todo)} to check", flush=True)
    with ThreadPoolExecutor(8) as pool, partial.open("a") as f:
        for i, row in enumerate(pool.map(audit, todo), 1):
            done[row["id"]] = row
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            if i % 50 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    results = [done[n["id"]] for n in narratives]
    (DATA_DIR / "experience_audit.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    partial.unlink()
    print("done", len(results), flush=True)


if __name__ == "__main__":
    main()
