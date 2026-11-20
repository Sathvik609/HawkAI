import random
import threading
import time

import numpy as np
from google import genai
from google.genai import errors, types

import config

_client = None
_client_lock = threading.Lock()
RETRYABLE = {429, 500, 502, 503, 504}


def client() -> genai.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = genai.Client()
    return _client


def with_retry(fn, attempts: int = 6, base: float = 1.5):
    for i in range(attempts):
        try:
            return fn()
        except errors.APIError as e:
            if getattr(e, "code", None) in RETRYABLE and i < attempts - 1:
                time.sleep(base * (2 ** i) + random.random())
                continue
            raise


def thinking_config(model: str, budget: int):
    m = model.lower()
    if m.startswith("gemini-2.5"):
        if "pro" in m:
            budget = max(budget, 128)
        return types.ThinkingConfig(thinking_budget=budget)
    if m.startswith("gemini-3"):
        try:
            return types.ThinkingConfig(thinking_level="low" if budget <= 1024 else "high")
        except Exception:
            return None
    return None


def gen_config(model: str, budget: int, **kwargs) -> types.GenerateContentConfig:
    if not model.lower().startswith("gemini-2.5"):
        kwargs.pop("temperature", None)
    return types.GenerateContentConfig(thinking_config=thinking_config(model, budget), **kwargs)


def embed(texts: list[str], task_type: str, model: str, dim: int, batch: int = 50) -> np.ndarray:
    out = []
    for start in range(0, len(texts), batch):
        part = texts[start:start + batch]
        # Each text needs its own Content, otherwise newer models merge them into one embedding.
        res = with_retry(lambda: client().models.embed_content(
            model=model,
            contents=[types.Content(parts=[types.Part(text=t)]) for t in part],
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=dim),
        ))
        out.extend(e.values for e in res.embeddings)
    vectors = np.asarray(out, dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-12
    return vectors


def generate_json(model: str, prompt: str, schema, budget: int = 0, temperature: float = 0.2):
    cfg = gen_config(model, budget, temperature=temperature,
                     response_mime_type="application/json", response_schema=schema)
    res = with_retry(lambda: client().models.generate_content(model=model, contents=prompt, config=cfg))
    if getattr(res, "parsed", None) is not None:
        return res.parsed
    return schema.model_validate_json(res.text)


def stream_text(model: str, contents, system: str | None = None, budget: int = 1024, temperature: float = 0.7):
    cfg = gen_config(model, budget, temperature=temperature, system_instruction=system)
    stream = with_retry(lambda: client().models.generate_content_stream(model=model, contents=contents, config=cfg))
    for chunk in stream:
        try:
            text = chunk.text
        except Exception:
            text = None
        if text:
            yield text
