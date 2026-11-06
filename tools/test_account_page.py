#!/usr/bin/env python3
"""Test account.html, account.js and the navbar's account hint in a headless Chrome or Chromium
(CHROME=path, else one on PATH or Playwright's), against a stand-in for the accounts service's
/api/ (forza-live, accounts/README.md) served on 127.0.0.1 with the assembled site.

Checks:
- a copy of the site without /api/ says accounts are not available;
- signed out, the page shows the sign-in methods the service offers, as forms that post to
  allauth's provider redirect with callback_url /account.html, and the 13-or-older line;
- signed in, it shows the account, with the API's text as text, and the navbar says "Account"
  and drops Download;
- ?error=busy is shown, and the address is cleaned;
- the game link: the launcher's #link=<token> asks to link the gamertag the service names (as
  text), or to sign in first; a claimed request says to confirm in the launcher, a linked one
  says so, a token the service does not know is explained; a linked account shows its game and
  Unlink, and a frozen link says why;
- the other pages never call /api/ for a visitor who did not sign in;
- likes (likes.js) for a visitor who signed in: linked to the game, a thumbs-up pressed for what
  the key liked and none on its own items, with the live totals, and a click that takes a like
  back and moves the total; not linked, a link to the account page; signed out since, the hint
  goes;
- My items: a linked account lists its game's photos and designs with their state and likes
  (player text as text, thumbnails only for public ones), links to its public page (found in the
  site's data files), says when its likes do not count yet, and deletes an item after a confirm
  (asking for a recent sign-in when the service says so).

  python3 tools/test_account_page.py   # needs no packages

The site is assembled with tools/assemble.sh, which copies tracked files: git add new pages first.
"""
import base64
import glob
import html
import html.parser
import json
import os
import re
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
DUMPS = {}

CONFIG = {"status": 200, "data": {
    "account": {"login_methods": ["email"], "is_open_for_signup": False, "login_by_code_enabled": True},
    "socialaccount": {"providers": [{"id": "discord", "name": "Discord", "flows": ["provider_redirect"]},
                                    {"id": "github", "name": "GitHub", "flows": ["provider_redirect"]}]}}}
TOKENS = {"P" * 43: "pending", "C" * 43: "claimed", "L" * 43: "linked"}
LINKED = {"state": "frozen", "gamertag": "DriverA", "since": "2026-11-03"}
ACTIVE = {"state": "active", "gamertag": "DriverA", "since": "2026-11-03", "counted": False}
OWNER = "0a1b2c3d4e5f"   # the player id of DriverA's photos in the site's data
P_MINE, P_OTHER, P_HELD, D_MINE = "MINEAAAAAAAA", "OTHERBBBBBBB", "HELDCCCCCCCC", "DESIGNDDDDDD"
ITEMS = {"items": [
    {"kind": "photo", "id": P_MINE, "title": "<i>Mine</i>", "state": "live", "public": True, "likes": 5, "created": 2},
    {"kind": "photo", "id": P_HELD, "title": "", "state": "held", "public": False, "likes": 0, "created": 1},
    {"kind": "design", "id": D_MINE, "title": "FLAMES", "state": "pending", "public": False, "likes": 0, "created": 0}],
    "counted": False}


def photo(pid, owner, tag, likes):
    return {"id": pid, "title": "Photo " + pid[:4], "gamertag": tag, "owner": owner, "likes": likes,
            "src": f"photos/{pid}.jpg", "w": 1280, "h": 720, "thumb": f"photos/{pid}-thumb.jpg", "tw": 480, "th": 270}


