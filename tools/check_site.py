#!/usr/bin/env python3
"""Check an assembled site (tools/assemble.sh OUT): every local href/src of every page names a
file that is there, and the JSON data files parse. When the community photos were built into it
(tools/build_gallery.py), also data/gallery.json: only the fields the pages show (never a "xuid";
an "owner" only as the server's 12-hex-digit player id, and "likes" only as a count), every listed
photo there and no unlisted one, sizes within the limits, and JPEGs
without metadata (no APP1-APP15 or COM segment, nothing after the end). When the community
designs were (tools/build_designs.py), data/designs.json the same way: only the fields the pages
show, every listed preview there and no unlisted one, and PNGs of pixels alone (IHDR, PLTE,
tRNS, IDAT and IEND chunks, nothing after the end). When the mods and texture packs hub was
(tools/build_hub.py), data/hub.json and the item pages mods/<id>.html: only the fields the pages
show, downloads from the download host by SHA-256, one page per item, no markup from player text.
When the community videos were (tools/build_videos.py), data/videos.json: only the fields the
pages show, every listed video and poster there and no unlisted one, MP4s of the boxes the
server's converter writes (an ISO brand, one moov with an H.264 track, mdat and padding only),
and posters without metadata.
When Photo of the Month was (tools/build_potm.py), data/photo-of-the-month.json: only the fields
the pages show, months and ids that are months and ids, counts that are counts, and every photo
it names one that data/gallery.json lists, with the same files.
  tools/check_site.py OUT"""
import html.parser
import json
import re
import sys
import zlib
from pathlib import Path
from urllib.parse import urlsplit

GALLERY_KEYS = {"id", "title", "gamertag", "owner", "likes", "car", "location", "src", "w", "h", "thumb", "tw",
                "th"}
FULL_WIDTH, THUMB_WIDTH, MAX_SIDE = 1920, 480, 7680
DESIGN_KEYS = {"id", "type", "code", "title", "gamertag", "owner", "likes", "car", "downloads", "rating",
               "ratings", "createdAt", "src", "w", "h"}
