#!/usr/bin/env python3
"""Test tools/build_designs.py against a fixture feed served on 127.0.0.1. Its previews are
SYNTHETIC, drawn here with Pillow at test time: never commit a screenshot of the game, or any
other image from it, to this repository.

Checks what a build publishes: newest first, only the fields the pages show (no "xuid" or
"owner" anywhere in the output, no description), PNGs of pixels alone, the size limits, the
entries and files it must refuse (a SHA-256 mismatch, other formats, pixel bombs, too small,
too large or too wide, broken or animated files, previews on another host, bad share codes,
types or counts), the cache (a second build downloads nothing; a withdrawn design leaves the
site and the cache), the cap, and an unset, unreachable, missing (HTTP 404) or broken feed.
check_site.py must pass on the result and fail on a tampered one. Then, in a headless Chrome or
Chromium (CHROME=path, else one on PATH or Playwright's): designs.html lists each kind in its
tab with the players' text as text, the copy button copies the share code (or selects it), the
tabs switch by click, arrow key and address, the sort and the filter work, and online.html shows
the 3 newest and hides its section when there are none.

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
from test_build_gallery import (LEAK, QuietFiles, assemble, browser_args, check, dom_of, failures,  # noqa: E402
                                find_browser, free_port, jpeg_with_metadata, serve_dir)
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
        "l1": add("l1", "livery", one, NOW - 100, "Sunset stripes", "Synthetic One", "Synthetic GT", 120, (4.6, 25)),
        "v1": add("v1", "vinylgroup", png(vinyl(256, 128, 2, "Synthetic vinyl 1")), NOW - 150, "Flame decal",
                  "Synthetic Six", "A car a vinyl group does not keep", 55, (4.0, 4)),
        "l2": add("l2", "livery", png_with_metadata(livery(288, 160, 3, "Synthetic paint job 2")), NOW - 200, markup,
                  "<b>Tag</b>", "Synthetic Roadster <i>", 3, (5, 1), xuid=LEAK, owner=LEAK, ownerXuid=LEAK,
                  description=f"made by owner xuid {LEAK}"),
        "l3": add("l3", "livery", jpeg_with_metadata(livery(288, 160, 4, "Synthetic JPEG paint job")), NOW - 300,
                  weird_title, "Synthetic Three", None, 900, (0, 0)),
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
              "long_title": long_title[:80], "entries": {}}
    for e in entries:  # the first entry of each id: the one the build keeps
        if isinstance(e, dict):
            expect["entries"].setdefault(e["id"], e)
    return entries, files, liveries, vinyls, expect


# ---- the fixture server ----

class Feed:
    def __init__(self):
        self.doc = {"version": 1, "generated": NOW, "designs": []}
        self.raw = None          # bytes served instead of the doc (a broken feed)
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
        if self.path.startswith("/community/design-img/"):
            f.requests[self.path] += 1
            if f.fail_images:
                return self.reply(503, b"down", "text/plain")
            if self.path in f.files:
                return self.reply(200, f.files[self.path], "image/png")
        self.reply(404, b"not found", "text/plain")


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
        if LEAK.encode() in data or any(w in text.lower() for w in (b"xuid", b"owner")):
            leaks.append(f.name)
    check(not leaks, f"no 'xuid', 'owner' or the leaked XUID anywhere in the output {leaks or ''}")
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

    entries, files, liveries, vinyls, expect = fixtures()
    feed = Feed()
    feed.doc["designs"], feed.files = entries, files
    tmp = Path(tempfile.mkdtemp(prefix="designs-test-"))
    try:
        site, cache = tmp / "site", tmp / "cache"
        assemble(site)

        print("-- a build with the fixture feed (GALLERY_FEED_URL names the photos feed)")
        rc, log, outputs = build(site, cache, feed.url + "/community/gallery.json")
        check(rc == 0, "the build exits 0")
        check_published(site, liveries, vinyls, expect, log)
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
        check(sorted(f.name for f in (site / "designs").iterdir()) == sorted(f"{i}.png" for i in rest[:4]),
              "designs/ holds only what is listed")

        print("-- an empty feed")
        feed.doc["designs"] = []
        rc, log, _ = build(site, cache, feed.url)
        check(rc == 0 and published(site)["designs"] == [] and not list((site / "designs").iterdir()), "no designs")
        check(not list(cache.iterdir()), "and an empty cache")
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
        rc, log, _ = build(site, cache, feed.url)
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
            build(site, cache, feed.url)
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
