#!/usr/bin/env python3
"""Test tools/build_designs.py against a fixture feed served on 127.0.0.1. Its previews are
SYNTHETIC, drawn here with Pillow at test time: never commit a screenshot of the game, or any
other image from it, to this repository.

Checks what a build publishes: newest first, only the fields the pages show (no "xuid" anywhere
in the output, an "owner" only as the server's 12-hex-digit player id, no description), the like
totals from a stand-in for the accounts service (none, and no failure, when it is down), PNGs of
pixels alone, the size limits, the
entries and files it must refuse (a SHA-256 mismatch, other formats, pixel bombs, too small,
too large or too wide, broken or animated files, previews on another host, bad share codes,
types or counts), the cache (a second build downloads nothing; a withdrawn design leaves the
site and the cache), the cap, and an unset, unreachable, missing (HTTP 404) or broken feed.
check_site.py must pass on the result and fail on a tampered one. Then, in a headless Chrome or
Chromium (CHROME=path, else one on PATH or Playwright's): designs.html lists each kind in its
tab with the players' text as text, the copy button copies the share code (or selects it), the
tabs switch by click, arrow key and address, the sort and the filter work, and online.html shows
the 3 newest and hides its section when there are none; each card shows its like total and links
the creator's gamertag to their page, and profile.html?p=<owner> lists that player's designs.
Each design's own page (designs/<id>.html): one per published design and no other, the players' text
escaped in the markup, its attributes and the structured data (a title that closes the script element
cannot), its own title, description, canonical address and social preview, the creator linked only
with a player id, the steps of its kind, the like total, a page without a picture, the sitemap's one
entry per design (replaced on each build, the other entries kept, a file shared with another release
left as it was), check_site.py refusing a stray, missing or tampered page; in the browser the card
links to the page, the page links back, its like and Copy buttons work, and at a phone's width the
page does not scroll sideways.

  python3 tools/test_build_designs.py                # needs Pillow (tools/requirements.txt)
  python3 tools/test_build_designs.py --serve 8000   # then serve the fixture site for a look

The site is assembled with tools/assemble.sh, which copies tracked files: git add new pages first.
"""
import argparse
import base64
import collections
import functools
import hashlib
import html
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import build_designs  # noqa: E402
import check_site  # noqa: E402
from build_gallery import Refused  # noqa: E402
from test_build_gallery import (LEAK, OWNER_A, OWNER_B, Dom, QuietFiles, assemble, browser_args, check, dom_of,  # noqa: E402
                                failures, find_browser, free_port, jpeg_with_metadata, serve_dir)
from test_build_gallery import FeedHandler as _GalleryHandler  # noqa: E402

NOW = int(time.time())


def did(name):
    return base64.b32encode(hashlib.sha256(name.encode()).digest()).decode()[:12]


def share_code(name):
    return base64.b32encode(hashlib.sha256(b"code " + name.encode()).digest()).decode()[:6]


# ---- synthetic previews ----

