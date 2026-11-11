#!/usr/bin/env python3
"""Test tools/build_potm.py and the Photo of the Month pages against a stand-in for the accounts
service's /api/potm/ routes (forza-live, accounts/README.md, "Photo of the Month") served on
127.0.0.1, with a gallery of SYNTHETIC photos drawn here with Pillow (never commit a picture of the
game to this repository).

The build: only the fields the pages show, checked (ids, months, counts and times; anything else
the service sends by mistake, a XUID included, is dropped), every photo joined with the files the
gallery published, the newest winner featured only when its photo is in the gallery, last month's
standings asked for only when the contest ran then, and an unset, unreachable, broken or
not-running service (an empty file; --require exits 1 when it cannot be read). check_site.py
passes on what it writes and refuses a file that names photos the gallery does not list.

The pages, in a headless Chrome or Chromium (CHROME=path, else one on PATH or Playwright's):
- the home page shows the featured winner with the player's text as text, and makes no call to
  /api/; with no winner its section stays hidden;
- photo-of-the-month.html shows the live standings from /api/potm/standings when the service
  answers, and the built copy when it does not; each entry with its photo, votes, likes and share
  links that carry the page's address with the entry's anchor; last month's state (being checked,
  or its winner only); the past winners;
- the rules page says how old an account must be, from the built file, and marks the prize as a
  placeholder.

  python3 tools/test_build_potm.py   # needs Pillow (tools/requirements.txt)
"""
import copy
import functools
import html.parser
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from test_build_gallery import browser_args, find_browser, free_port  # noqa: E402

LEAK = "0009000000000001"   # a made-up XUID the service leaks by mistake
P1, P2, P3, P4, GONE = "POTMAAAAAAAA", "POTMBBBBBBBB", "POTMCCCCCCCC", "POTMDDDDDDDD", "POTMGONEAAAA"
OWNER = "0a1b2c3d4e5f"
NOV_START, DEC_START = 1793491200, 1796083200
OCT_START = 1790812800

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# ---- the fixture site: a gallery of synthetic photos ----

