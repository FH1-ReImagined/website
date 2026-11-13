#!/usr/bin/env python3
"""Test tools/build_videos.py against a fixture feed served on 127.0.0.1. Its videos and posters are
SYNTHETIC, made here at test time (MP4 boxes written by hand, posters drawn with Pillow): never
commit game footage, or any picture from the game, to this repository.

Checks what a build publishes: newest approved first, only the fields videos.js shows (no "xuid"
anywhere, an "owner" only as the server's 12-hex-digit player id), the like totals (?kind=video),
the MP4s as the server sent them and the posters re-encoded without metadata, the files it must
refuse (a SHA-256 mismatch, an MP4 with other boxes or no H.264 track, a size that is not the
feed's, a poster that is not a JPEG or is too large, paths that are not the video's own, files on
another host), the cache (a second build downloads nothing; a withdrawn video leaves the site and
the cache), the caps (videos and MB), a server without the videos feed (404: no videos, exit 0),
and an unset, unreachable or broken feed. Then, in a headless Chrome or Chromium (CHROME=path,
else one on PATH or Playwright's): videos.html lists the videos with their posters, titles as
text, length, date, gamertag link and like total, the empty and error states, the player (a click
opens it with the video, closing it drops the video), and at a phone's width one column and no
sideways scrolling.

  python3 tools/test_build_videos.py                # needs Pillow (tools/requirements.txt)
  python3 tools/test_build_videos.py --serve 8000   # then serve the fixture site for a look
"""
import argparse
import collections
import functools
import hashlib
import html
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import check_site  # noqa: E402
from test_account_page import Dom, find_browser  # noqa: E402
from test_build_gallery import jpeg, jpeg_with_metadata, scene  # noqa: E402

NOW = int(time.time())
LEAK = "2535411122223333"  # a made-up XUID the feed leaks by mistake
OWNER_A, OWNER_B = "0a1b2c3d4e5f", "f00dcafe1234"
B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
EVIL = '<img src=x onerror="alert(1)">'

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def vid(name):
    h = hashlib.sha256(name.encode()).digest()
    return "".join(B32[b & 31] for b in h[:12])


def box(kind, body=b""):
    return struct.pack(">I", 8 + len(body)) + kind + body


def mp4(seed, extra=b"", track=b"avc1", brand=b"isom"):
    """An MP4's top-level boxes as the server's converter writes them, with made-up contents."""
    return (box(b"ftyp", brand + b"\x00\x00\x02\x00isomiso2avc1mp41")
            + box(b"moov", box(b"trak", box(b"stsd", track + bytes(40))))
            + extra + box(b"mdat", hashlib.sha256(seed.encode()).digest() * 64))


# ---- the fixtures ----