def livery(w, h, seed, label):
    """A made-up car side view on a studio backdrop: a body, a cabin, two wheels, stripes and a
    label. Nothing of the game."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), (rnd.randint(20, 50), rnd.randint(14, 34), rnd.randint(50, 90)))
    d = ImageDraw.Draw(im)
    d.rectangle((0, int(h * .78), w, h), fill=(34, 24, 66))
    paint = (rnd.randint(120, 255), rnd.randint(40, 200), rnd.randint(40, 200))
    x0, x1, y0, y1 = w * .1, w * .9, h * .42, h * .72
    d.rounded_rectangle((x0, y0, x1, y1), radius=int(h * .1), fill=paint)
    d.polygon([(w * .3, y0), (w * .4, h * .22), (w * .64, h * .22), (w * .74, y0)], fill=paint)
    d.polygon([(w * .34, y0 - 2), (w * .42, h * .27), (w * .62, h * .27), (w * .69, y0 - 2)], fill=(190, 220, 240))
    stripe = (255, 240, 220) if sum(paint) < 450 else (30, 20, 60)
    for k in range(rnd.randint(1, 3)):
        yy = y0 + (y1 - y0) * (.3 + .18 * k)
        d.rectangle((x0 + 6, yy, x1 - 6, yy + h * .03), fill=stripe)
    for cx in (w * .27, w * .73):
        r = h * .11
        d.ellipse((cx - r, y1 - r, cx + r, y1 + r), fill=(18, 14, 30))
        d.ellipse((cx - r / 2, y1 - r / 2, cx + r / 2, y1 + r / 2), fill=(170, 170, 185))
    d.text((6, 4), label, fill=(255, 245, 225), font=ImageFont.load_default(size=max(10, h // 12)))
    return im


def vinyl(w, h, seed, label, opaque=False):
    """A made-up decal: stars and rings on a see-through background (or an opaque one)."""
    rnd = random.Random(seed)
    im = Image.new("RGBA", (w, h), (36, 24, 72, 255) if opaque else (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for _ in range(4):
        cx, cy, r = rnd.randint(20, w - 20), rnd.randint(20, h - 20), rnd.randint(12, h // 3)
        color = (rnd.randint(150, 255), rnd.randint(60, 220), rnd.randint(40, 160), 255)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=max(3, r // 4))
    d.regular_polygon((w / 2, h / 2, h / 4), 5, rotation=rnd.randint(0, 72), fill=(255, 211, 107, 255))
    d.text((6, 4), label, fill=(255, 245, 225, 255), font=ImageFont.load_default(size=max(10, h // 10)))
    return im


def png(im, **kw):
    buf = io.BytesIO()
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def png_with_metadata(im):
    """A PNG with text, compressed text, XMP, EXIF, an ICC profile, a time and a resolution, all
    naming the leak where they can, and bytes after its end."""
    from PIL import ImageCms
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", f"owner xuid {LEAK}")
    info.add_text("Author", f"owner {LEAK}", zip=True)
    info.add_itxt("XML:com.adobe.xmp", f'<x:xmpmeta owner="xuid {LEAK}"/>')
    exif = Image.Exif()
    exif[0x013B] = f"owner xuid {LEAK}"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    data = png(im, pnginfo=info, exif=exif, icc_profile=icc, dpi=(300, 300))
    return data + b"trailing owner xuid " + LEAK.encode()


def outside_pixels(data):
    """Every byte of a PNG but its compressed pixels (the IDAT chunks' data)."""
    i, kept = 8, [data[:8]]
    while i + 8 <= len(data):
        n, kind = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
        kept.append(data[i:i + 8] if kind == b"IDAT" else data[i:i + 12 + n])
        i += 12 + n
    return b"".join(kept) + data[i:]


def fixtures():
    """(feed entries, {path: bytes}, expected liveries and vinyl groups newest first, expectations)."""
    files, entries = {}, []

    def add(name, kind, data, created, title="", gamertag="Synthetic Driver", car=None, downloads=0,
            rating=(0, 0), preview=None, sha=None, **extra):
        i = did(name)
        path = f"/community/design-img/{i}.png"
        if data is not None:
            files[path] = data
        e = {"id": i, "type": kind, "code": share_code(name), "title": title, "description": "", "gamertag": gamertag,
             "car": {"name": car} if car is not None else None, "layers": 40, "downloads": downloads,
             "createdAt": created,
             "preview": preview or path, "w": 288 if kind == "livery" else 256, "h": 160 if kind == "livery" else 128,
             "sha256": sha or hashlib.sha256(data or b"").hexdigest()}
        if rating is not None:  # None: no rating in the entry at all
            e["rating"] = {"avg": rating[0], "count": rating[1]}
        e.update(extra)
        entries.append(e)
        return i

    weird_title = "Two\nlines \u202eand a bidi override\t"
    long_title = "A very long paint job title " * 6
    markup = '<img src=x onerror="alert(1)"> & "quotes"'
    one = png(livery(288, 160, 1, "Synthetic paint job 1"))
    good = {
        "l1": add("l1", "livery", one, NOW - 100, "Sunset stripes", "Synthetic One", "Synthetic GT", 120, (4.6, 25),
                  owner=OWNER_A),
        "v1": add("v1", "vinylgroup", png(vinyl(256, 128, 2, "Synthetic vinyl 1")), NOW - 150, "Flame decal",
                  "Synthetic Six", "A car a vinyl group does not keep", 55, (4.0, 4), owner=OWNER_B),
        "l2": add("l2", "livery", png_with_metadata(livery(288, 160, 3, "Synthetic paint job 2")), NOW - 200, markup,
                  "<b>Tag</b>", "Synthetic Roadster <i>", 3, (5, 1), xuid=LEAK, owner=LEAK, ownerXuid=LEAK,
                  description=f"made by owner xuid {LEAK}"),
        "l3": add("l3", "livery", jpeg_with_metadata(livery(288, 160, 4, "Synthetic JPEG paint job")), NOW - 300,
                  weird_title, "Synthetic Three", None, 900, (0, 0), owner=OWNER_A),
        "v2": add("v2", "vinylgroup", png(vinyl(256, 128, 5, "Synthetic vinyl 2").quantize(64, Image.Quantize.FASTOCTREE)),
                  NOW - 350, "Number plate", "Synthetic Seven"),  # a palette PNG with a transparent colour
        "l4": add("l4", "livery", png(livery(1024, 512, 6, "Synthetic big preview")), NOW - 400, long_title,
                  "Synthetic Four", "Synthetic GT Sport", 40, (3.9, 60)),
        "v3": add("v3", "vinylgroup", png(vinyl(256, 128, 7, "Synthetic vinyl 3", opaque=True)), NOW - 450,
                  "Club badge", "Synthetic Eight", None, 7, (2.5, 2)),
        "l5": add("l5", "livery", png(livery(288, 160, 8, "Synthetic paint job 5")), NOW - 500, "", "Synthetic Five",
                  "Synthetic Hatch", 0, rating=None),
        "l6": add("l6", "livery", one, NOW - 600, "Same file, another design", "Synthetic Nine", "Synthetic GT", 120,
                  (4.6, 25)),
    }

    # The files to refuse are older than the good ones, so the cap test keeps good ones.
    bad = {}
    base = livery(288, 160, 9, "refused")
    other = png(base)
    bad["badsha"] = add("badsha", "livery", other, NOW - 2050, "Wrong hash", sha=hashlib.sha256(other + b"x").hexdigest())
    gif = io.BytesIO()
    base.save(gif, "GIF")
    bad["gif"] = add("gif", "livery", gif.getvalue(), NOW - 2051, "A GIF")
    bad["bomb"] = add("bomb", "livery", png(Image.new("1", (9000, 9000))), NOW - 2052, "Pixel bomb")
    bad["toolarge"] = add("toolarge", "livery", png(livery(2048, 1024, 10, "too large")), NOW - 2053, "Too large")
    bad["tiny"] = add("tiny", "vinylgroup", png(vinyl(128, 128, 11, "").resize((24, 24))), NOW - 2054, "Too small")
    bad["wide"] = add("wide", "vinylgroup", png(vinyl(1000, 100, 12, "wide")), NOW - 2055, "Too wide")
    bad["trunc"] = add("trunc", "livery", png(base)[:len(png(base)) // 2], NOW - 2056, "Truncated")
    bad["text"] = add("text", "livery", b"this is not an image, owner xuid " + LEAK.encode(), NOW - 2057, "Text")
    bad["huge"] = add("huge", "livery", b"\x89PNG\r\n\x1a\n" + os.urandom((1 << 20) + 10), NOW - 2058, "Too many bytes")
    bad["deep"] = add("deep", "livery", png(Image.new("I;16", (288, 160), 300)), NOW - 2059, "16-bit PNG")
    frames = [vinyl(256, 128, s, "frame") for s in (13, 14)]
    apng = io.BytesIO()
    frames[0].save(apng, "PNG", save_all=True, append_images=frames[1:])
    bad["apng"] = add("apng", "vinylgroup", apng.getvalue(), NOW - 2060, "Animated")
    bad["offsite"] = add("offsite", "livery", other, NOW - 2061, "Elsewhere",
                         preview="http://198.51.100.7/community/design-img/x.png")
    bad["missing"] = add("missing", "livery", None, NOW - 2062, "404", sha="0" * 64)
    # Entries refused for their fields, each with a good preview.
    fields = {"type": "tune", "code": "K7-Q2", "createdAt": (NOW - 10) * 1000, "downloads": -1, "gamertag": " \n"}
    for key, value in fields.items():
        bad[f"bad {key}"] = add(f"bad {key}", "livery", other, NOW - 10, f"Bad {key}", **{key: value})
    bad["long code"] = add("long code", "livery", other, NOW - 10, "Long code", code="ABCDEFGHIJKLMNOP")
    bad["bad rating"] = add("bad rating", "livery", other, NOW - 10, "Bad rating", rating=(7, 3))
    bad["bool downloads"] = add("bool downloads", "livery", other, NOW - 10, "Bool", downloads=True)
    l1 = next(e for e in entries if e["id"] == good["l1"])
    entries.append({"id": "../../etc/pw", "type": "livery", "code": "ABCD12", "gamertag": "x", "createdAt": NOW,
                    "sha256": "0" * 64, "preview": "/community/design-img/x.png"})
    entries.append({k: v for k, v in l1.items() if k != "createdAt"} | {"id": did("nocreated"), "code": "NOCRT1"})
    entries.append("junk")
    random.Random(7).shuffle(entries)
    # after the originals: refused
    entries.append(dict(l1, title="Duplicate id", code="DUPID1"))
    bad["same code"] = add("same code", "vinylgroup", other, NOW - 10, "Same code", code=l1["code"].lower())
    liveries = [good[k] for k in ("l1", "l2", "l3", "l4", "l5", "l6")]
    vinyls = [good[k] for k in ("v1", "v2", "v3")]
    expect = {"good": good, "bad": bad, "markup": markup, "weird_title": "Two lines and a bidi override",
              "long_title": long_title[:80], "entries": {},
              "owners": {good["l1"]: OWNER_A, good["v1"]: OWNER_B, good["l3"]: OWNER_A},
              "likes": {good["l1"]: 7, good["v1"]: 2, good["l3"]: 1, good["l4"]: "9", "AAAA": -1}}
    for e in entries:  # the first entry of each id: the one the build keeps
        if isinstance(e, dict):
            expect["entries"].setdefault(e["id"], e)
    return entries, files, liveries, vinyls, expect


# ---- the fixture server ----

class Feed:
    def __init__(self):
        self.doc = {"version": 1, "generated": NOW, "designs": []}
        self.raw = None          # bytes served instead of the doc (a broken feed)
        self.likes = {}          # the accounts service's public totals of designs
        self.likes_status = 200
        self.missing = False     # the designs feed answers 404 (a server without it)
        self.files = {}
        self.requests = collections.Counter()   # preview path -> requests
        self.fail_images = False
        handler = functools.partial(FeedHandler, self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


class FeedHandler(_GalleryHandler):
    def do_GET(self):
        f = self.feed
        if self.path == "/community/designs.json":
            if f.missing:
                return self.reply(404, b"not found", "text/plain")
            body = f.raw if f.raw is not None else json.dumps(f.doc).encode()
            return self.reply(200, body, "application/json")
        if self.path == "/api/likes/counts?kind=design":
            body = json.dumps({"version": 1, "kind": "design", "generated": NOW, "counts": f.likes}).encode()
            return self.reply(f.likes_status, body, "application/json")
        if self.path.startswith("/community/design-img/"):
            f.requests[self.path] += 1
            if f.fail_images:
                return self.reply(503, b"down", "text/plain")
            if self.path in f.files:
                return self.reply(200, f.files[self.path], "image/png")
        self.reply(404, b"not found", "text/plain")


# ---- running the build ----

def build(site, cache, feed_url, *extra, likes_url=None):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "LIKES_URL")}
    if likes_url:
        env["LIKES_URL"] = likes_url
    out_file = site.parent / "github_output"
    out_file.write_text("")
    env["GITHUB_OUTPUT"] = str(out_file)
    if feed_url is None:
        env.pop("GALLERY_FEED_URL", None)
    else:
        env["GALLERY_FEED_URL"] = feed_url
    args = [sys.executable, str(TOOLS / "build_designs.py"), str(site)] + (["--cache", str(cache)] if cache else []) + list(extra)
    p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600)
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines() if "=" in line)
    return p.returncode, p.stdout + p.stderr, outputs


