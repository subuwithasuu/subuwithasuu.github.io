"""Real food photos from Unsplash (default) or Pexels.

  UNSPLASH_ACCESS_KEY  - used when set (Unsplash bans AI-generated images)
  PEXELS_API_KEY       - fallback (Pexels paused new keys in Nov 2026)

Photos are matched to the dish by checking the provider's own description,
so a pin titled "Lemon Garlic Shrimp" never shows a random burger.
Unsplash API guidelines followed: images are hotlinked from images.unsplash.com
on the site, the photographer + Unsplash are credited with referral links, and
the download endpoint is triggered for every photo we use.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from common import BUILD, log

STOP = {"with", "and", "the", "easy", "best", "quick", "recipe", "homemade", "simple",
        "minute", "minutes", "healthy", "one", "pot", "pan", "sheet", "for", "of", "in",
        "high", "protein", "meal"}
UTM = "utm_source=easy_protein_kitchen&utm_medium=referral"


def provider() -> str:
    if os.environ.get("UNSPLASH_ACCESS_KEY"):
        return "unsplash"
    if os.environ.get("PEXELS_API_KEY"):
        return "pexels"
    raise RuntimeError("set UNSPLASH_ACCESS_KEY (or PEXELS_API_KEY)")


# ---- providers: each returns normalised candidates -------------------------
def _unsplash(query: str) -> list[dict]:
    r = requests.get("https://api.unsplash.com/search/photos", timeout=30,
                     params={"query": query, "per_page": 30, "orientation": "portrait",
                             "content_filter": "high"},
                     headers={"Authorization": f"Client-ID {os.environ['UNSPLASH_ACCESS_KEY']}",
                              "Accept-Version": "v1"})
    r.raise_for_status()
    out = []
    for p in r.json().get("results", []):
        if p.get("premium") or p.get("plus"):          # Unsplash+ is not free
            continue
        out.append({
            "id": f"u-{p['id']}", "width": p["width"], "height": p["height"],
            "alt": " ".join(filter(None, [p.get("alt_description"), p.get("description")])),
            "photographer": p["user"]["name"],
            "photographer_url": f"{p['user']['links']['html']}?{UTM}",
            "page_url": f"{p['links']['html']}?{UTM}",
            "source": "Unsplash", "source_url": f"https://unsplash.com/?{UTM}",
            "src": p["urls"]["raw"], "web": p["urls"]["regular"],
            "download_location": p["links"]["download_location"],
        })
    return out


def _pexels(query: str) -> list[dict]:
    r = requests.get("https://api.pexels.com/v1/search", timeout=30,
                     params={"query": query, "per_page": 40, "orientation": "portrait"},
                     headers={"Authorization": os.environ["PEXELS_API_KEY"]})
    r.raise_for_status()
    return [{
        "id": p["id"], "width": p["width"], "height": p["height"], "alt": p.get("alt") or "",
        "photographer": p["photographer"], "photographer_url": p["photographer_url"],
        "page_url": p["url"], "source": "Pexels", "source_url": "https://www.pexels.com",
        "src": p["src"]["original"], "web": p["src"]["large2x"],
    } for p in r.json().get("photos", [])]


def _words(s: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in STOP}


def _mark_used(photo: dict) -> None:
    """Unsplash requires hitting download_location when a photo is used."""
    if photo.get("download_location"):
        try:
            requests.get(photo["download_location"], timeout=20,
                         headers={"Authorization": f"Client-ID {os.environ['UNSPLASH_ACCESS_KEY']}"})
        except requests.RequestException as e:
            log(f"unsplash download ping failed: {e}")


# Main foods: a photo described with one of these must not show a DIFFERENT one than
# the recipe uses (e.g. "teriyaki chicken" photo on a beef recipe).
MAIN_FOODS = {"chicken", "beef", "steak", "pork", "bacon", "ham", "turkey", "lamb", "salmon", "tuna",
              "shrimp", "prawn", "fish", "cod", "crab", "tofu", "tempeh", "egg", "pasta", "noodle",
              "pizza", "burger", "cake", "pancake", "waffle", "cookie", "donut", "doughnut", "bread",
              "sushi", "taco", "curry", "soup", "salad", "oat", "smoothie"}


def conflicts(alt: set[str], recipe_text: str) -> set[str]:
    """Main foods in the photo description that the recipe doesn't contain."""
    have = {w.rstrip("s") for w in re.findall(r"[a-z]+", recipe_text.lower())}
    return {f for f in MAIN_FOODS if f in alt and f not in have}


def find_photos(queries: list[str], must_match: list[str], used_ids: set, want: int = 6,
                recipe_text: str = "") -> list[dict]:
    """Return up to `want` relevant portrait-ish photos, best first."""
    search = _unsplash if provider() == "unsplash" else _pexels
    must = {w.rstrip("s").lower() for w in must_match}
    picked: dict = {}
    for q in queries:
        try:
            results = search(q)
        except requests.RequestException as e:
            log(f"photo search failed for {q!r}: {e}")
            continue
        qw = _words(q)
        for p in results:
            if p["id"] in used_ids or p["id"] in picked:
                continue
            if p["height"] < 1200 or p["width"] < 800:
                continue
            alt = _words(p["alt"])
            if must and not (alt & must):
                continue
            if recipe_text and conflicts(alt, recipe_text):
                continue
            p["score"] = len(alt & qw) * 2 + len(alt & must) * 3 + (1 if p["height"] > p["width"] else 0)
            picked[p["id"]] = p
        if len(picked) >= want * 2:
            break
    best = sorted(picked.values(), key=lambda x: -x["score"])[:want]
    for p in best:
        _mark_used(p)
    return best


def download(photo: dict, width: int = 1600) -> Path:
    """Download (cached) a resized JPEG of the photo. No API key needed."""
    if photo.get("local"):                       # test fixtures
        return Path(photo["local"]) if Path(photo["local"]).is_absolute() else BUILD.parent / photo["local"]
    out = BUILD / "photos" / f"{photo['id']}_{width}.jpg"
    if out.exists() and out.stat().st_size > 10_000:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    if photo.get("source") == "Unsplash":
        url = f"{photo['src']}&w={width}&fm=jpg&q=85&fit=max"
    else:
        url = f"{photo['src']}?auto=compress&cs=tinysrgb&w={width}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    out.write_bytes(r.content)
    return out
