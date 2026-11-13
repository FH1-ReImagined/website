#!/usr/bin/env python3
"""Copy the community videos the Forza Live server published (converted from the game's movie
uploads and passed by the screening or a maintainer) into the assembled site.

  GALLERY_FEED_URL=URL [LIKES_URL=URL] tools/build_videos.py OUT [--cache DIR] [--max-videos N]
      [--max-mb MB] [--require-feed]

The videos come from the same Forza Live server as the community photos (tools/build_gallery.py):
GALLERY_FEED_URL names it (http://host:port, or its gallery feed), and the videos feed is its
/community/videos.json. Each video the cache does not hold yet is downloaded from the feed's
server with its poster, both checked against the feed's SHA-256. The MP4 is checked box by box
(an ISO brand, one moov with an H.264 track, mdat and padding only, filling the file exactly; the
server's converter wrote it without metadata) and kept as it is: OUT/videos/<id>.mp4. The poster
is decoded with Pillow and re-encoded without any metadata, at most POSTER_WIDTH wide:
OUT/videos/<id>.jpg. OUT/data/videos.json lists them, newest approved first, with only what
videos.js shows. The pages insert the text with textContent, so nothing is escaped here.

Each video keeps its owner's player id ("owner", as the photos do) and gets its like total
("likes") from LIKES_URL (?kind=video), as tools/build_gallery.py says. At most --max-videos
videos and --max-mb MB of them are published (the newest; the rest are logged): a site served
from GitHub Pages has 1 GB in all.

The cache (DIR) holds the MP4s and the re-encoded posters under the SHA-256 of the server's files,
so the server sends each video once, and a note for each file that failed the checks after its
SHA-256 matched. Videos that left the feed are deleted from it. A server without the videos feed
(HTTP 404) gives an empty list, like no GALLERY_FEED_URL; an unreachable feed too, unless
--require-feed (the scheduled builds), which exits 1 so the published site stays as it was.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The download, the text cleaning and the cache bookkeeping are the photos' (it also sets
# Pillow's pixel limit and turns its decompression-bomb warning into an error).
from build_gallery import (Refused, Unreachable, clean_text, fetch, is_int, owner_of, prune_cache, read_likes,  # noqa: E402
                           write_atomic)
from check_site import mp4_boxes, video_problems  # noqa: E402  (the site's own checks)

MAX_VIDEOS = 100                     # the newest; older ones are dropped (and logged)
MAX_MB = 700                         # the MP4s together (GitHub Pages: 1 GB for the whole site)
MAX_FEED_BYTES = 8 << 20
MAX_VIDEO_BYTES = 32 << 20           # the server's own cap on a converted video
MAX_POSTER_BYTES = 4 << 20
MIN_SIDE, MAX_SIDE = 64, 1920
MAX_ASPECT = 3.0
MAX_SECONDS = 600
POSTER_WIDTH, POSTER_QUALITY = 960, 84
PIPELINE = "v1"                      # bump when the checks or the poster's re-encoding change
TEXT_LIMITS = {"gamertag": 32, "title": 80}
MAX_TIME = 10 ** 11

ID_RE = re.compile(r"[A-Z2-7]{12}")
SHA_RE = re.compile(r"[0-9a-fA-F]{64}")


def log(msg):
    print(f"videos: {msg}", flush=True)


def warn(msg):
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    print(f"{prefix}videos: {msg}", flush=True)


def check_mp4(data):
    """An MP4 as the server's converter writes it (check_site.mp4_boxes, the same check as the
    server's own); Refused for anything else."""
    try:
        mp4_boxes(data)
    except ValueError as e:
        raise Refused(str(e)) from None


def entry_of(raw):
    """The fields of a feed entry this build uses, checked; everything else is ignored."""
    if not isinstance(raw, dict):
        raise Refused("not an object")
    vid = raw.get("id")
    if not isinstance(vid, str) or not ID_RE.fullmatch(vid):
        raise Refused("bad id")
    if raw.get("video") != f"/community/video/{vid}.mp4" or raw.get("poster") != f"/community/video/{vid}.jpg":
        raise Refused("bad video or poster path")
    for key in ("sha256", "posterSha256"):
        if not isinstance(raw.get(key), str) or not SHA_RE.fullmatch(raw[key]):
            raise Refused(f"bad {key}")
    if not (is_int(raw.get("approvedAt")) and raw["approvedAt"] <= MAX_TIME):
        raise Refused("bad approvedAt")
    seconds = raw.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= MAX_SECONDS:
        raise Refused("bad seconds")
    size = raw.get("size")
    if not (is_int(size) and 0 < size <= MAX_VIDEO_BYTES):
        raise Refused("bad size")
    gamertag = clean_text(raw.get("gamertag"), TEXT_LIMITS["gamertag"])
    if not gamertag:
        raise Refused("no gamertag")
    e = {"id": vid, "sha256": raw["sha256"].lower(), "posterSha256": raw["posterSha256"].lower(),
         "video": raw["video"], "poster": raw["poster"], "approvedAt": raw["approvedAt"],
         "seconds": round(float(seconds), 1), "size": size, "gamertag": gamertag,
         "title": clean_text(raw.get("title"), TEXT_LIMITS["title"]) or ""}
    owner = owner_of(raw)
    if owner:
        e["owner"] = owner
    return e


