#!/usr/bin/env python3
"""Test tools/build_gallery.py against a fixture feed served on 127.0.0.1. Its images are
SYNTHETIC, drawn here with Pillow at test time: never commit a screenshot of the game, or any
other image from it, to this repository.

Checks what a build publishes: newest approved first, only the fields the pages show (no "xuid"
or "owner" anywhere in the output), JPEGs without metadata, the size limits, the files it must
refuse (a SHA-256 mismatch, other formats, pixel bombs, oversized, broken or animated files,
images on another host), the cache (a second build downloads nothing; a withdrawn photo leaves
the site and the cache), the cap, and an unset, unreachable or broken feed. Then, in a headless
Chrome or Chromium (CHROME=path, else one on PATH or Playwright's): the front page shows the 6
newest photos with the players' text as text, gallery.html lists them all, and with an empty
gallery the front page hides its photos section.

  python3 tools/test_build_gallery.py                # needs Pillow (tools/requirements.txt)
  python3 tools/test_build_gallery.py --serve 8000   # then serve the fixture site for a look

The site is assembled with tools/assemble.sh, which copies tracked files: git add new pages first.
"""
import argparse
import base64
import collections
import functools
import glob
import hashlib
import html.parser
import io
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import build_gallery  # noqa: E402
import check_site  # noqa: E402

NOW = int(time.time())
LEAK = "2535411122223333"  # a made-up XUID the feed leaks by mistake

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def pid(name):
    return base64.b32encode(hashlib.sha256(name.encode()).digest()).decode()[:12]


# ---- synthetic images ----

