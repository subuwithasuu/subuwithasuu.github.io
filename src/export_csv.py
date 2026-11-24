"""BACKUP route if API access is slow: Pinterest's own bulk-upload CSV.

Renders the next N planned image pins into site/pins/ (served by GitHub Pages so
Pinterest can fetch them) and writes site/pins/bulk.csv in Pinterest's bulk-create
format, with 'Publish date' so Pinterest itself schedules them.
Upload it at pinterest.com > Create > Create Pin > Bulk create (max 200 rows per file).
The same CSV can also be pasted into a Google Sheet for a Make.com scenario.

  python src/export_csv.py --days 14
"""
from __future__ import annotations

import argparse
import csv
import shutil

from common import RECIPES_DIR, SITE_OUT, load_config, now_utc, read_json, recipe_url
from queue_plan import plan
import make_pin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    cfg = load_config()
    q = plan(cfg, now_utc(), persist=False, horizon=a.days)
    out_dir = SITE_OUT / "pins"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in q["slots"]:
        it = s.get("item")
        if s["status"] != "planned" or not it or it["kind"] != "image":
            continue
        r = read_json(RECIPES_DIR / f"{it['slug']}.json", None)
        v = r["pin_variants"][it["variant"]]
        img = make_pin.render_for_recipe(cfg, r, it["variant"])
        shutil.copy(img, out_dir / img.name)
        rows.append({"Title": v["title"][:100], "Media URL": f"{cfg['site']['base_url']}/pins/{img.name}",
                     "Pinterest board": r["board"], "Thumbnail": "", "Description": v["description"][:500],
                     "Link": recipe_url(cfg, r), "Publish date": s["due"][:19],
                     "Keywords": ", ".join(r.get("keywords", [])[:8])})
        if len(rows) >= 200:
            break
    with open(out_dir / "bulk.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["Title"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} pins -> {out_dir / 'bulk.csv'}")


if __name__ == "__main__":
    main()
