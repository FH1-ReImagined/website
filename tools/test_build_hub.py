#!/usr/bin/env python3
"""Test tools/build_hub.py and the hub's pages (mods.html, mods/<id>.html, hub.js) against a
stand-in for the accounts service's /api/hub/ (forza-live, accounts/README.md, "Mods and texture
packs hub") served on 127.0.0.1.

The build: the hub off (HTTP 404) or unset gives "on": false, no item pages and mods.html keeps
no sitemap entry; on, only the checked fields, the download address made from the download host and
the item's SHA-256 and folder (never the one the service sends), the download host refused when
it is not https or is this site, one page per item with the uploader's text escaped, and the
sitemap entries. check_site.py passes, and refuses pages that do not match the list.

The pages, in a headless Chrome or Chromium: the list with the uploaders' text as text, the
download links and SHA-256s, the filter, no call to /api/ for a visitor who did not sign in; for
one who did, the upload (POST /api/hub/items, then the zip as the body of a PUT with the CSRF
header), "Your uploads" with their states and reasons, and a report from an item's page; when
/api/hub/mine refuses as the service does (403 not linked, 409 on hold, 409 no speed trap with
its message), no upload form and the share note says why.

  python3 tools/test_build_hub.py   # needs Pillow (tools/requirements.txt), for build_gallery's import
"""
import functools
import html
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from test_build_gallery import browser_args, find_browser  # noqa: E402

failures = []
SHA_A, SHA_B = "a" * 64, "b" * 64
EVIL = '<script>alert(1)</script><img src=x onerror="alert(2)">'
ITEMS = [
    {"id": "MODAAAAAAAAA", "kind": "mod", "title": EVIL, "description": "First line " + EVIL + "\nSecond line",
     "version": "1.2", "author": "<b>Sam</b>", "created": 1, "published": 2, "size": 2048, "files": 3,
     "sha256": SHA_A, "folder": "Better-title", "likes": 4, "download": "https://evil.example/steal.exe",
     "xuid": "0009000000000001"},
    {"id": "PACKBBBBBBBB", "kind": "pack", "title": "HD roads", "description": "", "version": "", "author": "Ann",
     "created": 3, "published": 4, "size": 5 << 20, "files": 200, "sha256": SHA_B, "folder": "HD-roads", "likes": 0,
     "download": "x"},
    {"id": "../../etc/pw", "kind": "mod", "title": "x", "sha256": SHA_A, "folder": "x", "created": 1, "published": 1,
     "size": 1, "files": 1, "likes": 0},
    {"id": "BADSHAAAAAAA", "kind": "mod", "title": "x", "sha256": "../x", "folder": "x", "created": 1, "published": 1,
     "size": 1, "files": 1, "likes": 0},
    {"id": "BADKINDAAAAA", "kind": "exe", "title": "x", "sha256": SHA_A, "folder": "x", "created": 1, "published": 1,
     "size": 1, "files": 1, "likes": 0},
]


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# What forza-live accounts/members/likes.py sends for a key the game server forgot.
KEY_FORGOTTEN = ("The Public server no longer knows your game's key: play on it again (drive through any speed trap "
                 "camera), then try again.")


class Api:
    mine_refusal = None  # (status, doc) GET /api/hub/mine answers instead, when set
    status = 200
    downloads = "https://downloads.example"
    items = ITEMS
    calls, posts, puts = [], [], []
    mine = []


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, doc):
        body = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.path.startswith("/api/"):
            return super().do_GET()
        Api.calls.append(self.path)
        if self.path == "/api/hub/items":
            if Api.status != 200:
                return self.reply(Api.status, {"error": "not available"})
            return self.reply(200, {"version": 1, "generated": 1, "downloads": Api.downloads, "items": Api.items})
        if self.path == "/api/hub/mine":
            if Api.mine_refusal:
                return self.reply(*Api.mine_refusal)
            return self.reply(200, {"items": Api.mine, "max_bytes": 1 << 20})
        if self.path.startswith("/api/likes/"):
            return self.reply(401, {"detail": "Unauthorized"})
        if self.path.startswith("/api/auth/browser/v1/config"):
            return self.reply(200, {"status": 200})
        return self.reply(404, {"status": 404})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Api.posts.append((self.path, body, "X-CSRFToken" in self.headers))
        if self.path == "/api/hub/items":
            return self.reply(200, {"id": "NEWAAAAAAAAA", "state": "draft", "upload": "/api/hub/items/NEWAAAAAAAAA/file",
                                    "max_bytes": 1 << 20})
        if self.path == "/api/hub/reports":
            return self.reply(200, {"ok": True})
        return self.reply(404, {"error": "not found"})

    def do_PUT(self):
        data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Api.puts.append((self.path, self.headers.get("Content-Type"), "X-CSRFToken" in self.headers, data))
        Api.mine = [{"id": "NEWAAAAAAAAA", "kind": "mod", "title": "<i>Mine</i>", "version": "", "state": "queued",
                     "reason": "", "created": 1, "size": len(data), "sha256": "", "likes": 0},
                    {"id": "OLDAAAAAAAAA", "kind": "pack", "title": "Old", "version": "", "state": "refused",
                     "reason": "It was refused: \"x.exe\": .exe files are not allowed.", "created": 0, "size": 1,
                     "sha256": "", "likes": 0}]
        return self.reply(200, {"ok": True, "state": "queued"})


