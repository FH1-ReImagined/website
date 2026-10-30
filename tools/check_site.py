#!/usr/bin/env python3
"""Check an assembled site (tools/assemble.sh OUT): every local href/src of every page names a
file that is there, and the JSON data files parse. When the community photos were built into it
(tools/build_gallery.py), also data/gallery.json: only the fields the pages show (never a "xuid"
or an "owner"), every listed photo there and no unlisted one, sizes within the limits, and JPEGs
without metadata (no APP1-APP15 or COM segment, nothing after the end).
  tools/check_site.py OUT"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

GALLERY_KEYS = {"id", "title", "gamertag", "car", "location", "src", "w", "h", "thumb", "tw", "th"}
FULL_WIDTH, THUMB_WIDTH, MAX_SIDE = 1920, 480, 7680
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


def check_gallery(root):
    path = root / "data" / "gallery.json"
    text = path.read_text(encoding="utf-8")
    bad = [f"data/gallery.json: contains {w!r}" for w in ("xuid", "owner") if w in text.lower()]
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
                 "gallery.html", "photos.js", "nav.js", "robots.txt", "sitemap.xml", ".nojekyll",
                 "LICENSE", "NOTICE.md"):
        if not (root / name).is_file():
            bad.append(f"missing {name}")
    # The front page's photos section starts hidden: photos.js shows it only when there are photos.
    index = (root / "index.html").read_text(encoding="utf-8") if (root / "index.html").is_file() else ""
    m = re.search(r'<section\b[^>]*\bid="photos"[^>]*>', index)
    if not m or not re.search(r"\shidden[\s=>]", m.group(0)):
        bad.append('index.html: <section id="photos"> must start hidden')
    for token in ("features.html#mountains", "features.html#extras", "features.html#numbers",
                  "download.html#highway", "download.html#finish", "online.html#online"):
        if token not in index:
            bad.append(f"index.html: missing redirect to {token}")
    sm = (root / "sitemap.xml").read_text(encoding="utf-8") if (root / "sitemap.xml").is_file() else ""
    for loc in ("https://forza.dyotson.dev/", "https://forza.dyotson.dev/features.html",
                "https://forza.dyotson.dev/online.html", "https://forza.dyotson.dev/download.html",
                "https://forza.dyotson.dev/leaderboards.html", "https://forza.dyotson.dev/gallery.html"):
        if loc not in sm:
            bad.append(f"sitemap.xml: missing {loc}")
    # The same navbar on every page. aria-current="page" is the only difference.
    navs = {}
    for page in sorted(root.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        if "<title>" not in text or 'name="description"' not in text or 'rel="canonical"' not in text:
            bad.append(f"{page.name}: needs a title, description and canonical URL")
        found = re.search(r'<header class="topbar">.*?</header>', text, re.S)
        if not found:
            bad.append(f"{page.name}: no navbar")
            continue
        nav = re.sub(r'\s*aria-current="page"', "", found.group(0))
        navs.setdefault(nav, []).append(page.name)
        if 'aria-current="page"' not in found.group(0):
            bad.append(f"{page.name}: mark the current page with aria-current")
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
    print("\n".join(bad) or f"ok: {sum(1 for _ in root.rglob('*') if _.is_file())} files")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
