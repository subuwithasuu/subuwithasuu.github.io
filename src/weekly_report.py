"""Weekly summary -> report.md (the workflow opens it as a GitHub Issue, which
GitHub emails to you). Optional direct email if SMTP_* env vars are set."""
from __future__ import annotations

import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage

from common import (POSTED_FILE, REJECTED_FILE, ROOT, load_config, load_recipes, log, now_utc, read_json,
                    write_json)
from queue_plan import content_runway_days, load_queue
import learn
from learn import METRICS_FILE


def main() -> int:
    cfg = load_config()
    now = now_utc()
    week_ago = now - timedelta(days=7)
    posted = read_json(POSTED_FILE, [])
    q = load_queue()
    recipes = load_recipes()
    week = [p for p in posted if datetime.fromisoformat(p["posted_at"]) >= week_ago]
    failed = [s for s in q["slots"] if s["status"] == "failed" and datetime.fromisoformat(s["due"]) >= week_ago]
    rejected = [r for r in read_json(REJECTED_FILE, []) if r["date"] >= week_ago.date().isoformat()]

    stats = []
    metrics = read_json(METRICS_FILE, {})
    try:
        from pinterest import Pinterest
        api = Pinterest()
        # lifetime-to-date numbers for every pin from the last 85 days (API window is 90)
        recent = [p for p in posted if datetime.fromisoformat(p["posted_at"]) >= now - timedelta(days=85)][-300:]
        end = now.date().isoformat()
        for p in recent:
            try:
                start = p["posted_at"][:10]
                m = api.pin_analytics(p["pin_id"], start, end, video=p["kind"] == "video")
                metrics[p["pin_id"]] = {**m, "fetched": end}
                if datetime.fromisoformat(p["posted_at"]) >= now - timedelta(days=30):
                    stats.append({**p, **m})
            except Exception as e:      # analytics can lag for brand-new pins
                log(f"analytics {p['pin_id']}: {e}")
        write_json(METRICS_FILE, metrics)
    except Exception as e:
        log(f"no analytics: {e}")
    # Without the API (RSS / CSV pins): rank pins by real site visits from GoatCounter
    clicks = {}
    try:
        import clicks as clicks_mod
        since = now - timedelta(days=85)
        clicks = clicks_mod.pin_clicks(cfg, since, now)
        for p in posted:
            if p.get("via") in ("rss", "csv") and datetime.fromisoformat(p["posted_at"]) >= since:
                metrics[p["pin_id"]] = {"SAVE": 0, "OUTBOUND_CLICK": clicks.get(p["pin_id"], 0),
                                        "source": "goatcounter", "fetched": now.date().isoformat()}
        write_json(METRICS_FILE, metrics)
    except Exception as e:
        log(f"no click data: {e}")
    learnings = learn.run(posted, metrics, now)

    def tot(k):
        return sum(int(s.get(k, 0) or 0) for s in stats)

    lines = [f"# Pinterest weekly report - {now.date().isoformat()}", "",
             f"**Posted this week:** {len(week)} pins "
             f"({sum(1 for p in week if p['kind'] == 'video')} video, {sum(1 for p in week if p['kind'] == 'image')} image)",
             f"**Failed posts this week:** {len(failed)}",
             f"**Recipes on site:** {len(recipes)}  |  **rejected by fact-check this week:** {len(rejected)}",
             f"**Content runway:** ~{content_runway_days(cfg, q, recipes):.0f} days of unposted pins", ""]
    if stats:
        lines += ["## Last 30 days (pins posted in that window)",
                  f"Impressions **{tot('IMPRESSION')}** | Saves **{tot('SAVE')}** | "
                  f"Pin clicks **{tot('PIN_CLICK')}** | Outbound clicks to site **{tot('OUTBOUND_CLICK')}** | "
                  f"Video views **{tot('VIDEO_MRC_VIEW')}**", "",
                  "## Top pins by impressions", "| Pin | Board | Type | Impr. | Saves | Site clicks |", "|---|---|---|---|---|---|"]
        for s in sorted(stats, key=lambda s: -int(s.get("IMPRESSION", 0) or 0))[:10]:
            lines.append(f"| [{s['title'][:60]}](https://www.pinterest.com/pin/{s['pin_id']}/) | {s['board']} | {s['kind']} | "
                         f"{s.get('IMPRESSION', 0)} | {s.get('SAVE', 0)} | {s.get('OUTBOUND_CLICK', 0)} |")
        lines.append("")
    elif clicks:
        month = [p for p in posted if datetime.fromisoformat(p["posted_at"]) >= now - timedelta(days=30)]
        top = sorted(month, key=lambda p: -clicks.get(p["pin_id"], 0))[:10]
        lines += ["## Last 30 days: visits to the site from each pin (GoatCounter)",
                  f"Total pin visits **{sum(clicks.get(p['pin_id'], 0) for p in month)}** "
                  f"from {len(month)} pins posted in that window", "",
                  "| Pin | Board | Type | Site visits |", "|---|---|---|---|"]
        lines += [f"| {p['title'][:60]} | {p['board']} | {p['kind']} | {clicks.get(p['pin_id'], 0)} |" for p in top]
        lines.append("")
    else:
        lines += ["_No click data yet (GoatCounter not set up, or no visits yet)._", ""]
    lines += learn.report_lines(learnings, cfg)
    if failed:
        lines += ["## Failures", *[f"- `{s['slot']}` {s.get('item', {}).get('slug')}: {s.get('error', '')[:200]}" for s in failed], ""]
    if rejected:
        lines += ["## Recipes rejected by the fact-check", *[f"- {r['title']} ({r['board']})" for r in rejected[:20]], ""]
    lines += ["## This week's pins", *[f"- {p['posted_at'][:10]} {p['title']} ({p['board']}, {p['kind']})" for p in week]]
    report = "\n".join(lines)
    (ROOT / "report.md").write_text(report, encoding="utf-8")
    print(report)

    if os.environ.get("SMTP_HOST") and os.environ.get("REPORT_TO"):
        msg = EmailMessage()
        msg["Subject"] = f"Pinterest weekly report {now.date().isoformat()}"
        msg["From"] = os.environ.get("SMTP_USER")
        msg["To"] = os.environ["REPORT_TO"]
        msg.set_content(report)
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 465))) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
        log("email sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
