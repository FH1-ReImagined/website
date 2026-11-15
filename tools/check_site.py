#!/usr/bin/env python3
"""Check an assembled site (tools/assemble.sh OUT): every local href/src/srcset of every page names a
file that is there, and the JSON data files parse. When the community photos were built into it
(tools/build_gallery.py), also data/gallery.json: only the fields the pages show (never a "xuid";
an "owner" only as the server's 12-hex-digit player id, and "likes" only as a count), every listed
photo there and no unlisted one, sizes within the limits, and JPEGs
without metadata (no APP1-APP15 or COM segment, nothing after the end). When the community
designs were (tools/build_designs.py), data/designs.json the same way: only the fields the pages
show, every listed preview there and no unlisted one, and PNGs of pixels alone (IHDR, PLTE,
tRNS, IDAT and IEND chunks, nothing after the end); one page designs/<id>.html for each listed
design and no other, its local links there, no markup from player text, structured data that
parses, and one sitemap entry for each page and none for a design that is not listed. When the mods
and texture packs hub was (tools/build_hub.py), data/hub.json and the item pages mods/<id>.html: only the fields the pages
show, downloads from the download host by SHA-256, one page per item, no markup from player text.
When the community videos were (tools/build_videos.py), data/videos.json: only the fields the
pages show, every listed video and poster there and no unlisted one, MP4s of the boxes the
server's converter writes (an ISO brand, one moov with an H.264 track, mdat and padding only),
and posters without metadata.
The lists the scripts fill start aria-busy (the page keeps room for them), and every picture of
the pages over 100 KB has a srcset of smaller copies.
When Photo of the Month was (tools/build_potm.py), data/photo-of-the-month.json: only the fields
the pages show, months and ids that are months and ids, counts that are counts, and every photo
it names one that data/gallery.json lists, with the same files.
Every page's own markup has a language, alt text and width and height on its pictures, a label
on each form control, a name on each button, one h1, headings that skip no level, each id once and
ARIA references to ids that are there.
Photo of the Month is wholly on or wholly off (tools/potm_switch.py): its navbar and footer links,
sitemap entries, noindex, switched elements and the home page's winner section all in the same
state, never half, and none of them written in another form than the switch's own; while it is off,
no page but its own two links to it outside what the switch hides.
Every page, a design's and a hub item's too, has what a share preview needs (og:title,
og:description, og:url its canonical address, og:image a picture of the site that is there,
og:site_name, twitter:card); the sitemap lists every page without noindex, under its canonical
address, and nothing else.
  tools/check_site.py OUT"""
import html.parser
import json
import re
import sys
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import potm_switch

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
# from that event's page. Photo of the Month itself is in the navbar only while it is switched on
# (tools/potm_switch.py, README.md).
NOT_IN_NAV = {"profile.html", "online.html", "photo-of-the-month-rules.html"}
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
        bad += video_problems(root, f"data/videos.json video {i}", v)
        if isinstance(v, dict):
            listed.update((str(v.get("src")), str(v.get("poster"))))
    if (root / "videos").is_dir():
        bad += [f"videos/{f.name}: not in data/videos.json"
                for f in sorted((root / "videos").iterdir()) if f"videos/{f.name}" not in listed]
    return bad


def video_problems(root, where, v):
    """What is wrong with one entry of data/videos.json and its files ([] for none).
    tools/build_videos.py leaves out any entry this finds wrong, so one entry never stops a build."""
    bad = []
    if not isinstance(v, dict) or not re.fullmatch(r"[A-Z2-7]{12}", str(v.get("id"))):
        return [f"{where}: no id"]
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
        return bad + [f"{where}: src/poster are not videos/{vid}.mp4 and videos/{vid}.jpg"]
    if not (is_size(v.get("w"), POSTER_WIDTH) and is_size(v.get("h"), MAX_SIDE) and is_size(v.get("size"), MAX_VIDEO_BYTES)):
        return bad + [f"{where}: sizes out of range"]
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
    listed, codes, ids = set(), set(), set()
    for i, d in enumerate(doc["designs"]):
        where = f"data/designs.json design {i}"
        if not isinstance(d, dict) or not re.fullmatch(r"[A-Za-z0-9]{6,32}", str(d.get("id"))):
            bad.append(f"{where}: no id")
            continue
        did = d["id"]
        ids.add(did)
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
    pages = {f"designs/{i}.html" for i in ids}
    if (root / "designs").is_dir():
        bad += [f"designs/{f.name}: not in data/designs.json"
                for f in sorted((root / "designs").iterdir()) if f"designs/{f.name}" not in listed | pages]
    for rel in sorted(pages):
        bad += design_page_problems(root, rel)
    sm = (root / "sitemap.xml").read_text(encoding="utf-8") if (root / "sitemap.xml").is_file() else ""
    in_sitemap = re.findall(r"<loc>https://forza\.dyotson\.dev/(designs/[^<]*)</loc>", sm)
    if sorted(in_sitemap) != sorted(pages):
        bad.append(f"sitemap.xml: the design pages listed {sorted(set(in_sitemap) ^ pages)[:5]} do not match "
                   f"data/designs.json, or one is listed twice")
    return bad


