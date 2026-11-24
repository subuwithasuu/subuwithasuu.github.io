"""Hourly job: plan the calendar, then publish every pin that is due.

  python src/post_due.py            # real posting
  DRY_RUN=1 python src/post_due.py  # render everything, post nothing
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timedelta

from common import (BOARDS_FILE, BUILD, POSTED_FILE, QUEUE_FILE, RECIPES_DIR, load_config, log,
                    now_utc, read_json, recipe_url, write_json)
from queue_plan import plan
import make_pin

MAX_ATTEMPTS = 3
STALE_HOURS = 30     # a pin overdue by more than this is skipped (e.g. after a long outage)


def ensure_boards(api, cfg) -> dict:
    known = read_json(BOARDS_FILE, {})
    want = [b for b in cfg["boards"] if b["name"] not in known]
    if not want:
        return known
    existing = {b["name"].lower(): b["id"] for b in api.list_boards()}
    for b in want:
        if b["name"].lower() in existing:
            known[b["name"]] = existing[b["name"].lower()]
        else:
            created = api.create_board(b["name"], b["description"])
            known[b["name"]] = created["id"]
            log(f"created board {b['name']} -> {created['id']}")
    write_json(BOARDS_FILE, known)
    return known


def check_live_site(cfg) -> str | None:
    """Pins must link to the FINAL address from the very first pin. Returns an error
    message unless the site really serves our pages at config site.base_url
    (right host, HTTPS, no redirect elsewhere)."""
    import requests
    from urllib.parse import urlparse
    base = cfg["site"]["base_url"]
    if not base.startswith("https://") or "YOUR-" in base:
        return f"site.base_url is not a final https address: {base}"
    url = f"{base}/privacy/"
    try:
        r = requests.get(url, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        return f"site not reachable at {url}: {e}"
    if r.status_code != 200:
        return f"{url} returned HTTP {r.status_code}"
    if urlparse(r.url).netloc != urlparse(base).netloc:
        return f"{url} redirects to {r.url}; pins would point at the wrong address"
    if cfg["site"]["name"] not in r.text:
        return f"{url} does not look like our site (name '{cfg['site']['name']}' not found)"
    return None


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

    api = None
    boards = {}
    if not dry:
        problem = check_live_site(cfg)
        if problem:
            log(f"NOT POSTING: {problem}")
            return 1          # red run -> GitHub emails you; nothing is posted
        from pinterest import Pinterest
        api = Pinterest()
        boards = ensure_boards(api, cfg)
        if api.new_refresh_token:            # hand the rotated token to the workflow
            out = os.environ.get("NEW_REFRESH_TOKEN_FILE")
            if out:
                with open(out, "w") as f:
                    f.write(api.new_refresh_token)

    posted_log = read_json(POSTED_FILE, [])
    done = 0
    for slot in due:
        if done >= cfg["schedule"]["max_posts_per_run"]:
            break
        if now - datetime.fromisoformat(slot["due"]) > timedelta(hours=STALE_HOURS):
            slot["status"] = "skipped"
            log(f"skip stale slot {slot['slot']}")
            continue
        it = slot["item"]
        recipe = read_json(RECIPES_DIR / f"{it['slug']}.json", None)
        if not recipe:
            slot["status"] = "failed"
            slot["error"] = "recipe file missing"
            continue
        v = recipe["pin_variants"][it["variant"] % len(recipe["pin_variants"])]
        link = recipe_url(cfg, recipe)
        try:
            if it["kind"] == "video":
                import make_video
                media = make_video.render_for_recipe(cfg, recipe)
            else:
                media = make_pin.render_for_recipe(cfg, recipe, it["variant"], it.get("layout"), it.get("palette"))
            if dry:
                log(f"[dry] {slot['slot']} {it['kind']} -> {media.name} | {v['title']}")
                slot["dry_run_media"] = str(media.relative_to(BUILD.parent))
                done += 1
                continue
            fn = api.create_video_pin if it["kind"] == "video" else api.create_image_pin
            pin = fn(boards[recipe["board"]], media, v["title"], v["description"], link,
                     v.get("alt_text") or recipe["title"])
            slot.update(status="posted", pin_id=pin["id"], posted_at=now_utc().isoformat())
            d_layout, d_pal = make_pin.pin_style(cfg, recipe, it["variant"])
            posted_log.append({"pin_id": pin["id"], "slot": slot["slot"], "slug": it["slug"],
                               "kind": it["kind"], "variant": it["variant"], "board": recipe["board"],
                               "title": v["title"], "posted_at": slot["posted_at"],
                               # attributes the weekly feedback loop compares:
                               "hook_type": v.get("hook_type"), "overlay": v.get("overlay"),
                               "layout": (it.get("layout") or d_layout) if it["kind"] == "image" else "video",
                               "palette": it.get("palette", d_pal) if it["kind"] == "image" else None,
                               "category": recipe.get("category"), "protein_g": recipe.get("protein_g"),
                               "total_minutes": recipe.get("total_minutes")})
            log(f"POSTED {slot['slot']} {it['kind']} pin {pin['id']} | {v['title']}")
            done += 1
        except Exception as e:                       # keep going; retry next run
            slot["attempts"] = slot.get("attempts", 0) + 1
            slot["error"] = str(e)[:500]
            if slot["attempts"] >= MAX_ATTEMPTS:
                slot["status"] = "failed"
            log(f"ERROR {slot['slot']}: {e}")
            traceback.print_exc()
    if not dry:
        write_json(POSTED_FILE, posted_log)
        write_json(QUEUE_FILE, q)
    log(f"run finished: {done} {'rendered' if dry else 'posted'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