def published(site):
    return json.loads((site / "data" / "designs.json").read_text(encoding="utf-8"))


def output_files(site):
    return [site / "data" / "designs.json"] + sorted((site / "designs").iterdir())


def check_site_ok(site):
    r = subprocess.run([sys.executable, str(TOOLS / "check_site.py"), str(site)], capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip()


# ---- the checks ----

def score(e):
    return (e["rating"] * e["ratings"] + 9) / (e["ratings"] + 3)


def check_published(site, liveries, vinyls, expect, log):
    doc = published(site)
    designs = doc["designs"]
    ids = [d["id"] for d in designs]
    by = {d["id"]: d for d in designs}
    created = {i: expect["entries"][i]["createdAt"] for i in ids}
    check(doc.get("version") == 1 and set(doc) == {"version", "generated", "designs"}, "designs.json: version 1, no extra fields")
    check(sorted(ids) == sorted(liveries + vinyls) and ids == sorted(ids, key=lambda i: -created[i]),
          f"published the 9 good designs, newest first ({len(ids)} published)")
    check([i for i in ids if by[i]["type"] == "livery"] == liveries and
          [i for i in ids if by[i]["type"] == "vinylgroup"] == vinyls, "6 paint jobs and 3 vinyl groups")
    good, bad = expect["good"], expect["bad"]
    for name, i in bad.items():
        check(i not in by and not (site / "designs" / f"{i}.png").exists(), f"refused: {name}")
    check(by.get(good["l1"], {}).get("title") == "Sunset stripes", "the duplicate id is refused, the first one kept")
    check("SHA-256 does not match the feed" in log and "the preview is not on the feed's server" in log,
          "the log names the SHA-256 mismatch and the preview on another host")
    check(all(set(d) <= check_site.DESIGN_KEYS for d in designs), "only the fields the pages show")
    check(all("description" not in d and "layers" not in d and "sha256" not in d for d in designs),
          "no description, layer count or hash")
    leaks = []
    for f in output_files(site):
        data = f.read_bytes()
        if f.suffix == ".json":
            text = data
        else:
            text = outside_pixels(data)
        if LEAK.encode() in data or b"xuid" in text.lower() or (f.suffix != ".json" and b"owner" in text.lower()):
            leaks.append(f.name)
    check(not leaks, f"no 'xuid' or the leaked XUID anywhere in the output, no 'owner' in a PNG {leaks or ''}")
    check({d["id"]: d["owner"] for d in designs if "owner" in d} == expect["owners"],
          "owner: only the server's player ids (the XUID the feed leaked is dropped)")
    if expect.get("with_likes"):
        want = {i: n for i, n in expect["likes"].items() if isinstance(n, int) and n >= 0}
        check({d["id"]: d.get("likes") for d in designs} == {i: want.get(i, 0) for i in by},
              "likes: every design has its total (0 when none; bad counts left out)")
    else:
        check(not any("likes" in d for d in designs), "no like totals without LIKES_URL")
    m = by.get(good["l2"], {})
    check(m.get("title") == expect["markup"] and m.get("gamertag") == "<b>Tag</b>" and m.get("car") == "Synthetic Roadster <i>",
          "player text kept as written (not escaped)")
    check(by.get(good["l3"], {}).get("title") == expect["weird_title"], "control and bidi characters dropped")
    check(by.get(good["l4"], {}).get("title") == expect["long_title"], "long titles cut to 80 characters")
    check("car" not in by.get(good["l3"], {}) and "car" not in by.get(good["v1"], {}),
          "no car when the feed has none, and none for a vinyl group")
    check(by.get(good["l1"], {}).get("car") == "Synthetic GT", "a paint job's car")
    check((m.get("rating"), m.get("ratings"), by.get(good["l3"], {}).get("rating"), by.get(good["l5"], {}).get("ratings"))
          == (5.0, 1, 0, 0), "ratings: average and count; none when nobody rated")
    check(by.get(good["l3"], {}).get("downloads") == 900, "downloads kept")
    codes = {d["id"]: d["code"] for d in designs}
    check(all(codes[i] == expect["entries"][i]["code"] for i in ids), "share codes kept as the feed has them")

    chunks_ok, pillow_ok, sizes_ok = True, True, True
    for d in designs:
        data = (site / d["src"]).read_bytes()
        try:
            w, h, types = check_site.png_chunks(data)
        except ValueError as e:
            chunks_ok = False
            print(f"     {d['src']}: {e}")
            continue
        if set(types) - check_site.PNG_PIXEL_CHUNKS:
            chunks_ok = False
            print(f"     {d['src']}: chunks {sorted(set(t.decode() for t in types))}")
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "PNG" or im.size != (d["w"], d["h"]) or im.info or len(im.getexif()):
                pillow_ok = False
                print(f"     {d['src']}: {im.format} {im.size} info={sorted(im.info)}")
        if (w, h) != (d["w"], d["h"]) or w > 576:
            sizes_ok = False
    check(chunks_ok, "only IHDR, PLTE, tRNS, IDAT and IEND chunks and nothing after the end in any output PNG")
    check(pillow_ok, "Pillow finds no metadata in any output PNG and the sizes match designs.json")
    check(sizes_ok, "size limits: 576 px wide at most")
    sizes = {d["id"]: (d["w"], d["h"]) for d in designs}
    check(sizes.get(good["l4"]) == (576, 288), "a 1024x512 preview -> 576x288")
    check(sizes.get(good["l1"]) == (288, 160) and sizes.get(good["v1"]) == (256, 128), "288x160 and 256x128 stay as they are")

    def mode(i):
        with Image.open(site / "designs" / f"{i}.png") as im:
            return im.mode
    check(mode(good["v1"]) == "RGBA" and mode(good["v2"]) == "RGBA", "a vinyl group's see-through background stays")
    check(mode(good["l1"]) == "RGB" and mode(good["v3"]) == "RGB", "an opaque preview is RGB")


def parsed(text):
    d = Dom()
    d.feed(text)
    return d


def meta(page, key):
    return next((a.get("content") for t, a in page.tags if t == "meta" and (a.get("property") == key or a.get("name") == key)), None)


def ld_of(text):
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)
    return json.loads(m.group(1)) if m else None


def element_text(text, tag):
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", text, re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))) if m else None


def site_entries(site):
    return re.findall(r"<loc>([^<]+)</loc>", (site / "sitemap.xml").read_text(encoding="utf-8"))


