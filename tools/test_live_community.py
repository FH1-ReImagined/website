#!/usr/bin/env python3
"""Test the community photos and designs that show before the next full build:

- live.js on gallery.html and designs.html, in a headless Chrome or Chromium (CHROME=path, else
  one on PATH or Playwright's), against a stand-in for the site's /live/community/ route: the
  feed's order, the build's copy for an item the build has, the feed's picture route for one it
  has not, an item the feed no longer lists left out, malformed entries refused, player text as
  text, and the build's data as before when there is no live feed;
- tools/site_hook.py: a signed call runs the build command, a bad, stale or replayed signature
  and a bad body do not, and a burst of calls makes at most two builds;
- tools/build_community.sh: it builds into a copy of the served release and switches the symlink,
  leaves the served release untouched, builds Photo of the Month again after the photos (a
  photo the gallery no longer has leaves it), and changes nothing when a feed or the accounts
  service cannot be read or the site is not served through a symlink.

  python3 tools/test_live_community.py   # needs Pillow (tools/requirements.txt) and flock
"""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from test_account_page import Dom, find_browser  # noqa: E402

failures = []
SHA = "ab" * 32
EVIL = '<img src=x onerror="alert(1)">'


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def assemble(dest):
    subprocess.run([str(TOOLS / "assemble.sh"), str(dest)], check=True)


def jpeg(path, w, h, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), colour).save(path, quality=80)


def png(path, w, h, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), colour).save(path)


def baked_photo(pid, tag):
    return {"id": pid, "title": "Built " + pid[:3], "gamertag": tag, "src": f"photos/{pid}.jpg", "w": 640, "h": 360,
            "thumb": f"photos/{pid}-thumb.jpg", "tw": 480, "th": 270}


def feed_photo(pid, tag, title="", t=100):
    return {"id": pid, "gamertag": tag, "owner": "0a1b2c3d4e5f", "title": title, "takenAt": t, "approvedAt": t,
            "w": 1920, "h": 1080, "sha256": SHA, "image": f"/community/img/{pid}.png"}


def baked_design(did, t):
    return {"id": did, "type": "livery", "code": "CODE" + did[:4], "title": "Built " + did[:4], "gamertag": "Maker",
            "downloads": 1, "rating": 0, "ratings": 0, "createdAt": t, "src": f"designs/{did}.png", "w": 576, "h": 320}


def feed_design(did, t, title=""):
    return {"id": did, "type": "livery", "code": "CODE" + did[:4], "title": title, "description": "x", "gamertag": "Maker",
            "owner": "0a1b2c3d4e5f", "car": {"name": "Ford Focus RS"}, "layers": None, "downloads": 3,
            "rating": {"avg": 4.5, "count": 2}, "createdAt": t, "preview": f"/community/design-img/{did}.png",
            "w": 576, "h": 320, "sha256": SHA}


class Live:
    """What /live/community/ answers: on (the feeds below) or off (404, as on a copy without it)."""
    on = True
    photos, designs = [], []


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if not self.path.startswith("/live/"):
            return super().do_GET()
        if not Live.on:
            return self.reply(404, b"<html>Not Found</html>", "text/html")
        if self.path == "/live/community/gallery.json":
            return self.reply(200, json.dumps({"version": 1, "generated": 1, "photos": Live.photos}).encode(), "application/json")
        if self.path == "/live/community/designs.json":
            return self.reply(200, json.dumps({"version": 1, "generated": 1, "designs": Live.designs}).encode(), "application/json")
        return self.reply(404, b"{}", "application/json")

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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


