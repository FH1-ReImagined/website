#!/usr/bin/env python3
"""Copy the mods and texture packs hub's list (the website accounts service's public
/api/hub/items) into the assembled site: OUT/data/hub.json, one page per item (OUT/mods/<id>.html,
with its own title and description, for search engines and for sharing), and their addresses in
OUT/sitemap.xml.

  HUB_URL=URL tools/build_hub.py OUT [--require]

HUB_URL is the accounts service: the site's origin, or any URL on it such as LIKES_URL (only its
scheme, host and port are used). While the service has no hub (HTTP 404, FORZA_HUB off), or
HUB_URL is unset, data/hub.json says so ("on": false), no item page is made, and mods.html says
the hub is not open yet. With the hub on, mods.html goes into the sitemap with the item pages.

Only the fields the pages show are kept, checked. The download address is made here from the
service's download host (an https origin that is not this site's) and the item's SHA-256 and
folder; the address the service sends is not used. Every text is the uploader's own: the pages
escape it (html.escape) and hub.js inserts it with textContent.
"""
import argparse
import html
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_gallery import Refused, Unreachable, clean_text, fetch, is_int  # noqa: E402

SITE = "https://forza.dyotson.dev"
ID_RE = re.compile(r"[A-Z2-7]{12}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
FOLDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
KINDS = {"mod": "Mod", "pack": "Texture pack"}
MAX_BYTES = 8 << 20
MAX_ITEMS = 5000
MAX_COUNT = 10 ** 12


def log(msg):
    print(f"hub: {msg}", flush=True)


def warn(msg):
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    print(f"{prefix}hub: {msg}", flush=True)


def base_of(url):
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc or "@" in u.netloc:
        raise Refused(f"HUB_URL must be an http(s) URL, not {url!r}")
    return f"{u.scheme}://{u.netloc}"


def downloads_of(value):
    """The download host's origin, or Refused: https (http only on this machine, for tests), no
    path, query or user, and not this site."""
    u = urlsplit(value) if isinstance(value, str) else None
    local = u is not None and u.hostname in ("127.0.0.1", "localhost")
    if (u is None or u.scheme not in (("https", "http") if local else ("https",)) or not u.hostname
            or u.path not in ("", "/") or u.query or u.fragment or "@" in u.netloc):
        raise Refused("the download host is not an https origin")
    if u.hostname == urlsplit(SITE).hostname:
        raise Refused("the download host is this site's host")
    return f"{u.scheme}://{u.netloc}"


def item_of(raw, downloads):
    if not isinstance(raw, dict):
        raise Refused("not an object")
    iid, kind, sha, folder = raw.get("id"), raw.get("kind"), raw.get("sha256"), raw.get("folder")
    if not (isinstance(iid, str) and ID_RE.fullmatch(iid)):
        raise Refused("bad id")
    if kind not in KINDS:
        raise Refused(f"{iid}: bad kind")
    if not (isinstance(sha, str) and SHA_RE.fullmatch(sha) and isinstance(folder, str) and FOLDER_RE.fullmatch(folder)):
        raise Refused(f"{iid}: bad sha256 or folder")
    for k in ("created", "published", "size", "files", "likes"):
        if not (is_int(raw.get(k)) and raw[k] <= MAX_COUNT):
            raise Refused(f"{iid}: bad {k}")
    title = clean_text(raw.get("title"), 60)
    if not title:
        raise Refused(f"{iid}: no title")
    description = raw.get("description") if isinstance(raw.get("description"), str) else ""
    description = "\n".join(filter(None, (clean_text(line, 1000) for line in description.split("\n"))))[:1000]
    return {"id": iid, "kind": kind, "title": title, "description": description,
            "version": clean_text(raw.get("version"), 20) or "", "author": clean_text(raw.get("author"), 32) or "",
            "created": raw["created"], "published": raw["published"], "size": raw["size"], "files": raw["files"],
            "sha256": sha, "folder": folder, "likes": raw["likes"],
            "download": f"{downloads}/{sha}/{folder}.zip"}


def read(url):
    try:
        data = fetch(url, MAX_BYTES, tries=2)
    except Refused as e:
        if e.status == 404:
            return None
        raise
    try:
        return json.loads(data)
    except ValueError as e:
        raise Refused(f"not JSON ({e})") from None


def size_text(n):
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} bytes"