def check_design_pages(site, expect):
    """designs/<id>.html for every published design, as tools/build_designs.py writes them."""
    designs = published(site)["designs"]
    by = {d["id"]: d for d in designs}
    pages = sorted(f.stem for f in (site / "designs").glob("*.html"))
    check(pages == sorted(by), f"one page per published design and no other ({len(pages)} pages, {len(by)} designs)")
    good = expect["good"]
    texts = {i: (site / "designs" / f"{i}.html").read_text(encoding="utf-8") for i in pages}
    check(not any(check_site.markup_from_text(t) for t in texts.values()), "no page has markup from the players' text")
    check(not any(LEAK in t or "xuid" in t.lower() for t in texts.values()), "no page names a XUID")

    l1, l2, v1 = texts.get(good["l1"], ""), texts.get(good["l2"], ""), texts.get(good["v1"], "")
    p1 = parsed(l1)
    check(f'<link rel="canonical" href="https://forza.dyotson.dev/designs/{good["l1"]}.html">' in l1
          and element_text(l1, "title") == "Sunset stripes by Synthetic One - Paint job - Forza Horizon 1: ReImagined",
          "its own title and canonical address")
    desc = meta(p1, "description") or ""
    check("paint job for the Synthetic GT by Synthetic One" in desc and expect["entries"][good["l1"]]["code"] in desc
          and meta(p1, "og:description") == desc, "its own description: the kind, the car, the creator and the share code")
    check(meta(p1, "og:image") == f"https://forza.dyotson.dev/designs/{good['l1']}.png" and meta(p1, "og:image:width") == "288"
          and meta(p1, "og:image:height") == "160" and meta(p1, "twitter:card") == "summary_large_image"
          and meta(p1, "og:url") == f"https://forza.dyotson.dev/designs/{good['l1']}.html",
          "its social preview is the design's picture, with its size")
    check(element_text(l1, "h1") == "Sunset stripes" and "Synthetic GT" in p1.texts, "the name and the car")
    imgs = [a for t, a in p1.tags if t == "img" and a.get("src") == f"{good['l1']}.png"]
    check(len(imgs) == 1 and imgs[0].get("width") == "288" and imgs[0].get("height") == "160"
          and "Sunset stripes" in imgs[0].get("alt", ""), "the picture, with its size and a text alternative")
    check(any(t == "a" and a.get("href") == f"../profile.html?p={OWNER_A}" for t, a in p1.tags),
          "the creator links to their player page")
    iso = time.strftime("%Y-%m-%d", time.gmtime(expect["entries"][good["l1"]]["createdAt"]))
    check(any(t == "time" and a.get("datetime") == iso for t, a in p1.tags), f"the day it was shared ({iso}, UTC)")
    slots = [a for t, a in p1.tags if t == "span" and "like-slot" in a.get("class", "")]
    want = str(expect["likes"][good["l1"]]) if expect.get("with_likes") else None
    check(len(slots) == 1 and slots[0].get("data-kind") == "design" and slots[0].get("data-id") == good["l1"]
          and slots[0].get("data-likes") == want, f"the like slot of the cards, with the total ({want})")
    copy = [a for t, a in p1.tags if t == "button" and "copy" in a.get("class", "")]
    check(len(copy) == 1 and copy[0].get("data-code") == expect["entries"][good["l1"]]["code"], "the share code's Copy button")
    check("gamertag" in element_text(l1, "ol") and " text " in f" {element_text(v1, 'ol')} ",
          "the steps of its kind: the gamertag field for a paint job, the text field for a vinyl group")
    howto = (site / "designs.html").read_text(encoding="utf-8")
    check(element_text(l1, "ol") in html.unescape(re.sub(r"<[^>]+>", "", howto)), "in the words designs.html uses")
    check(any(t == "a" and a.get("href") == f"../designs.html#design-{good['l1']}" for t, a in p1.tags),
          "the page links back to its card on designs.html")
    check(any(t == "link" and a.get("href") == "../style.css" for t, a in p1.tags)
          and not any(t == "a" and a.get("aria-current") for t, a in p1.tags), "addresses one folder up, no current page in the navbar")
    ld = ld_of(l1) or {}
    check(ld.get("name") == "Sunset stripes" and ld.get("creator", {}).get("name") == "Synthetic One"
          and ld.get("dateCreated") == iso and ld.get("image", "").endswith(f"/designs/{good['l1']}.png"),
          "structured data: the name, the creator, the day and the picture")

    p2 = parsed(l2)
    check(element_text(l2, "h1") == expect["markup"] and "&lt;img src=x onerror=" in l2
          and not any(t == "img" and "onerror" in a for t, a in p2.tags) and not any(t == "i" for t, a in p2.tags),
          "a title of markup is text, escaped")
    check("<b>Tag</b>" in p2.texts and "Synthetic Roadster <i>" in p2.texts, "a gamertag and a car of markup too")
    check(element_text(l2, "title").startswith(expect["markup"] + " by <b>Tag</b>")
          and expect["markup"] in (meta(p2, "og:title") or ""), "and in the title and the attributes")
    check((ld_of(l2) or {}).get("name") == expect["markup"] and "<img" not in re.search(
        r'<script type="application/ld\+json">(.*?)</script>', l2, re.S).group(1), "and in the structured data, with no <")
    check(not any(t == "a" and a.get("href", "").startswith("../profile.html") for t, a in p2.tags) and
          "owner" not in by[good["l2"]], "a creator without a player id: no link (the XUID the feed sent is not one)")
    check("A car a vinyl group does not keep" not in v1 and "For the" not in texts.get(good["l3"], "x"),
          "no car for a vinyl group, or when the feed has none")
    l5 = parsed(texts.get(good["l5"], ""))
    check(element_text(texts.get(good["l5"], ""), "h1") == "Untitled paint job" and "No ratings yet" in l5.texts,
          "an untitled design and one nobody rated")


def check_page_unit():
    """design_page() on its own: text that tries to leave its place, a design without a picture."""
    template = build_designs.template_of(TOOLS.parent / "designs.html")
    hostile = {"id": "HOSTILE1", "type": "livery", "code": "ABC123", "gamertag": "Dr \"Quote\" O'Hara & Co",
               "title": "</script><script>alert(1)</script><!-- \u2028", "car": "Car</b><img src=x onerror=alert(1)>",
               "owner": OWNER_A, "downloads": 1, "rating": 0, "ratings": 0, "createdAt": NOW,
               "src": "designs/HOSTILE1.png", "w": 288, "h": 160}
    text = build_designs.design_page(template, hostile)
    page = parsed(text)
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)
    check(not check_site.markup_from_text(text) and text.count("<script") == 5,
          "a title that closes the script element opens no script (the 2 in the head, the 3 of the site)")
    check(block is not None and not re.search(r"[<>&\u2028]", block.group(1)) and
          json.loads(block.group(1)).get("name") == hostile["title"], "the structured data holds it as text: no <, > or &")
    check(element_text(text, "h1") == hostile["title"] and element_text(text, "title").startswith(hostile["title"]),
          "the heading and the title show it as written")
    check(meta(page, "og:title").startswith(hostile["title"] + " by " + hostile["gamertag"])
          and hostile["car"] in meta(page, "description"), "attributes keep quotes and markup as text")
    check(not any(t == "img" and "onerror" in a for t, a in page.tags), "no element from the car's name")
    bare = dict(hostile, id="NOPICTURE1", title="Bare")
    for k in ("src", "w", "h", "owner"):
        bare.pop(k)
    text = build_designs.design_page(template, bare)
    page = parsed(text)
    check("No picture" in page.texts and not any(t == "img" and "NOPICTURE1" in a.get("src", "") for t, a in page.tags)
          and meta(page, "og:image") == "https://forza.dyotson.dev/images/social-preview.png"
          and meta(page, "twitter:card") == "summary" and "image" not in (ld_of(text) or {"image": 1}),
          "a design without a picture: a placeholder, and the site's social preview")
    check(not any(t == "a" and "profile.html" in a.get("href", "") for t, a in page.tags) and "Dr \"Quote\" O'Hara & Co" in page.texts,
          "a creator without a page: the gamertag as plain text")


def check_sitemap(site, before):
    """One sitemap entry for each design's page; every entry the sitemap had besides is kept."""
    ids = [d["id"] for d in published(site)["designs"]]
    locs = site_entries(site)
    mine = [u for u in locs if u.startswith("https://forza.dyotson.dev/designs/")]
    check(sorted(mine) == sorted(f"https://forza.dyotson.dev/designs/{i}.html" for i in ids),
          f"sitemap.xml: one entry for each design's page ({len(mine)} for {len(ids)} designs)")
    check([u for u in locs if u not in mine] == before, "sitemap.xml: the other entries are kept, in their order")