def fixtures():
    files, entries = {}, []

    def add(name, video, poster, approved, title="", gamertag="Synthetic Driver", sha=None, psha=None,
            size=None, **extra):
        i = vid(name)
        if video is not None:
            files[f"/community/video/{i}.mp4"] = video
        if poster is not None:
            files[f"/community/video/{i}.jpg"] = poster
        e = {"id": i, "gamertag": gamertag, "title": title, "takenAt": approved - 600, "approvedAt": approved,
             "seconds": 15.27, "w": 640, "h": 360, "size": len(video or b"") if size is None else size,
             "sha256": sha or hashlib.sha256(video or b"").hexdigest(),
             "posterSha256": psha or hashlib.sha256(poster or b"").hexdigest(),
             "video": f"/community/video/{i}.mp4", "poster": f"/community/video/{i}.jpg"}
        e.update(extra)
        entries.append(e)
        return i

    def poster(seed, w=640, h=360):
        return jpeg(scene(w, h, seed, "Synthetic replay"))

    good = {
        "first": add("first", mp4("first"), poster(1), NOW - 100, "Subaru WRX '05 on Beaumont - Stage 3",
                     "Synthetic One", owner=OWNER_A),
        "hd": add("hd", mp4("hd"), poster(2, 1280, 720), NOW - 200, "A wide clip", "Synthetic Two", owner=OWNER_B,
                  w=1280, h=720, seconds=64.0),
        "meta": add("meta", mp4("meta"), jpeg_with_metadata(scene(640, 360, 3, "meta")), NOW - 300, EVIL,
                    "<b>Tag</b>", xuid=LEAK, owner=LEAK),
        "notitle": add("notitle", mp4("notitle"), poster(4), NOW - 400, "", "Synthetic Four", seconds=4.2),
    }
    bad = {}
    other = mp4("other")
    bad["badsha"] = add("badsha", other, poster(10), NOW - 2000, "Wrong hash", sha=hashlib.sha256(other + b"x").hexdigest())
    bad["badposter"] = add("badposter", mp4("bp"), poster(11), NOW - 2001, "Wrong poster hash", psha="0" * 64)
    bad["meta-box"] = add("meta-box", mp4("mb", extra=box(b"meta", b"owner xuid " + LEAK.encode())), poster(12),
                          NOW - 2002, "Another box")
    bad["mpeg4"] = add("mpeg4", mp4("m4", track=b"mp4v"), poster(13), NOW - 2003, "No H.264")
    bad["notmp4"] = add("notmp4", b"#EXTM3U\nhttp://198.51.100.7/x.ts\n", poster(14), NOW - 2004, "A playlist")
    bad["size"] = add("size", mp4("sz"), poster(15), NOW - 2005, "Wrong size", size=99)
    png = io.BytesIO()
    scene(640, 360, 16, "png").save(png, "PNG")
    bad["pngposter"] = add("pngposter", mp4("pp"), png.getvalue(), NOW - 2006, "PNG poster")
    bad["bigposter"] = add("bigposter", mp4("bg"), jpeg(scene(3840, 2160, 17, "big")), NOW - 2007, "Huge poster")
    bad["missing"] = add("missing", None, None, NOW - 2008, "404", sha="1" * 64, psha="2" * 64, size=100)
    offsite = add("offsite", mp4("off"), poster(18), NOW - 2009, "Elsewhere")
    entries[-1]["video"] = "http://198.51.100.7/community/video/%s.mp4" % offsite
    bad["offsite"] = offsite
    entries.append({"id": "../../etc/pw", "gamertag": "x", "approvedAt": NOW, "sha256": "0" * 64,
                    "posterSha256": "0" * 64, "seconds": 1, "size": 1, "video": "/community/video/x.mp4",
                    "poster": "/community/video/x.jpg"})
    entries.append({"id": vid("noseconds"), "gamertag": "x", "approvedAt": NOW, "sha256": "0" * 64,
                    "posterSha256": "0" * 64, "size": 1, "video": f"/community/video/{vid('noseconds')}.mp4",
                    "poster": f"/community/video/{vid('noseconds')}.jpg"})
    entries.append("junk")
    first = next(e for e in entries if isinstance(e, dict) and e["id"] == good["first"])
    entries.append(dict(first, title="Duplicate id"))
    order = [good[k] for k in ("first", "hd", "meta", "notitle")]
    likes = {good["first"]: 7, good["hd"]: 1, good["meta"]: -3, "../../etc/pw": 4}
    return entries, files, order, {"good": good, "bad": bad, "likes": likes, "entries": entries}


# ---- the fixture server ----

class Feed:
    def __init__(self):
        self.doc = {"version": 1, "generated": NOW, "videos": []}
        self.raw, self.status = None, 200
        self.likes, self.likes_requests = {}, []
        self.files, self.requests = {}, collections.Counter()
        handler = functools.partial(FeedHandler, self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


class FeedHandler(SimpleHTTPRequestHandler):
    def __init__(self, feed, *a, **kw):
        self.feed = feed
        super().__init__(*a, **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        f = self.feed
        if self.path == "/community/videos.json":
            body = f.raw if f.raw is not None else json.dumps(f.doc).encode()
            return self.reply(f.status, body, "application/json")
        if self.path.startswith("/api/likes/counts"):
            f.likes_requests.append(self.path)
            return self.reply(200, json.dumps({"version": 1, "kind": "video", "generated": NOW,
                                               "counts": f.likes}).encode(), "application/json")
        if self.path in f.files:
            f.requests[self.path] += 1
            return self.reply(200, f.files[self.path], "video/mp4" if self.path.endswith(".mp4") else "image/jpeg")
        self.reply(404, b"not found", "text/plain")

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class QuietFiles(SimpleHTTPRequestHandler):
    api_calls = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/") or self.path.startswith("/live/"):
            QuietFiles.api_calls.append(self.path)
        return super().do_GET()


def serve_dir(path):
    handler = functools.partial(QuietFiles, directory=str(path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def build(site, cache, feed_url, *extra, likes_url=None):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "LIKES_URL", "GALLERY_FEED_URL")}
    if likes_url:
        env["LIKES_URL"] = likes_url
    if feed_url:
        env["GALLERY_FEED_URL"] = feed_url
    out_file = site.parent / "github_output"
    out_file.write_text("")
    env["GITHUB_OUTPUT"] = str(out_file)
    args = [sys.executable, str(TOOLS / "build_videos.py"), str(site)] + (["--cache", str(cache)] if cache else []) + list(extra)
    p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600)
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines() if "=" in line)
    return p.returncode, p.stdout + p.stderr, outputs