SETUP = r"""
try { localStorage.setItem("fh-signed-in", "1"); } catch (e) {}
window.confirm = function () { return true; };
"""
LIKES_TEST = r"""
(function () {
  function controls() {
    var out = {};
    Array.prototype.forEach.call(document.querySelectorAll("#photos > li"), function (li) {
      var c = li.querySelector(".like");
      out[li.id.replace(/^photo-/, "")] = c ? { tag: c.tagName.toLowerCase(), pressed: c.getAttribute("aria-pressed"),
        text: c.textContent, href: c.getAttribute("href"), own: c.classList.contains("own") } : null;
    });
    return out;
  }
  function done(out) {
    var pre = document.createElement("pre");
    pre.id = "test-results";
    pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }
  setTimeout(function () {
    var out = { before: controls(), hint: localStorage.getItem("fh-signed-in") };
    var b = document.querySelector("#photo-OTHERBBBBBBB button.like");
    if (!b) return done(out);
    b.click();
    setTimeout(function () { out.after = controls(); done(out); }, 1500);
  }, 1500);
})();
"""
ITEMS_TEST = r"""
(function () {
  function done(out) {
    var pre = document.createElement("pre");
    pre.id = "test-results";
    pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }
  function rows() {
    return Array.prototype.map.call(document.querySelectorAll("#items-list > li"), function (li) {
      var img = li.querySelector("img");
      return { text: li.querySelector(".item-body").textContent, img: img ? img.getAttribute("src") : null,
               button: li.querySelector("button").getAttribute("aria-label") };
    });
  }
  setTimeout(function () {
    var out = { rows: rows(), shown: !document.getElementById("items").hidden,
                note: document.getElementById("items-note").hidden ? "" : document.getElementById("items-note").textContent,
                profile: document.getElementById("items-profile").hidden ? null : document.getElementById("items-profile-link").getAttribute("href") };
    var del = document.querySelector("#items-list > li button");
    del.click();
    setTimeout(function () {
      out.reauth = !document.getElementById("items-reauth").hidden;
      document.querySelector("#items-list > li button").click();
      setTimeout(function () {
        out.message = document.getElementById("account-message").textContent;
        out.after = rows();
        done(out);
      }, 1500);
    }, 1500);
  }, 2500);
})();
"""
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
    DUMPS["last"] = p.stdout
    d = Dom()
    d.feed(p.stdout)
    return d


def results_of(browser, url, profile):
    """The JSON a test harness on the page wrote into <pre id="test-results">, or None."""
    dom_of(browser, url, profile)
    m = re.search(r'<pre id="test-results">(.*?)</pre>', DUMPS.get("last", ""), re.S)
    return json.loads(html.unescape(m.group(1))) if m else None