def check_tampering(site):
    """check_site.py fails on what the build must never publish."""
    data = site / "data" / "designs.json"
    original = data.read_bytes()
    doc = json.loads(original)
    first = doc["designs"][0]

    def fails(what):
        ok, out = check_site_ok(site)
        check(not ok, f"check_site.py refuses {what}: {out.splitlines()[0] if out else ''}")

    doc["designs"][0] = dict(first, xuid=LEAK)
    data.write_text(json.dumps(doc))
    fails("a design with a xuid")
    doc["designs"][0] = dict(first, code="NO-GOOD")
    data.write_text(json.dumps(doc))
    fails("a bad share code")
    doc["designs"][0] = dict(first, owner=LEAK)
    data.write_text(json.dumps(doc))
    fails("an owner that is a XUID")
    doc["designs"][0] = dict(first, likes=-3)
    data.write_text(json.dumps(doc))
    fails("a negative like total")
    data.write_bytes(original)

    png_path = site / first["src"]
    kept = png_path.read_bytes()
    with Image.open(io.BytesIO(kept)) as im:
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "a text chunk")
        png_path.write_bytes(png(im, pnginfo=info))
    fails("a preview with a text chunk")
    png_path.write_bytes(kept + b"more")
    fails("bytes after a preview's end")
    png_path.write_bytes(kept)

    stray = site / "designs" / "STRAYSTRAY12.png"
    stray.write_bytes(kept)
    fails("a preview designs.json does not list")
    stray.unlink()

    page = site / "designs" / f"{first['id']}.html"
    text = page.read_text(encoding="utf-8")
    (site / "designs" / "STRAYSTRAY12.html").write_text(text, encoding="utf-8")
    fails("a page for a design designs.json does not list")
    (site / "designs" / "STRAYSTRAY12.html").unlink()
    page.unlink()
    fails("a listed design without its page")
    page.write_text(text.replace("<h1>", '<h1><img src="x" onerror="alert(1)">', 1), encoding="utf-8")
    fails("a page with markup in its text")
    page.write_text(text.replace('"name":', '"name":"<b>",', 1), encoding="utf-8")
    fails("a page whose structured data has markup")
    page.write_text(text.replace('href="../designs.html#', 'href="../nowhere.html#', 1), encoding="utf-8")
    fails("a page with a link to nothing")
    page.write_text(text.replace("<h1>", "<h1>My xuid tribute ", 1), encoding="utf-8")
    ok, out = check_site_ok(site)
    check(ok, f"but passes a page whose player text says 'xuid' (a title must never stop a build) {'' if ok else out}")
    page.write_text(text, encoding="utf-8")
    sitemap = site / "sitemap.xml"
    sm = sitemap.read_text(encoding="utf-8")
    entry = f"  <url>\n    <loc>https://forza.dyotson.dev/designs/{first['id']}.html</loc>\n  </url>\n"
    sitemap.write_text(sm.replace("</urlset>", entry + "</urlset>"), encoding="utf-8")
    fails("a design listed twice in the sitemap")
    sitemap.write_text(re.sub(r"\s*<url>\s*<loc>https://forza\.dyotson\.dev/designs/[^<]*</loc>.*?</url>", "", sm, count=1, flags=re.S),
                       encoding="utf-8")
    fails("a design missing from the sitemap")
    sitemap.write_text(sm, encoding="utf-8")

    online = site / "online.html"
    page = online.read_text(encoding="utf-8")
    online.write_text(page.replace("data-designs-section hidden", "data-designs-section"), encoding="utf-8")
    fails("an Online page whose designs section does not start hidden")
    online.write_text(page, encoding="utf-8")
    ok, out = check_site_ok(site)
    check(ok, "and passes once it is put back")


HARNESS = r"""
(function () {
  "use strict";
  var out = {}, copied = [];
  function stub(fn) { Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: fn } }); }
  stub(function (t) { copied.push(t); return Promise.resolve(); });
  function ids(sel) {
    return Array.prototype.map.call(document.querySelectorAll(sel + " > li"), function (li) { return li.id.replace(/^design-/, ""); });
  }
  function state() {
    return {
      selected: Array.prototype.map.call(document.querySelectorAll('[role="tab"]'), function (t) { return t.getAttribute("aria-selected"); }),
      tabIndex: Array.prototype.map.call(document.querySelectorAll('[role="tab"]'), function (t) { return t.tabIndex; }),
      paintHidden: document.getElementById("paint-jobs").hidden,
      vinylHidden: document.getElementById("vinyl-groups").hidden,
      hash: location.hash
    };
  }
  function later(f) { return new Promise(function (r) { setTimeout(function () { r(f()); }, 80); }); }
  function done() {
    var pre = document.createElement("pre");
    pre.id = "test-results";
    pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }
  document.addEventListener("designs:shown", function first() {
    document.removeEventListener("designs:shown", first);
    var buttons = document.querySelectorAll("#paint-jobs .copy");
    out.code = buttons[0].dataset.code;
    out.codeShown = buttons[0].parentNode.querySelector("code").textContent;
    buttons[0].click();
    later(function () {
      out.copied = copied.slice();
      out.label = buttons[0].textContent;
      out.live = document.querySelector("[aria-live]").textContent;
      stub(function () { return Promise.reject(new Error("denied")); });
      document.execCommand = function () { return false; };
      out.code2 = buttons[1].dataset.code;
      buttons[1].click();
    }).then(function () { return later(function () {
      out.fallbackLabel = buttons[1].textContent;
      out.selection = String(window.getSelection());
      out.before = state();
      document.getElementById("tab-vinyl-groups").click();
      out.afterClick = state();
      document.getElementById("tab-vinyl-groups").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
      out.afterKey = state();
      out.focused = document.activeElement ? document.activeElement.id : "";
      var sort = document.getElementById("design-sort");
      sort.value = "downloads"; sort.dispatchEvent(new Event("change"));
      out.byDownloads = ids("#paint-jobs .designs");
      sort.value = "rating"; sort.dispatchEvent(new Event("change"));
      out.byRating = ids("#paint-jobs .designs");
      sort.value = "newest"; sort.dispatchEvent(new Event("change"));
      out.byNewest = ids("#paint-jobs .designs");
      var f = document.getElementById("design-filter");
      f.value = "synthetic gt"; f.dispatchEvent(new Event("input"));
      out.filterCar = ids("#paint-jobs .designs");
      f.value = "  <B>TAG "; f.dispatchEvent(new Event("input"));
      out.filterTag = ids("#paint-jobs .designs");
      out.filterTagVinyl = ids("#vinyl-groups .designs");
      f.value = "nothing matches this"; f.dispatchEvent(new Event("input"));
      out.filterNone = ids("#paint-jobs .designs");
      var st = document.querySelector("#paint-jobs .design-state");
      out.noneState = [st.textContent, st.hidden];
      f.value = ""; f.dispatchEvent(new Event("input"));
      out.cleared = ids("#paint-jobs .designs");
      done();
    }); });
  });
})();
"""


PAGE_HARNESS = r"""
(function () {
  "use strict";
  var copied = [];
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: function (t) { copied.push(t); return Promise.resolve(); } } });
  setTimeout(function () {
    var b = document.querySelector("button.copy");
    b.click();
    setTimeout(function () {
      var like = document.querySelector(".like-slot .like");
      var pre = document.createElement("pre");
      pre.id = "test-results";
      pre.textContent = JSON.stringify({ code: b.dataset.code, copied: copied, label: b.textContent,
                                         like: like ? like.getAttribute("href") : null,
                                         likeText: like ? like.textContent : null });
      document.body.append(pre);
    }, 300);
  }, 500);
})();
"""

CARD_HITS = r"""
(function () {
  "use strict";
  // What a click on each part of the first card reaches: the picture and the rating go to the page
  // (the title's link covers the card), the creator, the like and Copy stay their own.
  document.addEventListener("designs:shown", function () {
    setTimeout(function () {
      var li = document.querySelector("#paint-jobs .designs > li");
      function at(sel) {
        var e = li.querySelector(sel);
        e.scrollIntoView({ block: "center", behavior: "instant" });
        var r = e.getBoundingClientRect();
        var hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        var a = hit && hit.closest("a, button");
        return a ? a.tagName.toLowerCase() + " " + (a.getAttribute("href") || a.className) : hit ? hit.tagName : "";
      }
      var pre = document.createElement("pre");
      pre.id = "test-results";
      pre.textContent = JSON.stringify({ img: at("img"), rating: at(".rating"), by: at(".design-by a"), like: at(".like"),
                                         copy: at("button.copy"), title: at(".design-title a") });
      document.body.append(pre);
    }, 300);
  });
})();
"""

