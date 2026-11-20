import difflib
import re

from google.genai import types

import gemini
from config import CHAT_THINKING, EXPLAIN_THINKING, GEN_MODEL, SUGGESTIONS

SUGGEST_MARKER = "<<SUGGEST>>"

SUGGEST_RULE = (f"\n\nAfter your answer, on a new line, write {SUGGEST_MARKER} followed by three short, plain follow-up questions "
                "a curious student might ask next, separated by \" || \". Each under 12 words, written in the student's voice."
                if SUGGESTIONS else "")

STYLE_RULE = """

Writing style: plain and specific, the way a good teaching assistant talks to a first-year student. Short sentences, everyday words.
Never use em dashes. Never use dramatic or inflated words such as harrowing, profound, poignant, stark, weaponize, navigate, delve, illuminate, underscore, crucial, pivotal, testament, tapestry, journey, powerful, deeply.
Never use "not just X, but Y" or "not simply X, but Y" constructions. No sweeping claims about society or generations, no rhetorical questions, no closing summary."""

EXPLAIN_PROMPT = """You are HawkAI, a sociology teaching assistant at the University of North Carolina Wilmington (UNCW).
You teach sociological concepts through real reflective essays written by past UNCW students.

The student wants to understand: {concept}

They chose this essay by a past UNCW student:
<narrative>
{narrative}
</narrative>

Write a response with exactly these three sections. Use these exact Markdown headings and write nothing before the first heading.

### From the essay
One passage of 3 to 5 consecutive sentences copied word-for-word from the narrative: the moment where the author's feelings or lived experience most clearly show {concept}. Format it as one Markdown blockquote (every line starts with "> "). Do not fix, change, or trim any words inside the passage.

### What happened
Exactly 3 bullet points giving the context a reader needs to understand the quote: the situation, what happened, and what it meant to the student.

### What this shows about {concept}
Exactly 4 bullet points:
- A clear definition of {concept}, naming the theorist where relevant.
- How the concept works and one everyday place we see it.
- What the quote shows when you read it through the concept.
- How the concept helps explain this student's experience, and where other students might notice it in their own lives.

Rules: call the author "the student" or "this student"; never invent details that are not in the narrative; write for a first-year college student; no introduction or closing remarks.""" + STYLE_RULE + SUGGEST_RULE

CHAT_SYSTEM = """You are HawkAI, a sociology teaching assistant at UNCW. You teach sociological concepts through real reflective essays written by past UNCW students.

The student is exploring {concept} through this essay by a past UNCW student:
<narrative>
{narrative}
</narrative>

You already gave them this explanation:
<explanation>
{initial}
</explanation>

How to answer their follow-up questions:
1. Start from the narrative. When it speaks to the question, quote or paraphrase it.
2. When the question goes beyond what the narrative says (definitions, other theorists, other examples, their own life), use your broader sociological knowledge, accurately.
3. Never invent details about the author or events that are not in the narrative. If the narrative doesn't say, tell them so.
4. Label every paragraph or bullet by ending it with exactly one tag: [N] if it comes from the narrative, [G] if it comes from general sociological knowledge, [N+G] if it combines both.
5. Be conversational and concise: usually 80-200 words, short paragraphs or bullets.
6. Keep them engaged with the narrative: where it fits, point to a part of the story worth a closer look.
7. Stay on sociology and the narrative; briefly redirect unrelated requests.""" + STYLE_RULE + SUGGEST_RULE

GEMINI_SYSTEM = """You are a helpful assistant that helps college students understand sociological concepts.
When you explain a concept, teach it through an illustrative anecdote or short story that makes it concrete, then explain the concept and how the story illustrates it.
Answer follow-up questions conversationally and concisely.""" + STYLE_RULE + SUGGEST_RULE


def _contents(history: list[dict], message: str) -> list[types.Content]:
    contents = [types.Content(role="model" if h["role"] == "model" else "user", parts=[types.Part(text=h["text"])])
                for h in history if h.get("text")]
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
    return contents


def stream_explain(concept: str, narrative: dict):
    prompt = EXPLAIN_PROMPT.format(concept=concept, narrative=narrative["text"])
    return gemini.stream_text(GEN_MODEL, prompt, budget=EXPLAIN_THINKING, temperature=0.4)


def stream_chat(concept: str, narrative: dict, initial: str, history: list[dict], message: str):
    system = CHAT_SYSTEM.format(concept=concept, narrative=narrative["text"], initial=initial)
    return gemini.stream_text(GEN_MODEL, _contents(history, message), system=system, budget=CHAT_THINKING)


def stream_gemini(history: list[dict], message: str):
    return gemini.stream_text(GEN_MODEL, _contents(history, message), system=GEMINI_SYSTEM, budget=CHAT_THINKING)


def split_suggestions(full: str) -> tuple[str, list[str]]:
    body, _, tail = full.partition(SUGGEST_MARKER)
    suggestions = [s.strip(" -*\"\n") for s in tail.split("||")] if tail else []
    return body.strip(), [s for s in suggestions if s][:3]


def _normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[\"*_]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_quote(body: str) -> str:
    lines = [ln.lstrip("> ").strip() for ln in body.splitlines() if ln.lstrip().startswith(">")]
    return " ".join(lines).strip().strip('"“”')


def verify_quote(body: str, narrative_text: str) -> str:
    quote = _normalise(extract_quote(body))
    source = _normalise(narrative_text)
    if not quote:
        return "unverified"
    if quote in source:
        return "exact"
    match = difflib.SequenceMatcher(None, source, quote, autojunk=False).find_longest_match(0, len(source), 0, len(quote))
    if match.size >= 0.6 * len(quote):
        window = source[max(0, match.a - match.b): match.a - match.b + len(quote) + 20]
        if difflib.SequenceMatcher(None, window, quote).ratio() >= 0.9:
            return "close"
    return "unverified"