def test_pages(tmp):
    print("-- the pages, with and without the live feed")
    browser = find_browser()
    if not browser:
        if os.environ.get("CI"):
            check(False, "a browser (set CHROME=path)")
        else:
            print("skip the page checks: no Chrome or Chromium found (set CHROME=path)")
        return
    site = tmp / "site"
    assemble(site)
    A, B, NEW = "AAAAAAAAAAAA", "BBBBBBBBBBBB", "NEWNEWNEWNEW"
    for pid in (A, B):
        jpeg(site / "photos" / f"{pid}.jpg", 640, 360, (180, 90, 40))
        jpeg(site / "photos" / f"{pid}-thumb.jpg", 480, 270, (180, 90, 40))
    (site / "data" / "gallery.json").write_text(json.dumps(
        {"version": 1, "generated": 1, "photos": [baked_photo(A, "Anna"), baked_photo(B, "Ben")]}))
    D1, D2, D3 = "DESIGNONE01", "DESIGNTWO02", "DESIGNTHR03"
    for did in (D1, D3):
        png(site / "designs" / f"{did}.png", 576, 320, (90, 60, 40))
    (site / "data" / "designs.json").write_text(json.dumps(
        {"version": 1, "generated": 1, "designs": [baked_design(D1, 10), baked_design(D3, 5)]}))
    Live.photos = [feed_photo(NEW, "<b>Nova</b>", "Just " + EVIL, 300), feed_photo(A, "Anna", "", 100),
                   dict(feed_photo("BADID", "X"), id="bad id"),
                   dict(feed_photo("FOREIGNFOREI", "X"), image="https://elsewhere.example/x.png"),
                   dict(feed_photo("MISMATCHMISM", "X"), image="/community/img/AAAAAAAAAAAA.png"),
                   feed_photo("NOGAMERTAGXX", "‮\u0007")]
    Live.designs = [feed_design(D2, 20, "Fresh"), feed_design(D1, 10),
                    dict(feed_design("DESIGNBAD04", 30), preview="/community/img/x.png"),
                    dict(feed_design("DESIGNBAD05", 30), code="no!")]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        Live.on = True
        d = dom_of(browser, base + "/gallery.html", tmp)
        shown = [a["data-full"] for t, a in d.tags if t == "button" and "data-full" in a]
        check(shown == [f"/live/community/img/{NEW}.png", f"photos/{A}.jpg"],
              "gallery: the feed's order, its picture for a new photo, the build's copy for one it has")
        check(f"photos/{B}.jpg" not in shown, "gallery: a photo the feed no longer lists is gone at once")
        check(not any(x in " ".join(shown) for x in ("BADID", "FOREIGN", "elsewhere", "NOGAMERTAG")) and len(shown) == 2,
              "gallery: malformed entries (bad id, a picture elsewhere or of another id, no gamertag) are refused")
        imgs = {a.get("src"): a for t, a in d.tags if t == "img"}
        new_img = imgs.get(f"/live/community/img/{NEW}.png")
        check(new_img is not None and new_img.get("loading") == "lazy", "gallery: the new photo's picture is lazy-loaded")
        check("<b>Nova</b>" in d.texts and not any(t == "img" and a.get("src") == "x" for t, a in d.tags),
              "gallery: the player's text goes in as text")
        d = dom_of(browser, base + "/designs.html", tmp)
        ids = [a["id"] for t, a in d.tags if t == "li" and a.get("id", "").startswith("design-")]
        check(f"design-{D2}" in ids and f"design-{D1}" in ids, "designs: a new design shows next to the build's")
        check(f"design-{D3}" not in ids and not any("BAD" in i for i in ids), "designs: hidden and malformed ones are left out")
        srcs = {a.get("src") for t, a in d.tags if t == "img"}
        check(f"/live/community/design-img/{D2}.png" in srcs and f"designs/{D1}.png" in srcs,
              "designs: the feed's preview route for the new one, the build's file for the other")
        Live.on = False
        d = dom_of(browser, base + "/gallery.html", tmp)
        shown = [a["data-full"] for t, a in d.tags if t == "button" and "data-full" in a]
        check(shown == [f"photos/{A}.jpg", f"photos/{B}.jpg"], "no live feed: the gallery shows the build's photos")
        d = dom_of(browser, base + "/designs.html", tmp)
        ids = [a["id"] for t, a in d.tags if t == "li" and a.get("id", "").startswith("design-")]
        check(sorted(ids) == sorted([f"design-{D1}", f"design-{D3}"]), "no live feed: the designs page shows the build's")
        Live.on = True
        d = dom_of(browser, base + "/index.html", tmp)
        shown = [a["data-full"] for t, a in d.tags if t == "button" and a.get("data-full", "").startswith("photos/")]
        check(shown == [f"photos/{A}.jpg", f"photos/{B}.jpg"], "the front page keeps to the build's photos")
    finally:
        httpd.shutdown()


