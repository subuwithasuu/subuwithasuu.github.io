"""Write new recipes with Gemini, fact-check them, attach Pexels photos, save.

Pipeline per recipe:
  1. idea      - Gemini proposes a dish for a board (avoids existing titles)
  2. write     - Gemini writes the full recipe as strict JSON, in its own words
  3. verify    - a second Gemini call WITH Google Search compares quantities,
                 ratios, temperatures and times to established published recipes
                 and returns pass / fix / reject (+ the sources it checked)
  4. rules     - validate.py hard checks (food safety temps, absurd amounts...)
  5. photos    - real Pexels photos whose alt text matches the dish
Only recipes that pass every step are saved. Rejections go to rejected.json.

Usage:
  python src/gen_recipes.py --count 12
  python src/gen_recipes.py --initial      # top up to content.initial_recipes
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date

from common import (RECIPES_DIR, REJECTED_FILE, load_config, load_recipes, log,
                    read_json, slugify, write_json)
from gemini import Gemini, GeminiError
from validate import validate
from nutrition import check_protein
import pexels
from queue_plan import content_runway_days, load_queue


def add_protein_sublines(r: dict) -> None:
    """Half the pin designs lead with the verified protein number (the niche's #1 hook)."""
    for i, v in enumerate(r.get("pin_variants", [])):
        if i % 2 == 0:
            v["subline"] = f"{r['protein_g']}g Protein • {r['total_minutes']} Min"

SYSTEM = (
    "You are a meticulous recipe developer and food-safety-aware test-kitchen editor. "
    "You only publish recipes that work exactly as written, with standard, proven "
    "ingredient ratios, realistic times and correct temperatures. You never invent "
    "unusual quantities. You write in clear, original wording (never copy text from "
    "other sites)."
)

RECIPE_SCHEMA = """{
  "title": "Recipe name as people search it, e.g. 'Garlic Butter Shrimp Pasta'",
  "description": "2 sentences, appetizing and specific, no health claims",
  "cuisine": "e.g. American, Italian, Mexican, Indian",
  "category": "Dinner | Lunch | Breakfast | Dessert | Snack | Side",
  "servings": 4,
  "protein_g_per_serving": 35,
  "prep_minutes": 10,
  "cook_minutes": 15,
  "total_minutes": 25,
  "ingredients": [
    {"qty": "1 1/2", "unit": "cups", "item": "long-grain white rice", "metric": "300 g", "note": "rinsed"}
  ],
  "steps": ["Imperative, specific step with heat level, time AND a doneness cue."],
  "tips": ["1-3 genuinely useful tips: substitutions, storage, make-ahead"],
  "storage": "How long it keeps in the fridge/freezer",
  "tools": ["named kitchen tools/equipment used, e.g. '12-inch skillet'"],
  "keywords": ["6-10 Pinterest search phrases people really type"],
  "pin_variants": [
    {"title": "<=100 chars, keyword-first pin title",
     "description": "<=450 chars, natural keyword-rich pin description ending with a soft call to action",
     "overlay": "2-6 words for the image text box",
     "subline": "<=5 words, e.g. 'Ready in 25 Minutes'",
     "alt_text": "<=200 chars, describes the finished dish"}
  ],
  "video_script": {
    "hook": "spoken opening line, <=12 words, mention the protein as e.g. '35 grams of protein'",
    "steps_short": ["3-6 spoken lines, <=14 words each, matching the real steps in order"],
    "outro": "spoken closing line, <=14 words, mention the full recipe is on the website"
  },
  "photo_queries": ["3 Pexels search phrases for the finished dish, most specific first"],
  "must_match": ["2-4 single words; a stock photo's description must contain one of them, e.g. 'shrimp','pasta'"]
}"""


def ideas_for_board(gem: Gemini, cfg: dict, board: dict, n: int, existing: list[str]) -> list[dict]:
    prompt = f"""Niche: {cfg['niche']['description']}. Audience: {cfg['niche']['audience']}.
Pinterest board: "{board['name']}" - {board['description']}
Topic hints: {', '.join(board['topics'])}

Suggest {n} different, popular, well-established HIGH-PROTEIN dishes for this board that people
actively search for on Pinterest (think gym-goers and macro trackers).{" Vegetarian only." if board.get("vegetarian") else ""} Classic, proven dishes only (no strange fusions). Total time
<= {cfg['content']['max_total_time_minutes']} minutes unless the board is about make-ahead food.
They must be easy to find stock photos for (a common, recognisable finished dish).
Do NOT repeat or closely resemble any of these existing recipes:
{json.dumps(existing[-150:])}

Return JSON: [{{"title": "...", "why": "one line"}}]"""
    out = gem.generate_json(prompt, system=SYSTEM, temperature=0.9)
    return [o for o in out if isinstance(o, dict) and o.get("title")][:n]


def write_recipe(gem: Gemini, cfg: dict, board: dict, title: str) -> dict:
    prompt = f"""Write the recipe "{title}" for the Pinterest board "{board['name']}".
Audience: {cfg['niche']['audience']}.
This is a HIGH-PROTEIN recipe site: aim for {board.get('min_protein', cfg['protein']['min_per_serving'])}g+
protein per serving using real protein foods (lean meat, fish, eggs, Greek yogurt, cottage cheese,
tofu, tempeh, lentils, beans, edamame, cheese; protein powder only where it is normal, e.g. oats/snacks).
{"VEGETARIAN: no meat, poultry, fish, seafood, gelatin or fish sauce. Eggs and dairy are fine." if board.get("vegetarian") else ""}
{f"Total time must be <= {board['max_minutes']} minutes." if board.get("max_minutes") else ""}
Rules:
- Use the standard, proven proportions for this dish (as found in reputable test-kitchen recipes).
- US measurements in qty/unit, with a metric equivalent in "metric". The metric field MUST be a
  weight in grams (or ml for liquids) for every protein ingredient, e.g. "450 g".
- protein_g_per_serving: honest estimate from the ingredient weights (it is re-checked by code;
  overstating it gets the recipe rejected). Any protein number in pin text must match it.
- Every ingredient must be used in the steps; every step must say heat level/time and a doneness cue.
- Poultry: state internal temperature 165°F (74°C). Ground meat: 160°F (71°C).
- Oven temperatures in °F with °C in brackets.
- total_minutes must be >= prep_minutes + cook_minutes.
- Make exactly {cfg['mix']['pins_per_recipe']} pin_variants, each with a different angle
  (speed, flavour, ease, occasion/budget) and different wording.
- No health, diet or medical claims.

Return ONLY JSON in this exact shape:
{RECIPE_SCHEMA}"""
    return gem.generate_json(prompt, system=SYSTEM, temperature=0.5)


def verify_recipe(gem: Gemini, recipe: dict) -> dict:
    core = {k: recipe[k] for k in ("title", "servings", "prep_minutes", "cook_minutes",
                                    "total_minutes", "ingredients", "steps")}
    prompt = f"""You are fact-checking a recipe before publication.
Search the web for 2-3 established recipes for this same dish from reputable test kitchens
or food publishers (e.g. Serious Eats, BBC Good Food, Allrecipes, King Arthur, NYT Cooking,
Bon Appetit, RecipeTin Eats). Compare, per serving:
- ingredient ratios (fat, salt, liquid, flour/leavening, spices, protein, starch)
- cooking temperatures and times, doneness cues, food-safety temperatures
- whether the method actually produces the dish.
Small stylistic differences are fine. Anything that would make the dish fail, taste wrong,
or be unsafe must be fixed.

RECIPE:
{json.dumps(core, ensure_ascii=False, indent=1)}

Reply with ONLY a JSON object inside a ```json code block:
{{"verdict": "pass" | "fix" | "reject",
  "sources": [{{"name": "publisher - recipe title", "url": "..."}}],
  "issues": ["specific problem and the standard value"],
  "corrections": {{ only the fields to replace, same shape as the recipe, e.g. "ingredients": [...], "steps": [...], "cook_minutes": 12 }} }}
Use "reject" only if the dish is not a real, established dish or cannot be fixed."""
    try:
        return gem.generate_json(prompt, grounded=True, json_mode=False, temperature=0.1)
    except (GeminiError, json.JSONDecodeError) as e:
        log(f"grounded verify failed ({e}); falling back to ungrounded check")
        res = gem.generate_json(prompt.replace("Search the web for", "Recall"), temperature=0.1)
        res["grounded"] = False
        return res


def reject(title: str, board: str, reason) -> None:
    rej = read_json(REJECTED_FILE, [])
    rej.append({"title": title, "board": board, "reason": reason, "date": date.today().isoformat()})
    write_json(REJECTED_FILE, rej[-500:])
    log(f"REJECTED {title}: {reason}")


def make_one(gem: Gemini, cfg: dict, board: dict, title: str, used_photo_ids: set[int]) -> dict | None:
    log(f"\n== {board['name']} :: {title}")
    try:
        r = write_recipe(gem, cfg, board, title)
    except (GeminiError, json.JSONDecodeError) as e:
        reject(title, board["name"], f"write failed: {e}")
        return None
    r["board"] = board["name"]
    r["slug"] = slugify(r.get("title") or title)

    checks = []
    for round_no in range(2):
        try:
            v = verify_recipe(gem, r)
        except (GeminiError, json.JSONDecodeError) as e:
            reject(title, board["name"], f"verify failed: {e}")
            return None
        checks.append({"verdict": v.get("verdict"), "issues": v.get("issues", []),
                       "sources": v.get("sources", []), "grounded": v.get("grounded", True)})
        verdict = v.get("verdict")
        log(f"verify round {round_no + 1}: {verdict} {v.get('issues', [])}")
        if verdict == "pass":
            break
        if verdict == "reject":
            reject(title, board["name"], {"verify": v.get("issues")})
            return None
        corr = v.get("corrections") or {}
        for k in ("ingredients", "steps", "servings", "prep_minutes", "cook_minutes", "total_minutes", "tips"):
            if k in corr and corr[k]:
                r[k] = corr[k]
    else:
        reject(title, board["name"], {"verify": "still not passing after fixes", "checks": checks})
        return None

    errors, warns = validate(r, board.get("max_minutes", cfg["content"]["max_total_time_minutes"]))
    errors += check_protein(r, cfg, board)
    add_protein_sublines(r)
    for w in warns:
        log(f"  warn: {w}")
    if errors:
        reject(title, board["name"], {"rules": errors})
        return None

    photos = pexels.find_photos(r.get("photo_queries") or [r["title"]],
                                r.get("must_match") or [], used_photo_ids, want=6)
    if len(photos) < 3:
        reject(title, board["name"], f"only {len(photos)} matching photos on Pexels")
        return None
    r["photos"] = photos
    r["verification"] = {"checks": checks, "rule_warnings": warns, "model": gem.models[0]}
    r["created"] = date.today().isoformat()
    return r


def pick_boards(cfg: dict, recipes: list[dict], count: int) -> list[dict]:
    """Spread new recipes so the emptiest boards are filled first."""
    counts = {b["name"]: 0 for b in cfg["boards"]}
    for r in recipes:
        if r["board"] in counts:
            counts[r["board"]] += 1
    plan = []
    for _ in range(count):
        name = min(counts, key=lambda k: (counts[k], random.random()))
        counts[name] += 1
        plan.append(next(b for b in cfg["boards"] if b["name"] == name))
    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--initial", action="store_true")
    ap.add_argument("--auto", action="store_true",
                    help="daily mode: build the initial library, then only write more when runway is short")
    a = ap.parse_args()
    cfg = load_config()
    recipes = load_recipes()
    c = cfg["content"]
    if a.auto:
        if len(recipes) < c["initial_recipes"]:
            count = min(c["initial_recipes"] - len(recipes), 25)     # ~25/day fits the free tier
        elif content_runway_days(cfg, load_queue(), recipes) < c["min_runway_days"]:
            count = c["recipes_per_week"]
        else:
            count = 0
    elif a.initial:
        count = max(0, c["initial_recipes"] - len(recipes))
    else:
        count = a.count or c["recipes_per_week"]
    if count == 0:
        log("nothing to generate")
        return 0

    gem = Gemini(cfg)
    titles = [r["title"] for r in recipes]
    used = {p["id"] for r in recipes for p in r.get("photos", [])}
    plan = pick_boards(cfg, recipes, count)
    per_board: dict[str, int] = {}
    for b in plan:
        per_board[b["name"]] = per_board.get(b["name"], 0) + 1

    made = 0
    for bname, n in per_board.items():
        board = next(b for b in cfg["boards"] if b["name"] == bname)
        try:
            ideas = ideas_for_board(gem, cfg, board, n + 2, titles)   # +2 spares for rejects
        except (GeminiError, json.JSONDecodeError) as e:
            log(f"ideas failed for {bname}: {e}")
            continue
        got = 0
        for idea in ideas:
            if got >= n:
                break
            if slugify(idea["title"]) in {slugify(t) for t in titles}:
                continue
            r = make_one(gem, cfg, board, idea["title"], used)
            if not r:
                continue
            if (RECIPES_DIR / f"{r['slug']}.json").exists():
                r["slug"] += "-2"
            write_json(RECIPES_DIR / f"{r['slug']}.json", r)
            titles.append(r["title"])
            used |= {p["id"] for p in r["photos"]}
            got += 1
            made += 1
            log(f"SAVED {r['slug']}")
    log(f"\ngenerated {made}/{count} recipes")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
