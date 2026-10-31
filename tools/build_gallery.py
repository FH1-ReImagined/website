#!/usr/bin/env python3
"""Copy the community photos a maintainer approved on the Forza Live server into the assembled
site, re-encoded here: no file the server sends is published as it is.

  GALLERY_FEED_URL=URL tools/build_gallery.py OUT [--cache DIR] [--max-photos N] [--require-feed]

GALLERY_FEED_URL is the server's public feed, http://host:port/community/gallery.json (or just
http://host:port). Each photo the cache does not hold yet is downloaded from the feed's server,
checked against the feed's SHA-256, decoded with Pillow (JPEG or PNG only, with a pixel limit)
and re-encoded without any metadata: OUT/photos/<id>.jpg (at most 1920 px wide, never upscaled)
and OUT/photos/<id>-thumb.jpg (at most 480 px wide). OUT/data/gallery.json lists them, newest
approved first, with only what the pages show (photos.js). The pages insert the text with
textContent, so nothing is escaped here.

The cache (DIR, kept between runs by actions/cache in .github/workflows/pages.yml) holds the
re-encoded copies under the SHA-256 of the server's file, so the server sends each photo once,
and a note for each file that failed the checks after its SHA-256 matched, so it is not fetched
again. Photos that left the feed are deleted from it: a withdrawn photo is gone from the next
build.
With GITHUB_OUTPUT set, cache-key=<hash of what the cache holds> is written for the save step.

With no GALLERY_FEED_URL, or a feed that cannot be read, it writes an empty gallery (the front
page then hides its photos section) and exits 0 so the site still deploys, as the leaderboards
copy does on a push. --require-feed (the scheduled runs) exits 1 instead when the URL is set
but the feed, or a photo, cannot be fetched, so the published site stays as it was.
A photo that fails a check is left out and logged; it never fails the build.
"""
import argparse
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image

FULL_WIDTH, FULL_QUALITY = 1920, 85
THUMB_WIDTH, THUMB_QUALITY = 480, 80
MAX_PHOTOS = 500                     # the newest; older ones are dropped (and logged)
MAX_FEED_BYTES = 8 << 20
MAX_IMAGE_BYTES = 16 << 20           # the server takes uploads of up to 6 MiB
MIN_SIDE, MAX_SIDE = 180, 7680
MAX_ASPECT = 3.0
MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK"}
PIPELINE = "v1"                      # bump when the re-encoding changes: the cache then refills
TIMEOUT = 30
USER_AGENT = "FH1-ReImagined-website-build (+https://github.com/FH1-ReImagined/website)"
TEXT_LIMITS = {"gamertag": 32, "title": 80, "car": 80, "location": 80}

# Pillow refuses images over its pixel limit only with a warning (an error at twice the limit).
Image.MAX_IMAGE_PIXELS = 7680 * 4320
warnings.simplefilter("error", Image.DecompressionBombWarning)

ID_RE = re.compile(r"[A-Za-z2-7]{12}")
SHA_RE = re.compile(r"[0-9a-fA-F]{64}")


class Refused(Exception):
    """The photo failed a check: leave it out."""
    status = None  # the HTTP status, when the server answered 4xx


def _refused_status(code):
    err = Refused(f"HTTP {code}")
    err.status = code
    return err


class Unreachable(Exception):
    """The server could not be reached (or failed): a scheduled run keeps the published site."""


def log(msg):
    print(f"gallery: {msg}", flush=True)


def warn(msg):
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    print(f"{prefix}gallery: {msg}", flush=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl} refused", headers, fp)


_opener = urllib.request.build_opener(_NoRedirect)


