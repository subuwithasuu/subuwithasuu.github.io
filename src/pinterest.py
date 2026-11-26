"""Pinterest API v5 client for posting to OUR OWN account.

Secrets (GitHub Secrets / env):
  PINTEREST_APP_ID, PINTEREST_APP_SECRET, PINTEREST_REFRESH_TOKEN
  or PINTEREST_ACCESS_TOKEN directly (e.g. the sandbox token from the developer portal)
  PINTEREST_API_BASE  - default https://api.pinterest.com/v5
                        Trial apps: https://api-sandbox.pinterest.com/v5
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import requests

from common import log

SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"
OAUTH_URL = "https://www.pinterest.com/oauth/"
TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"


class PinterestError(RuntimeError):
    pass


def exchange_code(app_id: str, secret: str, code: str, redirect_uri: str) -> dict:
    r = requests.post(TOKEN_URL, auth=(app_id, secret), timeout=30, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "continuous_refresh": "true"})
    if r.status_code != 200:
        raise PinterestError(f"token exchange failed: {r.status_code} {r.text[:300]}")
    return r.json()


def refresh(app_id: str, secret: str, refresh_token: str) -> dict:
    r = requests.post(TOKEN_URL, auth=(app_id, secret), timeout=30, data={
        "grant_type": "refresh_token", "refresh_token": refresh_token, "scope": SCOPES})
    if r.status_code != 200:
        raise PinterestError(f"token refresh failed: {r.status_code} {r.text[:300]}")
    return r.json()


class Pinterest:
    def __init__(self):
        self.base = os.environ.get("PINTEREST_API_BASE", "https://api.pinterest.com/v5").rstrip("/")
        self.new_refresh_token: str | None = None
        token = os.environ.get("PINTEREST_ACCESS_TOKEN")
        if not token:
            app_id = os.environ.get("PINTEREST_APP_ID")
            secret = os.environ.get("PINTEREST_APP_SECRET")
            rt = os.environ.get("PINTEREST_REFRESH_TOKEN")
            if not (app_id and secret and rt):
                raise PinterestError("Pinterest credentials missing (need APP_ID, APP_SECRET, REFRESH_TOKEN)")
            data = refresh(app_id, secret, rt)
            token = data["access_token"]
            if data.get("refresh_token") and data["refresh_token"] != rt:
                self.new_refresh_token = data["refresh_token"]
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(4):
            r = self.s.request(method, f"{self.base}{path}", timeout=60, **kw)
            if r.status_code in (200, 201):
                return r.json() if r.content else {}
            if r.status_code == 204:
                return {}
            if r.status_code in (429, 500, 502, 503):
                wait = 15 * (attempt + 1)
                log(f"pinterest {method} {path}: {r.status_code}, retry in {wait}s")
                time.sleep(wait)
                continue
            raise PinterestError(f"{method} {path}: {r.status_code} {r.text[:500]}")
        raise PinterestError(f"{method} {path}: gave up")

    # ---- account / boards -------------------------------------------------
    def me(self) -> dict:
        return self._req("GET", "/user_account")

    def list_boards(self) -> list[dict]:
        out, bookmark = [], None
        while True:
            params = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            data = self._req("GET", "/boards", params=params)
            out += data.get("items", [])
            bookmark = data.get("bookmark")
            if not bookmark:
                return out

    def update_board_description(self, board_id: str, description: str) -> dict:
        return self._req("PATCH", f"/boards/{board_id}", json={"description": description[:500]})

    def create_board(self, name: str, description: str) -> dict:
        return self._req("POST", "/boards", json={"name": name, "description": description[:500],
                                                    "privacy": "PUBLIC"})

    # ---- pins -------------------------------------------------------------
    def create_image_pin(self, board_id: str, image: Path, title: str, description: str,
                         link: str, alt_text: str) -> dict:
        data = base64.b64encode(image.read_bytes()).decode()
        return self._req("POST", "/pins", json={
            "board_id": board_id, "title": title[:100], "description": description[:500],
            "link": link, "alt_text": alt_text[:500],
            "media_source": {"source_type": "image_base64", "content_type": "image/jpeg", "data": data}})

    def upload_video(self, video: Path) -> str:
        reg = self._req("POST", "/media", json={"media_type": "video"})
        with open(video, "rb") as f:
            r = requests.post(reg["upload_url"], data=reg["upload_parameters"],
                              files={"file": (video.name, f, "video/mp4")}, timeout=300)
        if r.status_code not in (200, 201, 204):
            raise PinterestError(f"video upload failed: {r.status_code} {r.text[:300]}")
        media_id = reg["media_id"]
        for _ in range(60):                      # up to ~10 minutes
            st = self._req("GET", f"/media/{media_id}")
            if st.get("status") == "succeeded":
                return media_id
            if st.get("status") == "failed":
                raise PinterestError(f"video processing failed: {st}")
            time.sleep(10)
        raise PinterestError("video processing timed out")

    def create_video_pin(self, board_id: str, video: Path, title: str, description: str,
                         link: str, alt_text: str) -> dict:
        media_id = self.upload_video(video)
        return self._req("POST", "/pins", json={
            "board_id": board_id, "title": title[:100], "description": description[:500],
            "link": link, "alt_text": alt_text[:500],
            "media_source": {"source_type": "video_id", "media_id": media_id,
                             "cover_image_key_frame_time": 1}})

    def pin_analytics(self, pin_id: str, start: str, end: str, video: bool = False) -> dict:
        metrics = "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK" + (",VIDEO_MRC_VIEW" if video else "")
        data = self._req("GET", f"/pins/{pin_id}/analytics",
                         params={"start_date": start, "end_date": end, "metric_types": metrics})
        allm = data.get("all", {})
        summ = allm.get("summary_metrics") or allm.get("lifetime_metrics")
        if summ:
            return summ
        tot: dict = {}
        for day in allm.get("daily_metrics", []):
            for k, v in (day.get("metrics") or {}).items():
                tot[k] = tot.get(k, 0) + (v or 0)
        return tot
