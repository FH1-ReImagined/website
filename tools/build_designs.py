#!/usr/bin/env python3
"""Copy the paint jobs and vinyl groups players shared from the game's storefront into the
assembled site, with their previews re-encoded here: no file the server sends is published as
it is.

  GALLERY_FEED_URL=URL tools/build_designs.py OUT [--cache DIR] [--max-designs N] [--require-feed]

The designs come from the same Forza Live server as the community photos (tools/build_gallery.py):
GALLERY_FEED_URL is http://host:port, or its /community/gallery.json, and the designs feed is
/community/designs.json beside it. The feed lists only the designs the server screened, newest
first. Each preview the cache does not hold yet is downloaded from the feed's server, checked
against the feed's SHA-256, decoded with Pillow (PNG or JPEG only, with size limits) and
re-encoded as a PNG from its pixels alone, without any metadata: OUT/designs/<id>.png (at most
576 px wide; the game's previews are 288x160 for paint jobs and 256x128 for vinyl groups, and a
transparent background stays transparent). OUT/data/designs.json lists them, newest first, with
only what the pages show (designs.js): never a XUID or the design itself, and no description.
The pages insert the text with textContent, so nothing is escaped here.

The cache (DIR, kept between runs by actions/cache in .github/workflows/pages.yml) holds the
re-encoded previews under the SHA-256 of the server's file, and a note for each file that failed
the checks after its SHA-256 matched. Designs that left the feed are deleted from it. With
GITHUB_OUTPUT set, cache-key=<hash of what the cache holds> is written for the save step.

With no GALLERY_FEED_URL, a feed that cannot be read, or a server that has no designs feed
(HTTP 404), it writes an empty list (the pages then hide their designs sections) and exits 0.
--require-feed (the scheduled runs) exits 1 instead when the server cannot be reached or sends
a broken feed, or a preview cannot be fetched, so the published site stays as it was; a 404 is
not an outage and still gives an empty list. A design that fails a check is left out and
logged; it never fails the build.
"""
import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image

# The download, the text cleaning and the cache bookkeeping are the photos' (it also sets
# Pillow's pixel limit and turns its decompression-bomb warning into an error).
from build_gallery import Refused, Unreachable, clean_text, fetch, is_int, prune_cache, write_atomic

TYPES = ("livery", "vinylgroup")
MAX_DESIGNS = 500                    # the newest; older ones are dropped (and logged)
MAX_FEED_BYTES = 8 << 20
MAX_PREVIEW_BYTES = 1 << 20          # the game's own preview field holds 150 KiB
MIN_SIDE, MAX_SIDE = 32, 1024
MAX_ASPECT = 4.0
PREVIEW_WIDTH = 576                  # twice the game's paint job preview
MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK"}
PIPELINE = "d1"                      # bump when the re-encoding changes: the cache then refills
TEXT_LIMITS = {"gamertag": 32, "title": 80, "car": 80}
MAX_COUNT = 10 ** 9
MAX_TIME = 10 ** 11                  # createdAt is in unix seconds

ID_RE = re.compile(r"[A-Za-z0-9]{6,32}")
CODE_RE = re.compile(r"[A-Za-z0-9]{4,15}")   # it must fit the storefront's gamertag field
SHA_RE = re.compile(r"[0-9a-fA-F]{64}")
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def log(msg):
    print(f"designs: {msg}", flush=True)


def warn(msg):
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    print(f"{prefix}designs: {msg}", flush=True)


def count_of(raw, key):
    v = raw.get(key, 0)
    if not is_int(v) or v > MAX_COUNT:
        raise Refused(f"bad {key}")
    return v


def rating_of(raw):
    """(average rounded to 0.01, count) of a feed entry's rating; (0, 0) when it has none."""
    r = raw.get("rating")
    if r is None:
        return 0, 0
    if not isinstance(r, dict):
        raise Refused("bad rating")
    avg, n = r.get("avg", 0), r.get("count", 0)
    if not is_int(n) or n > MAX_COUNT:
        raise Refused("bad rating count")
    if isinstance(avg, bool) or not isinstance(avg, (int, float)) or not math.isfinite(avg) or not 0 <= avg <= 5:
        raise Refused("bad rating average")
    return (round(float(avg), 2), n) if n else (0, 0)