def published(site):
    return json.loads((site / "data" / "videos.json").read_text(encoding="utf-8"))


def site_check(site):
    r = subprocess.run([sys.executable, str(TOOLS / "check_site.py"), str(site)], capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip()


def check_published(site, order, expect, files, log):
    doc = published(site)
    videos = doc["videos"]
    by = {v["id"]: v for v in videos}
    check(set(doc) == {"version", "generated", "videos"} and doc["version"] == 1, "videos.json: version 1, no extra fields")
    check([v["id"] for v in videos] == order, f"published the {len(order)} good videos, newest approved first")
    for name, i in expect["bad"].items():
        check(i not in by and not (site / "videos" / f"{i}.mp4").exists(), f"refused: {name}")
    check(all(set(v) <= check_site.VIDEO_KEYS for v in videos), "only the fields the page shows")
    out = b"".join(f.read_bytes() for f in [site / "data" / "videos.json"] + sorted((site / "videos").iterdir()))
    check(LEAK.encode() not in out and b"xuid" not in (site / "data" / "videos.json").read_bytes().lower(),
          "no 'xuid' or the leaked XUID anywhere in the output")
    check({v["id"]: v.get("owner") for v in videos if "owner" in v} == {order[0]: OWNER_A, order[1]: OWNER_B},
          "owner: only the server's player ids")
    check(all(v.get("likes") == {order[0]: 7, order[1]: 1}.get(v["id"], 0) for v in videos),
          "likes: every video has its total (0 when none; bad counts left out)")
    meta = by.get(expect["good"]["meta"], {})
    check(meta.get("title") == EVIL and meta.get("gamertag") == "<b>Tag</b>", "player text kept as written (not escaped)")
    same = all((site / v["src"]).read_bytes() == files[f"/community/video/{v['id']}.mp4"] for v in videos)
    check(same, "the MP4s are the server's, byte for byte")
    posters_ok = True
    for v in videos:
        data = (site / v["poster"]).read_bytes()
        w, h, markers, _ = check_site.jpeg_segments(data)
        if any(0xE1 <= m <= 0xEF or m == 0xFE for m in markers) or (w, h) != (v["w"], v["h"]):
            posters_ok = False
    check(posters_ok, "posters re-encoded: no metadata segment, sizes as listed")
    hd = by.get(expect["good"]["hd"], {})
    check((hd.get("w"), hd.get("h"), hd.get("seconds")) == (960, 540, 64.0), "a 1280x720 poster becomes 960x540")
    ok, out = site_check(site)
    check(ok, f"check_site.py passes on the built site: {out[:300]}")


# ---- the pages ----

PLAYER_TEST = r"""
(function () {
  // No video is fetched in the test (a fetch in progress holds the headless browser's clock):
  // play() and load() only say what they were asked.
  var calls = [];
  HTMLMediaElement.prototype.play = function () { calls.push("play"); return Promise.resolve(); };
  HTMLMediaElement.prototype.load = function () { calls.push("load"); };
  var out = {calls: calls};
  function done(final) {
    var pre = document.getElementById("test-results") || document.createElement("pre");
    pre.id = "test-results";
    out.final = !!final;
    pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }
  done();
  document.addEventListener("videos:shown", function () {
    out.shown = true;
    done();
    setTimeout(function () {
      var b = document.querySelector("#videos > li button.video-open");
      if (!b) { out.error = "no card"; return done(); }
      out.label = b.getAttribute("aria-label");
      b.focus();  // as a visitor's click or key does
      b.click();
      var box = document.getElementById("player"), v = box.querySelector("video");
      out.open = box.open;
      out.src = v.getAttribute("src");
      out.poster = v.getAttribute("poster");
      out.cap = box.querySelector(".viewer-cap b").textContent + " | " + box.querySelector(".viewer-cap span").textContent;
      out.scrollLocked = document.documentElement.classList.contains("viewer-open");
      done();
      box.addEventListener("close", function () {  // after videos.js's own close listener
        out.closed = !box.open;
        out.srcAfter = v.getAttribute("src");
        out.focus = document.activeElement === b;
        done(true);
      }, { once: true });
      box.close();
    }, 300);
  });
})();
"""

NARROW_TEST = r"""
(function () {
  var frame = document.querySelector("iframe");
  setTimeout(function () {
    var d = frame.contentDocument, w = frame.contentWindow;
    var cards = Array.prototype.map.call(d.querySelectorAll("#videos > li"), function (li) {
      var r = li.getBoundingClientRect();
      return { l: Math.round(r.left), w: Math.round(r.width) };
    });
    var pre = document.createElement("pre");
    pre.id = "test-results";
    pre.textContent = JSON.stringify({ width: w.innerWidth, scroll: d.documentElement.scrollWidth,
                                       client: d.documentElement.clientWidth, cards: cards });
    document.body.append(pre);
  }, 4000);
})();
"""


def browser_run(browser, url, profile):
    profile = Path(tempfile.mkdtemp(dir=profile))
    args = [browser, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--mute-audio", "--autoplay-policy=no-user-gesture-required",
            f"--user-data-dir={profile}", "--virtual-time-budget=15000", "--dump-dom", url]
    if "headless-shell" not in browser:
        args.insert(1, "--headless=new")
    p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    d = Dom()
    d.feed(p.stdout)
    m = re.search(r'<pre id="test-results">(.*?)</pre>', p.stdout, re.S)
    return d, (json.loads(html.unescape(m.group(1))) if m else None)


def check_pages(browser, site, order, expect, profile):
    httpd, base = serve_dir(site)
    try:
        QuietFiles.api_calls.clear()
        page, _ = browser_run(browser, base + "/videos.html", profile)
        cards = [a for t, a in page.tags if t == "button" and "video-open" in a.get("class", "").split()]
        check(len(cards) == len(order), f"videos.html: {len(order)} cards ({len(cards)})")
        posters = [a.get("src") for t, a in page.tags if t == "img" and a.get("src", "").startswith("videos/")]
        check(posters == [f"videos/{i}.jpg" for i in order], "the posters, newest first")
        imgs = [a for t, a in page.tags if t == "img" and a.get("src", "").startswith("videos/")]
        check(all(a.get("loading") == "lazy" and a.get("width") and a.get("height") for a in imgs),
              "posters are lazy-loaded with width and height")
        text = page.text()
        check("0:15" in text and "1:04" in text and "0:04" in text, "each card shows the video's length")
        check("Subaru WRX '05 on Beaumont - Stage 3" in text and "Untitled clip" in text, "titles, and a placeholder")
        check(EVIL in page.texts and "<b>Tag</b>" in page.texts, "player text inserted as text")
        check(not any(t == "img" and ("onerror" in a or a.get("src") == "x") for t, a in page.tags),
              "the player's markup is not turned into elements")
        check(any(t == "time" for t, a in page.tags) and "Shared" in text, "each card says when it was shared")
        by = {a.get("href") for t, a in page.tags if t == "a" and a.get("class") == "photo-by"}
        check(f"profile.html?p={OWNER_A}" in by, "the gamertag links to the player's page")
        likes = [a for t, a in page.tags if t == "a" and "like" in a.get("class", "").split()]
        check(len(likes) == len(order) and any(a.get("aria-label", "").startswith("7 likes") for a in likes),
              "a like total on each video (7 on the first)")
        check(cards and cards[0].get("aria-label", "").startswith("Play Subaru WRX"), "the card's button says what it plays")
        check(QuietFiles.api_calls == ["/live/community/videos.json"],
              f"no call to /api/ for a visitor who did not sign in, and only the live feed's ({QuietFiles.api_calls})")

        (site / "player-test.js").write_text(PLAYER_TEST)
        text = (site / "videos.html").read_text(encoding="utf-8")
        (site / "player-test.html").write_text(text.replace('<script src="videos.js"></script>',
                                                            '<script src="player-test.js"></script>\n<script src="videos.js"></script>'))
        for _ in range(3):  # the headless browser's clock now and then stops before the dialog's close event
            _, r = browser_run(browser, base + "/player-test.html", profile)
            if r and r.get("final"):
                break
        check(bool(r) and r.get("open") and r.get("src") == f"videos/{order[0]}.mp4"
              and r.get("poster") == f"videos/{order[0]}.jpg", f"a click opens the player with that video ({r})")
        check(bool(r) and r.get("cap", "").startswith("Subaru WRX '05 on Beaumont - Stage 3 | Synthetic One"),
              "the player's caption: title, gamertag, date, length")
        check(bool(r) and r.get("scrollLocked"), "the page behind does not scroll while it plays")
        check(bool(r) and r.get("calls") == ["play", "load"], "it plays at once, and closing stops the download")
        check(bool(r) and r.get("closed") and r.get("srcAfter") is None and r.get("focus"),
              f"closing it drops the video and gives focus back to the card ({r and (r.get('closed'), r.get('srcAfter'), r.get('focus'))})")

        # the live feed (live.js): its order, the build's copy of what the build has, the feed's own
        # routes for a video only it lists, a video it no longer lists left out, a bad entry refused
        new = vid("live-only")
        live = site / "live" / "community"
        live.mkdir(parents=True)
        entries = {e["id"]: e for e in expect["entries"] if isinstance(e, dict) and "id" in e}
        fresh = dict(entries[order[0]], id=new, title="Fresh " + EVIL, video=f"/community/video/{new}.mp4",
                     poster=f"/community/video/{new}.jpg", approvedAt=NOW)
        broken = dict(fresh, id=vid("broken"), poster="/community/video/AAAAAAAAAAAA.jpg")
        (live / "videos.json").write_text(json.dumps({"version": 1, "videos": [fresh, broken, entries[order[1]],
                                                                              entries[order[0]]]}))
        page, _ = browser_run(browser, base + "/videos.html", profile)
        posters = [a.get("src") for t, a in page.tags if t == "img" and "video" in a.get("src", "")]
        check(posters == [f"/live/community/video/{new}.jpg", f"videos/{order[1]}.jpg", f"videos/{order[0]}.jpg"],
              f"the live feed's order, the build's copies, a new one from the feed's route ({posters})")
        check("Fresh " + EVIL in page.texts, "a live entry's text inserted as text")
        shutil.rmtree(site / "live")

        (site / "narrow-test.js").write_text(NARROW_TEST)
        (site / "narrow.html").write_text('<!doctype html><meta charset="utf-8"><body style="margin:0">'
                                          '<iframe src="videos.html" width="390" height="2400" style="border:0"></iframe>'
                                          '<script src="narrow-test.js"></script></body>')
        _, r = browser_run(browser, base + "/narrow.html", profile)
        check(bool(r) and r["width"] == 390 and r["scroll"] <= r["client"],
              f"at 390 px wide (a phone): no sideways scrolling ({r and (r['scroll'], r['client'])})")
        check(bool(r) and len(r["cards"]) == len(order) and len({c["l"] for c in r["cards"]}) == 1
              and all(c["l"] >= 12 and c["l"] + c["w"] <= 390 - 12 for c in r["cards"]),  # the gallery's margins
              f"at a phone's width: one column, inside the page's margins ({r and r['cards']})")

        (site / "data" / "videos.json").write_text(json.dumps({"version": 1, "generated": NOW, "videos": []}))
        page, _ = browser_run(browser, base + "/videos.html", profile)
        check("No videos yet." in page.text() and not any(t == "button" and "video-open" in a.get("class", "")
                                                           for t, a in page.tags), "no videos: the empty state")
        (site / "data" / "videos.json").unlink()
        page, _ = browser_run(browser, base + "/videos.html", profile)
        check("could not be loaded" in page.text(), "no data file and no live feed: the error state")
    finally:
        httpd.shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(description="Test tools/build_videos.py with synthetic videos.")
    ap.add_argument("--serve", type=int, metavar="PORT", help="afterwards, serve the fixture site on this port")
    args = ap.parse_args()

    entries, files, order, expect = fixtures()
    feed = Feed()
    feed.doc["videos"], feed.files, feed.likes = entries, files, expect["likes"]
    tmp = Path(tempfile.mkdtemp(prefix="videos-test-"))
    try:
        site, cache = tmp / "site", tmp / "cache"
        subprocess.run([str(TOOLS / "assemble.sh"), str(site)], check=True)

        print("-- a build with the fixture feed")
        rc, log, outputs = build(site, cache, feed.url, likes_url=feed.url)
        check(rc == 0, "the build exits 0")
        check(feed.likes_requests == ["/api/likes/counts?kind=video"], "the like totals are asked for once, for videos")
        check_published(site, order, expect, files, log)
        check("SHA-256 does not match" in log and "unexpected box" in log, "the log names the refusals")
        check(bool(outputs.get("cache-key")), "cache-key written to GITHUB_OUTPUT")
        first = {f.name: f.read_bytes() for f in sorted((site / "videos").iterdir())}

        print("-- the same feed again")
        unverified = {f"/community/video/{expect['bad'][k]}.{x}" for k in ("badsha", "badposter") for x in ("mp4", "jpg")}
        feed.requests.clear()
        rc, log, outputs2 = build(site, cache, feed.url)
        again = set(feed.requests) - unverified
        check(rc == 0 and not again, f"a second build fetches no video again, good or refused {sorted(again) or ''}")
        check({f.name: f.read_bytes() for f in sorted((site / "videos").iterdir())} == first, "and publishes the same files")
        check(outputs2.get("cache-key") == outputs.get("cache-key"), "with the same cache key")

        print("-- a withdrawn video and the caps")
        gone = order[1]
        feed.doc["videos"] = [e for e in entries if not (isinstance(e, dict) and e.get("id") == gone)]
        rc, log, _ = build(site, cache, feed.url, "--max-videos", "2")
        rest = [i for i in order if i != gone]
        check(rc == 0 and [v["id"] for v in published(site)["videos"]] == rest[:2], "the 2 newest left are published")
        check(not (site / "videos" / f"{gone}.mp4").exists() and not any(gone in f.name for f in cache.iterdir()),
              "the withdrawn video is gone from the site and the cache")
        check(not list(cache.glob(hashlib.sha256(files[f'/community/video/{gone}.mp4']).hexdigest() + "*")),
              "its MP4 is gone from the cache")
        rc, log, _ = build(site, cache, feed.url, "--max-mb", "0")
        check(rc == 0 and published(site)["videos"] == [] and "dropped over the cap" in log, "--max-mb 0: nothing fits")
        feed.doc["videos"] = entries

        print("-- no feed, a server without videos, an unreachable feed, a broken feed")
        rc, log, _ = build(site, cache, None, "--require-feed")
        check(rc == 0 and published(site)["videos"] == [], "GALLERY_FEED_URL unset: no videos, exit 0")
        feed.status = 404
        rc, log, _ = build(site, cache, feed.url, "--require-feed")
        check(rc == 0 and published(site)["videos"] == [] and "no videos feed" in log,
              "a server without the videos feed (404): no videos, exit 0 even with --require-feed")
        feed.status = 200
        dead = f"http://127.0.0.1:{free_port()}"
        rc, _, _ = build(site, cache, dead)
        check(rc == 0 and published(site)["videos"] == [], "unreachable feed: no videos, exit 0")
        rc, _, _ = build(site, cache, dead, "--require-feed")
        check(rc == 1, "unreachable feed with --require-feed (scheduled builds): exit 1")
        feed.raw = b"<html>"
        rc, _, _ = build(site, cache, feed.url)
        check(rc == 0 and published(site)["videos"] == [], "a feed that is not JSON: no videos, exit 0")
        feed.raw = None
        rc, _, _ = build(site, cache, feed.url + "/community/gallery.json", likes_url=feed.url)
        check(rc == 0 and [v["id"] for v in published(site)["videos"]] == order,
              "GALLERY_FEED_URL naming the gallery feed finds videos.json beside it")

        print("-- the page, in a browser")
        browser = find_browser()
        if browser:
            print(f"     {browser}")
            (tmp / "profiles").mkdir()
            check_pages(browser, site, order, expect, tmp / "profiles")
        elif os.environ.get("CI"):
            check(False, "a browser (set CHROME=path)")
        else:
            print("skip the page checks: no Chrome or Chromium found (set CHROME=path)")

        print(f"\n{len(failures)} failed" if failures else "\nall passed")
        if args.serve:
            build(site, cache, feed.url, likes_url=feed.url)
            print(f"serving the fixture site on http://127.0.0.1:{args.serve}/ (Ctrl-C to stop)", flush=True)
            handler = functools.partial(QuietFiles, directory=str(site))
            try:
                ThreadingHTTPServer(("127.0.0.1", args.serve), handler).serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