def build(site, url, *extra):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "HUB_URL")}
    if url:
        env["HUB_URL"] = url
    p = subprocess.run([sys.executable, str(TOOLS / "build_hub.py"), str(site)] + list(extra), env=env,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def hub_doc(site):
    return json.loads((site / "data" / "hub.json").read_text())


def check_site(site):
    return subprocess.run([sys.executable, str(TOOLS / "check_site.py"), str(site)], capture_output=True, text=True)


class Dom(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.texts = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        if data.strip():
            self.texts.append(data.strip())


def dump(browser, url, profiles):
    profile = Path(tempfile.mkdtemp(dir=profiles))
    p = subprocess.run(browser_args(browser, profile) + ["--dump-dom", url], capture_output=True, text=True, timeout=120)
    d = Dom()
    d.feed(p.stdout)
    d.raw = p.stdout
    return d


SIGNED_IN = r"""
try { localStorage.setItem("fh-signed-in", "1"); } catch (e) {}
window.confirm = function () { return true; };
"""
UPLOAD_TEST = r"""
setTimeout(function () {
  var form = document.getElementById("hub-form");
  var out = { formShown: !form.hidden };
  form.elements.kind.value = "mod";
  form.elements.title.value = "Better title";
  form.elements.version.value = "1.2";
  form.elements.description.value = "A new title screen";
  form.elements.own_work.checked = true;
  var dt = new DataTransfer();
  dt.items.add(new File([new Uint8Array([0x50, 0x4b, 3, 4, 1, 2, 3])], "better.zip", { type: "application/zip" }));
  form.elements.file.files = dt.files;
  form.requestSubmit();
  setTimeout(function () {
    out.message = document.querySelector("[data-hub-message]").textContent;
    out.mine = Array.prototype.map.call(document.querySelectorAll("[data-hub-mine] li"), function (li) { return li.textContent; });
    var pre = document.createElement("pre"); pre.id = "test-results"; pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }, 1500);
}, 800);
"""
MINE_TEST = r"""
setTimeout(function () {
  var note = document.querySelector("[data-hub-share-note]");
  var how = note.querySelector("a[href='faq.html#q10']");
  var out = { formShown: !document.getElementById("hub-form").hidden, note: note.hidden ? null : note.textContent,
              how: !!how };
  var pre = document.createElement("pre"); pre.id = "test-results"; pre.textContent = JSON.stringify(out);
  document.body.append(pre);
}, 1500);
"""
REPORT_TEST = r"""
setTimeout(function () {
  var b = Array.prototype.filter.call(document.querySelectorAll("[data-hub-actions] button"), function (x) { return x.textContent === "Report"; })[0];
  var out = { report: !!b };
  if (b) {
    b.click();
    var f = document.querySelector(".hub-report");
    f.elements.reason.value = "malware";
    f.elements.note.value = "it runs a program";
    f.requestSubmit();
  }
  setTimeout(function () {
    var s = document.querySelector(".hub-report-status");
    out.status = s ? s.textContent : null;
    var pre = document.createElement("pre"); pre.id = "test-results"; pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }, 800);
}, 800);
"""


def results(d):
    m = re.search(r'<pre id="test-results">(.*?)</pre>', d.raw, re.S)
    return json.loads(html.unescape(m.group(1))) if m else {}


def check_pages(browser, site, profiles):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        Api.calls.clear()
        page = dump(browser, base + "/mods.html", profiles)
        cards = [a["id"] for t, a in page.tags if t == "li" and a.get("class") == "hub-card"]
        check(cards == ["item-MODAAAAAAAAA", "item-PACKBBBBBBBB"], "mods.html lists the two good items")
        check(EVIL in page.texts and "<b>Sam</b>" in " ".join(page.texts), "the uploader's text as text")
        check(not any(t == "script" and "alert" in str(a) for t, a in page.tags) and
              not any(t == "img" and "onerror" in a for t, a in page.tags), "no markup from the uploader's text")
        downloads = [a["href"] for t, a in page.tags if t == "a" and "hub-download" in a.get("class", "")]
        check(downloads == [f"https://downloads.example/{SHA_A}/Better-title.zip",
                            f"https://downloads.example/{SHA_B}/HD-roads.zip"], "downloads from the download host")
        check(SHA_A in page.texts and SHA_B in page.texts, "each SHA-256 shown")
        check(any(t == "a" and a.get("href") == "mods/MODAAAAAAAAA.html" for t, a in page.tags), "a link to each item's page")
        check(Api.calls == [], "no call to /api/ for a visitor who did not sign in")
        share = next((a for t, a in page.tags if t == "section" and a.get("id") == "hub-share"), {})
        check("hidden" not in share, "the share panel says how to share")

        item = dump(browser, base + "/mods/MODAAAAAAAAA.html", profiles)
        check(EVIL in item.texts and not any(t == "img" and "onerror" in a for t, a in item.tags),
              "the item page: the title as text")
        check(any(t == "a" and a.get("href") == f"https://downloads.example/{SHA_A}/Better-title.zip" for t, a in item.tags),
              "the item page: its download")
        likes = [a for t, a in item.tags if t == "a" and "like" in a.get("class", "").split()]
        check(len(likes) == 1 and likes[0].get("href") == "account.html", "the item page: its like total")
        check(any(t == "link" and a.get("href") == "../style.css" for t, a in item.tags), "the item page's addresses are one folder up")

        print("-- a visitor who signed in")
        (site / "hub-setup.js").write_text(SIGNED_IN)
        (site / "hub-test.js").write_text(UPLOAD_TEST)
        text = (site / "mods.html").read_text()
        text = text.replace('<script src="likes.js"></script>', '<script src="hub-setup.js"></script>\n<script src="likes.js"></script>')
        text = text.replace('<script src="hub.js"></script>', '<script src="hub.js"></script>\n<script src="hub-test.js"></script>')
        (site / "hub-test.html").write_text(text)
        Api.posts, Api.puts, Api.mine = [], [], []
        r = results(dump(browser, base + "/hub-test.html", profiles))
        check(r.get("formShown") is True, "the upload form is shown")
        check(Api.posts[:1] == [("/api/hub/items", {"kind": "mod", "title": "Better title", "version": "1.2",
                                                   "description": "A new title screen", "own_work": True}, True)],
              "POST /api/hub/items with the CSRF header")
        check(Api.puts and Api.puts[0][:3] == ("/api/hub/items/NEWAAAAAAAAA/file", "application/zip", True)
              and Api.puts[0][3] == bytes([0x50, 0x4b, 3, 4, 1, 2, 3]), "the zip as the body of the PUT, with the CSRF header")
        check("Uploaded" in (r.get("message") or ""), "it says it was uploaded and is being checked")
        mine = r.get("mine") or []
        check(len(mine) == 2 and "<i>Mine</i>" in mine[0] and "Waiting for the checks" in mine[0]
              and ".exe files are not allowed" in mine[1], "Your uploads: states and reasons, as text")

        (site / "mine-test.js").write_text(MINE_TEST)
        (site / "mine-test.html").write_text(text.replace('<script src="hub-test.js"></script>', '<script src="mine-test.js"></script>'))
        for refusal, says, how, what in (
                ((403, {"error": "link your game first"}), "Online > Online > Website account", True, "403 link your game first"),
                ((409, {"error": "this link needs the operator"}), "on hold", False, "409 this link needs the operator"),
                ((409, {"error": "play online first", "message": KEY_FORGOTTEN}), KEY_FORGOTTEN, True,
                 "409 play online first, with its message"),
                ((403, {"error": "not available"}), "banned", False, "403 not available (a banned game)")):
            Api.mine_refusal = refusal
            r = results(dump(browser, base + "/mine-test.html", profiles))
            check(r.get("formShown") is False and says in (r.get("note") or "") and r.get("how") is how,
                  "Your uploads refused (" + what + "): no form, and the note says why")
        Api.mine_refusal = None

        (site / "mods" / "report-test.js").write_text(REPORT_TEST)
        text = (site / "mods" / "MODAAAAAAAAA.html").read_text()
        text = text.replace('<script src="../likes.js"></script>', '<script src="../hub-setup.js"></script>\n<script src="../likes.js"></script>')
        text = text.replace('<script src="../hub.js"></script>', '<script src="../hub.js"></script>\n<script src="report-test.js"></script>')
        (site / "mods" / "report-test.html").write_text(text)
        Api.posts = []
        r = results(dump(browser, base + "/mods/report-test.html", profiles))
        check(r.get("report") is True and Api.posts == [("/api/hub/reports", {"item": "MODAAAAAAAAA", "reason": "malware",
                                                                             "note": "it runs a program"}, True)],
              "an item's page: a report with its reason and note")
        check("Thank you" in (r.get("status") or ""), "and it says thank you")
    finally:
        httpd.shutdown()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="hub-test-"))
    api = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp)))
    threading.Thread(target=api.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{api.server_address[1]}/api/likes/counts"
    try:
        site = tmp / "site"
        subprocess.run([str(TOOLS / "assemble.sh"), str(site)], check=True)
        check('content="noindex"' not in (site / "mods.html").read_text(), "mods.html is for search engines")

        print("-- the hub off, or no HUB_URL")
        Api.status = 404
        rc, _ = build(site, url, "--require")
        check(rc == 0 and hub_doc(site)["on"] is False and not (site / "mods").exists(), "off (404): on false, no pages")
        check("mods.html" not in (site / "sitemap.xml").read_text(), "and no sitemap entry for it")
        check(check_site(site).returncode == 0, "check_site.py passes")
        rc, _ = build(site, None)
        check(rc == 0 and hub_doc(site)["on"] is False, "HUB_URL unset: on false")
        Api.status = 503
        check(build(site, url, "--require")[0] == 1 and build(site, url)[0] == 0, "HTTP 503: exit 1 only with --require")

        print("-- the download host")
        Api.status = 200
        for bad in ("http://downloads.example", "https://forza.dyotson.dev", "https://downloads.example/files",
                    "https://user@downloads.example", "javascript:alert(1)", None):
            Api.downloads = bad
            rc, _ = build(site, url)
            check(rc == 0 and hub_doc(site)["on"] is False, f"refused: {bad}")
        Api.downloads = "https://downloads.example"

        print("-- the hub on")
        rc, log = build(site, url)
        doc = hub_doc(site)
        check(rc == 0 and doc["on"] is True and [i["id"] for i in doc["items"]] == ["MODAAAAAAAAA", "PACKBBBBBBBB"],
              "the two good items, the bad ones left out")
        text = (site / "data" / "hub.json").read_text()
        check("xuid" not in text and "0009000000000001" not in text and "steal.exe" not in text,
              "no XUID, and not the download address the service sent")
        check(doc["items"][0]["download"] == f"https://downloads.example/{SHA_A}/Better-title.zip", "the download made here")
        check(sorted(p.name for p in (site / "mods").iterdir()) == ["MODAAAAAAAAA.html", "PACKBBBBBBBB.html"],
              "one page per item")
        page = (site / "mods" / "MODAAAAAAAAA.html").read_text()
        sys.path.insert(0, str(TOOLS))
        import check_site as cs
        check(all(cs.markup_from_text(t) for t in ('<p onclick="x">', "<script>alert(1)</script>", "<iframe src=x>",
                                                   '<script src="https://evil.example/x.js"></script>')),
              "check_site.py finds an event handler, a script or a frame")
        check(not cs.markup_from_text(page) and "&lt;script&gt;alert(1)&lt;/script&gt;" in page,
              "the item page escapes the uploader's text")
        check(re.search(r"<title>&lt;script&gt;.* - Mod - Forza Horizon 1: ReImagined</title>", page) is not None
              and '<link rel="canonical" href="https://forza.dyotson.dev/mods/MODAAAAAAAAA.html">' in page,
              "its own title and canonical address")
        sitemap = (site / "sitemap.xml").read_text()
        check(all(f"https://forza.dyotson.dev/{p}" in sitemap for p in ("mods.html", "mods/MODAAAAAAAAA.html",
                                                                         "mods/PACKBBBBBBBB.html")), "the sitemap lists them")
        r = check_site(site)
        check(r.returncode == 0, f"check_site.py passes: {r.stdout.strip()}")
        (site / "mods" / "EXTRAAAAAAAA.html").write_text("<html></html>")
        check(check_site(site).returncode == 1, "check_site.py refuses a page that is not in the list")
        (site / "mods" / "EXTRAAAAAAAA.html").unlink()

        browser = find_browser()
        if browser:
            print(f"     {browser}")
            (tmp / "profiles").mkdir()
            check_pages(browser, site, tmp / "profiles")
        elif os.environ.get("CI"):
            check(False, "a browser (set CHROME=path)")
        else:
            print("skip the page checks: no Chrome or Chromium found (set CHROME=path)")
    finally:
        api.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