def videos_url(url):
    """The videos feed of the server GALLERY_FEED_URL names."""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise Refused(f"GALLERY_FEED_URL must be an http(s) URL, not {url!r}")
    if u.path.endswith(".json"):
        return urljoin(url, "videos.json")
    return url.split("?", 1)[0].rstrip("/") + "/community/videos.json"


def read_feed(url):
    try:
        doc = json.loads(fetch(url, MAX_FEED_BYTES, tries=3))
    except ValueError as e:
        raise Refused(f"not JSON ({e})") from None
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("videos"), list):
        raise Refused("not a version 1 feed")
    return doc["videos"]


def reencode_poster(data):
    """(JPEG, (w, h)) of a poster, made from its pixels alone, at most POSTER_WIDTH wide."""
    if not data.startswith(b"\xff\xd8\xff"):
        raise Refused("the poster is not a JPEG")
    try:
        with Image.open(io.BytesIO(data), formats=("JPEG",)) as im:
            w, h = im.size
            if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
                raise Refused(f"poster size {w}x{h}")
            if max(w / h, h / w) > MAX_ASPECT:
                raise Refused(f"poster aspect ratio {w}x{h}")
            if im.mode not in ("L", "RGB", "CMYK"):
                raise Refused(f"poster mode {im.mode}")
            im.load()
            rgb = im.convert("RGB")
    except Refused:
        raise
    except Exception as e:  # anything Pillow raises on a broken or hostile file
        raise Refused(f"cannot decode the poster ({type(e).__name__}: {e})") from None
    pixels = Image.frombytes("RGB", rgb.size, rgb.tobytes())  # a new image: none of im.info
    if pixels.width > POSTER_WIDTH:
        pixels = pixels.resize((POSTER_WIDTH, max(1, round(pixels.height * POSTER_WIDTH / pixels.width))),
                               Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    pixels.save(buf, "JPEG", quality=POSTER_QUALITY, optimize=True, progressive=True)
    return buf.getvalue(), pixels.size


def cache_paths(cache, sha, poster_sha):
    return cache / f"{sha}-{PIPELINE}.mp4", cache / f"{poster_sha}-{PIPELINE}-poster.jpg"


def refusal_path(cache, sha):
    return cache / f"{sha}-{PIPELINE}.refused"


def refused_before(cache, sha):
    if cache is None or not refusal_path(cache, sha).is_file():
        return None
    return refusal_path(cache, sha).read_text(encoding="utf-8", errors="replace")[:200] or "refused"


def from_cache(cache, e):
    """(MP4 bytes, poster bytes, poster size) from the cache, or None."""
    if cache is None:
        return None
    mp4_p, poster_p = cache_paths(cache, e["sha256"], e["posterSha256"])
    if not (mp4_p.is_file() and poster_p.is_file()):
        return None
    try:
        mp4 = mp4_p.read_bytes()
        if hashlib.sha256(mp4).hexdigest() != e["sha256"]:
            raise ValueError("the cached video's SHA-256")
        with Image.open(poster_p, formats=("JPEG",)) as im:
            size = im.size
        if not (0 < size[0] <= POSTER_WIDTH and 0 < size[1] <= MAX_SIDE):
            raise ValueError(f"poster size {size}")
        return mp4, poster_p.read_bytes(), size
    except Exception as err:
        warn(f"dropping a damaged cache entry {e['sha256'][:12]} ({err})")
        mp4_p.unlink(missing_ok=True)
        poster_p.unlink(missing_ok=True)
        return None


def write_videos(out, videos):
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "generated": int(time.time()), "videos": videos}
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    (data / "videos.json").write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="the assembled site (tools/assemble.sh OUT)")
    ap.add_argument("--cache", type=Path, help="videos and re-encoded posters kept between builds")
    ap.add_argument("--max-videos", type=int, default=MAX_VIDEOS)
    ap.add_argument("--max-mb", type=int, default=MAX_MB, help="the published MP4s together, in MB")
    ap.add_argument("--require-feed", action="store_true",
                    help="exit 1 when GALLERY_FEED_URL is set but the feed or a video cannot be fetched")
    args = ap.parse_args()

    out_dir = args.out / "videos"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)

    url = os.environ.get("GALLERY_FEED_URL", "").strip()
    if not url:
        log("GALLERY_FEED_URL is not set: no videos")
        write_videos(args.out, [])
        return 0
    try:
        url = videos_url(url)
        raw = read_feed(url)
    except (Refused, Unreachable) as e:
        write_videos(args.out, [])
        if getattr(e, "status", None) == 404:  # a server without community videos: not an outage
            warn(f"the server has no videos feed ({url}, HTTP 404): no videos")
            return 0
        if args.require_feed:
            print(f"::error::videos: the feed {url} could not be read ({e})", flush=True)
            return 1
        warn(f"the feed {url} could not be read ({e}): no videos")
        return 0
    log(f"feed {url}: {len(raw)} videos")
    likes = read_likes("video", ID_RE, warn)

    origin = urlsplit(url)[:2]
    entries, ids, refused = [], set(), 0
    for i, item in enumerate(raw):
        try:
            e = entry_of(item)
            if e["id"] in ids:
                raise Refused("duplicate id")
        except Refused as err:
            vid = item.get("id") if isinstance(item, dict) else None
            warn(f"refused feed entry {i} ({vid if isinstance(vid, str) and ID_RE.fullmatch(vid) else '?'}): {err}")
            refused += 1
            continue
        ids.add(e["id"])
        entries.append(e)
    entries.sort(key=lambda e: e["approvedAt"], reverse=True)  # newest approved first
    kept, budget, dropped = [], args.max_mb << 20, []
    for e in entries:
        if len(kept) < args.max_videos and e["size"] <= budget:
            kept.append(e)
            budget -= e["size"]
        else:
            dropped.append(e)
    if dropped:
        names = ", ".join(e["id"] for e in dropped[:20]) + (", ..." if len(dropped) > 20 else "")
        warn(f"{len(dropped)} older videos dropped over the cap of {args.max_videos} videos or "
             f"{args.max_mb} MB: {names}")

    videos, keep, downloaded, cached, unreachable = [], set(), 0, 0, 0
    for e in kept:
        vid = e["id"]
        try:
            hit = from_cache(args.cache, e)
            if hit:
                cached += 1
            elif known := refused_before(args.cache, e["sha256"]):
                keep.add(refusal_path(args.cache, e["sha256"]).name)
                raise Refused(f"{known} (in an earlier build)")
            else:
                got = {}
                for key, sha_key, limit in (("video", "sha256", MAX_VIDEO_BYTES), ("poster", "posterSha256", MAX_POSTER_BYTES)):
                    src = urljoin(url, e[key])
                    if urlsplit(src)[:2] != origin:
                        raise Refused(f"the {key} is not on the feed's server")
                    data = fetch(src, limit, tries=2)
                    if hashlib.sha256(data).hexdigest() != e[sha_key]:
                        raise Refused(f"the {key}'s SHA-256 does not match the feed")
                    got[key] = data
                try:
                    if len(got["video"]) != e["size"]:
                        raise Refused("the video's size does not match the feed")
                    check_mp4(got["video"])
                    poster, size = reencode_poster(got["poster"])
                except Refused as err:  # these bytes will always fail: remember them
                    if args.cache:
                        write_atomic(refusal_path(args.cache, e["sha256"]), str(err).encode())
                        keep.add(refusal_path(args.cache, e["sha256"]).name)
                    raise
                hit = (got["video"], poster, size)
                if args.cache:
                    mp4_p, poster_p = cache_paths(args.cache, e["sha256"], e["posterSha256"])
                    write_atomic(mp4_p, hit[0])
                    write_atomic(poster_p, hit[1])
                downloaded += 1
        except Refused as err:
            warn(f"refused {vid}: {err}")
            refused += 1
            continue
        except Unreachable as err:
            warn(f"could not fetch {vid}: {err}")
            unreachable += 1
            continue
        mp4, poster, (w, h) = hit
        (out_dir / f"{vid}.mp4").write_bytes(mp4)
        (out_dir / f"{vid}.jpg").write_bytes(poster)
        if args.cache:
            keep.update(p.name for p in cache_paths(args.cache, e["sha256"], e["posterSha256"]))
        v = {"id": vid, "title": e["title"], "gamertag": e["gamertag"]}
        if "owner" in e:
            v["owner"] = e["owner"]
        if likes is not None:
            v["likes"] = likes.get(vid, 0)
        v.update(src=f"videos/{vid}.mp4", poster=f"videos/{vid}.jpg", w=w, h=h, seconds=e["seconds"],
                 size=len(mp4), date=e["approvedAt"])
        problems = video_problems(args.out, vid, v)
        if problems:  # what tools/check_site.py would refuse never reaches the site (or stops it)
            warn(f"refused {vid}: {'; '.join(problems)[:300]}")
            (out_dir / f"{vid}.mp4").unlink(missing_ok=True)
            (out_dir / f"{vid}.jpg").unlink(missing_ok=True)
            refused += 1
            continue
        videos.append(v)

    write_videos(args.out, videos)
    log(f"{len(videos)} videos published ({downloaded} downloaded, {cached} from the cache); "
        f"{refused} refused, {unreachable} not fetched, {len(dropped)} over the cap")
    if args.cache:
        key = prune_cache(args.cache, keep)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(f"cache-key={key}\n")
    if unreachable and args.require_feed:
        print(f"::error::videos: {unreachable} videos could not be fetched", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
