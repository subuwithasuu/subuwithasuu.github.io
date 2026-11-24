"""Protein per serving, recalculated from the ingredient list.

Values are grams of protein per 100 g, rounded down from USDA FoodData Central
(raw weight for meat/fish unless the ingredient says cooked; dry weight for
grains/pasta/lentils unless it says cooked/canned). Deliberately conservative:
anything not in the table counts as 0 g, so the result is a FLOOR. A recipe may
only claim slightly more than this floor (config protein.max_overclaim_pct).
"""
from __future__ import annotations

import re

from validate import parse_qty

# (pattern, protein g/100g, grams per cup, grams per item, grams per can)
TABLE: list[tuple[str, float, float | None, float | None, float | None]] = [
    (r"protein powder|whey|casein|pea protein", 75, None, 30, None),
    (r"egg whites?", 10.5, 243, 33, None),
    (r"\beggs?\b", 12.4, 243, 50, None),
    (r"greek yogurt|skyr", 9, 245, 170, None),
    (r"cottage cheese", 11, 226, None, None),
    (r"\byogh?urt", 3.5, 245, 170, None),
    (r"(cooked|rotisserie|shredded|grilled)\b.*chicken|chicken\b.*(cooked|rotisserie|shredded)", 27, 140, None, None),
    (r"ground chicken|minced chicken", 17, None, None, None),
    (r"chicken breast|chicken tenders|chicken cutlet", 22, None, 200, None),
    (r"chicken thigh", 19, None, 110, None),
    (r"chicken sausage|turkey sausage", 14, None, 85, None),
    (r"ground turkey|minced turkey", 18.5, None, None, None),
    (r"turkey breast|deli turkey|turkey", 21, None, None, None),
    (r"\bchicken\b", 19, None, None, None),
    (r"ground beef|minced beef|beef mince", 18.5, None, None, None),
    (r"steak|sirloin|flank|beef", 20, None, 225, None),
    (r"ground pork|minced pork", 17, None, None, None),
    (r"pork tenderloin|pork loin|pork chop|pork", 20, None, 170, None),
    (r"\bham\b", 16, None, None, None),
    (r"bacon", 12, None, 28, None),
    (r"sausage", 12, None, 85, None),
    (r"smoked salmon", 18, None, None, None),
    (r"salmon", 20, None, 170, None),
    (r"tuna", 24, None, None, 113),          # canned, drained
    (r"shrimp|prawns", 20, None, None, None),
    (r"cod|tilapia|white fish|halibut|haddock|pollock|mahi", 18, None, 170, None),
    (r"silken tofu", 5, 250, None, None),
    (r"tofu", 13, 250, None, None),
    (r"tempeh", 19, 166, None, None),
    (r"seitan", 25, None, None, None),
    (r"edamame", 11, 155, None, None),
    (r"(dry|dried|uncooked) (red |green |brown |black )?lentils", 24, 192, None, None),
    (r"lentils", 9, 198, None, 240),
    (r"(dry|dried) chickpeas", 19, 200, None, None),
    (r"chickpeas|garbanzo", 7, 164, None, 240),
    (r"(dry|dried) .*beans", 21, 190, None, None),
    (r"black beans|kidney beans|pinto beans|white beans|cannellini|navy beans|refried beans|\bbeans\b", 7, 172, None, 240),
    (r"cooked quinoa", 4.4, 185, None, None),
    (r"quinoa", 14, 170, None, None),
    (r"oats|oatmeal", 13, 80, None, None),
    (r"parmesan|parmigiano|pecorino", 28, 90, None, None),
    (r"mozzarella", 22, 112, None, None),
    (r"cheddar|monterey|colby|mexican blend|shredded cheese|swiss|gruyere|provolone", 23, 113, 28, None),
    (r"feta", 14, 150, None, None),
    (r"ricotta", 11, 246, None, None),
    (r"almond milk|oat milk|coconut milk|rice milk", 0.5, 240, None, None),
    (r"soy milk", 3, 243, None, None),
    (r"\bmilk\b", 3.3, 244, None, None),
    (r"peanut butter|almond butter|nut butter", 22, 256, None, None),
    (r"powdered peanut butter|pb2", 45, 96, None, None),
    (r"peanuts|almonds|cashews|pistachios", 20, 145, None, None),
    (r"hemp seeds|hemp hearts", 31, 160, None, None),
    (r"chia", 16, 160, None, None),
    (r"protein pasta|chickpea pasta|lentil pasta", 20, None, None, None),
    (r"cooked (pasta|spaghetti|penne|noodles|rice noodles)", 5, 140, None, None),
    (r"pasta|spaghetti|penne|linguine|fettuccine|macaroni|rigatoni|orzo|noodles", 12, 100, None, None),
    (r"cooked (white |brown )?rice", 2.6, 158, None, None),
    (r"\brice\b", 7, 185, None, None),
    (r"tortilla|wrap", 8, None, 45, None),
    (r"bread|bun|bagel|english muffin|pita", 9, None, 35, None),
    (r"flour", 10, 125, None, None),
]

