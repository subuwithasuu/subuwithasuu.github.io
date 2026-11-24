# Pinterest Autopilot

A fully automated Pinterest account plus a recipe website, run by GitHub Actions on free tools only
(Gemini free tier, Unsplash, Pillow, ffmpeg, Kokoro TTS). Start with **[SETUP.md](SETUP.md)**.

```
config.yaml          niche, boards, schedule, design: the one file to edit
src/gen_recipes.py   write + fact-check recipes (Gemini) + Unsplash photos
src/validate.py      hard recipe safety/sanity rules
src/make_pin.py      1000x1500 image pins (3 layouts x 5 palettes)
src/make_video.py    1080x1920 narrated video pins (Kokoro + ffmpeg)
src/queue_plan.py    posting calendar (ramp, then 3-5/day, ~25% video)
src/post_due.py      hourly poster (DRY_RUN=1 to render only)
src/pinterest.py     Pinterest API v5 client
src/build_site.py    static recipe site with schema.org Recipe markup
src/weekly_report.py weekly summary (GitHub Issue / optional email)
src/auth.py          one-time Pinterest login on your Mac
src/export_csv.py    backup: Pinterest bulk-upload CSV
tests/run_tests.py   offline tests (run in a scratch copy)
```