def fetch(url, limit, tries):
    """The body of url, at most limit bytes. Raises Unreachable for network trouble and 5xx
    replies, Refused for the rest (4xx, redirects, too large)."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with _opener.open(req, timeout=TIMEOUT) as r:
                n = r.headers.get("Content-Length", "")
                if n.isdigit() and int(n) > limit:
                    raise Refused(f"larger than {limit} bytes")
                data = r.read(limit + 1)
            if len(data) > limit:
                raise Refused(f"larger than {limit} bytes")
            return data
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise _refused_status(e.code) from None
            err = Unreachable(f"HTTP {e.code}")
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            err = Unreachable(str(getattr(e, "reason", e)) or type(e).__name__)
        if attempt + 1 < tries:
            time.sleep(2 * (attempt + 1))
    raise err


def clean_text(value, limit):
    """Player text as written, without control or format characters (line breaks, bidi
    overrides), whitespace collapsed, at most limit characters. Not escaped: the pages use
    textContent."""
    if not isinstance(value, str):
        return None
    kept = []
    for c in value:
        if c.isspace():
            kept.append(" ")
        elif unicodedata.category(c) not in ("Cc", "Cf", "Cs") or c == "\u200d":  # keep ZWJ (emoji)
            kept.append(c)
    return " ".join("".join(kept).split())[:limit]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def entry_of(raw):
    """The fields of a feed entry this build uses, checked. Everything else in the entry is
    ignored, so nothing the server adds by mistake can reach the site."""
    if not isinstance(raw, dict):
        raise Refused("not an object")
    pid, sha, image = raw.get("id"), raw.get("sha256"), raw.get("image")
    if not isinstance(pid, str) or not ID_RE.fullmatch(pid):
        raise Refused("bad id")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise Refused("bad sha256")
    if not isinstance(image, str) or not image:
        raise Refused("no image")
    if not is_int(raw.get("approvedAt")):
        raise Refused("bad approvedAt")
    e = {"id": pid, "sha256": sha.lower(), "image": image, "approvedAt": raw["approvedAt"]}
    gamertag = clean_text(raw.get("gamertag"), TEXT_LIMITS["gamertag"])
    if not gamertag:
        raise Refused("no gamertag")
    e["gamertag"] = gamertag
    e["title"] = clean_text(raw.get("title"), TEXT_LIMITS["title"]) or ""
    for key in ("car", "location"):
        text = clean_text(raw.get(key), TEXT_LIMITS[key])
        if text:
            e[key] = text
    return e


def feed_url(url):
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise Refused(f"GALLERY_FEED_URL must be an http(s) URL, not {url!r}")
    return url if u.path.endswith(".json") else url.rstrip("/") + "/community/gallery.json"


def read_feed(url):
    try:
        doc = json.loads(fetch(url, MAX_FEED_BYTES, tries=3))
    except ValueError as e:
        raise Refused(f"not JSON ({e})") from None
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("photos"), list):
        raise Refused("not a version 1 feed")
    return doc["photos"]


def reencode(data):
    """(full JPEG, thumbnail JPEG, full size, thumbnail size) from a JPEG or PNG file, made from
    its pixels alone: no metadata of the file (EXIF, XMP, ICC, comments, text chunks) survives."""
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
        raise Refused("not a JPEG or PNG file")
    try:
        with Image.open(io.BytesIO(data), formats=("JPEG", "PNG")) as im:
            w, h = im.size
            if im.format not in ("JPEG", "PNG"):
                raise Refused(f"format {im.format}")
            if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE) or w * h > Image.MAX_IMAGE_PIXELS:
                raise Refused(f"size {w}x{h}")
            if max(w / h, h / w) > MAX_ASPECT:
                raise Refused(f"aspect ratio {w}x{h}")
            if getattr(im, "n_frames", 1) != 1:
                raise Refused("animated")
            if im.mode not in MODES:
                raise Refused(f"mode {im.mode}")
            im.load()
            rgb = im.convert("RGB")
    except Refused:
        raise
    except Exception as e:  # anything Pillow raises on a broken or hostile file
        raise Refused(f"cannot decode ({type(e).__name__}: {e})") from None
    pixels = Image.frombytes("RGB", rgb.size, rgb.tobytes())  # a new image: none of im.info
    full, thumb = scaled(pixels, FULL_WIDTH), scaled(pixels, THUMB_WIDTH)
    return encode(full, FULL_QUALITY), encode(thumb, THUMB_QUALITY), full.size, thumb.size


def scaled(im, width):
    if im.width <= width:
        return im
    return im.resize((width, max(1, round(im.height * width / im.width))), Image.Resampling.LANCZOS)


def encode(im, quality):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def jpeg_size(path, max_width):
    """The size of a JPEG this script wrote earlier (a cached copy), or ValueError."""
    with Image.open(path, formats=("JPEG",)) as im:
        w, h = im.size
    if not (0 < w <= max_width and 0 < h <= MAX_SIDE):
        raise ValueError(f"size {w}x{h}")
    return w, h


def cache_paths(cache, sha):
    return cache / f"{sha}-{PIPELINE}.jpg", cache / f"{sha}-{PIPELINE}-thumb.jpg"


def refusal_path(cache, sha):
    return cache / f"{sha}-{PIPELINE}.refused"


def refused_before(cache, sha):
    """Why this file (its SHA-256 checked) failed the checks in an earlier build, or None."""
    if cache is None or not refusal_path(cache, sha).is_file():
        return None
    return refusal_path(cache, sha).read_text(encoding="utf-8", errors="replace")[:200] or "refused"


def from_cache(cache, sha):
    """(full bytes, thumb bytes, full size, thumb size) from the cache, or None."""
    if cache is None:
        return None
    full_p, thumb_p = cache_paths(cache, sha)
    if not (full_p.is_file() and thumb_p.is_file()):
        return None
    try:
        return (full_p.read_bytes(), thumb_p.read_bytes(),
                jpeg_size(full_p, FULL_WIDTH), jpeg_size(thumb_p, THUMB_WIDTH))
    except Exception as e:
        warn(f"dropping a damaged cache entry {sha[:12]} ({e})")
        full_p.unlink(missing_ok=True)
        thumb_p.unlink(missing_ok=True)
        return None


def write_atomic(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def to_cache(cache, sha, full, thumb):
    if cache is None:
        return
    full_p, thumb_p = cache_paths(cache, sha)
    write_atomic(full_p, full)
    write_atomic(thumb_p, thumb)


def prune_cache(cache, keep):
    """Delete every file of the cache but those named in keep; return the cache key: a hash of
    the names left."""
    left = []
    for f in sorted(cache.iterdir()):
        if f.name in keep and f.is_file():
            left.append(f.name)
        elif f.is_file() or f.is_symlink():
            f.unlink()
    return hashlib.sha256("\n".join(left).encode()).hexdigest()[:24]


def write_gallery(out, photos):
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "generated": int(time.time()), "photos": photos}
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    (data / "gallery.json").write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="the assembled site (tools/assemble.sh OUT)")
    ap.add_argument("--cache", type=Path, help="re-encoded copies kept between builds")
    ap.add_argument("--max-photos", type=int, default=MAX_PHOTOS)
    ap.add_argument("--require-feed", action="store_true",
                    help="exit 1 when GALLERY_FEED_URL is set but the feed or a photo cannot be fetched")
    args = ap.parse_args()

    photos_dir = args.out / "photos"
    shutil.rmtree(photos_dir, ignore_errors=True)
    photos_dir.mkdir(parents=True)
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)

    url = os.environ.get("GALLERY_FEED_URL", "").strip()
    if not url:
        log("GALLERY_FEED_URL is not set: an empty gallery")
        write_gallery(args.out, [])
        return 0
    try:
        url = feed_url(url)
        raw = read_feed(url)
    except (Refused, Unreachable) as e:
        write_gallery(args.out, [])
        if args.require_feed:
            print(f"::error::gallery: the feed {url} could not be read ({e})", flush=True)
            return 1
        warn(f"the feed {url} could not be read ({e}): an empty gallery")
        return 0
    log(f"feed {url}: {len(raw)} photos")

    origin = urlsplit(url)[:2]
    entries, seen, refused = [], set(), 0
    for i, item in enumerate(raw):
        try:
            e = entry_of(item)
            if e["id"] in seen:
                raise Refused("duplicate id")
        except Refused as err:
            pid = item.get("id") if isinstance(item, dict) else None
            warn(f"refused feed entry {i} ({pid if isinstance(pid, str) and ID_RE.fullmatch(pid) else '?'}): {err}")
            refused += 1
            continue
        seen.add(e["id"])
        entries.append(e)
    entries.sort(key=lambda e: e["approvedAt"], reverse=True)  # newest approved first
    dropped = entries[args.max_photos:]
    entries = entries[:args.max_photos]
    if dropped:
        ids = ", ".join(e["id"] for e in dropped[:20]) + (", ..." if len(dropped) > 20 else "")
        warn(f"{len(dropped)} older photos dropped over the cap of {args.max_photos}: {ids}")

    photos, keep, downloaded, cached, unreachable = [], set(), 0, 0, 0
    for e in entries:
        pid, sha = e["id"], e["sha256"]
        try:
            hit = from_cache(args.cache, sha)
            if hit:
                cached += 1
            elif known := refused_before(args.cache, sha):
                keep.add(refusal_path(args.cache, sha).name)
                raise Refused(f"{known} (in an earlier build)")
            else:
                src = urljoin(url, e["image"])
                if urlsplit(src)[:2] != origin:
                    raise Refused("the image is not on the feed's server")
                data = fetch(src, MAX_IMAGE_BYTES, tries=2)
                if hashlib.sha256(data).hexdigest() != sha:
                    raise Refused("SHA-256 does not match the feed")
                try:
                    hit = reencode(data)
                except Refused as err:  # these bytes will always fail: remember them
                    if args.cache:
                        write_atomic(refusal_path(args.cache, sha), str(err).encode())
                        keep.add(refusal_path(args.cache, sha).name)
                    raise
                to_cache(args.cache, sha, hit[0], hit[1])
                downloaded += 1
        except Refused as err:
            warn(f"refused {pid}: {err}")
            refused += 1
            continue
        except Unreachable as err:
            warn(f"could not fetch {pid}: {err}")
            unreachable += 1
            continue
        full, thumb, (w, h), (tw, th) = hit
        (photos_dir / f"{pid}.jpg").write_bytes(full)
        (photos_dir / f"{pid}-thumb.jpg").write_bytes(thumb)
        if args.cache:
            keep.update(f.name for f in cache_paths(args.cache, sha))
        p = {"id": pid, "title": e["title"], "gamertag": e["gamertag"]}
        for key in ("car", "location"):
            if key in e:
                p[key] = e[key]
        p.update(src=f"photos/{pid}.jpg", w=w, h=h, thumb=f"photos/{pid}-thumb.jpg", tw=tw, th=th)
        photos.append(p)

    write_gallery(args.out, photos)
    log(f"{len(photos)} photos published ({downloaded} downloaded, {cached} from the cache); "
        f"{refused} refused, {unreachable} not fetched, {len(dropped)} over the cap")
    if args.cache:
        key = prune_cache(args.cache, keep)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(f"cache-key={key}\n")
    if unreachable and args.require_feed:
        print(f"::error::gallery: {unreachable} photos could not be fetched", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
