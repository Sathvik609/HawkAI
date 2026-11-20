import concurrent.futures as cf
import json
import re

from pydantic import BaseModel, Field

import gemini
from config import DATA_DIR, LITE_MODEL

OUT = DATA_DIR / "content_notes.json"

TOPICS = {
    "suicide": r"suicid|kill (my|him|her|them)sel|(take|took|end|ended) (my|his|her|their) (own )?life",
    "self_harm": r"self[- ]?harm|self[- ]?injur|cut(ting)? (my|him|her|them)sel",
    "eating_disorders": r"eating disorder|anorexi|bulimi|\bbing(e|ing)\b|purg(e|ing)\b|starv(e|ed|ing) (my|him|her|them)sel",
    "sexual_violence": r"sexual(ly)? assault|\brap(e|ed|ist)\b|molest|sexual abuse",
    "abuse": r"\babus(e|ed|ive|er)\b|domestic violence|beat (me|her|him)\b",
    "addiction": r"overdos|addict|alcoholi|\bopioid|\brehab\b",
}

LABELS = {
    "suicide": "suicide",
    "self_harm": "self-harm",
    "eating_disorders": "eating disorders",
    "sexual_violence": "sexual violence",
    "abuse": "abuse",
    "addiction": "addiction",
}

PROMPT = """You are writing content notes for essays that first-year college students will read.

For each topic below, answer true only if the essay describes it happening to a real person (the author, someone they know,
or a public figure) or discusses it for more than a passing sentence. Answer false for a single passing statistic, a book or
theory title, or figurative use such as "addicted to my phone" or "abuse of power".

- suicide: suicide, suicidal thoughts or attempts
- self_harm: self-harm or self-injury
- eating_disorders: anorexia, bulimia, binge eating or other disordered eating
- sexual_violence: rape, sexual assault or sexual abuse
- abuse: physical or emotional abuse of a person, including child abuse and partner violence
- addiction: drug or alcohol addiction or overdose

<essay>
{text}
</essay>"""


class Notes(BaseModel):
    suicide: bool = Field(description="Essay describes or discusses suicide.")
    self_harm: bool = Field(description="Essay describes or discusses self-harm.")
    eating_disorders: bool = Field(description="Essay describes or discusses eating disorders.")
    sexual_violence: bool = Field(description="Essay describes or discusses sexual violence.")
    abuse: bool = Field(description="Essay describes or discusses abuse of a person.")
    addiction: bool = Field(description="Essay describes or discusses drug or alcohol addiction.")


def candidates(text: str) -> set[str]:
    return {t for t, p in TOPICS.items() if re.search(p, text, re.I)}


def classify(narrative: dict) -> list[str]:
    hits = candidates(narrative["text"])
    if not hits:
        return []
    notes = gemini.generate_json(LITE_MODEL, PROMPT.format(text=narrative["text"]), Notes)
    return [LABELS[t] for t in TOPICS if t in hits and getattr(notes, t)]


def main():
    narratives = json.loads((DATA_DIR / "narratives.json").read_text())
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [n for n in narratives if n["id"] not in done]
    print(f"{len(todo)} essays to check, {sum(1 for n in todo if candidates(n['text']))} need the model")
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for n, notes in zip(todo, pool.map(classify, todo)):
            done[n["id"]] = notes
            if notes:
                print(f"  {n['title'][:50]:50s} {', '.join(notes)}")
    OUT.write_text(json.dumps({k: v for k, v in done.items()}, indent=1))
    print(f"{sum(1 for v in done.values() if v)} of {len(done)} essays have a content note")


if __name__ == "__main__":
    main()
