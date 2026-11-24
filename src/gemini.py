"""Tiny Gemini REST client (free tier). Key comes from env GEMINI_API_KEY."""
from __future__ import annotations

import json
import os
import re
import time

import requests

from common import log

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiError(RuntimeError):
    pass


class Gemini:
    def __init__(self, cfg: dict):
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise GeminiError("GEMINI_API_KEY is not set")
        g = cfg["gemini"]
        self.models = [g["model"], *g.get("fallback_models", [])]
        self.min_gap = 7.0      # free tier is ~10 requests/minute; stay under it
        self._last = 0.0

    def _call(self, model: str, body: dict) -> dict:
        wait = self.min_gap - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(5):
            self._last = time.time()
            r = requests.post(API.format(model=model), json=body, timeout=180,
                              headers={"x-goog-api-key": self.key})
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 503):
                delay = 20 * (attempt + 1)
                log(f"gemini {model}: HTTP {r.status_code}, retry in {delay}s")
                time.sleep(delay)
                continue
            raise GeminiError(f"{model}: HTTP {r.status_code}: {r.text[:400]}")
        raise GeminiError(f"{model}: gave up after retries")

    def generate(self, prompt: str, *, system: str = "", json_mode: bool = True,
                 grounded: bool = False, temperature: float = 0.6) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if grounded:
            # Google Search grounding cannot be combined with JSON mode.
            body["tools"] = [{"google_search": {}}]
        elif json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        last_err = None
        for model in self.models:
            try:
                data = self._call(model, body)
                cands = data.get("candidates") or []
                if not cands:
                    raise GeminiError(f"no candidates: {str(data)[:300]}")
                parts = cands[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if text.strip():
                    return text
                raise GeminiError("empty response")
            except GeminiError as e:
                last_err = e
                log(f"gemini model {model} failed: {e}")
        raise GeminiError(str(last_err))

    def generate_json(self, prompt: str, **kw):
        text = self.generate(prompt, **kw)
        return extract_json(text)


def extract_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        return json.loads(m.group(1))
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise GeminiError(f"no JSON in response: {text[:300]}")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start:end + 1])
