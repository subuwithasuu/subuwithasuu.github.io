"""Weekly trend scan: what high-protein food is rising right now?

Once every 7 days (on the first recipe run after that), Gemini searches the web
(Google Search grounding, free tier) for food and protein trends that are rising
this month: dishes, ingredients, formats. Results go to data/state/trends.json and
are fed into the recipe idea prompt, so roughly a third of new recipes ride a
current trend. Trend recipes still pass the same fact-check, protein check and
validator as every other recipe, so they stay real dishes with real quantities.
If the search quota is used up, the previous week's trends are kept.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from common import STATE_DIR, log, now_utc, read_json, write_json

TRENDS_FILE = STATE_DIR / "trends.json"
MAX_AGE = timedelta(days=7)


def current() -> list[dict]:
    return read_json(TRENDS_FILE, {}).get("trends", [])


def refresh(gem, cfg: dict, force: bool = False) -> list[dict]:
    old = read_json(TRENDS_FILE, {})
    if not force and old.get("updated") and now_utc() - datetime.fromisoformat(old["updated"]) < MAX_AGE:
        return old.get("trends", [])
    boards = [b["name"] for b in cfg["boards"]]
    today = now_utc().date()
    prompt = f"""Today is {today.isoformat()}. Search the web for HIGH-PROTEIN food and recipe trends that are
rising right now (the last 4-6 weeks): on Pinterest (Pinterest Trends / Pinterest Predicts), TikTok and
Instagram food creators, Google search interest, food media and grocery news. Think of specific dishes,
ingredients (e.g. a protein ingredient getting popular), formats (e.g. bowls, bakes, sheet pans) and
seasonal/holiday moments coming up in the next 4 weeks.

Keep only trends that fit this niche: {cfg['niche']['description']}.
Skip supplements brands, diets with medical claims, and anything unsafe.

Reply with ONLY a JSON array inside a ```json code block, 5-8 items:
[{{"trend": "short name", "why_now": "one line with the evidence",
   "dish_ideas": ["2-3 concrete, real, cookable dishes riding this trend"],
   "boards": ["which of these boards it fits: {', '.join(boards)}"],
   "source": "one URL"}}]"""
    try:
        res = gem.generate_json(prompt, grounded=True, json_mode=False, temperature=0.3)
        trends = [t for t in res if isinstance(t, dict) and t.get("trend") and t.get("dish_ideas")][:8]
        if not trends:
            raise ValueError("empty trend list")
    except Exception as e:                       # grounding quota, bad JSON: keep last week's
        log(f"trend scan failed ({e}); keeping previous trends")
        return old.get("trends", [])
    write_json(TRENDS_FILE, {"updated": now_utc().isoformat(), "trends": trends})
    log("trends: " + "; ".join(t["trend"] for t in trends))
    return trends


def prompt_block(board: dict) -> str:
    """Lines for the idea prompt: trends that fit this board (or all, if none match)."""
    trends = current()
    fit = [t for t in trends if board["name"] in t.get("boards", [])] or trends
    if not fit:
        return ""
    lines = "\n".join(f"- {t['trend']}: {', '.join(t['dish_ideas'][:3])}" for t in fit[:5])
    return f"""
RISING RIGHT NOW (from this week's trend scan). Make about one third of your ideas ride one of these
trends when it genuinely fits this board, still as a real, proven dish:
{lines}
"""


if __name__ == "__main__":
    from common import load_config
    from gemini import Gemini
    cfg = load_config()
    print(json.dumps(refresh(Gemini(cfg), cfg, force=True), indent=1, ensure_ascii=False))