def entry_of(raw):
    """The fields of a feed entry this build uses, checked. Everything else in the entry (the
    description, the layer count, anything the server adds by mistake) is ignored, so it cannot
    reach the site."""
    if not isinstance(raw, dict):
        raise Refused("not an object")
    did, kind, code = raw.get("id"), raw.get("type"), raw.get("code")
    sha, preview = raw.get("sha256"), raw.get("preview")
    if not isinstance(did, str) or not ID_RE.fullmatch(did):
        raise Refused("bad id")
    if kind not in TYPES:
        raise Refused("bad type")
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        raise Refused("bad share code")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise Refused("bad sha256")
    if not isinstance(preview, str) or not preview:
        raise Refused("no preview")
    if not is_int(raw.get("createdAt")) or raw["createdAt"] >= MAX_TIME:
        raise Refused("bad createdAt (unix seconds)")
    gamertag = clean_text(raw.get("gamertag"), TEXT_LIMITS["gamertag"])
    if not gamertag:
        raise Refused("no gamertag")
    rating, ratings = rating_of(raw)
    e = {"id": did, "type": kind, "code": code, "title": clean_text(raw.get("title"), TEXT_LIMITS["title"]) or "",
         "gamertag": gamertag, "downloads": count_of(raw, "downloads"), "rating": rating, "ratings": ratings,
         "createdAt": raw["createdAt"], "sha256": sha.lower(), "preview": preview}
    car = raw.get("car")
    if kind == "livery" and isinstance(car, dict):   # a paint job fits one car; a vinyl group any
        name = clean_text(car.get("name"), TEXT_LIMITS["car"])
        if name:
            e["car"] = name
    return e


def designs_url(url):
    """The designs feed of the server GALLERY_FEED_URL names."""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise Refused(f"GALLERY_FEED_URL must be an http(s) URL, not {url!r}")
    if u.path.endswith(".json"):
        return urljoin(url, "designs.json")
    return url.split("?", 1)[0].rstrip("/") + "/community/designs.json"


def read_feed(url):
    try:
        doc = json.loads(fetch(url, MAX_FEED_BYTES, tries=3))
    except ValueError as e:
        raise Refused(f"not JSON ({e})") from None
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("designs"), list):
        raise Refused("not a version 1 feed")
    return doc["designs"]