SITE = "https://forza.dyotson.dev"


def _meta(text, key):
    """The content of <meta property|name="key">, unescaped, or None."""
    m = re.search(r'<meta (?:property|name)="' + re.escape(key) + r'" content="([^"]*)"', text)
    return html.unescape(m.group(1)) if m else None


def png_size(path):
    data = path.read_bytes()[:24]
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")) if data[:8] == PNG_SIG else None


def share_problems(root, rel, text):
    """What a link to the page pasted in a chat or a post would miss: a title, description, address
    and picture for the preview (og:url the canonical address, og:image a picture of this site
    that is there, with its real size when it says one), and twitter:card, which also makes Discord
    show the picture large."""
    bad = []
    for key in ("og:title", "og:description", "og:type", "og:url", "og:image", "og:site_name", "twitter:card"):
        if not _meta(text, key):
            bad.append(f"{rel}: needs <meta {key}> for its share preview")
    canonical = re.search(r'<link rel="canonical" href="([^"]*)">', text)
    url = _meta(text, "og:url")
    if canonical and url and html.unescape(canonical.group(1)) != url:
        bad.append(f"{rel}: og:url {url} is not its canonical address")
    image = _meta(text, "og:image")
    if image:
        local = root / image[len(SITE) + 1:] if image.startswith(SITE + "/") else None
        if not local:
            bad.append(f"{rel}: og:image {image} is not on this site")
        elif not local.is_file():
            bad.append(f"{rel}: og:image {image} is not there")
        elif _meta(text, "og:image:width") and local.suffix == ".png":
            said = (_meta(text, "og:image:width"), _meta(text, "og:image:height"))
            real = png_size(local)
            if real and said != (str(real[0]), str(real[1])):
                bad.append(f"{rel}: og:image is {real[0]}x{real[1]}, the page says {said[0]}x{said[1]}")
    return bad


def sitemap_problems(root, sm):
    """Every address in the sitemap is a page of the site that may be indexed, under its canonical
    address; every page of the site that may be indexed is in it (mods.html only once the hub is on)."""
    bad, listed = [], re.findall(r"<loc>([^<]*)</loc>", sm)
    for loc in listed:
        if not loc.startswith(SITE + "/"):
            bad.append(f"sitemap.xml: {loc} is not on this site")
            continue
        rel = loc[len(SITE) + 1:] or "index.html"
        page = root / rel
        if not page.is_file():
            bad.append(f"sitemap.xml: {loc} is not there")
            continue
        text = page.read_text(encoding="utf-8")
        canonical = re.search(r'<link rel="canonical" href="([^"]*)">', text)
        if not canonical or html.unescape(canonical.group(1)) != loc:
            bad.append(f"sitemap.xml: {loc} is not the canonical address of {rel}")
    if len(set(listed)) != len(listed):
        bad.append("sitemap.xml: an address is listed twice")
    for day in re.findall(r"<lastmod>([^<]*)</lastmod>", sm):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            bad.append(f"sitemap.xml: lastmod {day} is not a date")
    hub_on = False
    try:
        hub_on = json.loads((root / "data" / "hub.json").read_text(encoding="utf-8")).get("on") is True
    except (OSError, ValueError, AttributeError):
        pass
    for page in sorted(root.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        if 'content="noindex"' in text or (page.name == "mods.html" and not hub_on):
            continue
        loc = SITE + "/" + ("" if page.name == "index.html" else page.name)
        if loc not in listed:
            bad.append(f"sitemap.xml: missing {loc} (a page without noindex)")
    return bad


def design_page_problems(root, rel):
    """What is wrong with a design's page (tools/build_designs.py): missing, a local link to nothing,
    markup or a script from player text, structured data that does not parse, no title, description,
    canonical address or navbar."""
    page = root / rel
    if not page.is_file():
        return [f"{rel}: missing (every listed design has a page)"]
    text = page.read_text(encoding="utf-8")
    bad = []
    for ref in re.findall(r'(?:href|src)="([^"]+)"', text):
        u = urlsplit(ref)
        if u.scheme or ref.startswith(("#", "//", "mailto:")):
            continue
        if not (page.parent / u.path).resolve().is_file():
            bad.append(f"{rel}: {ref}")
    if markup_from_text(text):
        bad.append(f"{rel}: markup from player text")
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S):
        if re.search(r"[<>&]", block):
            bad.append(f"{rel}: <, > or & in its structured data")
        try:
            json.loads(block)
        except ValueError:
            bad.append(f"{rel}: its structured data is not JSON")
    canonical = f'<link rel="canonical" href="https://forza.dyotson.dev/{rel}">'
    if canonical not in text or "<title>" not in text or 'name="description"' not in text:
        bad.append(f"{rel}: needs its title, description and canonical URL")
    # Not a search for "xuid" in the text: a title may say it, and a player's text must never stop
    # the site from being published. The fields of data/designs.json are checked above.
    if '<header class="topbar">' not in text or "Official links:" not in text:
        bad.append(f"{rel}: needs the navbar and the footer")
    bad += share_problems(root, rel, text)
    return bad