def page(template, item):
    """The item's page: the mods.html navbar and footer around the item, every text escaped."""
    e = lambda v: html.escape(str(v), quote=True)  # noqa: E731
    kind = KINDS[item["kind"]]
    summary = (item["description"].split("\n")[0] if item["description"] else
               f"A {kind.lower()} for Forza Horizon 1: ReImagined, shared by {item['author'] or 'a player'}.")[:155]
    title = f"{item['title']} - {kind} - Forza Horizon 1: ReImagined"
    card = f'''<main class="board-page hub-page hub-item-page" id="main">
  <article class="panel hub-item" id="item-{e(item["id"])}" data-hub-item="{e(item["id"])}" data-hub-kind="{e(item["kind"])}">
    <p class="kicker"><a href="../mods.html">Mods and Textures</a> &middot; {e(kind)}</p>
    <h1>{e(item["title"])}{f' <span class="hub-version">{e(item["version"])}</span>' if item["version"] else ""}</h1>
    <p class="hub-meta">by <b>{e(item["author"] or "a player")}</b> &middot; {e(size_text(item["size"]))}, {e(item["files"])} files</p>
    {"".join(f"<p>{e(line)}</p>" for line in item["description"].split(chr(10)) if line) or ""}
    <p class="cta"><a class="btn primary" href="{e(item["download"])}" rel="noopener noreferrer"><span>Download</span><small>{e(item["folder"])}.zip, {e(size_text(item["size"]))}</small></a></p>
    <p class="hub-sha">SHA-256: <code>{e(item["sha256"])}</code></p>
    <div class="photo-foot hub-foot" data-hub-actions></div>
    <p class="aside">Add it in the launcher: <b>Mods</b> (or <b>Texture packs</b>) &gt; <b>Add</b>, and pick the downloaded zip. Mods and packs are made by players and checked for malware before they are shown here; report one that is broken, harmful or not the uploader's own work.</p>
  </article>
</main>
'''
    values = {"TITLE": e(title), "DESCRIPTION": e(summary), "CANONICAL": f"{SITE}/mods/{item['id']}.html"}
    out = re.sub(r"\{(TITLE|DESCRIPTION|CANONICAL)\}", lambda m: values[m.group(1)], template["head"])
    return out + template["header"] + "\n\n" + card + template["footer"]


def template_of(mods_html):
    """The pieces of mods.html an item page reuses, with every local address one folder up."""
    text = mods_html.read_text(encoding="utf-8")

    def up(chunk):
        return re.sub(r'(href|src)="(?!https?:|#|//|mailto:)([^"]+)"', r'\1="../\2"', chunk)
    head = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>document.documentElement.classList.add("js")</script>
<title>{{TITLE}}</title>
<meta name="description" content="{{DESCRIPTION}}">
<link rel="canonical" href="{{CANONICAL}}">
<meta name="theme-color" content="#140d2e">
<meta property="og:title" content="{{TITLE}}">
<meta property="og:description" content="{{DESCRIPTION}}">
<meta property="og:type" content="website">
<meta property="og:url" content="{{CANONICAL}}">
<meta property="og:image" content="{SITE}/images/social-preview.png">
<link rel="icon" href="../images/logo.svg" type="image/svg+xml">
<link rel="stylesheet" href="../style.css">
</head>
<body>
<a class="skip" href="#main">Skip to the content</a>
'''
    scene = text[text.index('<div class="scene"'):text.index('<header class="topbar">')]
    header = up(text[text.index('<header class="topbar">'):text.index("</header>") + len("</header>")])
    header = header.replace(' aria-current="page"', "")
    footer = up(text[text.index("<footer>"):text.index("</footer>") + len("</footer>")])
    scripts = '\n<script src="../nav.js"></script>\n<script src="../likes.js"></script>\n<script src="../hub.js"></script>\n</body>\n</html>\n'
    return {"head": head + "\n" + up(scene), "header": header, "footer": footer + "\n" + scripts}


def write(out, doc, on):
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "hub.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    pages = out / "mods"
    shutil.rmtree(pages, ignore_errors=True)
    mods_html = out / "mods.html"
    if not on:
        return
    template = template_of(mods_html)
    pages.mkdir()
    for item in doc["items"]:
        (pages / f"{item['id']}.html").write_text(page(template, item), encoding="utf-8")
    sitemap = out / "sitemap.xml"
    if sitemap.is_file():
        urls = [f"{SITE}/mods.html"] + [f"{SITE}/mods/{i['id']}.html" for i in doc["items"]]
        entries = "".join(f"  <url>\n    <loc>{html.escape(u)}</loc>\n    <changefreq>weekly</changefreq>\n"
                          f"    <priority>0.6</priority>\n  </url>\n" for u in urls)
        sm = sitemap.read_text(encoding="utf-8")
        sitemap.write_text(sm.replace("</urlset>", entries + "</urlset>"), encoding="utf-8")


def off():
    return {"version": 1, "generated": int(time.time()), "on": False, "items": []}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--require", action="store_true", help="exit 1 when HUB_URL is set but cannot be read")
    args = ap.parse_args()
    url = os.environ.get("HUB_URL", "").strip()
    if not url:
        log("HUB_URL is not set: no hub on this copy of the site")
        write(args.out, off(), False)
        return 0
    try:
        doc = read(base_of(url) + "/api/hub/items")
        if doc is None:
            log("the accounts service has no hub (HTTP 404)")
            write(args.out, off(), False)
            return 0
        if not (isinstance(doc, dict) and doc.get("version") == 1 and isinstance(doc.get("items"), list)):
            raise Refused("not a version 1 list")
        downloads = downloads_of(doc.get("downloads"))
    except (Refused, Unreachable) as e:
        write(args.out, off(), False)
        if args.require:
            print(f"::error::hub: {url} could not be read ({e})", flush=True)
            return 1
        warn(f"{url} could not be read ({e}): no hub in this build")
        return 0
    found, seen = [], set()
    for i, raw in enumerate(doc["items"][:MAX_ITEMS]):
        try:
            item = item_of(raw, downloads)
            if item["id"] in seen:
                raise Refused("listed twice")
        except Refused as e:
            warn(f"left out item {i}: {e}")
            continue
        seen.add(item["id"])
        found.append(item)
    write(args.out, {"version": 1, "generated": int(time.time()), "on": True, "items": found}, True)
    log(f"{len(found)} items, {sum(1 for i in found if i['kind'] == 'mod')} mods")
    return 0


if __name__ == "__main__":
    sys.exit(main())