def scene(w, h, seed, label):
    """A made-up sunset: gradient sky, sun, two ridges, a road and a label. Nothing of the game."""
    rnd = random.Random(seed)
    top = (rnd.randint(20, 70), rnd.randint(10, 40), rnd.randint(70, 130))
    bottom = (255, rnd.randint(90, 190), rnd.randint(40, 110))
    im = ImageOps.colorize(Image.linear_gradient("L").resize((w, h)), top, bottom)
    d = ImageDraw.Draw(im)
    r = h // 6
    cx, cy = rnd.randint(w // 5, 4 * w // 5), h // 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 220, 140))
    for depth, color in ((0.55, (70, 40, 110)), (0.7, (45, 28, 80))):
        pts = [(0, h)] + [(x, int(h * depth + rnd.randint(-h // 8, h // 8))) for x in range(0, w + 1, w // 8)] + [(w, h)]
        d.polygon(pts, fill=color)
    d.polygon([(w * 0.42, h), (w * 0.49, h * 0.72), (w * 0.51, h * 0.72), (w * 0.58, h)], fill=(30, 22, 55))
    font = ImageFont.load_default(size=max(12, h // 18))
    d.text((w // 30, h // 30), label, fill=(255, 245, 225), font=font)
    return im


def jpeg(im, **kw):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90, **kw)
    return buf.getvalue()


def png(im, **kw):
    buf = io.BytesIO()
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def jpeg_with_metadata(im):
    """A JPEG with EXIF (GPS, artist), XMP, an ICC profile and a comment, all naming the leak."""
    from PIL import ImageCms
    exif = Image.Exif()
    exif[0x013B] = f"owner xuid {LEAK}"          # Artist
    exif[0x010F] = "Synthetic camera"            # Make
    exif[0x8825] = {1: "N", 2: (40.0, 1.0, 0.0), 3: "W", 4: (105.0, 0.0, 0.0)}  # GPS
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    xmp = (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           f'<rdf:Description owner="xuid {LEAK}"/></rdf:RDF></x:xmpmeta>').encode()
    kw = dict(exif=exif, icc_profile=icc, comment=f"owner xuid {LEAK}".encode())
    try:
        data = jpeg(im, xmp=xmp, **kw)
    except TypeError:  # an older Pillow without xmp=
        data = jpeg(im, **kw)
    return data + b"trailing owner xuid " + LEAK.encode()  # bytes after EOI


def fixtures():
    """(feed entries, {path: bytes}, expected ids newest first, expectations)."""
    files, entries = {}, []

    def add(name, data, approved, title="", gamertag="Synthetic Driver", image=None, sha=None, **extra):
        i = pid(name)
        path = f"/community/img/{i}.jpg"
        if data is not None:
            files[path] = data
        e = {"id": i, "gamertag": gamertag, "title": title, "takenAt": approved - 600, "approvedAt": approved,
             "w": 1280, "h": 720, "sha256": sha or hashlib.sha256(data or b"").hexdigest(), "image": image or path}
        e.update(extra)
        entries.append(e)
        return i

    base = scene(1280, 720, 1, "Synthetic test photo")
    weird_title = "Two\nlines \u202eand a bidi override\t"
    long_title = "A very long title " * 12
    good = {
        "hd": add("hd", jpeg(scene(2560, 1440, 2, "Synthetic HD photo")), NOW - 100, "Sunset over the ridge", "Synthetic One"),
        "png": add("png", png(scene(1280, 720, 3, "Synthetic PNG photo").convert("RGBA"),
                              pnginfo=_pnginfo()), NOW - 200, "PNG, not JPEG", "Synthetic Two"),
        "meta": add("meta", jpeg_with_metadata(scene(1280, 720, 4, "Synthetic photo with metadata")), NOW - 300,
                    '<img src=x onerror="alert(1)"> & "quotes"', "<b>Tag</b>", car="Synthetic GT",
                    location="Nowhere Ridge", xuid=LEAK, owner=LEAK, ownerXuid=LEAK),
        "weird": add("weird", jpeg(scene(1280, 720, 5, "Synthetic photo 4")), NOW - 400, weird_title, "Synthetic Four"),
        "long": add("long", jpeg(scene(1280, 720, 6, "Synthetic photo 5")), NOW - 500, long_title, "Synthetic Five"),
        "notitle": add("notitle", jpeg(scene(1280, 720, 7, "Synthetic photo 6")), NOW - 600, "", "Synthetic Six"),
        "seven": add("seven", jpeg(scene(1280, 720, 8, "Synthetic photo 7")), NOW - 700, "Dusk run", "Synthetic Seven"),
        "same": add("same", jpeg(scene(1280, 720, 8, "Synthetic photo 7")), NOW - 800, "Same file, another id", "Synthetic Eight"),
    }
    # The files to refuse are older than the good ones, so the cap test keeps good ones.
    bad = {}
    other = jpeg(base)
    bad["badsha"] = add("badsha", other, NOW - 2050, "Wrong hash", sha=hashlib.sha256(other + b"x").hexdigest())
    gif = io.BytesIO()
    base.save(gif, "GIF")
    bad["gif"] = add("gif", gif.getvalue(), NOW - 2051, "A GIF")
    bad["bomb"] = add("bomb", png(Image.new("1", (9000, 9000))), NOW - 2052, "Pixel bomb")
    bad["wide"] = add("wide", jpeg(scene(2000, 200, 9, "wide")), NOW - 2053, "Too wide")
    bad["tiny"] = add("tiny", jpeg(scene(160, 90, 10, "tiny")), NOW - 2054, "Too small")
    bad["trunc"] = add("trunc", jpeg(base)[:20000], NOW - 2055, "Truncated")
    bad["text"] = add("text", b"this is not an image, owner xuid " + LEAK.encode(), NOW - 2056, "Text")
    bad["huge"] = add("huge", b"\xff\xd8\xff" + os.urandom((16 << 20) + 10), NOW - 2057, "Too many bytes")
    bad["deep"] = add("deep", png(Image.new("I;16", (1280, 720), 300)), NOW - 2058, "16-bit PNG")
    frames = [scene(640, 360, s, "frame") for s in (11, 12)]
    apng = io.BytesIO()
    frames[0].save(apng, "PNG", save_all=True, append_images=frames[1:])
    bad["apng"] = add("apng", apng.getvalue(), NOW - 2059, "Animated")
    bad["offsite"] = add("offsite", jpeg(base), NOW - 2060, "Elsewhere", image="http://198.51.100.7/community/img/x.jpg")
    bad["missing"] = add("missing", None, NOW - 2061, "404", sha="0" * 64)
    entries.append({"id": "../../etc/pw", "gamertag": "x", "title": "", "approvedAt": NOW, "sha256": "0" * 64,
                    "image": "/community/img/x.jpg"})
    entries.append({"id": pid("noapproved"), "gamertag": "x", "title": "", "sha256": "0" * 64,
                    "image": "/community/img/x.jpg"})
    entries.append({"id": pid("notag"), "gamertag": " \n", "title": "", "approvedAt": NOW, "sha256": "0" * 64,
                    "image": "/community/img/x.jpg"})
    entries.append("junk")
    random.Random(7).shuffle(entries)
    hd = next(e for e in entries if isinstance(e, dict) and e["id"] == good["hd"])
    entries.append(dict(hd, title="Duplicate id"))  # after the original: refused
    order = [good[k] for k in ("hd", "png", "meta", "weird", "long", "notitle", "seven", "same")]
    expect = {"good": good, "bad": bad, "weird_title": "Two lines and a bidi override",
              "long_title": long_title[:80], "meta_title": '<img src=x onerror="alert(1)"> & "quotes"'}
    return entries, files, order, expect


def _pnginfo():
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", f"owner xuid {LEAK}")
    info.add_itxt("XML:com.adobe.xmp", f"owner {LEAK}")
    return info


# ---- the fixture server ----

class Feed:
    def __init__(self):
        self.doc = {"version": 1, "generated": NOW, "photos": []}
        self.raw = None          # bytes served instead of the doc (a broken feed)
        self.files = {}
        self.requests = collections.Counter()   # image path -> requests
        self.fail_images = False
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
        if self.path == "/community/gallery.json":
            body = f.raw if f.raw is not None else json.dumps(f.doc).encode()
            return self.reply(200, body, "application/json")
        if self.path.startswith("/community/img/"):
            f.requests[self.path] += 1
            if f.fail_images:
                return self.reply(503, b"down", "text/plain")
            if self.path in f.files:
                return self.reply(200, f.files[self.path], "image/jpeg")
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


def serve_dir(path):
    handler = functools.partial(QuietFiles, directory=str(path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


# ---- running the build ----

def build(site, cache, feed_url, *extra):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS",)}
    out_file = site.parent / "github_output"
    out_file.write_text("")
    env["GITHUB_OUTPUT"] = str(out_file)
    if feed_url is None:
        env.pop("GALLERY_FEED_URL", None)
    else:
        env["GALLERY_FEED_URL"] = feed_url
    args = [sys.executable, str(TOOLS / "build_gallery.py"), str(site)] + (["--cache", str(cache)] if cache else []) + list(extra)
    p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600)
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines() if "=" in line)
    return p.returncode, p.stdout + p.stderr, outputs


def gallery(site):
    return json.loads((site / "data" / "gallery.json").read_text(encoding="utf-8"))


def output_files(site):
    return [site / "data" / "gallery.json"] + sorted((site / "photos").iterdir())


def assemble(dest):
    subprocess.run([str(TOOLS / "assemble.sh"), str(dest)], check=True)


# ---- the checks ----

def check_published(site, order, expect, log):
    doc = gallery(site)
    photos = doc["photos"]
    ids = [p["id"] for p in photos]
    check(doc.get("version") == 1 and set(doc) == {"version", "generated", "photos"}, "gallery.json: version 1, no extra fields")
    check(ids == order, f"published the 8 good photos, newest approved first ({len(ids)} published)")
    by = {p["id"]: p for p in photos}
    good, bad = expect["good"], expect["bad"]
    for name, i in bad.items():
        check(i not in by and not (site / "photos" / f"{i}.jpg").exists(), f"refused: {name}")
    check("SHA-256 does not match the feed" in log, "the log names the SHA-256 mismatch")
    check(all(set(p) <= check_site.GALLERY_KEYS for p in photos), "only the fields the pages show")
    # gallery.json whole; the JPEGs whole for the leaked number, and everything but their
    # compressed pixels for the words (4-5 random bytes can spell them by chance).
    leaks = []
    for f in output_files(site):
        data = f.read_bytes()
        text = data if f.suffix == ".json" else check_site.jpeg_segments(data)[3]
        if LEAK.encode() in data or any(w in text.lower() for w in (b"xuid", b"owner")):
            leaks.append(f.name)
    check(not leaks, f"no 'xuid', 'owner' or the leaked XUID anywhere in the output {leaks or ''}")
    m = by.get(good["meta"], {})
    check(m.get("title") == expect["meta_title"] and m.get("gamertag") == "<b>Tag</b>",
          "player text kept as written (not escaped)")
    check(m.get("car") == "Synthetic GT" and m.get("location") == "Nowhere Ridge", "car and location kept")
    check(by.get(good["weird"], {}).get("title") == expect["weird_title"], "control and bidi characters dropped")
    check(by.get(good["long"], {}).get("title") == expect["long_title"], "long titles cut to 80 characters")
    check("car" not in by.get(good["hd"], {}), "no empty optional fields")

    sizes_ok, meta_ok, pillow_ok = True, True, True
    for p in photos:
        for rel, w, h in ((p["src"], p["w"], p["h"]), (p["thumb"], p["tw"], p["th"])):
            data = (site / rel).read_bytes()
            try:
                jw, jh, markers, _ = check_site.jpeg_segments(data)
            except ValueError as e:
                meta_ok = False
                print(f"     {rel}: {e}")
                continue
            if any(0xE1 <= mk <= 0xEF or mk == 0xFE for mk in markers):
                meta_ok = False
                print(f"     {rel}: metadata markers {[hex(mk) for mk in markers]}")
            with Image.open(io.BytesIO(data)) as im:
                if im.format != "JPEG" or im.size != (w, h) or len(im.getexif()) or \
                        set(im.info) & {"exif", "icc_profile", "comment", "xmp", "photoshop", "adobe"}:
                    pillow_ok = False
                    print(f"     {rel}: {im.format} {im.size} exif={len(im.getexif())} info={sorted(im.info)}")
            if (jw, jh) != (w, h):
                sizes_ok = False
    check(meta_ok, "no EXIF, XMP, ICC or comment segment and nothing after the end in any output JPEG")
    check(pillow_ok, "Pillow finds no metadata in any output JPEG and the sizes match gallery.json")
    hd = by.get(good["hd"], {})
    check((hd.get("w"), hd.get("h"), hd.get("tw"), hd.get("th")) == (1920, 1080, 480, 270), "2560x1440 -> 1920x1080 and a 480x270 thumbnail")
    small = [p for p in photos if p["id"] != good["hd"]]
    check(all((p["w"], p["h"], p["tw"], p["th"]) == (1280, 720, 480, 270) for p in small), "1280x720 stays 1280x720 (no upscaling)")
    check(sizes_ok and all(p["w"] <= 1920 and p["tw"] <= 480 for p in photos), "size limits: 1920 px wide at most, thumbnails 480")


class Dom(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.texts = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.texts.append(data)


def find_browser():
    env = os.environ.get("CHROME", "")
    names = [shutil.which(env) or env if env else ""] + [shutil.which(n) or "" for n in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "chrome-headless-shell")]
    names += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True)
    names += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")), reverse=True)
    return next((n for n in names if n and os.access(n, os.X_OK)), None)


def browser_args(browser, profile):
    args = [browser, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", f"--user-data-dir={profile}", "--virtual-time-budget=15000"]
    if "headless-shell" not in browser:
        args.insert(1, "--headless=new")
    return args


def dom_of(browser, url, profile):
    profile = Path(tempfile.mkdtemp(dir=profile))  # a new profile each time: nothing cached
    p = subprocess.run(browser_args(browser, profile) + ["--dump-dom", url], capture_output=True, text=True, timeout=120)
    d = Dom()
    d.feed(p.stdout)
    return d


def check_pages(browser, site, order, expect, profile):
    httpd, base = serve_dir(site)
    try:
        index = dom_of(browser, base + "/index.html", profile)
        section = next((a for t, a in index.tags if t == "section" and a.get("id") == "photos"), None)
        check(section is not None and "hidden" not in section, "front page: the photos section is shown")
        shown = [a["data-full"] for t, a in index.tags if t == "button" and a.get("data-full", "").startswith("photos/")]
        check(shown == [f"photos/{i}.jpg" for i in order[:6]], "front page: the 6 newest photos, in order")
        check(not any(t == "img" and ("onerror" in a or a.get("src") == "x") for t, a in index.tags),
              "front page: the player's markup is not turned into elements")
        check(expect["meta_title"] in index.texts and "<b>Tag</b>" in index.texts, "front page: titles and gamertags inserted as text")
        caps = {a["data-full"]: a.get("data-cap") for t, a in index.tags if t == "button" and "data-full" in a}
        meta = expect["good"]["meta"]
        check(caps.get(f"photos/{meta}.jpg") == expect["meta_title"] + " \u2014 <b>Tag</b>", "lightbox caption is 'title \u2014 gamertag'")
        thumbs = [a for t, a in index.tags if t == "img" and a.get("src", "").startswith("photos/")]
        check(thumbs and all(a.get("loading") == "lazy" and a.get("width") and a.get("height") for a in thumbs),
              "thumbnails are lazy-loaded with width and height")

        page = dom_of(browser, base + "/gallery.html", profile)
        listed = [a["data-full"] for t, a in page.tags if t == "button" and a.get("data-full", "").startswith("photos/")]
        check(listed == [f"photos/{i}.jpg" for i in order], f"gallery.html: all {len(order)} photos, newest first")
        check(not any(t == "img" and ("onerror" in a or a.get("src") == "x") for t, a in page.tags),
              "gallery.html: the player's markup is not turned into elements")

        (site / "data" / "gallery.json").write_text(json.dumps({"version": 1, "generated": NOW, "photos": []}))
        index = dom_of(browser, base + "/index.html", profile)
        section = next((a for t, a in index.tags if t == "section" and a.get("id") == "photos"), None)
        check(section is not None and "hidden" in section, "empty gallery: the front page hides the photos section")
        page = dom_of(browser, base + "/gallery.html", profile)
        check(any("No photos yet" in t for t in page.texts), "empty gallery: gallery.html says there are no photos yet")
        (site / "data" / "gallery.json").unlink()
        index = dom_of(browser, base + "/index.html", profile)
        section = next((a for t, a in index.tags if t == "section" and a.get("id") == "photos"), None)
        check(section is not None and "hidden" in section, "no gallery.json: the front page hides the photos section")
    finally:
        httpd.shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(description="Test tools/build_gallery.py with synthetic photos.")
    ap.add_argument("--serve", type=int, metavar="PORT", help="afterwards, serve the fixture site on this port")
    args = ap.parse_args()

    entries, files, order, expect = fixtures()
    feed = Feed()
    feed.doc["photos"], feed.files = entries, files
    feed_url = feed.url + "/community/gallery.json"
    tmp = Path(tempfile.mkdtemp(prefix="gallery-test-"))
    try:
        site, cache = tmp / "site", tmp / "cache"
        assemble(site)

        print("-- a build with the fixture feed")
        rc, log, outputs = build(site, cache, feed_url)
        check(rc == 0, "the build exits 0")
        check_published(site, order, expect, log)
        unique = len({e["sha256"] for e in entries if isinstance(e, dict) and e.get("id") in order})
        noted = len([k for k in expect["bad"] if k not in ("badsha", "huge", "missing", "offsite")])
        check(len(list(cache.glob("*.jpg"))) == 2 * unique and len(list(cache.glob("*.refused"))) == noted,
              f"the cache holds the {unique} distinct files (2 copies each) and {noted} refusals")
        check(bool(outputs.get("cache-key")), "cache-key written to GITHUB_OUTPUT")
        r = subprocess.run([sys.executable, str(TOOLS / "check_site.py"), str(site)], capture_output=True, text=True)
        check(r.returncode == 0, f"check_site.py passes on the built site: {r.stdout.strip()}")
        first = {f.name: f.read_bytes() for f in output_files(site)[1:]}

        print("-- the same feed again")
        path = {e["id"]: e["image"] for e in entries if isinstance(e, dict)}
        unverified = {path[expect["bad"][k]] for k in ("badsha", "huge", "missing")}  # no file with that SHA-256
        feed.requests.clear()
        rc, log, outputs2 = build(site, cache, feed_url)
        again = set(feed.requests) - unverified
        check(rc == 0 and not again, f"a second build fetches no photo again, good or refused {sorted(again) or ''}")
        check({f.name: f.read_bytes() for f in output_files(site)[1:]} == first, "and publishes the same files")
        check(outputs2.get("cache-key") == outputs.get("cache-key"), "with the same cache key")

        print("-- a damaged cache entry")
        hd_sha = next(e["sha256"] for e in entries if isinstance(e, dict) and e.get("id") == expect["good"]["hd"])
        build_gallery.cache_paths(cache, hd_sha)[0].write_bytes(b"garbage")
        feed.requests.clear()
        rc, log, _ = build(site, cache, feed_url)
        check(rc == 0 and set(feed.requests) - unverified == {path[expect["good"]["hd"]]} and
              gallery(site)["photos"][0]["w"] == 1920, "is downloaded and re-encoded again")

        print("-- a withdrawn photo and the cap")
        gone = expect["good"]["png"]
        gone_sha = next(e["sha256"] for e in entries if isinstance(e, dict) and e.get("id") == gone)
        feed.doc["photos"] = [e for e in entries if not (isinstance(e, dict) and e.get("id") == gone)]
        rc, log, _ = build(site, cache, feed_url, "--max-photos", "5")
        rest = [i for i in order if i != gone]
        check(rc == 0 and [p["id"] for p in gallery(site)["photos"]] == rest[:5], "the 5 newest left are published")
        check("dropped over the cap of 5" in log and rest[5] in log, "the log names what the cap dropped")
        check(not (site / "photos" / f"{gone}.jpg").exists() and not list(cache.glob(f"{gone_sha}*")),
              "the withdrawn photo is gone from the site and the cache")
        check(sorted(f.name for f in (site / "photos").iterdir()) ==
              sorted(n for i in rest[:5] for n in (f"{i}.jpg", f"{i}-thumb.jpg")), "photos/ holds only what is listed")

        print("-- an empty feed")
        feed.doc["photos"] = []
        rc, log, _ = build(site, cache, feed_url)
        check(rc == 0 and gallery(site)["photos"] == [] and not list((site / "photos").iterdir()), "an empty gallery")
        check(not list(cache.iterdir()), "and an empty cache")
        r = subprocess.run([sys.executable, str(TOOLS / "check_site.py"), str(site)], capture_output=True, text=True)
        check(r.returncode == 0, "check_site.py passes on the empty gallery")

        print("-- no feed, an unreachable feed, a broken feed, images that fail")
        rc, log, out = build(site, cache, None, "--require-feed")
        check(rc == 0 and gallery(site)["photos"] == [] and not out.get("cache-key"), "GALLERY_FEED_URL unset: empty gallery, exit 0")
        dead = f"http://127.0.0.1:{free_port()}"
        rc, log, _ = build(site, cache, dead)
        check(rc == 0 and gallery(site)["photos"] == [], "unreachable feed: empty gallery, exit 0")
        rc, log, _ = build(site, cache, dead, "--require-feed")
        check(rc == 1, "unreachable feed with --require-feed (scheduled runs): exit 1")
        feed.raw = b"<html>not json</html>"
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and gallery(site)["photos"] == [], "a feed that is not JSON: empty gallery, exit 0")
        feed.raw = json.dumps({"version": 2, "photos": []}).encode()
        rc, log, _ = build(site, cache, feed.url, "--require-feed")
        check(rc == 1, "an unknown feed version with --require-feed: exit 1")
        feed.raw = None
        feed.doc["photos"] = [e for e in entries if isinstance(e, dict) and e.get("id") == expect["good"]["hd"]][:1]
        feed.fail_images = True
        rc, log, _ = build(site, cache, feed.url, "--require-feed")
        check(rc == 1, "the image route failing (HTTP 503) with --require-feed: exit 1")
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and gallery(site)["photos"] == [], "and without it: the photos are left out, exit 0")
        feed.doc["photos"], feed.fail_images = entries, False

        print("-- the pages, in a browser")
        rc, log, _ = build(site, cache, feed.url)
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
            build(site, cache, feed.url)
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
