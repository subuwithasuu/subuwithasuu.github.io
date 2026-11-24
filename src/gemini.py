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
        wanted = [g["model"], *g.get("fallback_models", [])]
        self.min_gap = 7.0      # free tier is ~10 requests/minute; stay under it
        self._last = 0.0
        # Google renames/retires models often. Ask which ones this key can use and
        # keep only those, plus the newest available Flash models as fallbacks.
        avail = self.available_models()
        if avail:
            keep = [m for m in wanted if m in avail]
            self.models = list(dict.fromkeys(keep + rank_flash(avail)))[:4]
            dropped = [m for m in wanted if m not in avail]
            if dropped:
                log(f"gemini: configured models not available any more: {dropped}")
        else:
            self.models = wanted
        if not self.models:
            raise GeminiError(f"no usable Gemini Flash model for this key; available: {sorted(avail)[:20]}")
        log(f"gemini: using {self.models}")

    def available_models(self) -> set[str]:
        """Model ids this key can call with generateContent ('' set if the list call fails)."""
        out, token = set(), None
        try:
            for _ in range(5):
                r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=30,
                                 headers={"x-goog-api-key": self.key},
                                 params={"pageSize": 200, **({"pageToken": token} if token else {})})
                if r.status_code != 200:
                    log(f"gemini: model list HTTP {r.status_code}")
                    return out
                d = r.json()
                for m in d.get("models", []):
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        out.add(m["name"].removeprefix("models/"))
                token = d.get("nextPageToken")
                if not token:
                    break
        except requests.RequestException as e:
            log(f"gemini: model list failed: {e}")
        return out

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


SKIP = re.compile(r"image|tts|audio|live|embed|vision|thinking|exp|learnlm|robotics|computer|8b", re.I)


def rank_flash(avail: set[str]) -> list[str]:
    """Newest stable Flash first, then -lite, then previews; text-only chat models only."""
    def version(m):
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", m)
        return float(v.group(1)) if v else 0.0
    flash = [m for m in avail if m.startswith("gemini-") and "flash" in m and not SKIP.search(m)]
    return sorted(flash, key=lambda m: ("preview" in m, -version(m), "lite" in m,
                                         bool(re.search(r"-\d{3}$|-\d{2}-\d{4}", m)), m))


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
