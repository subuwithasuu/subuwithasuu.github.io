"""Pexels photo search + download (free API, key in env PEXELS_API_KEY).

Pexels photos are free to use; we still credit the photographer on the site.
Photos are matched to the dish by checking Pexels' own alt text, so a pin
titled "Lemon Garlic Shrimp" never shows a random burger.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from common import BUILD, log

API = "https://api.pexels.com/v1/search"
STOP = {"with", "and", "the", "easy", "best", "quick", "recipe", "homemade", "simple",
        "minute", "minutes", "healthy", "one", "pot", "pan", "sheet", "for", "of", "in"}


def _key() -> str:
    k = os.environ.get("PEXELS_API_KEY")
    if not k:
        raise RuntimeError("PEXELS_API_KEY is not set")
    return k


def search(query: str, per_page: int = 40) -> list[dict]:
    r = requests.get(API, params={"query": query, "per_page": per_page, "orientation": "portrait"},
                     headers={"Authorization": _key()}, timeout=30)
    r.raise_for_status()
    return r.json().get("photos", [])


def _words(s: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in STOP}


def find_photos(queries: list[str], must_match: list[str], used_ids: set[int], want: int = 6) -> list[dict]:
    """Return up to `want` relevant portrait-ish photos, best first."""
    must = {w.rstrip("s").lower() for w in must_match}
    picked: dict[int, dict] = {}
    for q in queries:
        try:
            results = search(q)
        except requests.RequestException as e:
            log(f"pexels search failed for {q!r}: {e}")
            continue
        qw = _words(q)
        for p in results:
            if p["id"] in used_ids or p["id"] in picked:
                continue
            if p["height"] < 1200 or p["width"] < 800:
                continue
            alt = _words(p.get("alt") or "")
            if must and not (alt & must):
                continue
            score = len(alt & qw) * 2 + len(alt & must) * 3 + (1 if p["height"] > p["width"] else 0)
            picked[p["id"]] = {
                "id": p["id"],
                "alt": p.get("alt") or "",
                "photographer": p["photographer"],
                "photographer_url": p["photographer_url"],
                "pexels_url": p["url"],
                "src": p["src"]["original"],
                "web": p["src"]["large2x"],
                "score": score,
            }
        if len(picked) >= want * 2:
            break
    best = sorted(picked.values(), key=lambda x: -x["score"])[:want]
    return best


def download(photo: dict, width: int = 1600) -> Path:
    """Download (cached) a resized JPEG of the photo."""
    if photo.get("local"):                       # test fixtures
        return Path(photo["local"]) if Path(photo["local"]).is_absolute() else BUILD.parent / photo["local"]
    out = BUILD / "photos" / f"{photo['id']}_{width}.jpg"
    if out.exists() and out.stat().st_size > 10_000:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"{photo['src']}?auto=compress&cs=tinysrgb&w={width}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    out.write_bytes(r.content)
    return out