def jpeg(w, h, seed):
    im = Image.new("RGB", (w, h), (40 + seed * 30, 20, 90))
    ImageDraw.Draw(im).ellipse((w // 3, h // 4, 2 * w // 3, 3 * h // 4), fill=(255, 200, 120))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def make_gallery(site):
    (site / "photos").mkdir(exist_ok=True)
    photos = []
    for n, (pid, title, tag) in enumerate(((P1, '<img src=x onerror="alert(1)">', "<b>Shooter</b>"),
                                           (P2, "Dusk", "Second"), (P3, "", "Third"), (P4, "Old winner", "Fourth"))):
        (site / "photos" / f"{pid}.jpg").write_bytes(jpeg(640, 360, n))
        (site / "photos" / f"{pid}-thumb.jpg").write_bytes(jpeg(480, 270, n))
        p = {"id": pid, "title": title, "gamertag": tag, "likes": 3, "src": f"photos/{pid}.jpg", "w": 640, "h": 360,
             "thumb": f"photos/{pid}-thumb.jpg", "tw": 480, "th": 270}
        if pid == P1:
            p["owner"] = OWNER
        photos.append(p)
    (site / "data").mkdir(exist_ok=True)
    (site / "data" / "gallery.json").write_text(json.dumps({"version": 1, "generated": 1, "photos": photos}))


# ---- the stand-in accounts service ----

def standings(month, start, end, state, top, winner=None, entries=5):
    return {"version": 1, "month": month, "start": start, "end": end, "state": state, "accountDays": 14,
            "generated": start + 10, "entries": entries, "top": top, "winner": winner}


def entry(rank, pid, votes, likes, title="t", tag="g", **extra):
    return dict({"rank": rank, "id": pid, "title": title, "gamertag": tag, "votes": votes, "likes": likes,
                 "reached": NOV_START + 1000 * rank}, **extra)


def winner(month, pid, votes=9, title="Old winner", tag="Fourth", **extra):
    return dict({"month": month, "id": pid, "title": title, "gamertag": tag, "votes": votes,
                 "reached": OCT_START + 5, "confirmed": NOV_START + 5}, **extra)


class Service:
    def __init__(self):
        self.status = 200
        self.raw = None
        self.requests = []
        self.winners = {"version": 1, "generated": 1, "from": "2026-10", "winners": [
            winner("2026-10", P4, xuid=LEAK, owner=LEAK),
            winner("2026-10", P2),                                 # a second winner of the same month
            winner("1999-1", P2), winner("2026-09", "../../etc/x"), "junk"]}
        self.months = {
            None: standings("2026-11", NOV_START, DEC_START, "open", [
                entry(1, P1, 12, 15, '<img src=x onerror="alert(1)">', "<b>Shooter</b>", xuid=LEAK),
                entry(2, P2, 4, 4, "Dusk", "Second"),
                entry(3, P3, 1, 6, "", "Third")]),
            "2026-10": standings("2026-10", OCT_START, NOV_START, "confirmed", [entry(1, P4, 9, 11), entry(2, P2, 3, 3)],
                                 winner=winner("2026-10", P4)),
        }
        handler = functools.partial(ServiceHandler, self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def answer(self, path):
        self.requests.append(path)
        u = urllib.parse.urlsplit(path)
        if self.status != 200:
            return self.status, {"error": "not running"}
        if u.path == "/api/potm/winners":
            return 200, self.winners
        if u.path == "/api/potm/standings":
            month = urllib.parse.parse_qs(u.query).get("month", [None])[0]
            if month in self.months:
                return 200, self.months[month]
            return 404, {"error": "no such month"}
        return 404, {"error": "not found"}


class ServiceHandler(SimpleHTTPRequestHandler):
    def __init__(self, service, *a, **kw):
        self.service = service
        super().__init__(*a, **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        status, doc = self.service.answer(self.path)
        body = self.service.raw if self.service.raw is not None else json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SiteHandler(SimpleHTTPRequestHandler):
    """The assembled site, with /api/ passed to the stand-in service (or 404 without one)."""
    service = None
    api_calls = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/"):
            SiteHandler.api_calls.append((self.path, self.headers.get("Cookie")))
            if self.service is None:
                self.send_error(404)
                return
            status, doc = self.service.answer(self.path)
            body = json.dumps(doc).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


# ---- running the build ----

def build(site, url, *extra):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "POTM_URL")}
    if url is not None:
        env["POTM_URL"] = url
    p = subprocess.run([sys.executable, str(TOOLS / "build_potm.py"), str(site)] + list(extra), env=env,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def potm_doc(site):
    return json.loads((site / "data" / "photo-of-the-month.json").read_text(encoding="utf-8"))


def check_site(site):
    return subprocess.run([sys.executable, str(TOOLS / "check_site.py"), str(site)], capture_output=True, text=True)


def check_build(site, service):
    rc, log = build(site, service.url + "/api/likes/counts")
    check(rc == 0, "the build exits 0")
    doc = potm_doc(site)
    text = (site / "data" / "photo-of-the-month.json").read_text()
    check(set(doc) == {"version", "generated", "from", "featured", "winners", "months"} and doc["from"] == "2026-10",
          "version 1, the first contest month")
    check(LEAK not in text and "xuid" not in text.lower(), "no XUID and no 'xuid' anywhere")
    check([w["month"] for w in doc["winners"]] == ["2026-10"] and doc["winners"][0]["id"] == P4,
          "winners: one per month, the bad ones left out")
    check(set(doc["winners"][0]) == {"month", "id", "title", "gamertag", "votes", "reached", "confirmed", "src", "w", "h",
                                     "thumb", "tw", "th"}, "a winner: its fields and its photo's files, nothing else")
    f = doc["featured"]
    check(f and f["id"] == P4 and f["src"] == f"photos/{P4}.jpg" and f["w"] == 640, "the newest winner is featured, with its photo")
    check([m["month"] for m in doc["months"]] == ["2026-11", "2026-10"], "this month's and last month's standings")
    top = doc["months"][0]["top"]
    check([e["id"] for e in top] == [P1, P2, P3] and [e["rank"] for e in top] == [1, 2, 3], "the standings, in order")
    check(top[0]["title"] == '<img src=x onerror="alert(1)">' and top[0]["gamertag"] == "<b>Shooter</b>",
          "player text kept as written")
    check(top[0].get("owner") == OWNER and top[0]["thumb"] == f"photos/{P1}-thumb.jpg", "entries joined with the gallery")
    check(doc["months"][1]["winner"] == P4 and doc["months"][1]["state"] == "confirmed", "last month: its winner's id")
    check(any(p.startswith("/api/potm/standings?month=2026-10") for p in service.requests),
          "last month asked for by month")
    r = check_site(site)
    check(r.returncode == 0, f"check_site.py passes: {r.stdout.strip()}")


def check_refusals(site, service):
    print("-- what the build refuses or leaves out")
    saved = copy.deepcopy(service.months)
    service.months[None]["top"].insert(1, entry(4, "../../etc/pw", 1, 1))
    rc, log = build(site, service.url)
    top = potm_doc(site)["months"][0]["top"]
    check(rc == 0 and [(e["rank"], e["id"]) for e in top] == [(1, P1), (2, P2), (3, P3)],
          "an entry without an id: left out, the others ranked again")
    service.months = copy.deepcopy(saved)
    for bad in ({"votes": -1}, {"votes": True}, {"likes": 10 ** 12}, {"reached": "soon"}, {"id": P1}):
        service.months[None]["top"][1].update(bad)
        rc, _ = build(site, service.url)
        check(rc == 0 and [e["id"] for e in potm_doc(site)["months"][0]["top"]] == [P1, P3],
              f"an entry with {bad}: left out")
        service.months = copy.deepcopy(saved)
    for what, change in (("an unknown state", {"state": "won by bribes"}), ("no month", {"month": "../x"}),
                         ("bad times", {"end": 1}), ("version 2", {"version": 2})):
        service.months[None].update(change)
        rc, _ = build(site, service.url)
        doc = potm_doc(site)
        check(rc == 0 and doc["months"] == [] and doc["featured"]["id"] == P4,
              f"standings with {what}: left out, the winners still published")
        service.months = copy.deepcopy(saved)
    service.months[None]["top"][0]["title"] = "Two\nlines‮"
    rc, _ = build(site, service.url)
    check(potm_doc(site)["months"][0]["top"][0]["title"] == "Two lines", "control and bidi characters dropped")
    service.months = copy.deepcopy(saved)

    print("-- the newest winner's photo is gone from the gallery")
    service.winners["winners"].insert(0, winner("2026-11", GONE, title="Gone"))
    rc, log = build(site, service.url)
    doc = potm_doc(site)
    check(rc == 0 and doc["featured"] is None and doc["winners"][0]["id"] == GONE and "src" not in doc["winners"][0],
          "nothing featured (not an older winner), and the winner listed without files")
    check("not in the gallery" in log, "the log says why")
    check(check_site(site).returncode == 0, "check_site.py passes")
    service.winners["winners"].pop(0)

    print("-- the first contest month is this month: last month is not asked for")
    service.winners["from"] = "2026-11"
    service.requests.clear()
    rc, _ = build(site, service.url)
    check(not any("month=" in p for p in service.requests) and len(potm_doc(site)["months"]) == 1,
          "only this month's standings")
    service.winners["from"] = "2026-10"

    print("-- check_site.py refuses what the pages must not get")
    rc, _ = build(site, service.url)
    path = site / "data" / "photo-of-the-month.json"
    good = path.read_text()
    for what, change in (("an unlisted photo", lambda d: d["winners"][0].update(src="photos/POTMZZZZZZZZ.jpg")),
                         ("a XUID field", lambda d: d["months"][0]["top"][0].update(xuid=LEAK)),
                         ("an owner that is a XUID", lambda d: d["months"][0]["top"][0].update(owner=LEAK)),
                         ("a featured photo that is not the newest winner",
                          lambda d: d.update(featured=dict(d["months"][0]["top"][1], month="2026-10", confirmed=1)))):
        d = json.loads(good)
        change(d)
        path.write_text(json.dumps(d))
        check(check_site(site).returncode == 1, f"check_site.py refuses {what}")
    path.write_text(good)


def check_unavailable(site, service):
    print("-- no service, a service with no contest, a broken one")
    rc, log = build(site, None, "--require")
    check(rc == 0 and potm_doc(site)["from"] is None and potm_doc(site)["featured"] is None, "POTM_URL unset: exit 0, empty")
    service.status = 404
    rc, log = build(site, service.url, "--require")
    check(rc == 0 and potm_doc(site)["from"] is None, "no contest (404): exit 0 even with --require, empty")
    service.status = 503
    rc, _ = build(site, service.url)
    check(rc == 0 and potm_doc(site)["winners"] == [], "HTTP 503: empty, exit 0")
    rc, _ = build(site, service.url, "--require")
    check(rc == 1, "HTTP 503 with --require (scheduled runs): exit 1")
    service.status, service.raw = 200, b"<html>"
    rc, _ = build(site, service.url, "--require")
    check(rc == 1 and potm_doc(site)["winners"] == [], "not JSON with --require: exit 1, empty")
    service.raw = None
    rc, _ = build(site, f"http://127.0.0.1:{free_port()}")
    check(rc == 0 and potm_doc(site)["winners"] == [], "unreachable: empty, exit 0")
    rc, _ = build(site, "file:///etc/passwd")
    check(rc == 0 and potm_doc(site)["winners"] == [], "a URL that is not http(s): refused, empty")
    check(check_site(site).returncode == 0, "check_site.py passes on the empty file")


# ---- the pages ----

class Dom(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.texts, self.stack, self.inside = [], [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a, list(self.stack)))
        if tag not in ("img", "br", "input", "meta", "link", "source"):
            self.stack.append(a.get("id") or a.get("class") or tag)

    def handle_endtag(self, tag):
        if self.stack and tag not in ("img", "br", "input", "meta", "link", "source"):
            self.stack.pop()

    def handle_data(self, data):
        self.texts.append((data.strip(), list(self.stack)))


def dom_of(browser, url, profiles):
    profile = Path(tempfile.mkdtemp(dir=profiles))
    p = subprocess.run(browser_args(browser, profile) + ["--dump-dom", url], capture_output=True, text=True, timeout=120)
    d = Dom()
    d.feed(p.stdout)
    return d


def texts_in(d, where):
    return [t for t, stack in d.texts if t and where in stack]


def check_pages(browser, site, service, profiles):
    SiteHandler.service = None
    handler = functools.partial(SiteHandler, directory=str(site))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        SiteHandler.api_calls.clear()
        index = dom_of(browser, base + "/index.html", profiles)
        section = next((a for t, a, _ in index.tags if t == "section" and a.get("id") == "potm"), None)
        check(section is not None and "hidden" not in section, "home page: the Photo of the Month section is shown")
        check(any(t == "button" and a.get("data-full") == f"photos/{P4}.jpg" for t, a, _ in index.tags),
              "home page: the featured winner's photo")
        check("October 2026" in texts_in(index, "potm"), "home page: the month it won")
        check(SiteHandler.api_calls == [], "home page: no call to /api/")

        print("-- the leaderboard without the service: the built copy")
        page = dom_of(browser, base + "/photo-of-the-month.html", profiles)
        entries = [a["id"] for t, a, _ in page.tags if t == "li" and a.get("class") == "potm-entry"]
        check(entries[:3] == [f"photo-{P1}", f"photo-{P2}", f"photo-{P3}"], "this month's standings, in order")
        now = texts_in(page, "potm-now")
        check("November 2026" in now and "12 votes" in now, "the month's name, and the votes")
        check('<img src=x onerror="alert(1)">' in now and "<b>Shooter</b>" in now, "player text as text")
        check(not any(t == "img" and ("onerror" in a or a.get("src") == "x") for t, a, _ in page.tags),
              "the player's markup is not turned into elements")
        shares = [a["href"] for t, a, stack in page.tags if t == "a" and a.get("class") == "potm-share-btn"
                  and f"photo-{P1}" in stack]
        want = urllib.parse.quote(base + "/photo-of-the-month.html#photo-" + P1, safe="")
        check(len(shares) == 4 and all(want in h for h in shares), "four share links with the entry's address, encoded")
        check(all(h.startswith(("https://twitter.com/", "https://bsky.app/", "https://www.reddit.com/",
                                "https://www.facebook.com/")) for h in shares), "share links go to the networks only")
        check(all(a.get("rel") == "noopener noreferrer" and a.get("target") == "_blank"
                  for t, a, _ in page.tags if t == "a" and a.get("class") == "potm-share-btn"), "share links: noopener")
        likes = [a for t, a, stack in page.tags if t == "a" and "like" in a.get("class", "").split() and "potm-now" in stack]
        check(len(likes) == 3 and all(a.get("href") == "account.html" for a in likes), "a like button on each entry")
        before = texts_in(page, "potm-before")
        check("October 2026" in before and any("winner is confirmed" in t for t in before), "last month: its winner is confirmed")
        check([a["id"] for t, a, stack in page.tags if t == "li" and "potm-before" in stack] == [f"photo-{P4}"],
              "last month: only its winner")
        check(any(t == "li" and a.get("id") == "2026-10" for t, a, _ in page.tags), "past winners: October")
        calls = [c for c, _ in SiteHandler.api_calls]
        check("/api/potm/standings" in calls, "it asks for the live standings")

        print("-- the leaderboard with the service: live")
        SiteHandler.service = service
        service.months[None]["top"] = [entry(1, P2, 20, 21, "Dusk", "Second"), entry(2, P1, 12, 15)]
        SiteHandler.api_calls.clear()
        page = dom_of(browser, base + "/photo-of-the-month.html", profiles)
        entries = [a["id"] for t, a, _ in page.tags if t == "li" and a.get("class") == "potm-entry"]
        check(entries[:2] == [f"photo-{P2}", f"photo-{P1}"], "the live standings replace the built ones")
        now = texts_in(page, "potm-now")
        check("20 votes" in now and any(t.endswith("21 likes in all") for t in now),
              "votes and likes from the live answer")
        check(all(cookie is None for _, cookie in SiteHandler.api_calls), "no cookie sent to the public standings")

        print("-- last month being checked")
        service.months["2026-10"]["state"] = "verifying"
        service.months["2026-10"]["winner"] = None
        page = dom_of(browser, base + "/photo-of-the-month.html", profiles)
        before = texts_in(page, "potm-before")
        check(any("checking the votes" in t for t in before), "last month: being checked")
        check(len([1 for t, a, stack in page.tags if t == "li" and "potm-before" in stack]) == 2, "with its standings")

        rules = dom_of(browser, base + "/photo-of-the-month-rules.html", profiles)
        days = [t for t, stack in rules.texts if t == "14"]
        check(len(days) >= 1 and "October 2026" in [t for t, _ in rules.texts], "rules: the account age and the first month")
        check(any(t == "p" and a.get("class") == "potm-placeholder" for t, a, _ in rules.tags), "rules: the prize is a placeholder")
        check(any("Entering and voting are free" in t for t, _ in rules.texts), "rules: entering and voting are free")

        (site / "data" / "photo-of-the-month.json").write_text(json.dumps(
            {"version": 1, "generated": 1, "from": None, "featured": None, "winners": [], "months": []}))
        SiteHandler.service = None
        index = dom_of(browser, base + "/index.html", profiles)
        section = next((a for t, a, _ in index.tags if t == "section" and a.get("id") == "potm"), None)
        check(section is not None and "hidden" in section, "no winner: the home page hides the section")
        page = dom_of(browser, base + "/photo-of-the-month.html", profiles)
        check(any("has not started" in t for t in texts_in(page, "potm-now")), "no contest: the page says so")
    finally:
        httpd.shutdown()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="potm-test-"))
    service = Service()
    try:
        site = tmp / "site"
        subprocess.run([str(TOOLS / "assemble.sh"), str(site)], check=True)
        make_gallery(site)
        print("-- a build against the stand-in service")
        check_build(site, service)
        check_refusals(site, service)
        check_unavailable(site, service)
        print("-- the pages, in a browser")
        service.status, service.raw = 200, None
        rc, _ = build(site, service.url)
        browser = find_browser()
        if browser:
            print(f"     {browser}")
            (tmp / "profiles").mkdir()
            check_pages(browser, site, service, tmp / "profiles")
        elif os.environ.get("CI"):
            check(False, "a browser (set CHROME=path)")
        else:
            print("skip the page checks: no Chrome or Chromium found (set CHROME=path)")
        print(f"\n{len(failures)} failed" if failures else "\nall passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
