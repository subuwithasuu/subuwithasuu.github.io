"""Shared helpers: paths, config, JSON state, logging."""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RECIPES_DIR = DATA / "recipes"
STATE_DIR = DATA / "state"
QUEUE_FILE = STATE_DIR / "queue.json"
POSTED_FILE = STATE_DIR / "posted.json"
BOARDS_FILE = STATE_DIR / "boards.json"
REJECTED_FILE = STATE_DIR / "rejected.json"
SITE_OUT = ROOT / "site"          # built website (deployed to GitHub Pages)
BUILD = ROOT / "build"            # temp renders (git-ignored)
FONTS = ROOT / "fonts"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # The site address is deliberately NOT overridable: config.yaml is the single
    # source of truth, so pins can never link to a different address than the site.
    cfg["site"]["base_url"] = cfg["site"]["base_url"].rstrip("/")
    if os.environ.get("PINTEREST_VERIFY"):
        cfg["site"]["pinterest_verify"] = os.environ["PINTEREST_VERIFY"]
    if os.environ.get("GEMINI_MODEL"):
        cfg["gemini"]["model"] = os.environ["GEMINI_MODEL"]
    return cfg


def read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:70].strip("-")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_recipes() -> list[dict]:
    out = []
    for p in sorted(RECIPES_DIR.glob("*.json")):
        out.append(read_json(p, None))
    return [r for r in out if r]


def recipe_url(cfg: dict, recipe: dict) -> str:
    return f"{cfg['site']['base_url']}/recipes/{recipe['slug']}/"


def board_slug(name: str) -> str:
    return slugify(name)