class Api:
    """What the accounts service answers, by scenario: none (a static copy), out, in, linked
    (frozen), active (a usable link) or gone (the hint says signed in, the service does not)."""
    mode = "out"
    calls = []
    posts = []
    deletes = 0


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
        if self.path == "/api/me/items":
            if Api.mode == "active":
                items = dict(ITEMS, items=ITEMS["items"][1:] if Api.deletes > 1 else ITEMS["items"])
                return self.reply(200, json.dumps(items).encode(), "application/json")
            return self.reply(403, b'{"error": "link your game first"}', "application/json")
        if self.path.startswith("/api/likes/counts?kind="):
            counts = {P_MINE: 5, P_OTHER: 3} if "photo" in self.path else {}
            return self.reply(200, json.dumps({"version": 1, "kind": "photo", "generated": 0, "counts": counts}).encode(),
                              "application/json")
        if self.path.startswith("/api/likes/mine?kind="):
            if Api.mode == "active":
                doc = {"kind": "photo", "liked": [P_OTHER], "own": [P_MINE], "counted": True}
                return self.reply(200, json.dumps(doc).encode(), "application/json")
            if Api.mode == "in":
                return self.reply(403, b'{"error": "link your game first"}', "application/json")
            return self.reply(401, b'{"detail": "Unauthorized"}', "application/json")
        if self.path.startswith("/api/me"):
            if Api.mode in ("in", "linked", "active"):
                doc = dict(ME, link={"linked": LINKED, "active": ACTIVE}.get(Api.mode))
                return self.reply(200, json.dumps(doc).encode(), "application/json")
            return self.reply(401, b'{"detail": "Unauthorized"}', "application/json")
        return self.reply(404, b'{"status": 404}', "application/json")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Api.posts.append((self.path, body))
        if self.path == "/api/likes" and Api.mode == "active":
            ok = self.headers.get("Content-Type") == "application/json" and "X-CSRFToken" in self.headers
            doc = {"ok": True, "on": body.get("on"), "counted": True} if ok else {"error": "refused"}
            return self.reply(200 if ok else 403, json.dumps(doc).encode(), "application/json")
        if self.path == "/api/me/items/delete" and Api.mode == "active":
            Api.deletes += 1
            if Api.deletes == 1:
                return self.reply(403, b'{"error": "sign in again"}', "application/json")
            return self.reply(200, b'{"ok": true, "deleted": true}', "application/json")
        if self.path == "/api/link/peek" and Api.mode == "in":
            state = TOKENS.get(body.get("token"))
            if state is None:
                return self.reply(404, b'{"error": "not found"}', "application/json")
            doc = {"state": state, "gamertag": "<b>DriverA</b>", "expires": 0, "replaces": None}
            return self.reply(200, json.dumps(doc).encode(), "application/json")
        return self.reply(401, b'{"detail": "Unauthorized"}', "application/json")

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
            cta = [a for t, a in d.tags if t == "a" and a.get("class") == "nav-cta"]
            check(len(cta) == 1 and "hidden" not in cta[0], "the navbar has Download")

            print("-- signed in")
            Api.mode = "in"
            d = dom_of(browser, base + "/account.html", profile)
            check(d.shown("profile") and not d.shown("signin"), "the account is shown")
            check("K7Q2-XM4P" in d.text() and "Discord, Email" in d.text() and "2 browsers" in d.text(),
                  "with its id, sign-in methods and sessions")
            check(not any(t == "img" and a.get("src") == "x" for t, a in d.tags) and "<img src=x" in d.text(),
                  "events go in as text")
            check("Account" in [x.strip() for x in d.texts], "the navbar says Account")
            cta = [a for t, a in d.tags if t == "a" and a.get("class") == "nav-cta"]
            check(len(cta) == 1 and "hidden" in cta[0], "the navbar hides Download for a visitor who signed in")
            check(not d.shown("delete-reauth"), "no sign-in-again note after a recent sign-in")

            print("-- linking the game")
            d = dom_of(browser, base + "/account.html#link=" + "P" * 43, profile)
            check(d.shown("link-panel") and d.shown("link-buttons"), "the launcher's link asks to link")
            check("Link the game of <b>DriverA</b> to this account?" in d.text(),
                  "naming the gamertag the service gave, as text (its markup shows as typed)")
            check(Api.posts[-1] == ("/api/link/peek", {"token": "P" * 43}), "it peeks with the token from the address")
            d = dom_of(browser, base + "/account.html#link=" + "C" * 43, profile)
            check("Now confirm in the launcher. It shows account K7Q2-XM4P." in d.text() and not d.shown("link-buttons"),
                  "a claimed link says to confirm in the launcher")
            d = dom_of(browser, base + "/account.html#link=" + "L" * 43, profile)
            check("is linked to this account" in d.text(), "a linked one says so")
            d = dom_of(browser, base + "/account.html#link=" + "X" * 43, profile)
            check("That link or code does not work" in d.text(), "an unknown token is explained")
            check(d.shown("link-code-form") and "Not linked. Link it from the launcher" in d.text()
                  and not d.shown("game-actions"), "an account with no game: the code form and how to link")
            Api.mode = "out"
            Api.posts.clear()
            d = dom_of(browser, base + "/account.html#link=" + "P" * 43, profile)
            check(d.shown("link-panel") and d.shown("link-signin") and d.shown("signin"), "signed out: sign in first")
            check(Api.posts == [], "and nothing is sent before the sign-in")
            Api.mode = "linked"
            d = dom_of(browser, base + "/account.html", profile)
            check("DriverA (linked" in d.text() and d.shown("game-actions"), "a linked account shows its game and Unlink")
            check(d.shown("game-note") and "on hold" in d.text(), "a frozen link says why")

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

            print("-- likes, for a visitor who signed in")
            (site / "data").mkdir(exist_ok=True)
            (site / "data" / "gallery.json").write_text(json.dumps({"version": 1, "generated": 0, "photos": [
                photo(P_MINE, OWNER, "DriverA", 4), photo(P_OTHER, "f00dcafe1234", "<b>Other</b>", 2)]}))
            (site / "likes-setup.js").write_text(SETUP)
            (site / "likes-test.js").write_text(LIKES_TEST)
            page = (site / "gallery.html").read_text(encoding="utf-8")
            page = page.replace('<script src="likes.js"></script>',
                                '<script src="likes-setup.js"></script>\n<script src="likes.js"></script>')
            page = page.replace('<script src="photos.js"></script>',
                                '<script src="photos.js"></script>\n<script src="likes-test.js"></script>')
            (site / "likes-test.html").write_text(page, encoding="utf-8")
            Api.mode, Api.posts, Api.calls = "active", [], []
            r = results_of(browser, base + "/likes-test.html", profile) or {}
            before, after = r.get("before") or {}, r.get("after") or {}
            check(before.get(P_MINE) == {"tag": "span", "pressed": None, "text": "5", "href": None, "own": True},
                  "linked: the key's own photo has its live total and no button")
            check(before.get(P_OTHER) == {"tag": "button", "pressed": "true", "text": "3", "href": None, "own": False},
                  "linked: a photo the key liked has a pressed thumbs-up and the live total")
            check(Api.posts == [("/api/likes", {"kind": "photo", "item": P_OTHER, "on": False})],
                  "a click takes the like back (POST /api/likes, JSON, with the CSRF header)")
            check((after.get(P_OTHER) or {}).get("pressed") == "false" and (after.get(P_OTHER) or {}).get("text") == "2",
                  "and the button and the total follow")
            check(sorted(set(c.split("?")[0] for c in Api.calls)) == ["/api/auth/browser/v1/config", "/api/likes/counts",
                                                                     "/api/likes/mine"],
                  "the page asked for this visitor's likes and the totals once each (and the CSRF cookie)")
            Api.mode = "in"
            r = results_of(browser, base + "/likes-test.html", profile) or {}
            other = (r.get("before") or {}).get(P_OTHER) or {}
            check(other.get("tag") == "a" and other.get("href") == "account.html" and other.get("text") == "3",
                  "not linked: the live total, as a link to the account page")
            Api.mode = "gone"
            r = results_of(browser, base + "/likes-test.html", profile) or {}
            other = (r.get("before") or {}).get(P_OTHER) or {}
            check(other.get("tag") == "a" and other.get("text") == "3" and r.get("hint") is None,
                  "signed out since: a link to sign in, and the hint is gone")

            print("-- My items")
            (site / "data" / "designs.json").write_text(json.dumps({"version": 1, "generated": 0, "designs": []}))
            (site / "photos").mkdir(exist_ok=True)  # a 1x1 picture as the public photo's thumbnail
            (site / "photos" / f"{P_MINE}-thumb.jpg").write_bytes(base64.b64decode(
                "R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="))
            (site / "items-test.js").write_text(ITEMS_TEST)
            page = (site / "account.html").read_text(encoding="utf-8")
            page = page.replace('<script src="account.js"></script>',
                                '<script src="likes-setup.js"></script>\n<script src="account.js"></script>\n'
                                '<script src="items-test.js"></script>')
            (site / "items-test.html").write_text(page, encoding="utf-8")
            Api.mode, Api.posts, Api.deletes = "active", [], 0
            r = results_of(browser, base + "/items-test.html", profile) or {}
            rows = r.get("rows") or []
            check(r.get("shown") and len(rows) == 3, "a linked account lists its game's 3 photos and designs")
            check(rows and rows[0]["text"] == "<i>Mine</i>Photo \u00b7 On the site \u00b7 5 likes"
                  and rows[0]["img"] == f"photos/{P_MINE}-thumb.jpg", "with the title as text, its state, likes and thumbnail")
            check(len(rows) == 3 and rows[1]["img"] is None and "Held for the maintainer" in rows[1]["text"]
                  and "Untitled" in rows[1]["text"] and "Waiting for the automatic check" in rows[2]["text"],
                  "a held photo and a pending design: their state, no thumbnail")
            check(r.get("profile") == f"profile.html?p={OWNER}", "a link to the player's public page, from the site's data")
            check("count in the public totals once" in (r.get("note") or ""), "it says the key's likes do not count yet")
            check(r.get("reauth") is True, "a delete without a recent sign-in asks for one")
            check(Api.posts[:2] == [("/api/me/items/delete", {"kind": "photo", "id": P_MINE})] * 2,
                  "Delete sends the item (after the confirm)")
            check("is deleted" in (r.get("message") or "") and len(r.get("after") or []) == 2,
                  "a delete that worked says so and the list follows")
        finally:
            httpd.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d failed" % len(failures) if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
