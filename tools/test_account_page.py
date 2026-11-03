#!/usr/bin/env python3
"""Test account.html, account.js and the navbar's account hint in a headless Chrome or Chromium
(CHROME=path, else one on PATH or Playwright's), against a stand-in for the accounts service's
/api/ (forza-live, accounts/README.md) served on 127.0.0.1 with the assembled site.

Checks:
- a copy of the site without /api/ says accounts are not available;
- signed out, the page shows the sign-in methods the service offers, as forms that post to
  allauth's provider redirect with callback_url /account.html, and the 13-or-older line;
- signed in, it shows the account, with the API's text as text, and the navbar says "Account";
- ?error=busy is shown, and the address is cleaned;
- the other pages never call /api/.

  python3 tools/test_account_page.py   # needs no packages

The site is assembled with tools/assemble.sh, which copies tracked files: git add new pages first.
"""
import glob
import html.parser
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
failures = []

CONFIG = {"status": 200, "data": {
    "account": {"login_methods": ["email"], "is_open_for_signup": False, "login_by_code_enabled": True},
    "socialaccount": {"providers": [{"id": "discord", "name": "Discord", "flows": ["provider_redirect"]},
                                    {"id": "github", "name": "GitHub", "flows": ["provider_redirect"]}]}}}
ME = {"id": "K7Q2-XM4P", "created": "2026-11-03", "methods": ["discord", "email"], "sessions": 2, "recent": True,
      "link": None, "events": [{"at": "2026-11-03T12:00:00Z", "event": "<img src=x onerror=alert(1)>signed in"}]}


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


class Dom(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.texts = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.texts.append(data)

    def element(self, id_):
        return next((a for t, a in self.tags if a.get("id") == id_), None)

    def shown(self, id_):
        a = self.element(id_)
        return a is not None and "hidden" not in a

    def text(self):
        return " ".join(" ".join(self.texts).split())


def find_browser():
    env = os.environ.get("CHROME", "")
    names = [shutil.which(env) or env if env else ""] + [shutil.which(n) or "" for n in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "chrome-headless-shell")]
    names += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True)
    names += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")), reverse=True)
    return next((n for n in names if n and os.access(n, os.X_OK)), None)


def dom_of(browser, url, profile):
    profile = Path(tempfile.mkdtemp(dir=profile))
    args = [browser, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}", "--virtual-time-budget=10000", "--dump-dom", url]
    if "headless-shell" not in browser:
        args.insert(1, "--headless=new")
    p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    d = Dom()
    d.feed(p.stdout)
    return d


class Api:
    """What the accounts service answers, by scenario: none (a static copy), out or in."""
    mode = "out"
    calls = []


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if not self.path.startswith("/api/"):
            return super().do_GET()
        Api.calls.append(self.path)
        if Api.mode == "none":
            return self.reply(404, b"<html>Not Found</html>", "text/html")
        if self.path.startswith("/api/auth/browser/v1/config"):
            return self.reply(200, json.dumps(CONFIG).encode(), "application/json")
        if self.path.startswith("/api/me"):
            if Api.mode == "in":
                return self.reply(200, json.dumps(ME).encode(), "application/json")
            return self.reply(401, b'{"detail": "Unauthorized"}', "application/json")
        return self.reply(404, b'{"status": 404}', "application/json")

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    browser = find_browser()
    if not browser:
        print("no Chrome or Chromium found (set CHROME=path)")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="account-page-test-"))
    try:
        site = tmp / "site"
        subprocess.run([str(TOOLS / "assemble.sh"), str(site)], check=True)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        profile = tmp / "profiles"
        profile.mkdir()
        try:
            print("-- a copy of the site without /api/")
            Api.mode = "none"
            d = dom_of(browser, base + "/account.html", profile)
            check("not available" in d.text(), "account.html says accounts are not available")
            check(not d.shown("signin") and not d.shown("profile"), "and shows neither sign-in nor an account")

            print("-- signed out")
            Api.mode = "out"
            d = dom_of(browser, base + "/account.html", profile)
            check(d.shown("signin") and not d.shown("profile"), "the sign-in section is shown")
            forms = [a for t, a in d.tags if t == "form" and a.get("data-provider")]
            check(sorted(f["data-provider"] for f in forms if "hidden" not in f) == ["discord", "github"],
                  "Discord and GitHub, as the service offers them")
            check(all(f.get("action") == "/api/auth/browser/v1/auth/provider/redirect" and f.get("method") == "post"
                      for f in forms), "each is a form posting to allauth's provider redirect")
            callbacks = [a.get("value") for t, a in d.tags if t == "input" and a.get("name") == "callback_url"]
            check(callbacks == ["/account.html", "/account.html"], "returning to /account.html")
            check(d.shown("email-form") and not d.shown("code-form"), "the email form, and no code form yet")
            check("13 or older" in d.text(), "the 13-or-older line")
            nav = [a for t, a in d.tags if t == "a" and a.get("class") == "nav-account"]
            check(len(nav) == 1 and "Sign in" in d.text(), "the navbar says Sign in")

            print("-- signed in")
            Api.mode = "in"
            d = dom_of(browser, base + "/account.html", profile)
            check(d.shown("profile") and not d.shown("signin"), "the account is shown")
            check("K7Q2-XM4P" in d.text() and "Discord, Email" in d.text() and "2 browsers" in d.text(),
                  "with its id, sign-in methods and sessions")
            check(not any(t == "img" and a.get("src") == "x" for t, a in d.tags) and "<img src=x" in d.text(),
                  "events go in as text")
            check("Account" in [x.strip() for x in d.texts], "the navbar says Account")
            check(not d.shown("delete-reauth"), "no sign-in-again note after a recent sign-in")

            print("-- an error after a sign-in")
            Api.mode = "out"
            d = dom_of(browser, base + "/account.html?error=busy", profile)
            check(d.shown("account-message") and "Too many sign-ins" in d.text(), "?error=busy is explained")

            print("-- other pages")
            Api.calls.clear()
            for page in ("index.html", "gallery.html", "designs.html"):
                d = dom_of(browser, base + "/" + page, profile)
                check("Sign in" in [x.strip() for x in d.texts], page + ": the navbar says Sign in")
            check(Api.calls == [], "no other page calls /api/")
        finally:
            httpd.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d failed" % len(failures) if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