NARROW = r"""
(function () {
  var frames = Array.prototype.slice.call(document.querySelectorAll("iframe"));
  setTimeout(function () {
    var out = frames.map(function (frame) {
      var d = frame.contentDocument, w = frame.contentWindow;
      var boxes = Array.prototype.map.call(d.querySelectorAll(frame.dataset.boxes), function (e) {
        var r = e.getBoundingClientRect();
        return { l: Math.round(r.left), r: Math.round(r.right) };
      });
      return { page: frame.getAttribute("src"), width: w.innerWidth, scroll: d.documentElement.scrollWidth,
               client: d.documentElement.clientWidth, boxes: boxes };
    });
    var pre = document.createElement("pre");
    pre.id = "test-results";
    pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }, 4000);
})();
"""


def results_of(browser, url, profile):
    profile = Path(tempfile.mkdtemp(dir=profile))
    p = subprocess.run(browser_args(browser, profile) + ["--dump-dom", url], capture_output=True, text=True, timeout=120)
    m = re.search(r'<pre id="test-results">(.*?)</pre>', p.stdout, re.S)
    return json.loads(html.unescape(m.group(1))) if m else None


def check_pages(browser, site, liveries, vinyls, expect, profile):
    good = expect["good"]
    entries = expect["entries"]
    httpd, base = serve_dir(site)
    try:
        page = dom_of(browser, base + "/designs.html", profile)
        attrs = {a.get("id"): a for t, a in page.tags if a.get("id")}
        cards = [(a["id"][7:]) for t, a in page.tags if t == "li" and a.get("id", "").startswith("design-")]
        check(cards == liveries + vinyls, "designs.html: the paint jobs then the vinyl groups, newest first")
        check("hidden" not in attrs.get("paint-jobs", {"hidden": ""}) and "hidden" in attrs.get("vinyl-groups", {}),
              "designs.html: the Paint jobs tab is open, the Vinyl groups one closed")
        check(attrs.get("tab-paint-jobs", {}).get("aria-selected") == "true" and
              attrs.get("tab-vinyl-groups", {}).get("aria-selected") == "false", "designs.html: aria-selected on the tabs")
        check(not any(t in ("img", "b", "i") and ("onerror" in a or a.get("src") == "x") for t, a in page.tags) and
              not any(t == "i" for t, a in page.tags), "designs.html: the players' markup is not turned into elements")
        check(expect["markup"] in page.texts and "<b>Tag</b>" in page.texts and "Synthetic Roadster <i>" in page.texts,
              "designs.html: titles, gamertags and cars inserted as text")
        check("Untitled" in page.texts, "designs.html: a design without a title is 'Untitled'")
        check("A car a vinyl group does not keep" not in page.texts, "designs.html: no car on a vinyl group")
        buttons = [a.get("data-code") for t, a in page.tags if t == "button" and "copy" in a.get("class", "").split()]
        check(buttons == [entries[i]["code"] for i in liveries + vinyls], "designs.html: a copy button with each share code")
        imgs = [a for t, a in page.tags if t == "img" and a.get("src", "").startswith("designs/")]
        check(len(imgs) == 9 and all(a.get("loading") == "lazy" and a.get("width") and a.get("height") for a in imgs),
              "designs.html: previews are lazy-loaded with width and height")
        check("6" in page.texts and "3" in page.texts, "designs.html: the tabs count 6 paint jobs and 3 vinyl groups")
        check("Rated 5.0 out of 5 by 1 player" in [a.get("aria-label") for t, a in page.tags] and "No ratings yet" in page.texts
              and "900 downloads" in page.texts and "0 downloads" in page.texts,
              "designs.html: ratings and downloads are spelled out")
        likes = [a for t, a in page.tags if t == "a" and "like" in a.get("class", "").split()]
        check(len(likes) == 9 and all(a.get("href") == "account.html" for a in likes)
              and any(a.get("aria-label", "").startswith("7 likes") for a in likes),
              "designs.html: each design's like total, a link to sign in for a visitor who did not")
        creators = {a.get("href") for t, a in page.tags if t == "a" and a.get("href", "").startswith("profile.html")}
        check(creators == {f"profile.html?p={OWNER_A}", f"profile.html?p={OWNER_B}"},
              "designs.html: the creators' gamertags link to their pages (only those with a player id)")
        links = [a.get("href") for t, a in page.tags if t == "a" and re.fullmatch(r"designs/\w+\.html", a.get("href", ""))]
        check(links == [f"designs/{i}.html" for i in liveries + vinyls], "designs.html: each card's title links to its page")
        prof = dom_of(browser, base + f"/profile.html?p={OWNER_A}", profile)
        listed = [a["id"][7:] for t, a in prof.tags if t == "li" and a.get("id", "").startswith("design-")]
        check(listed == [good["l1"], good["l3"]], "profile.html: only that player's designs, newest first")
        check([a.get("href") for t, a in prof.tags if t == "a" and a.get("href", "").startswith("designs/")]
              == [f"designs/{good['l1']}.html", f"designs/{good['l3']}.html"], "profile.html: and links to their pages")

        own = dom_of(browser, base + f"/designs/{good['l1']}.html", profile)
        likes = [a for t, a in own.tags if t == "a" and "like" in a.get("class", "").split()]
        check(len(likes) == 1 and likes[0].get("href") == "../account.html" and likes[0].get("aria-label", "").startswith("7 likes"),
              "a design's page: its like total, and the sign-in link finds the account page")
        check(any(t == "img" and a.get("src") == f"{good['l1']}.png" for t, a in own.tags) and "Sunset stripes" in own.texts,
              "a design's page: its picture and name in the browser")
        page_text = (site / "designs" / f"{good['l1']}.html").read_text(encoding="utf-8")
        (site / "designs" / "page-test.js").write_text(PAGE_HARNESS, encoding="utf-8")
        (site / "designs" / "page-test.html").write_text(page_text.replace(
            '<script src="../designs.js"></script>', '<script src="../designs.js"></script>\n<script src="page-test.js"></script>'),
            encoding="utf-8")
        r = results_of(browser, base + "/designs/page-test.html", profile) or {}
        check(r.get("copied") == [entries[good["l1"]]["code"]] and r.get("label") == "Copied",
              "a design's page: Copy puts the share code on the clipboard")
        check(r.get("like") == "../account.html" and r.get("likeText") == "7", "and its like control is the cards' one")
        (site / "designs" / "page-test.js").unlink()
        (site / "designs" / "page-test.html").unlink()

        (site / "cards-test.js").write_text(CARD_HITS, encoding="utf-8")
        (site / "cards-test.html").write_text((site / "designs.html").read_text(encoding="utf-8").replace(
            '<script src="designs.js"></script>', '<script src="designs.js"></script>\n<script src="cards-test.js"></script>'),
            encoding="utf-8")
        r = results_of(browser, base + "/cards-test.html", profile) or {}
        to_page = f"a designs/{good['l1']}.html"
        check(r.get("img") == to_page and r.get("title") == to_page and r.get("rating") == to_page,
              f"a card: the picture, the title and the rest of the card open the design's page ({r})")
        check(r.get("by") == f"a profile.html?p={OWNER_A}" and r.get("like") == "a account.html" and r.get("copy") == "button copy",
              "a card: the creator, the like and Copy stay clickable above that link")
        (site / "cards-test.js").unlink()
        (site / "cards-test.html").unlink()

        (site / "narrow-test.js").write_text(NARROW, encoding="utf-8")
        (site / "narrow.html").write_text(
            '<!doctype html><meta charset="utf-8"><body style="margin:0">'
            f'<iframe src="designs/{good["l2"]}.html" data-boxes=".design-item-pic, .design-item h1, .design-code, .howto" width="390" height="2400" style="border:0"></iframe>'
            f'<iframe src="designs/{good["l4"]}.html" data-boxes=".design-item-pic, .design-item h1, .design-code, .howto" width="360" height="2400" style="border:0"></iframe>'
            '<iframe src="designs.html" data-boxes="#paint-jobs .design" width="390" height="2400" style="border:0"></iframe>'
            '<script src="narrow-test.js"></script></body>', encoding="utf-8")
        p = subprocess.run(browser_args(browser, Path(tempfile.mkdtemp(dir=profile))) + ["--dump-dom", base + "/narrow.html"],
                           capture_output=True, text=True, timeout=120)
        m = re.search(r'<pre id="test-results">(.*?)</pre>', p.stdout, re.S)
        narrow = json.loads(html.unescape(m.group(1))) if m else []
        check(len(narrow) == 3 and all(f["scroll"] <= f["client"] for f in narrow),
              f"at a phone's width (390 and 360 px): no sideways scrolling ({[(f['page'], f['scroll'], f['client']) for f in narrow]})")
        check(len(narrow) == 3 and all(f["boxes"] and all(b["l"] >= 12 and b["r"] <= f["width"] - 12 for b in f["boxes"]) for f in narrow),
              f"and the picture, the name, the share code and the steps inside the page's margins ({narrow and [f['boxes'][:4] for f in narrow]})")
        (site / "narrow-test.js").unlink()
        (site / "narrow.html").unlink()
        check(any("0 photos" in x and "2 designs" in x and "8 likes" in x for x in prof.texts)
              and "Synthetic One" in [x.strip() for x in prof.texts],
              "profile.html: their gamertag and how many designs and likes")

        (site / "designs-test.js").write_text(HARNESS, encoding="utf-8")
        harness = (site / "designs.html").read_text(encoding="utf-8").replace(
            '<script src="designs.js"></script>', '<script src="designs.js"></script>\n<script src="designs-test.js"></script>')
        (site / "designs-test.html").write_text(harness, encoding="utf-8")
        r = results_of(browser, base + "/designs-test.html", profile) or {}
        check(bool(r), "the page test ran")
        check(r.get("copied") == [r.get("code")] and r.get("code") == entries[liveries[0]]["code"] and
              r.get("codeShown") == r.get("code"), "Copy puts the share code on the clipboard")
        check(r.get("label") == "Copied" and r.get("code", "?") in r.get("live", ""), "and says so (on the button and to screen readers)")
        check(r.get("fallbackLabel") == "Selected" and r.get("selection") == r.get("code2"),
              "when the clipboard refuses, the code is selected instead")
        check(r.get("before", {}).get("selected") == ["true", "false"] and r.get("before", {}).get("vinylHidden") is True,
              "before: Paint jobs selected")
        a = r.get("afterClick", {})
        check(a.get("selected") == ["false", "true"] and a.get("paintHidden") is True and a.get("vinylHidden") is False
              and a.get("hash") == "#vinyl-groups" and a.get("tabIndex") == [-1, 0], "a click on Vinyl groups switches the tab")
        k = r.get("afterKey", {})
        check(k.get("selected") == ["true", "false"] and k.get("vinylHidden") is True and k.get("hash") == "#paint-jobs"
              and r.get("focused") == "tab-paint-jobs", "the left arrow goes back to Paint jobs, with the focus")
        rows = {i: {"createdAt": entries[i]["createdAt"], "downloads": entries[i]["downloads"],
                    "rating": round(entries[i].get("rating", {}).get("avg", 0), 2) if entries[i].get("rating", {}).get("count") else 0,
                    "ratings": entries[i].get("rating", {}).get("count", 0)} for i in liveries}
        by_dl = sorted(liveries, key=lambda i: (-rows[i]["downloads"], -rows[i]["createdAt"]))
        by_rating = sorted(liveries, key=lambda i: (-(rows[i]["ratings"] > 0), -score(rows[i]), -rows[i]["ratings"], -rows[i]["createdAt"]))
        check(r.get("byDownloads") == by_dl, f"Most downloaded: {r.get('byDownloads')}")
        top = r.get("byRating") or []
        check(top == by_rating and top.index(good["l4"]) < top.index(good["l2"]) and
              set(top[-2:]) == {good["l3"], good["l5"]}, "Top rated: sixty 3.9s beat one 5-star vote, unrated last")
        check(r.get("byNewest") == liveries, "Newest: back to newest first")
        check(r.get("filterCar") == [good["l1"], good["l4"], good["l6"]], "the filter finds a car (any case)")
        check(r.get("filterTag") == [good["l2"]] and r.get("filterTagVinyl") == [], "and a creator, in both tabs")
        check(r.get("filterNone") == [] and r.get("noneState") == ["No paint jobs match the filter.", False],
              "no match: it says so")
        check(r.get("cleared") == liveries, "an empty filter shows them all again")
        (site / "designs-test.js").unlink()
        (site / "designs-test.html").unlink()

        page = dom_of(browser, base + "/designs.html#vinyl-groups", profile)
        attrs = {a.get("id"): a for t, a in page.tags if a.get("id")}
        check("hidden" in attrs.get("paint-jobs", {}) and "hidden" not in attrs.get("vinyl-groups", {"hidden": ""}),
              "designs.html#vinyl-groups opens the Vinyl groups tab")
        page = dom_of(browser, base + f"/designs.html#design-{good['v3']}", profile)
        attrs = {a.get("id"): a for t, a in page.tags if a.get("id")}
        check(attrs.get("tab-vinyl-groups", {}).get("aria-selected") == "true", "a link to a vinyl group opens its tab")

        online = dom_of(browser, base + "/online.html", profile)
        section = next((a for t, a in online.tags if t == "section" and a.get("id") == "designs"), None)
        check(section is not None and "hidden" not in section, "online.html: the designs section is shown")
        shown = [a["id"][7:] for t, a in online.tags if t == "li" and a.get("id", "").startswith("design-")]
        newest = sorted(liveries + vinyls, key=lambda i: -entries[i]["createdAt"])[:3]
        check(shown == newest, "online.html: the 3 newest designs, both kinds, in order")
        check("Paint job" in online.texts and "Vinyl group" in online.texts, "online.html: each card says its kind")
        check(any(t == "a" and a.get("href") == "designs.html" for t, a in online.tags), "online.html: links to designs.html")

        (site / "data" / "designs.json").write_text(json.dumps({"version": 1, "generated": NOW, "designs": []}))
        online = dom_of(browser, base + "/online.html", profile)
        section = next((a for t, a in online.tags if t == "section" and a.get("id") == "designs"), None)
        check(section is not None and "hidden" in section, "no designs: online.html hides its designs section")
        page = dom_of(browser, base + "/designs.html", profile)
        check(any("No paint jobs yet" in t for t in page.texts), "no designs: designs.html says there are none yet")
        (site / "data" / "designs.json").unlink()
        online = dom_of(browser, base + "/online.html", profile)
        section = next((a for t, a in online.tags if t == "section" and a.get("id") == "designs"), None)
        check(section is not None and "hidden" in section, "no designs.json: online.html hides its designs section")
        page = dom_of(browser, base + "/designs.html", profile)
        check(any("could not be loaded" in t for t in page.texts), "no designs.json: designs.html says so")
    finally:
        httpd.shutdown()


