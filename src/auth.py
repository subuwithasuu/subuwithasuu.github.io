"""One-time Pinterest login on YOUR Mac (also the flow shown in the Standard-access demo video).

  export PINTEREST_APP_ID=...  PINTEREST_APP_SECRET=...
  .venv/bin/python src/auth.py

It opens Pinterest's own consent page in your browser (you log in there yourself),
catches the redirect on http://localhost:8085/callback, and prints the refresh token
for you to paste into GitHub Secrets as PINTEREST_REFRESH_TOKEN.
Add  http://localhost:8085/callback  as a Redirect URI in your app settings first.
"""
from __future__ import annotations

import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from pinterest import OAUTH_URL, SCOPES, exchange_code

REDIRECT = "http://localhost:8085/callback"


def main() -> int:
    app_id, secret = os.environ.get("PINTEREST_APP_ID"), os.environ.get("PINTEREST_APP_SECRET")
    if not (app_id and secret):
        print("Set PINTEREST_APP_ID and PINTEREST_APP_SECRET first.")
        return 1
    state = secrets.token_urlsafe(16)
    url = OAUTH_URL + "?" + urlencode({"client_id": app_id, "redirect_uri": REDIRECT,
                                       "response_type": "code", "scope": SCOPES, "state": state})
    result = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            result.update({k: v[0] for k, v in qs.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Connected. You can close this tab and go back to the terminal.</h2>")

        def log_message(self, *a):
            pass

    print("Opening Pinterest consent page:\n" + url)
    webbrowser.open(url)
    srv = HTTPServer(("localhost", 8085), H)
    while "code" not in result and "error" not in result:
        srv.handle_request()
    if result.get("state") != state or "code" not in result:
        print("Login failed:", result)
        return 1
    tok = exchange_code(app_id, secret, result["code"], REDIRECT)
    print("\nSuccess. Scopes:", tok.get("scope"))
    print("Access token expires in", tok.get("expires_in"), "s; refresh token expires in",
          tok.get("refresh_token_expires_in"), "s")
    print("\nPaste this into GitHub > repo > Settings > Secrets > Actions as PINTEREST_REFRESH_TOKEN:\n")
    print(tok["refresh_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
