"""Deterministic recipe checks. Errors block publishing; warnings are logged.

These catch the classic AI-recipe failures: impossible oven temperatures,
missing food-safety temperatures, absurd salt/leavening amounts, missing
quantities, times that don't add up, ingredients never used in the steps.
"""
from __future__ import annotations

import re
from fractions import Fraction

UNICODE_FRACTIONS = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4",
                     "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}

UNITS = {
    "", "cup", "cups", "tbsp", "tsp", "oz", "lb", "lbs", "g", "kg", "ml", "l",
    "clove", "cloves", "can", "cans", "pinch", "dash", "slice", "slices",
    "large", "medium", "small", "stalk", "stalks", "sprig", "sprigs", "bunch",
    "piece", "pieces", "fillet", "fillets", "handful", "stick", "sticks",
    "package", "packages", "jar", "block", "head", "inch", "quart", "pint",
    "fl oz", "whole", "leaves", "egg", "eggs", "scoop", "sheet", "sheets",
}

TBSP = {"tbsp": 1.0, "tsp": 1 / 3, "cup": 16.0, "cups": 16.0}

BANNED = re.compile(r"\b(cure[sd]?|detox|guaranteed weight loss|burns? fat|medical|"
                    r"prevents? cancer|miracle)\b", re.I)


def parse_qty(q) -> float | None:
    if q is None:
        return None
    if isinstance(q, (int, float)):
        return float(q)
    s = str(q).strip()
    for k, v in UNICODE_FRACTIONS.items():
        s = s.replace(k, " " + v)
    s = s.replace("-", " ").strip()
    if not s:
        return None
    # ranges like "2 to 3" -> take the upper bound
    s = re.split(r"\s+to\s+|–", s)[-1].strip()
    total = Fraction(0)
    try:
        for part in s.split():
            total += Fraction(part)
    except (ValueError, ZeroDivisionError):
        return None
    return float(total)


def _text(recipe: dict) -> str:
    return " ".join(recipe.get("steps", [])).lower()


def validate(recipe: dict, max_total: int = 60) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    req = ["title", "slug", "board", "description", "servings", "prep_minutes",
           "cook_minutes", "total_minutes", "ingredients", "steps", "pin_variants"]
    for k in req:
        if k not in recipe or recipe[k] in (None, "", []):
            errors.append(f"missing field: {k}")
    if errors:
        return errors, warns

    serv = recipe["servings"]
    if not isinstance(serv, int) or not 1 <= serv <= 16:
        errors.append(f"servings out of range: {serv}")
        serv = 4

    p, c, t = recipe["prep_minutes"], recipe["cook_minutes"], recipe["total_minutes"]
    if not all(isinstance(x, int) and x >= 0 for x in (p, c, t)):
        errors.append("times must be whole minutes")
    else:
        if t < p + c:
            errors.append(f"total time {t} < prep {p} + cook {c}")
        if t > p + c + 240:
            warns.append("total time much longer than prep+cook (resting/chilling?)")
        if t > max_total and "chill" not in _text(recipe) and "overnight" not in _text(recipe):
            warns.append(f"total time {t} min is above the {max_total} min target")

    ings = recipe["ingredients"]
    if not 2 <= len(ings) <= 25:
        errors.append(f"unusual ingredient count: {len(ings)}")
    steps_text = _text(recipe)
    for ing in ings:
        item = str(ing.get("item", "")).strip()
        if not item:
            errors.append(f"ingredient without name: {ing}")
            continue
        qty_raw = ing.get("qty")
        to_taste = "taste" in str(ing.get("note", "")).lower() or "taste" in str(qty_raw).lower()
        qty = parse_qty(qty_raw)
        if qty is None and not to_taste and "optional" not in str(ing.get("note", "")).lower():
            errors.append(f"no usable quantity for '{item}' (got {qty_raw!r})")
        unit = str(ing.get("unit", "")).strip().lower()
        if unit not in UNITS:
            warns.append(f"unusual unit '{unit}' for '{item}'")
        # main word of the ingredient should appear somewhere in the method
        words = [w for w in re.findall(r"[a-z]+", item.lower()) if len(w) > 3]
        if words and not any(w.rstrip("s") in steps_text for w in words):
            warns.append(f"ingredient '{item}' never mentioned in steps")
        # absurd-amount checks (per 4 servings)
        if qty is not None and unit in TBSP:
            tbsp = qty * TBSP[unit] * 4 / serv
            name = item.lower()
            for_water = "water" in str(ing.get("note", "")).lower() and "plus more" not in str(ing.get("note", "")).lower()
            if re.search(r"\bsalt\b", name) and tbsp > 2.5 and "brine" not in steps_text and not for_water:
                errors.append(f"too much salt: {qty} {unit} for {serv} servings")
            if re.search(r"baking (soda|powder)", name) and tbsp > 2.5:
                errors.append(f"too much {name}: {qty} {unit}")
            if re.search(r"cayenne|chili flakes|red pepper flakes", name) and tbsp > 2:
                warns.append(f"a lot of {name}: {qty} {unit}")

    steps = recipe["steps"]
    if not 3 <= len(steps) <= 14:
        errors.append(f"unusual step count: {len(steps)}")
    for s in steps:
        if len(s) > 420:
            warns.append(f"long step ({len(s)} chars)")

    # oven temperatures must be realistic
    for m in re.finditer(r"(\d{2,3})\s*°?\s*F\b", " ".join(steps)):
        f = int(m.group(1))
        if f >= 200 and not 225 <= f <= 550:
            errors.append(f"unrealistic temperature {f}°F")
    for m in re.finditer(r"(\d{2,3})\s*°\s*C\b", " ".join(steps)):
        cdeg = int(m.group(1))
        if cdeg >= 100 and not 100 <= cdeg <= 290:
            errors.append(f"unrealistic temperature {cdeg}°C")

    # food safety: poultry & ground meat need an internal temperature
    all_items = " ".join(str(i.get("item", "")).lower() for i in ings)
    raw_poultry = any(
        re.search(r"\b(chicken|turkey|duck)\b", it)
        and not re.search(r"\b(cooked|rotisserie|leftover|broth|stock|bouillon)\b", it)
        for it in (str(i.get("item", "")).lower() for i in ings))
    if raw_poultry:
        if "165" not in steps_text and "74" not in steps_text:
            errors.append("poultry recipe must state internal temp 165°F (74°C)")
    if re.search(r"\bground (beef|pork|lamb)\b|\bminced (beef|pork|lamb)\b", all_items):
        if "160" not in steps_text and "71" not in steps_text and "no longer pink" not in steps_text:
            errors.append("ground meat recipe must state 160°F (71°C) or 'no longer pink'")

    for v in recipe["pin_variants"]:
        if len(v.get("title", "")) > 100:
            errors.append("pin title over 100 chars")
        if len(v.get("description", "")) > 500:
            errors.append("pin description over 500 chars")
        if not v.get("overlay"):
            errors.append("pin variant without overlay text")

    blob = " ".join([recipe["title"], recipe["description"], *[v.get("description", "") for v in recipe["pin_variants"]]])
    if BANNED.search(blob):
        errors.append(f"health/medical claim found: {BANNED.search(blob).group(0)}")

    return errors, warns