OWNER_RE = re.compile(r"[0-9a-f]{12}")  # the server's player id (a salted hash), never a XUID
VIDEO_KEYS = {"id", "title", "gamertag", "owner", "likes", "src", "poster", "w", "h", "seconds", "size", "date"}
POSTER_WIDTH, MAX_VIDEO_BYTES = 960, 32 << 20
MP4_TOP = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide"}
MP4_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1"}
# Pages that are not in the navbar: no link in it is the current page. online.html (how to play
# online) is reached from the landing page, the FAQ and the footer, the Photo of the Month rules
# from that event's page. Photo of the Month itself is hidden until the contest runs (README.md).
NOT_IN_NAV = {"profile.html", "online.html", "photo-of-the-month.html", "photo-of-the-month-rules.html"}
PREVIEW_WIDTH, PREVIEW_MAX_SIDE = 576, 1024
MAX_TIME = 10 ** 11  # unix seconds
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_PIXEL_CHUNKS = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}
SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_segments(data):
    """(width, height, markers, headers) of a JPEG: every segment marker in order, scans
    included, and every byte but the entropy-coded data; ValueError if it is not a well-formed
    JPEG that ends at its EOI."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    i, size, markers, headers = 2, None, [], [data[:2]]
    while True:
        if i + 2 > len(data) or data[i] != 0xFF:
            raise ValueError(f"no marker at {i}")
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        markers.append(m)
        if m == 0xD9:
            if i + 2 != len(data):
                raise ValueError(f"{len(data) - i - 2} bytes after the end")
            break
        if 0xD0 <= m <= 0xD7:
            i += 2
            continue
        n = int.from_bytes(data[i + 2:i + 4], "big")
        if n < 2 or i + 2 + n > len(data):
            raise ValueError(f"bad segment {m:02X}")
        if m in SOF:
            size = (int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big"))
        headers.append(data[i:i + 2 + n])
        i += 2 + n
        if m == 0xDA:  # entropy-coded data up to the next marker (not a stuffed 00 or a restart)
            while True:
                i = data.find(b"\xff", i)
                if i < 0 or i + 1 >= len(data):
                    raise ValueError("scan without an end")
                if data[i + 1] == 0 or 0xD0 <= data[i + 1] <= 0xD7:
                    i += 2
                    continue
                break
    if not size:
        raise ValueError("no frame")
    return size[0], size[1], markers, b"".join(headers)


def mp4_boxes(data):
    """The top-level boxes of an MP4 as the game server's converter writes it (ftyp first, with an
    ISO brand; one moov holding an H.264 track; mdat; padding only; filling the file exactly);
    ValueError for anything else. tools/build_videos.py checks each video with it too."""
    i, seen = 0, []
    while i < len(data):
        if i + 8 > len(data):
            raise ValueError("the video ends inside a box")
        size, kind = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
        head = 8
        if size == 1:
            if i + 16 > len(data):
                raise ValueError("the video ends inside a box")
            size, head = int.from_bytes(data[i + 8:i + 16], "big"), 16
        elif size == 0:
            size = len(data) - i
        if size < head or i + size > len(data):
            raise ValueError("a box of the video has a bad size")
        if kind not in MP4_TOP:
            raise ValueError(f"an unexpected box {kind!r} in the video")
        if kind == b"ftyp" and (seen or size < 16 or data[i + 8:i + 12] not in MP4_BRANDS):
            raise ValueError("the video's ftyp box")
        if kind == b"moov":
            if b"moov" in seen:
                raise ValueError("two moov boxes")
            if b"avc1" not in data[i:i + size]:
                raise ValueError("the video has no H.264 track")
        seen.append(kind)
        i += size
    if not seen or seen[0] != b"ftyp" or b"moov" not in seen or b"mdat" not in seen:
        raise ValueError("not an MP4 file")
    return seen


def check_videos(root):
    path = root / "data" / "videos.json"
    text = path.read_text(encoding="utf-8")
    bad = [f"data/videos.json: contains {w!r}" for w in ("xuid",) if w in text.lower()]
    try:
        doc = json.loads(text)
    except ValueError:
        return bad  # reported with the other data files
    if not (isinstance(doc, dict) and doc.get("version") == 1 and isinstance(doc.get("videos"), list)):
        return bad + ["data/videos.json: not a version 1 list of videos"]
    if set(doc) - {"version", "generated", "videos"}:
        bad.append(f"data/videos.json: unexpected fields {sorted(set(doc) - {'version', 'generated', 'videos'})}")
    listed = set()
    for i, v in enumerate(doc["videos"]):
        where = f"data/videos.json video {i}"
        if not isinstance(v, dict) or not re.fullmatch(r"[A-Z2-7]{12}", str(v.get("id"))):
            bad.append(f"{where}: no id")
            continue
        vid = v["id"]
        if set(v) - VIDEO_KEYS:
            bad.append(f"{where}: unexpected fields {sorted(set(v) - VIDEO_KEYS)}")
        if not (isinstance(v.get("gamertag"), str) and v["gamertag"] and isinstance(v.get("title"), str)):
            bad.append(f"{where}: no gamertag or title")
        bad += check_owner_and_likes(where, v)
        s = v.get("seconds")
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 < s <= 600 or not is_count(v.get("date"), MAX_TIME):
            bad.append(f"{where}: bad seconds or date")
        if v.get("src") != f"videos/{vid}.mp4" or v.get("poster") != f"videos/{vid}.jpg":
            bad.append(f"{where}: src/poster are not videos/{vid}.mp4 and videos/{vid}.jpg")
            continue
        if not (is_size(v.get("w"), POSTER_WIDTH) and is_size(v.get("h"), MAX_SIDE) and is_size(v.get("size"), MAX_VIDEO_BYTES)):
            bad.append(f"{where}: sizes out of range")
            continue
        listed.update((v["src"], v["poster"]))
        if not (root / v["src"]).is_file():
            bad.append(f"{where}: missing {v['src']}")
        else:
            data = (root / v["src"]).read_bytes()
            if len(data) != v["size"]:
                bad.append(f"{where}: {v['src']} is {len(data)} bytes, data/videos.json says {v['size']}")
            try:
                mp4_boxes(data)
            except ValueError as e:
                bad.append(f"{v['src']}: {e}")
        if not (root / v["poster"]).is_file():
            bad.append(f"{where}: missing {v['poster']}")
        else:
            bad += check_jpeg(root / v["poster"], v["w"], v["h"], POSTER_WIDTH)
    if (root / "videos").is_dir():
        bad += [f"videos/{f.name}: not in data/videos.json"
                for f in sorted((root / "videos").iterdir()) if f"videos/{f.name}" not in listed]
    return bad


def check_jpeg(path, width, height, max_width):
    try:
        w, h, markers, _ = jpeg_segments(path.read_bytes())
    except ValueError as e:
        return [f"{path.name}: {e}"]
    bad = []
    meta = sorted({f"{m:02X}" for m in markers if 0xE1 <= m <= 0xEF or m == 0xFE})
    if meta:
        bad.append(f"{path.name}: metadata segments {', '.join(meta)}")
    if (w, h) != (width, height):
        bad.append(f"{path.name}: {w}x{h}, data/gallery.json says {width}x{height}")
    if w > max_width:
        bad.append(f"{path.name}: wider than {max_width} px")
    return bad


def is_size(v, limit):
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v <= limit


def is_count(v, limit=10 ** 9):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= limit


def check_gallery(root):
    path = root / "data" / "gallery.json"
    text = path.read_text(encoding="utf-8")
    bad = [f"data/gallery.json: contains {w!r}" for w in ("xuid",) if w in text.lower()]
    try:
        doc = json.loads(text)
    except ValueError:
        return bad  # reported with the other data files
    if not (isinstance(doc, dict) and doc.get("version") == 1 and isinstance(doc.get("photos"), list)):
        return bad + ["data/gallery.json: not a version 1 gallery"]
    if set(doc) - {"version", "generated", "photos"}:
        bad.append(f"data/gallery.json: unexpected fields {sorted(set(doc) - {'version', 'generated', 'photos'})}")
    listed = set()
    for i, p in enumerate(doc["photos"]):
        where = f"data/gallery.json photo {i}"
        if not isinstance(p, dict) or not re.fullmatch(r"[A-Za-z2-7]{12}", str(p.get("id"))):
            bad.append(f"{where}: no id")
            continue
        pid = p["id"]
        if set(p) - GALLERY_KEYS:
            bad.append(f"{where}: unexpected fields {sorted(set(p) - GALLERY_KEYS)}")
        if not (isinstance(p.get("gamertag"), str) and p["gamertag"] and isinstance(p.get("title"), str)):
            bad.append(f"{where}: no gamertag or title")
        bad += check_owner_and_likes(where, p)
        if p.get("src") != f"photos/{pid}.jpg" or p.get("thumb") != f"photos/{pid}-thumb.jpg":
            bad.append(f"{where}: src/thumb are not photos/{pid}.jpg and photos/{pid}-thumb.jpg")
            continue
        sizes = [("w", FULL_WIDTH), ("h", MAX_SIDE), ("tw", THUMB_WIDTH), ("th", MAX_SIDE)]
        if not all(is_size(p.get(k), limit) for k, limit in sizes):
            bad.append(f"{where}: sizes out of range")
            continue
        for rel, w, h, limit in ((p["src"], p["w"], p["h"], FULL_WIDTH), (p["thumb"], p["tw"], p["th"], THUMB_WIDTH)):
            listed.add(rel)
            if not (root / rel).is_file():
                bad.append(f"{where}: missing {rel}")
            else:
                bad += check_jpeg(root / rel, w, h, limit)
    if (root / "photos").is_dir():
        bad += [f"photos/{f.name}: not in data/gallery.json"
                for f in sorted((root / "photos").iterdir()) if f"photos/{f.name}" not in listed]
    return bad


def check_owner_and_likes(where, item):
    bad = []
    if "owner" in item and not (isinstance(item["owner"], str) and OWNER_RE.fullmatch(item["owner"])):
        bad.append(f"{where}: an owner that is not a player id")
    if "likes" in item and not is_count(item["likes"]):
        bad.append(f"{where}: bad likes")
    return bad


def png_chunks(data):
    """(width, height, chunk types in order) of a PNG; ValueError if it is not a well-formed PNG
    (every chunk's CRC right) that ends at its IEND."""
    if not data.startswith(PNG_SIG):
        raise ValueError("not a PNG")
    i, types = len(PNG_SIG), []
    while True:
        if i + 12 > len(data):
            raise ValueError("no IEND")
        n, kind = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
        end = i + 12 + n
        if end > len(data) or zlib.crc32(data[i + 4:end - 4]) != int.from_bytes(data[end - 4:end], "big"):
            raise ValueError(f"bad chunk {kind.decode('latin-1')!r}")
        if not types and (kind != b"IHDR" or n != 13):
            raise ValueError("no IHDR")
        types.append(kind)
        i = end
        if kind == b"IEND":
            if i != len(data):
                raise ValueError(f"{len(data) - i} bytes after the end")
            break
    w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return w, h, types


def check_png(path, width, height):
    try:
        w, h, types = png_chunks(path.read_bytes())
    except ValueError as e:
        return [f"{path.name}: {e}"]
    bad = []
    extra = sorted({t.decode("latin-1") for t in types} - {t.decode() for t in PNG_PIXEL_CHUNKS})
    if extra:
        bad.append(f"{path.name}: chunks other than pixels: {', '.join(extra)}")
    if (w, h) != (width, height):
        bad.append(f"{path.name}: {w}x{h}, data/designs.json says {width}x{height}")
    return bad


def check_designs(root):
    path = root / "data" / "designs.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []  # reported with the other data files
    if not (isinstance(doc, dict) and doc.get("version") == 1 and isinstance(doc.get("designs"), list)):
        return ["data/designs.json: not a version 1 list of designs"]
    bad = []
    if set(doc) - {"version", "generated", "designs"}:
        bad.append(f"data/designs.json: unexpected fields {sorted(set(doc) - {'version', 'generated', 'designs'})}")
    listed, codes = set(), set()
    for i, d in enumerate(doc["designs"]):
        where = f"data/designs.json design {i}"
        if not isinstance(d, dict) or not re.fullmatch(r"[A-Za-z0-9]{6,32}", str(d.get("id"))):
            bad.append(f"{where}: no id")
            continue
        did = d["id"]
        if set(d) - DESIGN_KEYS:  # never a XUID, the design itself or its description
            bad.append(f"{where}: unexpected fields {sorted(set(d) - DESIGN_KEYS)}")
        bad += check_owner_and_likes(where, d)
        if d.get("type") not in ("livery", "vinylgroup"):
            bad.append(f"{where}: bad type")
        if not re.fullmatch(r"[A-Za-z0-9]{4,15}", str(d.get("code"))) or str(d.get("code")).upper() in codes:
            bad.append(f"{where}: bad or repeated share code")
        codes.add(str(d.get("code")).upper())
        if not (isinstance(d.get("gamertag"), str) and d["gamertag"] and isinstance(d.get("title"), str)):
            bad.append(f"{where}: no gamertag or title")
        if "car" in d and not (d.get("type") == "livery" and isinstance(d["car"], str) and d["car"]):
            bad.append(f"{where}: a car only for a paint job")
        if not (is_count(d.get("downloads")) and is_count(d.get("ratings")) and is_count(d.get("createdAt"), MAX_TIME)):
            bad.append(f"{where}: bad counts or createdAt")
        r = d.get("rating")
        if isinstance(r, bool) or not isinstance(r, (int, float)) or not 0 <= r <= 5:
            bad.append(f"{where}: bad rating")
        if d.get("src") != f"designs/{did}.png":
            bad.append(f"{where}: src is not designs/{did}.png")
            continue
        if not (is_size(d.get("w"), PREVIEW_WIDTH) and is_size(d.get("h"), PREVIEW_MAX_SIDE)):
            bad.append(f"{where}: sizes out of range")
            continue
        listed.add(d["src"])
        if not (root / d["src"]).is_file():
            bad.append(f"{where}: missing {d['src']}")
        else:
            bad += check_png(root / d["src"], d["w"], d["h"])
    if (root / "designs").is_dir():
        bad += [f"designs/{f.name}: not in data/designs.json"
                for f in sorted((root / "designs").iterdir()) if f"designs/{f.name}" not in listed]
    return bad


class _Tags(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bad = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if any(name.startswith("on") for name, _ in attrs) or tag in ("iframe", "object", "embed", "base", "form"):
            self.bad = True
        if tag == "script":
            self.scripts.append(dict(attrs).get("src"))


def markup_from_text(text):
    """Whether a page has an element a generated page never has: an event handler, a frame, a
    form, or a script other than the site's own files and the one that sets the js class."""
    p = _Tags()
    p.feed(text)
    inline = text.count('<script>document.documentElement.classList.add("js")</script>')
    return p.bad or p.scripts.count(None) != inline or any(
        s and not re.fullmatch(r"\.\./[a-z]+\.js", s) for s in p.scripts)


HUB_KEYS = {"id", "kind", "title", "description", "version", "author", "created", "published", "size", "files",
            "sha256", "folder", "likes", "download"}


def check_hub(root):
    """data/hub.json (tools/build_hub.py) and the item pages mods/<id>.html."""
    where = "data/hub.json"
    try:
        doc = json.loads((root / where).read_text(encoding="utf-8"))
    except ValueError:
        return []
    if not (isinstance(doc, dict) and doc.get("version") == 1 and isinstance(doc.get("items"), list)
            and isinstance(doc.get("on"), bool)):
        return [f"{where}: not version 1"]
    bad, ids = [], set()
    if "xuid" in json.dumps(doc).lower():
        bad.append(f"{where}: contains 'xuid'")
    for i, item in enumerate(doc["items"]):
        at = f"{where} item {i}"
        if not isinstance(item, dict) or not re.fullmatch(r"[A-Z2-7]{12}", str(item.get("id"))):
            bad.append(f"{at}: no id")
            continue
        ids.add(item["id"])
        if set(item) - HUB_KEYS:
            bad.append(f"{at}: unexpected fields {sorted(set(item) - HUB_KEYS)}")
        sha, folder = str(item.get("sha256")), str(item.get("folder"))
        m = re.fullmatch(r"https://([^/?#@]+)/([0-9a-f]{64})/([A-Za-z0-9][A-Za-z0-9_-]{0,39})\.zip", str(item.get("download")))
        if not m or m.group(2) != sha or m.group(3) != folder or m.group(1) == "forza.dyotson.dev":
            bad.append(f"{at}: the download is not its SHA-256 and folder on the download host")
        if item.get("kind") not in ("mod", "pack") or not all(is_count(item.get(k), 10 ** 12) for k in
                                                               ("created", "published", "size", "files", "likes")):
            bad.append(f"{at}: bad kind or counts")
    pages = {p.stem for p in (root / "mods").glob("*.html")} if (root / "mods").is_dir() else set()
    if doc["on"] and pages != ids:
        bad.append(f"mods/: pages {sorted(pages ^ ids)} do not match data/hub.json")
    if not doc["on"] and pages:
        bad.append("mods/: item pages while the hub is off")
    for page in sorted((root / "mods").glob("*.html")) if (root / "mods").is_dir() else []:
        text = page.read_text(encoding="utf-8")
        for ref in re.findall(r'(?:href|src)="([^"]+)"', text):
            u = urlsplit(ref)
            if u.scheme or ref.startswith(("#", "//", "mailto:")):
                continue
            if not (page.parent / u.path).resolve().is_file():
                bad.append(f"mods/{page.name}: {ref}")
        if markup_from_text(text):
            bad.append(f"mods/{page.name}: markup from player text")
    return bad


POTM_KEYS = {"version", "generated", "from", "featured", "winners", "months"}
POTM_PHOTO_FILES = {"src", "w", "h", "thumb", "tw", "th", "owner"}
POTM_WINNER_KEYS = {"month", "id", "title", "gamertag", "votes", "reached", "confirmed"} | POTM_PHOTO_FILES
POTM_MONTH_KEYS = {"month", "start", "end", "state", "accountDays", "entries", "top", "winner"}
POTM_ENTRY_KEYS = {"rank", "id", "title", "gamertag", "votes", "likes", "reached"} | POTM_PHOTO_FILES
POTM_STATES = {"upcoming", "open", "verifying", "confirmed", "no winner"}
MONTH_RE = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
PHOTO_ID_RE = re.compile(r"[A-Za-z2-7]{12}")


def check_potm(root):
    path = root / "data" / "photo-of-the-month.json"
    text = path.read_text(encoding="utf-8")
    bad = [f"data/photo-of-the-month.json: contains {w!r}" for w in ("xuid",) if w in text.lower()]
    try:
        doc = json.loads(text)
    except ValueError:
        return bad  # reported with the other data files
    where = "data/photo-of-the-month.json"
    if not (isinstance(doc, dict) and doc.get("version") == 1 and isinstance(doc.get("winners"), list)
            and isinstance(doc.get("months"), list)):
        return bad + [f"{where}: not version 1"]
    if set(doc) - POTM_KEYS:
        bad.append(f"{where}: unexpected fields {sorted(set(doc) - POTM_KEYS)}")
    if doc.get("from") is not None and not (isinstance(doc["from"], str) and MONTH_RE.fullmatch(doc["from"])):
        bad.append(f"{where}: from is not a month")
    try:
        photos = {p["id"]: p for p in json.loads((root / "data" / "gallery.json").read_text(encoding="utf-8"))["photos"]}
    except (OSError, ValueError, KeyError, TypeError):
        photos = {}

    def photo(item, at, keys, counts):
        out = []
        if not isinstance(item, dict) or not PHOTO_ID_RE.fullmatch(str(item.get("id"))):
            return [f"{at}: no id"]
        if set(item) - keys:
            out.append(f"{at}: unexpected fields {sorted(set(item) - keys)}")
        if not (isinstance(item.get("title"), str) and isinstance(item.get("gamertag"), str)):
            out.append(f"{at}: no title or gamertag")
        if not all(is_count(item.get(k), MAX_TIME) for k in counts):
            out.append(f"{at}: bad counts or times")
        out += check_owner_and_likes(at, item)
        files = {k: item[k] for k in POTM_PHOTO_FILES if k in item}
        if files:
            listed = photos.get(item["id"])
            if not listed or any(listed.get(k) != v for k, v in files.items()) or "src" not in files:
                out.append(f"{at}: photo files that data/gallery.json does not list")
        return out

    for i, w in enumerate(doc["winners"]):
        at = f"{where} winner {i}"
        if not isinstance(w, dict) or not MONTH_RE.fullmatch(str(w.get("month"))):
            bad.append(f"{at}: no month")
            continue
        bad += photo(w, at, POTM_WINNER_KEYS, ("votes", "reached", "confirmed"))
    f = doc.get("featured")
    if f is not None:
        bad += photo(f, f"{where} featured", POTM_WINNER_KEYS, ("votes", "reached", "confirmed"))
        if not isinstance(f, dict) or "src" not in f or not doc["winners"] or doc["winners"][0].get("id") != f.get("id"):
            bad.append(f"{where}: featured is not the newest winner with its photo")
    for i, m in enumerate(doc["months"]):
        at = f"{where} month {i}"
        if not isinstance(m, dict) or not MONTH_RE.fullmatch(str(m.get("month"))) or m.get("state") not in POTM_STATES:
            bad.append(f"{at}: no month or state")
            continue
        if set(m) - POTM_MONTH_KEYS:
            bad.append(f"{at}: unexpected fields {sorted(set(m) - POTM_MONTH_KEYS)}")
        if not all(is_count(m.get(k), MAX_TIME) for k in ("start", "end", "accountDays", "entries")):
            bad.append(f"{at}: bad counts or times")
        if m.get("winner") is not None and not PHOTO_ID_RE.fullmatch(str(m["winner"])):
            bad.append(f"{at}: bad winner")
        for j, e in enumerate(m.get("top") if isinstance(m.get("top"), list) else []):
            bad += photo(e, f"{at} entry {j}", POTM_ENTRY_KEYS, ("rank", "votes", "likes", "reached"))
    return bad


def main():
    root = Path(sys.argv[1])
    bad = []
    for page in sorted(root.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        for ref in re.findall(r'(?:href|src|data-full)="([^"]+)"', text):
            u = urlsplit(ref)
            if u.scheme or ref.startswith(("#", "//", "mailto:")):
                continue
            path = u.path.lstrip("/")
            if path in ("", ".") or path.endswith("/"):
                path = path.rstrip("./") + "/index.html" if path.rstrip("./") else "index.html"
            if not (root / path).is_file():
                bad.append(f"{page.name}: {ref}")
    for f in sorted(root.glob("data/*.json")):
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            bad.append(f"{f.relative_to(root)}: {e}")
    for name in ("index.html", "features.html", "online.html", "download.html", "leaderboards.html",
                 "gallery.html", "photos.js", "videos.html", "videos.js", "designs.html", "designs.js", "press.html", "nav.js",
                 "account.html", "account.js", "live.js",
                 "likes.js", "online.js", "profile.html", "profile.js", "mods.html", "hub.js",
                 "photo-of-the-month.html", "photo-of-the-month-rules.html", "potm.js",
                 "robots.txt", "sitemap.xml", ".nojekyll",
                 "LICENSE", "NOTICE.md"):
        if not (root / name).is_file():
            bad.append(f"missing {name}")
    # The front page's photos section starts hidden: photos.js shows it only when there are photos.
    index = (root / "index.html").read_text(encoding="utf-8") if (root / "index.html").is_file() else ""
    m = re.search(r'<section\b[^>]*\bid="photos"[^>]*>', index)
    if not m or not re.search(r"\shidden[\s=>]", m.group(0)):
        bad.append('index.html: <section id="photos"> must start hidden')
    # The same for its Photo of the Month (#potm): potm.js shows it only when there is a winner.
    m = re.search(r'<section\b[^>]*\bid="potm"[^>]*>', index)
    if not m or not re.search(r"\shidden[\s=>]", m.group(0)):
        bad.append('index.html: <section id="potm"> must start hidden')
    # The players-online badge starts hidden too: online.js shows it only with a fresh count above 0.
    m = re.search(r'<a\b[^>]*\bid="online-now"[^>]*>', index)
    if not m or not re.search(r"\shidden[\s=>]", m.group(0)):
        bad.append('index.html: <a id="online-now"> must start hidden')
    # The same for the Online page's #designs: designs.js shows it only when there are designs.
    online = (root / "online.html").read_text(encoding="utf-8") if (root / "online.html").is_file() else ""
    m = re.search(r'<section\b[^>]*\bid="designs"[^>]*>', online)
    if not m or not re.search(r"\shidden[\s=>]", m.group(0)):
        bad.append('online.html: <section id="designs"> must start hidden')
    for token in ("features.html#mountains", "features.html#extras", "features.html#numbers",
                  "download.html#highway", "download.html#finish", "online.html#online"):
        if token not in index:
            bad.append(f"index.html: missing redirect to {token}")
    sm = (root / "sitemap.xml").read_text(encoding="utf-8") if (root / "sitemap.xml").is_file() else ""
    for loc in ("https://forza.dyotson.dev/", "https://forza.dyotson.dev/features.html",
                "https://forza.dyotson.dev/online.html", "https://forza.dyotson.dev/download.html",
                "https://forza.dyotson.dev/leaderboards.html", "https://forza.dyotson.dev/gallery.html",
                "https://forza.dyotson.dev/videos.html", "https://forza.dyotson.dev/designs.html",
                "https://forza.dyotson.dev/press.html"):
        if loc not in sm:
            bad.append(f"sitemap.xml: missing {loc}")
    # The account page: never indexed, and its sign-in forms post to the accounts service only.
    account = (root / "account.html").read_text(encoding="utf-8") if (root / "account.html").is_file() else ""
    if account and 'content="noindex"' not in account:
        bad.append("account.html: needs <meta name=\"robots\" content=\"noindex\">")
    for action in re.findall(r'<form\b[^>]*\baction="([^"]*)"', account):
        if action != "/api/auth/browser/v1/auth/provider/redirect":
            bad.append(f"account.html: a form posts to {action}")
    if "https://forza.dyotson.dev/account.html" in sm:
        bad.append("sitemap.xml: account.html is not for search engines")
    # Profile pages: one page for every player (profile.html?p=<id>), never indexed.
    profile = (root / "profile.html").read_text(encoding="utf-8") if (root / "profile.html").is_file() else ""
    if profile and 'content="noindex"' not in profile:
        bad.append("profile.html: needs <meta name=\"robots\" content=\"noindex\">")
    if "https://forza.dyotson.dev/profile.html" in sm:
        bad.append("sitemap.xml: profile.html is not for search engines")
    # The same navbar on every page. aria-current="page" is the only difference.
    navs = {}
    for page in sorted(root.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        if "<title>" not in text or 'name="description"' not in text or 'rel="canonical"' not in text:
            bad.append(f"{page.name}: needs a title, description and canonical URL")
        if "Official links:" not in text:
            bad.append(f"{page.name}: the footer needs the Official links note")
        # A page kept out of search engines is not in the sitemap either.
        if 'content="noindex"' in text and f"https://forza.dyotson.dev/{page.name}" in sm:
            bad.append(f"sitemap.xml: {page.name} has noindex, so it is not for search engines")
        found = re.search(r'<header class="topbar">.*?</header>', text, re.S)
        if not found:
            bad.append(f"{page.name}: no navbar")
            continue
        nav = re.sub(r'\s*aria-current="page"', "", found.group(0))
        navs.setdefault(nav, []).append(page.name)
        if 'aria-current="page"' not in found.group(0) and page.name not in NOT_IN_NAV:
            bad.append(f"{page.name}: mark the current page with aria-current")
        if 'aria-current="page"' in found.group(0) and page.name in NOT_IN_NAV:
            bad.append(f"{page.name}: is not in the navbar, so no link there is the current page")
    if len(navs) > 1:
        bad.append("navbar markup differs across pages: " +
                   "; ".join(", ".join(names) for names in navs.values()))
    sample = next(iter(navs), "")
    if sample and not re.search(r'<button\b[^>]*class="nav-toggle"[^>]*aria-controls="site-nav"', sample):
        bad.append("navbar: menu button must be a button that controls the nav")
    if sample and 'aria-expanded="false"' not in sample:
        bad.append("navbar: menu button needs aria-expanded")
    if (root / "data" / "gallery.json").is_file():
        bad += check_gallery(root)
    if (root / "data" / "designs.json").is_file():
        bad += check_designs(root)
    if (root / "data" / "videos.json").is_file():
        bad += check_videos(root)
    if (root / "data" / "hub.json").is_file():
        bad += check_hub(root)
    if (root / "data" / "photo-of-the-month.json").is_file():
        bad += check_potm(root)
    print("\n".join(bad) or f"ok: {sum(1 for _ in root.rglob('*') if _.is_file())} files")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
