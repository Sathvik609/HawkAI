import concurrent.futures as cf
import json
import math
import re
import threading
from collections import Counter, defaultdict

import numpy as np
from pydantic import BaseModel, Field

import gemini
from config import CACHE_DIR, DATA_DIR, FAST_MODEL, LITE_MODEL, MIN_RELEVANCE, NUM_CANDIDATES, NUM_OPTIONS

STOPWORDS = set("""a an the and or of to in on for with as by at from is are was were be been being this that these those
it its i me my we our us you your he him she her they them their his not no but if so than then there here what which
who whom how why when where do does did have has had can could would should will just about into over under more most
very also such one all any some each other own same too only out up down off again further once am im ive id dont""".split())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    words = [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower().replace("’", "'").replace("'s ", " "))
             if w not in STOPWORDS and len(w) > 1]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        postings = defaultdict(lambda: ([], []))
        lengths = []
        for i, doc in enumerate(docs):
            tokens = tokenize(doc)
            lengths.append(len(tokens))
            for term, tf in Counter(tokens).items():
                postings[term][0].append(i)
                postings[term][1].append(tf)
        self.n = len(docs)
        self.lengths = np.asarray(lengths, dtype=np.float32)
        self.avg = float(self.lengths.mean()) if self.n else 1.0
        self.postings = {t: (np.asarray(ids), np.asarray(tfs, dtype=np.float32)) for t, (ids, tfs) in postings.items()}

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float32)
        for term in set(tokenize(query)):
            if term not in self.postings:
                continue
            ids, tf = self.postings[term]
            idf = math.log(1 + (self.n - len(ids) + 0.5) / (len(ids) + 0.5))
            norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[ids] / self.avg)
            out[ids] += idf * tf * (self.k1 + 1) / norm
        return out


class QueryUnderstanding(BaseModel):
    is_sociology_topic: bool = Field(description="False if the request is not about a sociological concept, issue or experience.")
    concept: str = Field(description="The standard name of the concept the student wants to understand.")
    definition: str = Field(description="One plain, neutral sentence under 30 words defining the concept, naming the theorist if there is one. No dramatic words.")
    keywords: list[str] = Field(description="6-12 words or short phrases an essay about this concept would use, including key theorists.")
    experience_sketch: str = Field(description="2-3 sentences, written in first person as a college student, describing a lived experience that shows the concept.")


class RankedOption(BaseModel):
    id: str
    relevance: int = Field(description="1-10: how well this narrative helps a student understand the concept through lived experience.")
    title: str = Field(description="3-6 plain words saying what the essay is about, the way a librarian would label it, e.g. 'Swim team and an eating disorder'. Sentence case. No names, no dramatic words.")
    preview: str = Field(description="One plain sentence, at most 25 words, saying what happened to the student. Start with the event itself, not with 'A student'. No dramatic words, no em dashes.")


class Reranked(BaseModel):
    options: list[RankedOption]


UNDERSTAND_PROMPT = """A student using HawkAI (a tool that teaches sociology through essays written by past UNCW students) typed:
"{query}"

Work out which sociological concept they want to understand and describe it for a search engine."""

RERANK_PROMPT = """You are choosing which reflective essays by past UNCW sociology students to offer a current student
who wants to understand: {concept} ({definition})

Candidates (each with its most relevant passages):
{candidates}

Choose up to {k} narratives that would best help a first-year student understand {concept} through a real lived experience.
- The essay must genuinely engage with {concept} or clearly illustrate it.
- Prefer vivid, specific personal stories over abstract summaries of readings.
- Prefer variety: different kinds of experiences, not near-identical stories.
- Score relevance 1-10 honestly. Leave out anything below {min_rel}.
- Order from best to worst. Use the candidate ids exactly as given.
- Write the title and preview about what happened to this student, in plain, factual language a student would use. Never use names. Do not spoil the whole story.
- Never use em dashes or inflated words such as harrowing, profound, poignant, weaponize, navigate, journey, struggle with, grapple."""


