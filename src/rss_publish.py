"""Publish pins through Pinterest's own RSS auto-publish (no developer API needed).

Pinterest denied the API app's trial access (Nov 2026), so posting works like this:
  1. hourly: plan the calendar, render every pin that is due, save the image in
     data/pins/<slot>.jpg (committed, served by GitHub Pages at /pins/<slot>.jpg)
  2. record it in posted.json (via="rss")
  3. build_site.py writes one RSS feed per board (/feeds/<board-slug>.xml) with the
     latest published pins; each feed is connected to its board in Pinterest settings
     (Settings > Create Pins in bulk > Auto-publish). Pinterest reads the feeds and
     creates the pins itself, usually within a day.
RSS pins are images only, so the queue's video share is 0 in RSS mode.

  python src/rss_publish.py            # publish what is due
  DRY_RUN=1 python src/rss_publish.py  # render only, change nothing
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from PIL import Image

from common import (DATA, POSTED_FILE, QUEUE_FILE, RECIPES_DIR, load_config, log, now_utc,
                    read_json, recipe_url, write_json)
from queue_plan import plan
from post_due import STALE_HOURS, check_live_site
import make_pin

PINS_DIR = DATA / "pins"


def main() -> int:
    cfg = load_config()
    dry = os.environ.get("DRY_RUN") == "1"
    now = now_utc()
    q = plan(cfg, now, persist=not dry)
    due = [s for s in q["slots"] if s["status"] == "planned" and s.get("item")
           and s["due"] <= now.isoformat()]
    if not due:
        log("nothing due")
        return 0
    if not dry:
        problem = check_live_site(cfg)
        if problem:
            log(f"NOT PUBLISHING: {problem}")
            return 1
    posted = read_json(POSTED_FILE, [])
    PINS_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    for slot in due:
        if done >= cfg["schedule"]["max_posts_per_run"]:
            break
        if now - datetime.fromisoformat(slot["due"]) > timedelta(hours=STALE_HOURS):
            slot["status"] = "skipped"
            continue
        it = slot["item"]
        if it["kind"] != "image":                    # RSS cannot carry video pins
            slot["status"] = "skipped"
            slot["error"] = "video pins are not supported by RSS auto-publish"
            continue
        recipe = read_json(RECIPES_DIR / f"{it['slug']}.json", None)
        if not recipe:
            slot.update(status="failed", error="recipe file missing")
            continue
        v = recipe["pin_variants"][it["variant"] % len(recipe["pin_variants"])]
        try:
            img = make_pin.render_for_recipe(cfg, recipe, it["variant"], it.get("layout"), it.get("palette"))
        except Exception as e:                       # photo download hiccup etc.: retry next hour
            slot["attempts"] = slot.get("attempts", 0) + 1
            slot["error"] = str(e)[:300]
            if slot["attempts"] >= 3:
                slot["status"] = "failed"
            log(f"ERROR rendering {slot['slot']}: {e}")
            continue
        guid = slot["slot"].replace("#", "-")
        if dry:
            log(f"[dry] {slot['slot']} -> {img.name} | {v['title']}")
            done += 1
            continue
        dest = PINS_DIR / f"{guid}.jpg"
        Image.open(img).convert("RGB").save(dest, "JPEG", quality=84, optimize=True, progressive=True)
        d_layout, d_pal = make_pin.pin_style(cfg, recipe, it["variant"])
        stamp = now_utc().isoformat()
        slot.update(status="posted", pin_id=guid, posted_at=stamp, via="rss")
        posted.append({
            "pin_id": guid, "via": "rss", "slot": slot["slot"], "slug": it["slug"], "kind": "image",
            "variant": it["variant"], "board": recipe["board"], "title": v["title"][:100],
            "description": v["description"][:500], "alt_text": (v.get("alt_text") or recipe["title"])[:500],
            "link": f"{recipe_url(cfg, recipe)}?pin={guid}", "image": f"pins/{guid}.jpg",
            "posted_at": stamp,
            # attributes for the weekly feedback loop:
            "hook_type": v.get("hook_type"), "overlay": v.get("overlay"),
            "layout": it.get("layout") or d_layout, "palette": it.get("palette", d_pal),
            "category": recipe.get("category"), "protein_g": recipe.get("protein_g"),
            "total_minutes": recipe.get("total_minutes"),
        })
        log(f"PUBLISHED {slot['slot']} -> feed of '{recipe['board']}' | {v['title']}")
        done += 1
    if not dry:
        write_json(POSTED_FILE, posted)
        write_json(QUEUE_FILE, q)
    log(f"run finished: {done} {'rendered' if dry else 'published to RSS'}")
    # tell the workflow whether the site needs a rebuild
    out = os.environ.get("GITHUB_OUTPUT")
    if out and not dry:
        with open(out, "a") as f:
            f.write(f"published={done}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
