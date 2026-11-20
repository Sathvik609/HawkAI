# HawkAI

HawkAI helps UNCW sociology students understand a concept through essays written by past students in the course. A student searches for a concept, picks one of up to five essays, reads a quote from it with a short explanation, and can then ask follow-up questions about the essay or the concept.

Built for the Department of Sociology & Criminology at the University of North Carolina Wilmington as part of ongoing LLM research.
Research paper: *Generating Connections, Not Content: The Hawk AI Place-Based LLM* (Elliott, Engelman, Oh, Butler).

## Changes in 3.0

- The student chooses from up to five essays, each with a one-line preview, instead of getting one picked for them.
- Essays are split into passages, so the whole essay is searched, not just the start.
- Search combines meaning search, keyword search (BM25) and a Gemini rerank of the best passages.
- The answer streams in, and the student can keep asking follow-up questions about the chosen essay.
- Follow-up answers mark which parts come from the essay and which come from general sociology.
- The quote is checked against the essay, and the full essay can be opened with the quote highlighted.
- A plain-Gemini mode and event logging support the HawkAI vs. Gemini study.

## Run it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # add your Gemini API key
.venv/bin/python build_index.py   # only once, or after adding essays
.venv/bin/python warm_cache.py    # pre-computes results for common concepts
.venv/bin/python app.py
```

Then open http://localhost:5001.

- `?mode=gemini` opens the plain-Gemini version used as the comparison group.
- `?pid=P07` tags a study participant in the logs.
- Events are saved to `logs/events-YYYY-MM-DD.jsonl`.

## Adding essays

1. Put the anonymized files in the folder `setup.py` reads from and run it to add them to `embeddingDatabase.json`.
2. Run `build_index.py` and then `warm_cache.py` again. Essays that were already processed are skipped.

## Files

```
app.py           Flask server and API routes
retrieval.py     Search and reranking
generation.py    Prompts and streamed answers
gemini.py        Gemini client
build_index.py   Builds data/ from embeddingDatabase.json
warm_cache.py    Pre-computes search results for common concepts
event_log.py     Study event logging
config.py        Settings
data/            Built search index
templates/, static/   Front end
runtime.py, main.py, setup.py   HawkAI 2 pipeline (still works)
```

## Deploying

It is a standard Flask app served by gunicorn (see `Procfile`) and needs the `GOOGLE_API_KEY` environment variable. Use threaded workers so answers can stream:

```
gunicorn app:app --worker-class gthread --workers 1 --threads 16 --timeout 180
```

## Background

This project evolved through 20+ prototype iterations. It started as an exploration of Copilot Studio for classroom engagement, grew into a custom Gemini API integration, and became the technical foundation for a research paper on place-based LLMs.

The core thesis of the research: LLMs don't have to be tools for individual productivity. They can be platforms for connecting people through shared narrative. HawkAI is the proof of concept.
