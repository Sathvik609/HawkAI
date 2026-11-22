"""Grade every narrative for how much lived student experience it contains: clear, brief or none."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

import gemini
from config import DATA_DIR, GEN_MODEL
from generation import _match, _normalise


class Grade(BaseModel):
    level: Literal["clear", "brief", "none"]
    narrative_type: Literal["personal experience", "book or film response", "concept essay", "other"]
    experiences: list[str]
    evidence: str
    story_strength: int


PROMPT = """Below is a narrative written by a UNCW student for a sociology course. Decide how much of the student's own lived experience it contains.

What counts as lived experience: something from the student's life outside this course. Either something that happened to them, or something they saw happen to family, friends, classmates or coworkers. One or two sentences can be enough if they tell a real event or period (for example "When I got pulled over on the highway I was not afraid of the police").

What does NOT count:
- this course itself: class discussions, readings, videos shown in class, writing this essay
- reading a book, watching a film, or hearing the news, and the student's reaction to it
- hypothetical or "imagine" examples, other people's stories the student only read about
- general statements about society, and the student's opinions
- work or career background on its own: having a job, listing jobs, career plans, or a family member's job history. A job counts only when something happened there to the student or in front of them (how they were treated, a conflict, an incident)
- family background facts the student did not live through (for example a parent's life before the student was born)

level:
- "clear": tells at least one real event, situation or period from the student's life, with who, where or what happened
- "brief": only passing personal mentions or general feelings and habits ("social media makes me insecure", "growing up I noticed this"), no event actually told
- "none": no lived experience at all

Also give:
- narrative_type: "personal experience", "book or film response", "concept essay" or "other"
- experiences: a short plain label (under 10 words) for each separate experience, in order. Empty if level is "none"
- evidence: for "clear" or "brief", one sentence copied word for word from the narrative that shows it. Otherwise ""
- story_strength: 1 to 5. 1 = no experience, 2 = brief mention, 3 = one experience told plainly, 4 = experiences told with detail, 5 = detailed, emotional experiences at the heart of the narrative

Narrative:

{text}"""


def grade(n: dict) -> dict:
    g = gemini.generate_json(GEN_MODEL, PROMPT.format(text=n["text"]), Grade, budget=1024)
    found = bool(g.evidence) and _match(_normalise(g.evidence), _normalise(n["text"])) != "unverified"
    return {"id": n["id"], "title": n["title"], "words": int(n.get("words") or 0),
            "old_flag": str(n.get("has_personal_story")) == "True", "old_strength": int(n.get("story_strength") or 1),
            **g.model_dump(), "story_strength": max(1, min(5, g.story_strength)), "evidence_found": found}


def apply_grades(narratives: list[dict], grades_file) -> int:
    grades = {g["id"]: g for g in json.loads(open(grades_file).read())}
    overrides = DATA_DIR / "experience_overrides.json"
    if overrides.exists():
        for i, o in json.loads(overrides.read_text()).items():
            if i in grades:
                grades[i] = {**grades[i], **o}
    changed = 0
    for n in narratives:
        g = grades.get(n["id"])
        if not g:
            continue
        new = {"experience": g["level"], "has_personal_story": g["level"] == "clear", "story_strength": g["story_strength"]}
        changed += any(n.get(k) != v for k, v in new.items())
        n.update(new)
    return changed


def apply(narratives_file, grades_file) -> int:
    narratives = json.loads(open(narratives_file).read())
    changed = apply_grades(narratives, grades_file)
    with open(narratives_file, "w") as f:
        json.dump(narratives, f, indent=1, ensure_ascii=False)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA_DIR / "experience_check.json"))
    ap.add_argument("--apply", action="store_true", help="write the saved grades into narratives.json")
    args = ap.parse_args()
    if args.apply:
        print(f"updated {apply(DATA_DIR / 'narratives.json', args.out)} narratives")
        return
    narratives = json.loads((DATA_DIR / "narratives.json").read_text())
    partial = DATA_DIR / "experience_check.partial.jsonl"
    done = {}
    if partial.exists():
        for line in partial.read_text().splitlines():
            row = json.loads(line)
            done[row["id"]] = row
    todo = [n for n in narratives if n["id"] not in done]
    print(f"{len(done)} cached, {len(todo)} to check", flush=True)
    with ThreadPoolExecutor(8) as pool, partial.open("a") as f:
        for i, row in enumerate(pool.map(grade, todo), 1):
            done[row["id"]] = row
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            if i % 100 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    results = [done[n["id"]] for n in narratives]
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    partial.unlink()
    counts = {k: sum(r["level"] == k for r in results) for k in ("clear", "brief", "none")}
    print("done", counts, flush=True)


if __name__ == "__main__":
    main()