def reencode(data):
    """(PNG bytes, (w, h)) from a PNG or JPEG preview, made from its pixels alone: no chunk or
    segment of the file (text, EXIF, XMP, ICC, time) survives. RGBA when any pixel is see-through,
    else RGB."""
    if not (data.startswith(PNG_SIG) or data.startswith(b"\xff\xd8\xff")):
        raise Refused("not a PNG or JPEG file")
    try:
        with Image.open(io.BytesIO(data), formats=("PNG", "JPEG")) as im:
            w, h = im.size
            if im.format not in ("PNG", "JPEG"):
                raise Refused(f"format {im.format}")
            if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
                raise Refused(f"size {w}x{h}")
            if max(w / h, h / w) > MAX_ASPECT:
                raise Refused(f"aspect ratio {w}x{h}")
            if getattr(im, "n_frames", 1) != 1:
                raise Refused("animated")
            if im.mode not in MODES:
                raise Refused(f"mode {im.mode}")
            im.load()
            rgba = im.convert("RGBA")
    except Refused:
        raise
    except Exception as e:  # anything Pillow raises on a broken or hostile file
        raise Refused(f"cannot decode ({type(e).__name__}: {e})") from None
    pixels = Image.frombytes("RGBA", rgba.size, rgba.tobytes())  # a new image: none of im.info
    if pixels.getchannel("A").getextrema()[0] == 255:
        pixels = pixels.convert("RGB")
    if pixels.width > PREVIEW_WIDTH:
        pixels = pixels.resize((PREVIEW_WIDTH, max(1, round(pixels.height * PREVIEW_WIDTH / pixels.width))),
                               Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    pixels.save(buf, "PNG", optimize=True)
    return buf.getvalue(), pixels.size


def png_size(path):
    """The size of a PNG this script wrote earlier (a cached copy), or ValueError."""
    with Image.open(path, formats=("PNG",)) as im:
        w, h = im.size
        im.load()
    if not (0 < w <= PREVIEW_WIDTH and 0 < h <= MAX_SIDE):
        raise ValueError(f"size {w}x{h}")
    return w, h


def cache_path(cache, sha):
    return cache / f"{sha}-{PIPELINE}.png"


def refusal_path(cache, sha):
    return cache / f"{sha}-{PIPELINE}.refused"


def refused_before(cache, sha):
    """Why this file (its SHA-256 checked) failed the checks in an earlier build, or None."""
    if cache is None or not refusal_path(cache, sha).is_file():
        return None
    return refusal_path(cache, sha).read_text(encoding="utf-8", errors="replace")[:200] or "refused"


def from_cache(cache, sha):
    """(PNG bytes, size) from the cache, or None."""
    if cache is None or not cache_path(cache, sha).is_file():
        return None
    p = cache_path(cache, sha)
    try:
        return p.read_bytes(), png_size(p)
    except Exception as e:
        warn(f"dropping a damaged cache entry {sha[:12]} ({e})")
        p.unlink(missing_ok=True)
        return None


def write_designs(out, designs):
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "generated": int(time.time()), "designs": designs}
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    (data / "designs.json").write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="the assembled site (tools/assemble.sh OUT)")
    ap.add_argument("--cache", type=Path, help="re-encoded previews kept between builds")
    ap.add_argument("--max-designs", type=int, default=MAX_DESIGNS)
    ap.add_argument("--require-feed", action="store_true",
                    help="exit 1 when GALLERY_FEED_URL is set but the feed or a preview cannot be fetched")
    args = ap.parse_args()

    out_dir = args.out / "designs"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)

    url = os.environ.get("GALLERY_FEED_URL", "").strip()
    if not url:
        log("GALLERY_FEED_URL is not set: no designs")
        write_designs(args.out, [])
        return 0
    try:
        url = designs_url(url)
        raw = read_feed(url)
    except (Refused, Unreachable) as e:
        write_designs(args.out, [])
        if getattr(e, "status", None) == 404:  # a server without the designs feed: not an outage
            warn(f"the server has no designs feed ({url}, HTTP 404): no designs")
            return 0
        if args.require_feed:
            print(f"::error::designs: the feed {url} could not be read ({e})", flush=True)
            return 1
        warn(f"the feed {url} could not be read ({e}): no designs")
        return 0
    log(f"feed {url}: {len(raw)} designs")

    origin = urlsplit(url)[:2]
    entries, ids, codes, refused = [], set(), set(), 0
    for i, item in enumerate(raw):
        try:
            e = entry_of(item)
            if e["id"] in ids:
                raise Refused("duplicate id")
            if e["code"].upper() in codes:
                raise Refused("duplicate share code")
        except Refused as err:
            did = item.get("id") if isinstance(item, dict) else None
            warn(f"refused feed entry {i} ({did if isinstance(did, str) and ID_RE.fullmatch(did) else '?'}): {err}")
            refused += 1
            continue
        ids.add(e["id"])
        codes.add(e["code"].upper())
        entries.append(e)
    entries.sort(key=lambda e: e["createdAt"], reverse=True)  # newest first
    dropped = entries[args.max_designs:]
    entries = entries[:args.max_designs]
    if dropped:
        names = ", ".join(e["id"] for e in dropped[:20]) + (", ..." if len(dropped) > 20 else "")
        warn(f"{len(dropped)} older designs dropped over the cap of {args.max_designs}: {names}")

    designs, keep, downloaded, cached, unreachable = [], set(), 0, 0, 0
    for e in entries:
        did, sha = e["id"], e["sha256"]
        try:
            hit = from_cache(args.cache, sha)
            if hit:
                cached += 1
            elif known := refused_before(args.cache, sha):
                keep.add(refusal_path(args.cache, sha).name)
                raise Refused(f"{known} (in an earlier build)")
            else:
                src = urljoin(url, e["preview"])
                if urlsplit(src)[:2] != origin:
                    raise Refused("the preview is not on the feed's server")
                data = fetch(src, MAX_PREVIEW_BYTES, tries=2)
                if hashlib.sha256(data).hexdigest() != sha:
                    raise Refused("SHA-256 does not match the feed")
                try:
                    hit = reencode(data)
                except Refused as err:  # these bytes will always fail: remember them
                    if args.cache:
                        write_atomic(refusal_path(args.cache, sha), str(err).encode())
                        keep.add(refusal_path(args.cache, sha).name)
                    raise
                if args.cache:
                    write_atomic(cache_path(args.cache, sha), hit[0])
                downloaded += 1
        except Refused as err:
            warn(f"refused {did}: {err}")
            refused += 1
            continue
        except Unreachable as err:
            warn(f"could not fetch {did}: {err}")
            unreachable += 1
            continue
        png, (w, h) = hit
        (out_dir / f"{did}.png").write_bytes(png)
        if args.cache:
            keep.add(cache_path(args.cache, sha).name)
        d = {"id": did, "type": e["type"], "code": e["code"], "title": e["title"], "gamertag": e["gamertag"]}
        if "car" in e:
            d["car"] = e["car"]
        d.update(downloads=e["downloads"], rating=e["rating"], ratings=e["ratings"], createdAt=e["createdAt"],
                 src=f"designs/{did}.png", w=w, h=h)
        designs.append(d)

    write_designs(args.out, designs)
    log(f"{len(designs)} designs published ({downloaded} downloaded, {cached} from the cache); "
        f"{refused} refused, {unreachable} not fetched, {len(dropped)} over the cap")
    if args.cache:
        key = prune_cache(args.cache, keep)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(f"cache-key={key}\n")
    if unreachable and args.require_feed:
        print(f"::error::designs: {unreachable} previews could not be fetched", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