# things that merely *mention* a protein food: "pasta water", "chicken broth", "egg wash"...
SKIP = re.compile(r"\bwater\b|broth|stock|bouillon|\bjuice\b|\boil\b|seasoning|sauce|vinegar|extract", re.I)

_COMPILED =[(re.compile(p, re.I), *rest) for p, *rest in TABLE]
COUNT_UNITS = {"", "large", "medium", "small", "whole", "egg", "eggs", "slice", "slices", "fillet",
               "fillets", "piece", "pieces", "scoop", "scoops", "breast", "breasts", "thigh", "thighs"}


def grams(ing: dict, cup_g, each_g, can_g) -> float | None:
    """Weight in grams. Prefers the metric field ('225 g'), then qty + unit."""
    metric = str(ing.get("metric", "")).lower()
    m = re.search(r"([\d.]+)\s*(kg|g|ml|l)\b", metric)
    if m:
        v = float(m.group(1))
        return v * (1000 if m.group(2) in ("kg", "l") else 1)
    qty = parse_qty(ing.get("qty"))
    if qty is None:
        return None
    unit = str(ing.get("unit", "")).strip().lower()
    per = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "oz": 28.35, "lb": 453.6, "lbs": 453.6, "fl oz": 29.6}
    if unit in per:
        return qty * per[unit]
    if unit in ("cup", "cups") and cup_g:
        return qty * cup_g
    if unit == "tbsp" and cup_g:
        return qty * cup_g / 16
    if unit == "tsp" and cup_g:
        return qty * cup_g / 48
    if unit in ("can", "cans") and can_g:
        return qty * can_g
    if unit in COUNT_UNITS and each_g:
        return qty * each_g
    return None


def protein_per_serving(recipe: dict) -> tuple[float, list[str]]:
    """Return (protein floor per serving, list of ingredients we could not weigh)."""
    total = 0.0
    unknown = []
    for ing in recipe.get("ingredients", []):
        item = str(ing.get("item", ""))
        if SKIP.search(item):
            continue
        for rx, p100, cup_g, each_g, can_g in _COMPILED:
            if rx.search(item):
                g = grams(ing, cup_g, each_g, can_g)
                if g is None:
                    unknown.append(item)
                else:
                    total += g * p100 / 100
                break
    return total / max(1, recipe.get("servings", 1)), unknown


MEAT = re.compile(r"\b(chicken|beef|steak|pork|bacon|ham|turkey|sausage|salami|pepperoni|prosciutto|"
                  r"lamb|veal|duck|fish|salmon|tuna|cod|tilapia|shrimp|prawns?|anchov\w*|crab|lobster|"
                  r"scallops?|gelatin|fish sauce|lard)\b", re.I)
PROTEIN_CLAIM = re.compile(r"(\d{1,3})\s*(?:g|grams?)\s*(?:of\s+)?protein", re.I)


def check_protein(recipe: dict, cfg: dict, board: dict) -> list[str]:
    """Verify the protein claim + niche rules. Sets recipe['protein_g'] (verified value)
    and rewrites any protein number in pin text that exceeds it. Returns errors."""
    errors = []
    floor, unknown = protein_per_serving(recipe)
    claimed = recipe.get("protein_g_per_serving")
    pc = cfg["protein"]
    if not isinstance(claimed, (int, float)):
        errors.append("missing protein_g_per_serving")
        claimed = floor
    if claimed > floor * (1 + pc["max_overclaim_pct"] / 100) + 2:
        errors.append(f"protein overclaimed: says {claimed}g, ingredients give ~{floor:.0f}g"
                      + (f" (could not weigh: {unknown})" if unknown else ""))
    verified = int(min(claimed, floor))      # never show more than the ingredients support
    minimum = board.get("min_protein", pc["min_per_serving"])
    if verified < minimum:
        errors.append(f"only ~{verified}g protein per serving (board needs {minimum}g+)")
    if board.get("vegetarian"):
        for ing in recipe.get("ingredients", []):
            if MEAT.search(str(ing.get("item", ""))) and not re.search(r"vegetable|veggie|plant|vegan", str(ing.get("item", "")), re.I):
                errors.append(f"vegetarian board but contains '{ing['item']}'")
    if board.get("max_minutes") and recipe.get("total_minutes", 0) > board["max_minutes"]:
        errors.append(f"board needs <= {board['max_minutes']} min, recipe takes {recipe.get('total_minutes')}")
    recipe["protein_g"] = verified

    def fix(text: str) -> str:
        return PROTEIN_CLAIM.sub(lambda m: m.group(0).replace(m.group(1), str(verified))
                                 if int(m.group(1)) > verified else m.group(0), text)
    for v in recipe.get("pin_variants", []):
        for k in ("title", "description", "overlay", "subline", "alt_text"):
            if v.get(k):
                v[k] = fix(v[k])
    for k in ("title", "description"):
        recipe[k] = fix(recipe.get(k, ""))
    vs = recipe.get("video_script") or {}
    for k in ("hook", "outro"):
        if vs.get(k):
            vs[k] = fix(vs[k])
    vs["steps_short"] = [fix(s) for s in vs.get("steps_short", [])]
    return errors
