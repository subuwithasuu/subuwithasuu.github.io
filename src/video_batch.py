"""Weekly video pins (runs on GitHub Actions, your Mac can be off).

RSS auto-publish only carries images, so video pins go through Pinterest's bulk
.csv upload instead:
  1. this script tops the pool up to `video_batch.per_week` ready videos
     (one per recipe that has no video yet, boards balanced), renders them with the
     free Kokoro voice + recipe photos, and saves them to build/videos_out/
  2. the workflow uploads them to the GitHub release "videos"; the site build copies
     that release into /videos/ so each file has a public URL Pinterest can fetch
  3. the Mac uploader (scripts/mac_video_upload.py) turns the ready videos into a
     .csv with publish dates spread over the week and uploads it to Pinterest

State: data/state/videos.json, one entry per video:
  {id, slug, board, variant, title, description, link, keywords, file,
   status: ready|uploaded|failed, created, uploaded_at?, publish_at?}

  python src/video_batch.py            # render what is missing
  python src/video_batch.py --prune    # list release files older than keep_days after upload
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta

from common import BUILD, DATA, load_config, load_recipes, log, now_utc, read_json, recipe_url, write_json

VIDEOS_FILE = DATA / "state" / "videos.json"
OUT = BUILD / "videos_out"


def pick(cfg: dict, pool: list[dict], n: int) -> list[dict]:
    done = {v["slug"] for v in pool}
    recipes = [r for r in load_recipes() if r.get("video_script") and r.get("photos")
               and r["slug"] not in done]
    recipes.sort(key=lambda r: r.get("created", ""), reverse=True)
    per_board = Counter(v["board"] for v in pool)
    chosen = []
    while recipes and len(chosen) < n:            # always take from the least-covered board
        r = min(recipes, key=lambda r: per_board[r["board"]])   # ties: newest first
        recipes.remove(r)
        per_board[r["board"]] += 1
        chosen.append(r)
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prune", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    vb = cfg["video_batch"]
    pool = read_json(VIDEOS_FILE, [])

    if a.prune:                                   # print release files that can go
        cutoff = now_utc() - timedelta(days=vb["keep_days"])
        for v in pool:
            if v["status"] == "uploaded" and datetime.fromisoformat(v["uploaded_at"]) < cutoff \
                    and not v.get("pruned"):
                print(v["file"])
                v["pruned"] = True
        write_json(VIDEOS_FILE, pool)
        return 0

    ready = sum(v["status"] == "ready" for v in pool)
    need = max(0, vb["per_week"] - ready)
    log(f"{ready} videos ready, rendering {need}")
    if not need:
        return 0
    import make_video
    OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for r in pick(cfg, pool, need):
        vid = f"v-{r['slug'][:60]}"
        variant = len([v for v in pool if v["board"] == r["board"]]) % len(r["pin_variants"])
        pv = r["pin_variants"][variant]
        try:
            src = make_video.render_for_recipe(cfg, r)
        except Exception as e:                     # bad photo, voice hiccup: skip this recipe
            log(f"ERROR {r['slug']}: {e}")
            pool.append({"id": vid, "slug": r["slug"], "board": r["board"], "status": "failed",
                         "error": str(e)[:300], "created": now_utc().isoformat()})
            continue
        dest = OUT / f"{vid}.mp4"
        src.replace(dest)
        pool.append({
            "id": vid, "slug": r["slug"], "board": r["board"], "variant": variant,
            "title": pv["title"][:100], "description": pv["description"][:500],
            "link": f"{recipe_url(cfg, r)}?pin={vid}",
            "keywords": ", ".join(r.get("keywords", [])[:8]),
            "file": dest.name, "status": "ready", "created": now_utc().isoformat(),
        })
        made += 1
        log(f"VIDEO {dest.name} ({dest.stat().st_size / 1e6:.1f} MB) -> {r['board']}")
    write_json(VIDEOS_FILE, pool)
    log(f"rendered {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
