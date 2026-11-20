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
- The phrases in the quote that show the concept are highlighted.
- Essays that discuss suicide, self-harm, eating disorders, sexual violence, abuse or addiction carry a content note.
- A plain-Gemini mode and event logging support the HawkAI vs. Gemini study.

## Run it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # add your Gemini API key
.venv/bin/python build_index.py   # only once, or after adding essays
.venv/bin/python content_notes.py # tags essays that need a content note
.venv/bin/python warm_cache.py    # pre-computes results for common concepts
.venv/bin/python app.py
```

Then open http://localhost:5001.

- `?mode=gemini` opens the plain-Gemini version used as the comparison group.
- `?pid=P07` tags a study participant in the logs.
- Events are saved to the `events` table of the Postgres database in `DATABASE_URL` when it is set, and to `logs/events-YYYY-MM-DD.jsonl` otherwise.
- `DATABASE_URL=... .venv/bin/python export_logs.py [YYYY-MM-DD]` downloads the study events (optionally from a date on) to `exports/` as JSONL and CSV.

## Adding essays

1. Install the extra packages with `.venv/bin/pip install -r requirements-setup.txt`, put the anonymized files in the folder `setup.py` reads from and run it to add them to `embeddingDatabase.json`.
2. Run `build_index.py`, `content_notes.py` and then `warm_cache.py` again. Essays that were already processed are skipped.

## Files

```
app.py           Flask server and API routes
retrieval.py     Search and reranking
generation.py    Prompts and streamed answers
gemini.py        Gemini client
build_index.py   Builds data/ from embeddingDatabase.json
warm_cache.py    Pre-computes search results for common concepts
content_notes.py Tags essays on sensitive topics
event_log.py     Study event logging
export_logs.py   Downloads study events as JSONL and CSV
config.py        Settings
data/            Built search index
templates/, static/   Front end
runtime.py, main.py, setup.py   HawkAI 2 pipeline (still works)
```

## Deploying

On Vercel, import the GitHub repo as a new project and add `GOOGLE_API_KEY` under Settings > Environment Variables. `vercel.json` and `.vercelignore` keep the essay PDFs and backups out of the deployment. Study events go to a Postgres database: add one under the project's Storage tab (Neon, free plan) so Vercel sets `DATABASE_URL`, then redeploy. Without it, events are written to `/tmp` and lost.

Elsewhere it is a standard Flask app served by gunicorn (see `Procfile`) and needs the `GOOGLE_API_KEY` environment variable. Use threaded workers so answers can stream:

```
gunicorn app:app --worker-class gthread --workers 1 --threads 16 --timeout 180
```

## Background

This project evolved through 20+ prototype iterations. It started as an exploration of Copilot Studio for classroom engagement, grew into a custom Gemini API integration, and became the technical foundation for a research paper on place-based LLMs.

The core thesis of the research: LLMs don't have to be tools for individual productivity. They can be platforms for connecting people through shared narrative. HawkAI is the proof of concept.