class _Tags(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bad = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if any(name.startswith("on") for name, _ in attrs) or tag in ("iframe", "object", "embed", "base", "form"):
            self.bad = True
        if tag == "script" and dict(attrs).get("type") != "application/ld+json":  # structured data is not run
            self.scripts.append(dict(attrs).get("src"))


def markup_from_text(text):
    """Whether a page has an element a generated page never has: an event handler, a frame, a
    form, or a script other than the site's own files and the one that sets the js class."""
    p = _Tags()
    p.feed(text)
    inline = text.count('<script>document.documentElement.classList.add("js")</script>')
    return p.bad or p.scripts.count(None) != inline or any(
        s and not re.fullmatch(r"\.\./[a-z]+\.js", s) for s in p.scripts)


class _A11y(html.parser.HTMLParser):
    """The parts of a page's own markup a screen reader or a keyboard needs (the lists the scripts
    fill in are checked by the tests in headless Chrome)."""
    VOID = {"input", "select", "textarea"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.bad, self.ids, self.labels, self.controls, self.levels = [], set(), set(), [], []
        self.refs = []
        self.label_depth, self.button = 0, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            if a["id"] in self.ids:
                self.bad.append(f'id="{a["id"]}" twice')
            self.ids.add(a["id"])
        for name in ("aria-labelledby", "aria-describedby", "aria-controls"):
            self.refs += [(name, ref) for ref in a.get(name, "").split()]
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "img":
            if self.button is not None:  # a picture button is named by its picture's alt text
                self.button[1] += a.get("alt") or ""
            if "alt" not in a:
                self.bad.append(f"<img src=\"{a.get('src', '')}\"> has no alt (alt=\"\" for a picture that says nothing)")
            elif a.get("src") and not (a.get("width") and a.get("height")):
                self.bad.append(f"<img src=\"{a['src']}\"> needs width and height (the page jumps when it loads)")
        elif tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.labels.add(a["for"])
        elif tag in self.VOID and a.get("type") not in ("hidden", "submit", "button"):
            named = self.label_depth or a.get("aria-label") or a.get("aria-labelledby") or a.get("title")
            self.controls.append((a.get("id"), bool(named), f"<{tag} {a.get('id') or a.get('name') or ''}>"))
        elif tag == "button":
            self.button = [bool(a.get("aria-label") or a.get("aria-labelledby")), ""]
        elif re.fullmatch(r"h[1-6]", tag):
            self.levels.append(int(tag[1]))

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth = max(0, self.label_depth - 1)
        elif tag == "button" and self.button is not None:
            if not self.button[0] and not self.button[1].strip():
                self.bad.append("a <button> with no text and no aria-label")
            self.button = None

    def handle_data(self, data):
        if self.button is not None:
            self.button[1] += data


def a11y_problems(text):
    """What a page's own markup lacks: a language, alt text and dimensions on its pictures, a label
    on each form control, a name on each button, one h1, headings that skip no level, ids used once
    and ARIA references to ids that are there."""
    p = _A11y()
    p.feed(text)
    bad = list(p.bad)
    if not p.lang:
        bad.append("<html> needs lang")
    for name, ref in p.refs:
        if ref not in p.ids:
            bad.append(f'{name}="{ref}" names no element of the page')
    for cid, named, what in p.controls:
        if not named and cid not in p.labels:
            bad.append(f"{what} has no label")
    if p.levels.count(1) != 1:
        bad.append(f"{p.levels.count(1)} <h1> (one per page)")
    for a, b in zip(p.levels, p.levels[1:]):
        if b > a + 1:
            bad.append(f"<h{a}> followed by <h{b}> (a heading level skipped)")
            break
    return bad


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
        bad += share_problems(root, f"mods/{page.name}", text)
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
        srcsets = [c.split()[0] for v in re.findall(r'srcset="([^"]+)"', text) for c in v.split(",") if c.strip()]
        for ref in re.findall(r'(?:href|src|data-full)="([^"]+)"', text) + srcsets:
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
    # The lists the scripts fill start aria-busy: style.css keeps room for them until they are drawn,
    # so the page does not jump when they come in (photos.js and designs.js take it off).
    for name, marks in (("gallery.html", ["data-photos-live"]), ("online.html", ['data-designs="3"']),
                        ("designs.html", ['data-designs-type="livery"', 'data-designs-type="vinylgroup"']),
                        ("profile.html", ["data-photos-owner", "data-designs-owner"])):
        page = (root / name).read_text(encoding="utf-8") if (root / name).is_file() else ""
        for mark in marks:
            m = re.search(r"<ul\b[^>]*" + re.escape(mark) + r"[^>]*>", page)
            if not m or 'aria-busy="true"' not in m.group(0):
                bad.append(f"{name}: the list with {mark} must start aria-busy=\"true\"")
    # A big picture comes with smaller copies (srcset), so a phone or a small card does not load the
    # full size: every <img> of the pages' own pictures over 100 KB has a srcset whose files are there.
    for page in sorted(root.glob("*.html")):
        for tag in re.findall(r"<img\b[^>]*>", page.read_text(encoding="utf-8")):
            src = re.search(r'\ssrc="(images/[^"]+)"', tag)
            if src and (root / src.group(1)).is_file() and (root / src.group(1)).stat().st_size > 100 * 1024 \
                    and "srcset=" not in tag:
                bad.append(f"{page.name}: {src.group(1)} is over 100 KB: give it a srcset with smaller copies")
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
    bad += sitemap_problems(root, sm)
    for page in sorted(root.glob("*.html")):
        bad += share_problems(root, page.name, page.read_text(encoding="utf-8"))
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
    # Photo of the Month: wholly on or wholly off (tools/potm_switch.py), never half.
    potm_state, problems = potm_switch.site_state(root)
    bad += [f"Photo of the Month: {p}" for p in problems]
    # Off: nothing of it shown or linked outside its own two pages, whatever
    # data/photo-of-the-month.json says (the home page's winner section included). Also checked on a
    # site half switched but mostly off, the way it is most likely meant.
    parts, _ = potm_switch.parts_of(root)
    on_parts = sum(state == "on" for _, _, state in parts)
    if potm_state == "off" or (potm_state is None and on_parts <= len(parts) - on_parts):
        for page in sorted(root.glob("*.html")):
            if page.name in (potm_switch.PAGE, potm_switch.RULES):
                continue
            shown = potm_switch.shown_links(page.read_text(encoding="utf-8"))
            if shown:
                bad.append(f"{page.name}: links to Photo of the Month while it is off, outside what the switch hides "
                           f"(data-potm-switch hidden, data-potm-off): {', '.join(sorted(set(shown)))}")
    not_in_nav = NOT_IN_NAV | (set() if potm_state == "on" else {potm_switch.PAGE})
    # The same navbar on every page. aria-current="page" is the only difference.
    navs = {}
    for page in sorted(root.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        if "<title>" not in text or 'name="description"' not in text or 'rel="canonical"' not in text:
            bad.append(f"{page.name}: needs a title, description and canonical URL")
        if "Official links:" not in text:
            bad.append(f"{page.name}: the footer needs the Official links note")
        bad += [f"{page.name}: {p}" for p in a11y_problems(text)]
        # A page kept out of search engines is not in the sitemap either.
        if 'content="noindex"' in text and f"https://forza.dyotson.dev/{page.name}" in sm:
            bad.append(f"sitemap.xml: {page.name} has noindex, so it is not for search engines")
        found = re.search(r'<header class="topbar">.*?</header>', text, re.S)
        if not found:
            bad.append(f"{page.name}: no navbar")
            continue
        nav = re.sub(r'\s*aria-current="page"', "", found.group(0))
        navs.setdefault(nav, []).append(page.name)
        if 'aria-current="page"' not in found.group(0) and page.name not in not_in_nav:
            bad.append(f"{page.name}: mark the current page with aria-current")
        if 'aria-current="page"' in found.group(0) and page.name in not_in_nav:
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
