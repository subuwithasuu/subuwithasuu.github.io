"""Tiny Gemini REST client tuned for the FREE tier. Key comes from env GEMINI_API_KEY.

Free-tier realities this client handles (seen in real runs, Nov 2026):
  * Google retires model names; a model can be LISTED but answer 404.
  * Popular models answer 503 "high demand" for hours at a time.
  * Each model has its own small per-minute and per-day quota (429). Google Search
    grounding has an even smaller daily quota.
  * GitHub runners occasionally fail DNS for a few seconds.
Strategy: test-prompt the candidates at start-up, keep several working models, and
when one hits a limit, park it and move to the next one immediately instead of
sleeping through long retries. When grounding hits its limit, stop using it for the
rest of the run (the recipe fact-check then runs without web search).
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

from common import log

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LIST_API = "https://generativelanguage.googleapis.com/v1beta/models"
# Used when the model list cannot be fetched (e.g. a DNS blip on the runner).
KNOWN_FLASH = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
               "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]
KEEP = 4                     # how many working models to rotate between
PARK_SECONDS = 15 * 60       # how long a rate-limited model is skipped


class GeminiError(RuntimeError):
    pass


class RateLimited(GeminiError):
    pass


class Gemini:
    def __init__(self, cfg: dict):
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise GeminiError("GEMINI_API_KEY is not set")
        g = cfg["gemini"]
        wanted = [g["model"], *g.get("fallback_models", [])]
        self.min_gap = 6.0          # stay under ~10 requests/minute
        self._last = 0.0
        self.parked: dict[str, float] = {}
        self.grounding_ok = True
        avail = self.available_models()
        ranked = rank_flash(avail) if avail else KNOWN_FLASH
        log(f"gemini: key lists {len(avail)} models; flash candidates: {ranked[:10]}")
        candidates = list(dict.fromkeys([m for m in wanted if not avail or m in avail] + ranked))
        ok_models, busy = [], []
        for m in candidates[:12]:
            ok, why = self.probe(m)
            log(f"gemini: probe {m}: {'OK' if ok else why}")
            if ok:
                ok_models.append(m)
            elif why.startswith("HTTP 503") or why.startswith("HTTP 429") or why.startswith("network"):
                busy.append(m)      # temporarily unavailable: keep as a late fallback
            if len(ok_models) >= KEEP:
                break
        self.models = ok_models + busy[:2]
        if not ok_models:
            raise GeminiError("no Gemini model answered a test prompt (see probe lines above)")
        log(f"gemini: using {self.models}")

    # ---------------------------------------------------------------- setup
    def probe(self, model: str) -> tuple[bool, str]:
        """One tiny request: does this model actually answer right now?"""
        try:
            r = requests.post(API.format(model=model), timeout=60, headers={"x-goog-api-key": self.key},
                              json={"contents": [{"role": "user", "parts": [{"text": "Reply with the word OK."}]}]})
        except requests.RequestException as e:
            return False, f"network error {type(e).__name__}"
        self._last = time.time()
        if r.status_code == 200:
            return True, ""
        return False, f"HTTP {r.status_code}: {_msg(r)}"

    def available_models(self) -> set[str]:
        """Model ids this key can call with generateContent (empty set if listing fails)."""
        for attempt in range(3):
            out, token = set(), None
            try:
                for _ in range(5):
                    r = requests.get(LIST_API, timeout=30, headers={"x-goog-api-key": self.key},
                                     params={"pageSize": 200, **({"pageToken": token} if token else {})})
                    if r.status_code != 200:
                        log(f"gemini: model list HTTP {r.status_code}")
                        break
                    d = r.json()
                    for m in d.get("models", []):
                        if "generateContent" in m.get("supportedGenerationMethods", []):
                            out.add(m["name"].removeprefix("models/"))
                    token = d.get("nextPageToken")
                    if not token:
                        return out
            except requests.RequestException as e:
                log(f"gemini: model list failed (attempt {attempt + 1}): {type(e).__name__}")
            time.sleep(10)
        return set()

    # ---------------------------------------------------------------- calls
    def _call(self, model: str, body: dict) -> dict:
        wait = self.min_gap - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(2):
            self._last = time.time()
            try:
                r = requests.post(API.format(model=model), json=body, timeout=150,
                                  headers={"x-goog-api-key": self.key})
            except requests.RequestException as e:      # timeouts must not crash the run
                log(f"gemini {model}: {type(e).__name__}")
                time.sleep(10)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:                    # quota: no point retrying this model now
                raise RateLimited(f"{model}: HTTP 429: {_msg(r)[:120]}")
            if r.status_code in (500, 503):
                log(f"gemini {model}: HTTP {r.status_code} (busy)")
                time.sleep(8)
                continue
            raise GeminiError(f"{model}: HTTP {r.status_code}: {_msg(r)}")
        raise RateLimited(f"{model}: busy after retries")

    def generate(self, prompt: str, *, system: str = "", json_mode: bool = True,
                 grounded: bool = False, temperature: float = 0.6) -> str:
        if grounded and not self.grounding_ok:
            raise GeminiError("web-search grounding quota used up for this run")
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

        last_err: Exception | None = None
        for rnd in range(2):                    # second round waits for parked models if all are parked
            now = time.time()
            ready = [m for m in self.models if self.parked.get(m, 0) <= now]
            if not ready:
                soonest = min(self.parked.values()) - now
                if soonest > 20 * 60 or rnd == 1:
                    break
                log(f"gemini: all models rate-limited, waiting {int(soonest)}s")
                time.sleep(max(1, soonest))
                continue
            for model in ready:
                try:
                    data = self._call(model, body)
                    cands = data.get("candidates") or []
                    parts = (cands[0].get("content", {}).get("parts", []) if cands else [])
                    text = "".join(p.get("text", "") for p in parts)
                    if text.strip():
                        return text
                    raise GeminiError(f"{model}: empty response")
                except RateLimited as e:
                    last_err = e
                    if grounded:                # grounding has its own tiny daily quota
                        self.grounding_ok = False
                        log("gemini: grounding quota reached - fact-checks continue without web search")
                        raise GeminiError(str(e))
                    self.parked[model] = time.time() + PARK_SECONDS
                    log(f"gemini: parking {model} for {PARK_SECONDS // 60} min ({e})")
                except GeminiError as e:
                    last_err = e
                    log(f"gemini model {model} failed: {e}")
            if last_err and not isinstance(last_err, RateLimited):
                break
        raise GeminiError(str(last_err or "no model available"))

    def generate_json(self, prompt: str, **kw):
        text = self.generate(prompt, **kw)
        try:
            return extract_json(text)
        except (json.JSONDecodeError, GeminiError):
            # one repair attempt: ask for the same content as strict JSON
            fixed = self.generate("Return ONLY valid JSON (double-quoted keys, no comments, no trailing "
                                  "commas) for exactly this content:\n\n" + text[:12000],
                                  temperature=0.0, json_mode=True)
            return extract_json(fixed)


def _msg(r) -> str:
    try:
        return r.json().get("error", {}).get("message", "")[:200]
    except ValueError:
        return r.text[:200]


SKIP = re.compile(r"image|tts|audio|live|embed|vision|thinking|exp|learnlm|robotics|computer|8b|omni", re.I)


def rank_flash(avail: set[str]) -> list[str]:
    """Stable Flash models newest first (then -lite, then previews); text-only models."""
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
