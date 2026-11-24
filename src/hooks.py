"""Pin hooks: what Gemini is told, and the checks its output must pass.

Adapted for Pinterest from the installed skills:
  - viral-hooks (vyralcontent, MIT): specific beats clever, the hook must match the
    payoff, write a batch across archetypes, avoid vague teases / imperatives /
    clever-but-unclear lines.
  - hook-generator (charlie947, MIT): digits not words, one angle per variant.
Pinterest is a search engine, so the TITLE stays keyword-first and the hook lives
mostly in the image overlay. Every number in pin text must be true for the recipe.
"""
from __future__ import annotations

import re

from common import STATE_DIR, read_json

LEARNINGS_FILE = STATE_DIR / "learnings.json"

HOOK_TYPES = {
    "number":      "lead with the verified protein grams or another true number, e.g. '40g Protein Breakfast Burritos'",
    "speed":       "lead with the real total time, e.g. '20-Minute Chicken Fajita Bowls' (never less than total_minutes)",
    "how_without": "'How to X without Y', e.g. 'High-Protein Oats Without Protein Powder'",
    "contrarian":  "flip a common belief the recipe actually proves, e.g. 'Skip the Shake: 30g Protein Breakfast'",
    "outcome":     "the result the reader wants, e.g. 'Prep Once, Eat High-Protein All Week'",
    "teacher":     "the technique or secret inside the recipe, e.g. 'The Trick to Juicy Chicken Breast'",
    "budget":      "cheap / pantry angle WITHOUT any price, e.g. 'Cheap High-Protein Lentil Dinner'",
}

BANNED = re.compile(
    r"you won'?t believe|this changed everything|game[- ]?changer|stop scrolling|wait for it|"
    r"insane|mind[- ]?blowing|life[- ]?changing|best ever|guaranteed|miracle|shocking|"
    r"\bviral\b|you need to try|literally", re.I)

PROMPT_RULES = """PIN TEXT RULES (Pinterest is a visual SEARCH engine):
- title: keyword-first, the phrase people actually type (e.g. "High Protein Chicken Burrito Bowls"),
  then a short benefit. <=100 chars. No clickbait.
- overlay: the HOOK shown big on the image. 3-7 words. Concrete food noun + a TRUE number when possible.
- Each variant uses a DIFFERENT hook_type from: {types}.
  Prefer these hook types this week (they performed best): {winners}.
- Only use numbers that are true for THIS recipe: protein grams (protein_g_per_serving),
  total/prep/cook minutes, servings, number of ingredients. No prices, no "$", no calorie claims.
- Never: vague teases ("you won't believe"), imperatives ("stop scrolling"), hype words
  ("insane", "best ever", "game changer"), promises the recipe does not deliver.
- description: first sentence repeats the main keyword naturally and states protein + time;
  2-3 sentences; end with a soft save cue like "Save it for your next meal prep."
- subline: <=5 words supporting the overlay (e.g. "Ready in 25 Minutes", "Meal Prep Friendly")."""


def winners(max_n: int = 3) -> list[str]:
    """Hook types the feedback loop found to win (empty until there is enough data)."""
    L = read_json(LEARNINGS_FILE, {})
    w = (L.get("weights") or {}).get("hook_type") or {}
    ranked = sorted((k for k in w if k in HOOK_TYPES), key=lambda k: -w[k])
    return [k for k in ranked if w[k] > 1.05][:max_n]


def prompt_rules() -> str:
    win = winners()
    return PROMPT_RULES.format(types=", ".join(f"{k} ({v})" for k, v in HOOK_TYPES.items()),
                               winners=", ".join(win) if win else "no data yet - spread across types")


def _true_numbers(r: dict) -> set[int]:
    nums = {r.get("protein_g"), r.get("total_minutes"), r.get("prep_minutes"), r.get("cook_minutes"),
            r.get("servings"), len(r.get("ingredients", []))}
    return {int(n) for n in nums if isinstance(n, (int, float))}


def check_hooks(r: dict) -> tuple[list[str], list[str]]:
    """Errors block the recipe; warnings are logged. Run AFTER protein verification."""
    errors, warns = [], []
    truth = _true_numbers(r)
    seen_types = []
    for i, v in enumerate(r.get("pin_variants", [])):
        ht = v.get("hook_type")
        if ht not in HOOK_TYPES:
            warns.append(f"variant {i}: unknown hook_type {ht!r}")
            v["hook_type"] = ht = "number" if re.search(r"\d", v.get("overlay", "")) else "outcome"
        seen_types.append(ht)
        words = len(v.get("overlay", "").split())
        if not 2 <= words <= 8:
            errors.append(f"variant {i}: overlay has {words} words (want 3-7)")
        text = " ".join(v.get(k, "") for k in ("title", "overlay", "subline", "description"))
        if BANNED.search(text):
            errors.append(f"variant {i}: banned hype/tease phrase '{BANNED.search(text).group(0)}'")
        if "$" in text or re.search(r"\b\d+\s*(cents?|dollars?|calories|kcal)\b", text, re.I):
            errors.append(f"variant {i}: prices/calories are not verified - remove them")
        big = " ".join(v.get(k, "") for k in ("title", "overlay", "subline"))
        for m in re.finditer(r"(\d+)\s*[- ]?\s*(min(ute)?s?)\b(?!\s*(of\s+)?(prep|cook|active))", big, re.I):
            if int(m.group(1)) < r.get("total_minutes", 0):
                errors.append(f"variant {i}: claims {m.group(0)} but recipe takes {r['total_minutes']} min")
        for m in re.finditer(r"(?<![\d.])(\d{1,3})(?![\d.])", v.get("overlay", "") + " " + v.get("title", "")):
            if int(m.group(1)) not in truth:
                errors.append(f"variant {i}: number {m.group(1)} in pin text is not a fact of this recipe")
        kw = [k.lower() for k in r.get("keywords", [])[:3]]
        core = [w for w in re.findall(r"[a-z]+", r.get("title", "").lower()) if len(w) > 3]
        title = v.get("title", "").lower()
        if core and not any(w in title for w in core) and not any(k in title for k in kw):
            warns.append(f"variant {i}: title does not contain the dish keyword")
    if len(set(seen_types)) < min(3, len(seen_types)):
        warns.append(f"hook types not varied enough: {seen_types}")
    return errors, warns