class NarrativeIndex:
    def __init__(self, data_dir=DATA_DIR):
        self.meta = json.loads((data_dir / "index_meta.json").read_text())
        self.narratives = json.loads((data_dir / "narratives.json").read_text())
        self.by_id = {n["id"]: n for n in self.narratives}
        self.row = {n["id"]: i for i, n in enumerate(self.narratives)}
        chunks = json.loads((data_dir / "chunks.json").read_text())
        self.chunk_text = [c["text"] for c in chunks]
        self.chunk_is_text = np.asarray([c["kind"] == "text" for c in chunks])
        self.chunk_owner = np.asarray([self.row[c["narrative_id"]] for c in chunks])
        self.vectors = np.load(data_dir / "chunk_embeddings.npy")
        self.bm25 = BM25(self.chunk_text)
        self.strength = np.asarray([n["story_strength"] for n in self.narratives], dtype=np.float32)
        notes_file = data_dir / "content_notes.json"
        self.notes = json.loads(notes_file.read_text()) if notes_file.exists() else {}

    def _per_essay_max(self, chunk_scores: np.ndarray) -> np.ndarray:
        out = np.full(len(self.narratives), -np.inf, dtype=np.float32)
        np.maximum.at(out, self.chunk_owner, chunk_scores)
        return out

    def hybrid(self, query_vec, sketch_vec, keyword_query: str, top: int):
        dense_q = self.vectors @ query_vec
        dense_s = self.vectors @ sketch_vec
        lexical = self.bm25.scores(keyword_query)

        fused = np.zeros(len(self.narratives), dtype=np.float32)
        for weight, scores in ((1.0, dense_q), (1.0, dense_s), (0.8, lexical)):
            essay_scores = self._per_essay_max(scores)
            order = np.argsort(-essay_scores)
            if scores is lexical:
                order = order[essay_scores[order] > 0]
            fused[order] += weight / (60 + np.arange(1, len(order) + 1))
        fused += 0.002 * (self.strength - 3)

        order = np.argsort(-fused)
        story_rows = [r for r in order if self.strength[r] >= 3][: top // 2]
        best = list(dict.fromkeys([*order[: top - len(story_rows)], *story_rows]))
        best += [r for r in order if r not in best][: top - len(best)]
        passage_scores = dense_q + dense_s
        candidates = []
        for row in best:
            mask = (self.chunk_owner == row) & self.chunk_is_text
            idx = np.flatnonzero(mask)
            top_chunks = idx[np.argsort(-passage_scores[idx])[:2]] if len(idx) else []
            candidates.append((self.narratives[row], [self.chunk_text[i] for i in sorted(top_chunks)]))
        return candidates


class Searcher:

    def __init__(self, index: NarrativeIndex):
        self.index = index
        self.cache_file = CACHE_DIR / "search_cache.json"
        self.seed_file = DATA_DIR / "warm_searches.json"
        self.lock = threading.Lock()
        self.queries, self.concepts = {}, {}
        for f in (self.seed_file, self.cache_file):
            try:
                data = json.loads(f.read_text())
                if data.get("built_at") == index.meta["built_at"]:
                    self.queries.update(data["queries"])
                    self.concepts.update(data["concepts"])
            except Exception:
                pass

    @staticmethod
    def _key(text: str) -> str:
        key = re.sub(r"[^a-z0-9 ]", "", re.sub(r"[\s-]+", " ", text.lower())).strip()
        return re.sub(r"^the ", "", key)

    def snapshot(self) -> dict:
        return {"built_at": self.index.meta["built_at"], "queries": self.queries, "concepts": self.concepts}

    def _save(self):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self.snapshot(), indent=1, ensure_ascii=False))
        except OSError:
            pass

    def search(self, query: str) -> dict:
        qkey = self._key(query)
        with self.lock:
            ckey = self.queries.get(qkey)
            if ckey in self.concepts:
                return {**self.concepts[ckey], "cached": True}

        meta = self.index.meta
        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            query_vec_f = pool.submit(gemini.embed, [query], "RETRIEVAL_QUERY", meta["embed_model"], meta["embed_dim"])
            understood = gemini.generate_json(LITE_MODEL, UNDERSTAND_PROMPT.format(query=query), QueryUnderstanding)
            query_vec = query_vec_f.result()[0]

        if not understood.is_sociology_topic:
            return {"concept": None, "definition": None, "options": [],
                    "message": "HawkAI works with sociological concepts. Try one like the Looking-Glass Self or Anomie."}

        ckey = self._key(understood.concept)
        with self.lock:
            if ckey in self.concepts:
                self.queries[qkey] = ckey
                self._save()
                return {**self.concepts[ckey], "cached": True}

        sketch_vec = gemini.embed([f"{understood.concept}. {understood.experience_sketch}"], "RETRIEVAL_DOCUMENT",
                                  meta["embed_model"], meta["embed_dim"])[0]
        keyword_query = " ".join([understood.concept, query, *understood.keywords])
        candidates = self.index.hybrid(query_vec, sketch_vec, keyword_query, NUM_CANDIDATES)
        options = self._rerank(understood, candidates)

        result = {"concept": understood.concept, "definition": understood.definition, "options": options,
                  "message": None if options else
                  f"No past student essays clearly illustrate {understood.concept}. Try a related concept."}
        with self.lock:
            self.concepts[ckey] = result
            self.queries[qkey] = ckey
            self._save()
        return result

    def _rerank(self, understood: QueryUnderstanding, candidates) -> list[dict]:
        blocks = []
        for n, passages in candidates:
            body = "\n...\n".join(" ".join(p.split()[:220]) for p in passages)
            blocks.append(f'<candidate id="{n["id"]}">\nCatalogue: {n["title"]} - {n["preview"]} '
                          f'(concepts: {", ".join(n["concepts"])}; personal story strength {n["story_strength"]}/5)\n'
                          f'{body}\n</candidate>')
        prompt = RERANK_PROMPT.format(concept=understood.concept, definition=understood.definition,
                                      candidates="\n\n".join(blocks), k=NUM_OPTIONS, min_rel=MIN_RELEVANCE)
        try:
            ranked = gemini.generate_json(FAST_MODEL, prompt, Reranked, budget=1024).options
        except Exception as exc:
            print(f"Rerank failed, using hybrid order: {exc}")
            return [self._option(n) for n, _ in candidates[:NUM_OPTIONS]]

        options, seen = [], set()
        for r in ranked:
            n = self.index.by_id.get(r.id)
            if not n or r.id in seen or r.relevance < MIN_RELEVANCE:
                continue
            seen.add(r.id)
            options.append(self._option(n, r.title, r.preview, r.relevance))
            if len(options) == NUM_OPTIONS:
                break
        return options

    @staticmethod
    def _option(n: dict, title: str | None = None, preview: str | None = None, relevance: int | None = None) -> dict:
        return {"id": n["id"], "title": (title or n["title"]).strip(), "preview": (preview or n["preview"]).strip(),
                "concepts": n["concepts"][:4], "relevance": relevance, "words": n["words"]}