def post(url, body, secret, ts=None, sig=None):
    ts = int(time.time()) if ts is None else ts
    sig = sig or "sha256=" + hmac.new(secret, str(ts).encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Forza-Timestamp": str(ts), "X-Forza-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, sig
    except urllib.error.HTTPError as e:
        return e.code, sig


def test_hook(tmp):
    print("-- tools/site_hook.py")
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    runs = tmp / "runs.txt"
    fake = tmp / "fake_build.sh"
    fake.write_text(f"#!/bin/sh\necho run >> {runs}\nsleep 1\n")
    fake.chmod(0o755)
    secret = b"s" * 40
    env = dict(os.environ, SITE_HOOK_SECRET=secret.decode(), SITE_HOOK_BIND="127.0.0.1", SITE_HOOK_PORT=str(port),
               SITE_HOOK_COMMAND=str(fake), SITE_HOOK_MIN_GAP="0.5")
    p = subprocess.Popen([sys.executable, str(TOOLS / "site_hook.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/hook/community"
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/hook/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        body = json.dumps({"version": 1, "changed": ["gallery"]}).encode()
        code, _ = post(url, body, b"w" * 40)
        check(code == 401, "a call signed with another secret is refused")
        code, _ = post(url, body, secret, ts=int(time.time()) - 3600)
        check(code == 401, "a call an hour old is refused")
        code, sig = post(url, body, secret)
        check(code == 202, "a signed call is accepted")
        time.sleep(0.2)
        code, _ = post(url, body, secret, ts=int(sig and time.time()), sig=sig)
        check(code == 401, "the same signed call again is refused (no replays)")
        code, _ = post(url, json.dumps({"version": 1, "changed": ["everything"]}).encode(), secret)
        check(code == 400, "a body that asks for something else is refused")
        code, _ = post(url, b"x" * 5000, secret)
        check(code == 413, "a body over 4 KiB is refused")
        time.sleep(2)
        check(runs.is_file() and runs.read_text().count("run") == 1, "the accepted call ran the build once")
        runs.write_text("")
        for i in range(6):
            post(url, json.dumps({"version": 1, "changed": ["gallery", "designs"], "n": i}).encode(), secret)
        time.sleep(4)
        n = runs.read_text().count("run")
        check(1 <= n <= 2, f"six calls in a burst made {n} builds (one, and one more for the calls during it)")
        health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/hook/health", timeout=2).read())
        check(health.get("ok") is True and health.get("result") == 0, "the health route says the last build's result")
    finally:
        p.terminate()
        p.wait(timeout=10)


def test_partial_build(tmp):
    print("-- tools/build_community.sh")
    www = tmp / "www"
    first = www / "releases" / "full-1"
    assemble(first)
    jpeg(first / "photos" / "OLDOLDOLDOLD.jpg", 640, 360, (1, 2, 3))
    (first / "data" / "gallery.json").write_text('{"version":1,"generated":1,"photos":[]}\n')
    (first / "data" / "designs.json").write_text('{"version":1,"generated":1,"designs":[]}\n')
    # The served release's Photo of the Month names a photo, with its files, that the next gallery
    # no longer has (deleted since the full build).
    old_winner = {"month": "2026-10", "id": "OLDOLDOLDOLD", "title": "Old", "gamertag": "Someone", "votes": 3,
                  "reached": 1, "confirmed": 1, "src": "photos/OLDOLDOLDOLD.jpg", "w": 640, "h": 360}
    (first / "data" / "photo-of-the-month.json").write_text(json.dumps(
        {"version": 1, "generated": 1, "from": "2026-10", "featured": old_winner, "winners": [old_winner], "months": []}))
    potm_before = (first / "data" / "photo-of-the-month.json").read_text()
    (www / "current").symlink_to(first)
    served_before = (first / "data" / "gallery.json").read_text()
    env = dict(os.environ, SITE_LINK=str(www / "current"), RELEASES=str(www / "releases"), BUILD_LOCK=str(tmp / "lock"),
               GALLERY_CACHE=str(tmp / "gc"), DESIGNS_CACHE=str(tmp / "dc"))
    env.pop("GALLERY_FEED_URL", None)
    env.pop("LIKES_URL", None)
    env.pop("POTM_URL", None)
    script = str(TOOLS / "build_community.sh")
    p = subprocess.run([script], env=env, capture_output=True, text=True)
    now = (www / "current").resolve()
    check(p.returncode == 0 and now != first.resolve() and now.name.startswith("community-"),
          "it builds a new release and switches the site to it")
    check((first / "data" / "gallery.json").read_text() == served_before and (first / "photos" / "OLDOLDOLDOLD.jpg").is_file(),
          "the release served before is untouched")
    check(os.path.samefile(now / "index.html", first / "index.html"), "unchanged files are shared with the release before")
    check(json.loads((now / "data" / "gallery.json").read_text())["photos"] == [] and not (now / "photos" / "OLDOLDOLDOLD.jpg").exists(),
          "the photos are built again in the new release")
    potm = json.loads((now / "data" / "photo-of-the-month.json").read_text())
    check(potm["featured"] is None and potm["winners"] == [] and (first / "data" / "photo-of-the-month.json").read_text() == potm_before,
          "Photo of the Month is built again after the photos (no service here: empty), the served one untouched")
    p = subprocess.run([script], env=dict(env, POTM_URL="http://127.0.0.1:9/"), capture_output=True, text=True)
    leftovers = [x.name for x in (www / "releases").iterdir() if x.resolve() not in (now, first.resolve())]
    check(p.returncode != 0 and (www / "current").resolve() == now and not leftovers,
          "an accounts service that cannot be read for Photo of the Month switches nothing")
    p = subprocess.run([script], env=dict(env, GALLERY_FEED_URL="http://127.0.0.1:9/community/gallery.json"),
                       capture_output=True, text=True)
    leftovers = [x.name for x in (www / "releases").iterdir() if x.resolve() not in (now, first.resolve())]
    check(p.returncode != 0 and (www / "current").resolve() == now and not leftovers,
          "a feed that cannot be read switches nothing and leaves nothing behind")
    (www / "plain").mkdir()
    p = subprocess.run([script], env=dict(env, SITE_LINK=str(www / "plain")), capture_output=True, text=True)
    check(p.returncode == 2 and "not a symlink" in p.stdout, "a site not served through a symlink is left alone")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="live-community-"))
    try:
        test_pages(tmp)
        test_hook(tmp)
        test_partial_build(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("all passed" if not failures else f"{len(failures)} failed"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