def main():
    ap = argparse.ArgumentParser(description="Test tools/build_designs.py with synthetic previews.")
    ap.add_argument("--serve", type=int, metavar="PORT", help="afterwards, serve the fixture site on this port")
    args = ap.parse_args()

    print("-- the feed address")
    for given, want in (("http://h:1", "http://h:1/community/designs.json"),
                        ("http://h:1/", "http://h:1/community/designs.json"),
                        ("http://h:1/community/gallery.json", "http://h:1/community/designs.json"),
                        ("https://h/community/gallery.json?x=1", "https://h/community/designs.json")):
        check(build_designs.designs_url(given) == want, f"{given} -> {want}")
    try:
        build_designs.designs_url("ftp://h/community/gallery.json")
        check(False, "a URL that is not http(s) is refused")
    except Refused:
        check(True, "a URL that is not http(s) is refused")

    print("-- a design's page on its own")
    check_page_unit()

    entries, files, liveries, vinyls, expect = fixtures()
    feed = Feed()
    feed.doc["designs"], feed.files = entries, files
    tmp = Path(tempfile.mkdtemp(prefix="designs-test-"))
    try:
        site, cache = tmp / "site", tmp / "cache"
        assemble(site)
        sitemap_before = site_entries(site)
        shared = tmp / "sitemap-of-another-release.xml"
        os.link(site / "sitemap.xml", shared)  # what tools/build_community.sh's copy of a release does
        shared_text = shared.read_text(encoding="utf-8")

        print("-- a build with the fixture feed (GALLERY_FEED_URL names the photos feed)")
        feed.likes = expect["likes"]
        rc, log, outputs = build(site, cache, feed.url + "/community/gallery.json")
        check(rc == 0, "the build exits 0")
        check_published(site, liveries, vinyls, expect, log)
        check_design_pages(site, expect)
        check_sitemap(site, sitemap_before)
        check(shared.read_text(encoding="utf-8") == shared_text and not os.path.samefile(shared, site / "sitemap.xml"),
              "sitemap.xml is written anew: a release that shared the file keeps its own")
        expect["with_likes"] = True
        rc, log, _ = build(site, cache, feed.url + "/community/gallery.json", likes_url=feed.url)
        check(rc == 0, "with LIKES_URL: the build exits 0")
        check_published(site, liveries, vinyls, expect, log)
        check_design_pages(site, expect)
        check_sitemap(site, sitemap_before)
        feed.likes_status = 500
        rc, log, _ = build(site, cache, feed.url, "--require-feed", likes_url=feed.url)
        expect["with_likes"] = False
        check(rc == 0 and "no likes in this build" in log, "the like totals failing: no totals, exit 0 even with --require-feed")
        check_published(site, liveries, vinyls, expect, log)
        feed.likes_status = 200
        good_ids = set(liveries + vinyls)
        unique = len({e["sha256"] for e in entries if isinstance(e, dict) and e.get("id") in good_ids})
        noted = len([k for k in ("gif", "bomb", "toolarge", "tiny", "wide", "trunc", "text", "deep", "apng")])
        check(len(list(cache.glob("*.png"))) == unique and len(list(cache.glob("*.refused"))) == noted,
              f"the cache holds the {unique} distinct previews and {noted} refusals")
        check(bool(outputs.get("cache-key")), "cache-key written to GITHUB_OUTPUT")
        ok, out = check_site_ok(site)
        check(ok, f"check_site.py passes on the built site: {out}")
        check_tampering(site)
        first = {f.name: f.read_bytes() for f in output_files(site)[1:]}

        print("-- the same feed again (GALLERY_FEED_URL names the server)")
        path = {e["id"]: e["preview"] for e in entries if isinstance(e, dict)}
        unverified = {path[expect["bad"][k]] for k in ("badsha", "huge", "missing")}  # no file with that SHA-256
        feed.requests.clear()
        rc, log, outputs2 = build(site, cache, feed.url)
        again = set(feed.requests) - unverified
        check(rc == 0 and not again, f"a second build fetches no preview again, good or refused {sorted(again) or ''}")
        check({f.name: f.read_bytes() for f in output_files(site)[1:]} == first, "and publishes the same files")
        check(outputs2.get("cache-key") == outputs.get("cache-key"), "with the same cache key")

        print("-- a damaged cache entry")
        l1_sha = expect["entries"][expect["good"]["l1"]]["sha256"]
        build_designs.cache_path(cache, l1_sha).write_bytes(b"garbage")
        feed.requests.clear()
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and set(feed.requests) - unverified == {path[expect["good"]["l1"]]} and
              published(site)["designs"][0]["w"] == 288, "is downloaded and re-encoded again")

        print("-- a withdrawn design and the cap")
        gone = expect["good"]["l3"]
        gone_sha = expect["entries"][gone]["sha256"]
        feed.doc["designs"] = [e for e in entries if not (isinstance(e, dict) and e.get("id") == gone)]
        rc, log, _ = build(site, cache, feed.url, "--max-designs", "4")
        rest = [i for i in sorted(good_ids, key=lambda i: -expect["entries"][i]["createdAt"]) if i != gone]
        check(rc == 0 and [d["id"] for d in published(site)["designs"]] == rest[:4], "the 4 newest left are published")
        check("dropped over the cap of 4" in log and rest[4] in log, "the log names what the cap dropped")
        check(not (site / "designs" / f"{gone}.png").exists() and not list(cache.glob(f"{gone_sha}*")),
              "the withdrawn design is gone from the site and the cache")
        check(sorted(f.name for f in (site / "designs").iterdir()) == sorted(f"{i}.{x}" for i in rest[:4] for x in ("png", "html")),
              "designs/ holds only what is listed, a picture and a page each")
        check_sitemap(site, sitemap_before)
        check(f"designs/{gone}.html" not in (site / "sitemap.xml").read_text(), "the withdrawn design leaves the sitemap")

        print("-- an empty feed")
        feed.doc["designs"] = []
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and published(site)["designs"] == [] and not list((site / "designs").iterdir()), "no designs, no pages")
        check(not list(cache.iterdir()), "and an empty cache")
        check(site_entries(site) == sitemap_before, "and the sitemap as it was, without design entries")
        ok, out = check_site_ok(site)
        check(ok, "check_site.py passes with no designs")

        print("-- no feed, an unreachable, missing or broken feed, previews that fail")
        rc, log, out = build(site, cache, None, "--require-feed")
        check(rc == 0 and published(site)["designs"] == [] and not out.get("cache-key"), "GALLERY_FEED_URL unset: no designs, exit 0")
        dead = f"http://127.0.0.1:{free_port()}"
        rc, log, _ = build(site, cache, dead)
        check(rc == 0 and published(site)["designs"] == [], "unreachable feed: no designs, exit 0")
        rc, log, _ = build(site, cache, dead, "--require-feed")
        check(rc == 1, "unreachable feed with --require-feed (scheduled runs): exit 1")
        feed.missing = True
        feed.doc["designs"] = entries
        rc, log, _ = build(site, cache, feed.url, "--require-feed")
        check(rc == 0 and published(site)["designs"] == [] and "no designs feed" in log,
              "a server without the designs feed (HTTP 404), even with --require-feed: no designs, exit 0")
        feed.missing = False
        feed.raw = b"<html>not json</html>"
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and published(site)["designs"] == [], "a feed that is not JSON: no designs, exit 0")
        rc, log, _ = build(site, cache, feed.url, "--require-feed")
        check(rc == 1, "and with --require-feed: exit 1")
        feed.raw = json.dumps({"version": 2, "designs": []}).encode()
        rc, log, _ = build(site, cache, feed.url, "--require-feed")
        check(rc == 1, "an unknown feed version with --require-feed: exit 1")
        feed.raw = json.dumps({"version": 1, "photos": []}).encode()
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and published(site)["designs"] == [], "a feed without a designs list: no designs, exit 0")
        feed.raw = None
        feed.doc["designs"] = [e for e in entries if isinstance(e, dict) and e.get("id") == expect["good"]["l1"]][:1]
        shutil.rmtree(cache)
        feed.fail_images = True
        rc, log, _ = build(site, cache, feed.url, "--require-feed")
        check(rc == 1, "the preview route failing (HTTP 503) with --require-feed: exit 1")
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and published(site)["designs"] == [], "and without it: the designs are left out, exit 0")
        feed.doc["designs"], feed.fail_images = entries, False

        print("-- the pages, in a browser")
        rc, log, _ = build(site, cache, feed.url, likes_url=feed.url)
        browser = find_browser()
        if browser:
            print(f"     {browser}")
            (tmp / "profiles").mkdir()
            check_pages(browser, site, liveries, vinyls, expect, tmp / "profiles")
        elif os.environ.get("CI"):
            check(False, "a browser (set CHROME=path)")
        else:
            print("skip the page checks: no Chrome or Chromium found (set CHROME=path)")

        print(f"\n{len(failures)} failed" if failures else "\nall passed")
        if args.serve:
            build(site, cache, feed.url, likes_url=feed.url)
            print(f"serving the fixture site on http://127.0.0.1:{args.serve}/designs.html (Ctrl-C to stop)", flush=True)
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
