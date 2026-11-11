#!/usr/bin/env python3
"""Test the landing page's "N players online now" (online.js) in a headless Chrome or Chromium
(CHROME=path, else one on PATH or Playwright's), against a stand-in for the site's
/live/community/online.json route: hidden with no route, an error, a timeout, a malformed, stale
or future feed (by the answer's Date, plus its Age from a cache; the visitor's clock without a
Date), or nobody online; shown to a visitor whose clock is an hour off; "1 player online now" and
"1,234 players online now · 5 racing" when there are players; a refresh that changes the count
changes the text, one that does not leaves it alone (no new announcement); a refresh every
minute, none while the tab is hidden and one as soon as it is shown; and, on a desktop and a
phone, nothing under the badge moves when it appears and its hidden line is only a little room.

  python3 tools/test_online_count.py                    # the checks
  python3 tools/test_online_count.py --screenshot FILE  # also a picture of the landing page with a count
                                                        # (SHOT_SIZE=390,844 for a phone; default 1440,900)
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from test_account_page import find_browser  # noqa: E402

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def feed(players, racing=0, worlds=None, age=0, **extra):
    doc = {"version": 1, "generated": int(time.time()) - age, "players": players,
           "worlds": (1 if players else 0) if worlds is None else worlds, "racing": racing}
    doc.update(extra)
    return doc


SLOW, HOLD = 7, 9  # seconds: the slow feed answers after the page's 5 s timeout, the page is read after that
GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


class Opts:
    """An answer (as Feed takes it) sent with other headers: date None for no Date, else the
    Date that many seconds from now; age an Age header; length False for no Content-Length (the
    body ends when the connection closes)."""
    def __init__(self, answer, date=0, age=None, length=True):
        self.answer, self.date, self.age, self.length = answer, date, age, length


class Feed:
    """What /live/community/online.json answers, one entry per request (the last one repeats):
    None for a 404 page (a copy of the site without the route), "slow" for an answer after the
    page's timeout, (status, body, content type), a dict sent as JSON, or one of those in Opts."""
    answers = [None]
    calls = dropped = 0


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/hold-load.gif":
            time.sleep(HOLD)
            return self.reply(200, GIF, "image/gif")
        if not self.path.startswith("/live/"):
            return super().do_GET()
        if self.path != "/live/community/online.json":
            return self.reply(404, b"{}", "application/json")
        a = Feed.answers[min(Feed.calls, len(Feed.answers) - 1)]
        Feed.calls += 1
        opts = a if isinstance(a, Opts) else Opts(a)
        a = opts.answer
        if a is None:
            return self.reply(404, b"<html>Not Found</html>", "text/html")
        if a == "slow":
            time.sleep(SLOW)
            a = feed(5)
        if isinstance(a, dict):
            return self.reply(200, json.dumps(a).encode(), "application/json", opts)
        return self.reply(*a, opts=opts)

    def reply(self, code, body, ctype, opts=None):
        opts = opts or Opts(None)
        self.log_request(code)
        self.send_response_only(code)  # send_response would add a Date of its own
        self.send_header("Server", self.version_string())
        if opts.date is not None:
            self.send_header("Date", self.date_time_string(time.time() + opts.date))
        if opts.age is not None:
            self.send_header("Age", str(opts.age))
        self.send_header("Content-Type", ctype)
        if opts.length:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            Feed.dropped += 1  # the page gave up on the request


# Before online.js: with "hiddenTab" in the plan, the tab starts hidden until a "v" step; with
# "skew<seconds>", the visitor's clock (Date.now) is that far off.
SETUP = r"""
(function () {
  var skew = /(?:^#|,)skew(-?\d+)/.exec(location.hash);
  if (skew) {
    var now = Date.now.bind(Date), off = Number(skew[1]) * 1000;
    Date.now = function () { return now() + off; };
  }
  if (location.hash.indexOf("hiddenTab") < 0) return;
  window.__hidden = true;
  Object.defineProperty(document, "hidden", { configurable: true, get: function () { return window.__hidden; } });
})();
"""
# After online.js: runs the plan in the hash (comma-separated): w<ms> waits, r refreshes,
# v shows a hidden tab, p records what parse makes of a feed with more worlds than players;
# each step records the badge into <pre id="test-results">.
HARNESS = r"""
(function () {
  var b = document.getElementById("online-now"), t = b.querySelector(".live-text");
  var note = document.querySelector(".hero-note"), down = document.querySelector(".hero .cta");
  var changes = 0;
  new MutationObserver(function (l) { changes += l.length; }).observe(t, { childList: true, characterData: true, subtree: true });
  function state(step) {
    var n = note.getBoundingClientRect(), d = down.getBoundingClientRect(), p = b.getBoundingClientRect();
    return { step: step, hidden: b.hidden, text: t.textContent, visibility: getComputedStyle(b).visibility,
             at: [n.top, d.top], gap: n.top - d.bottom,
             pill: [p.top - d.bottom, n.top - p.bottom], width: innerWidth,
             changes: changes, live: b.parentNode.getAttribute("aria-live") };
  }
  var plan = location.hash.slice(1).split(",").filter(function (s) { return s && s !== "hiddenTab" && s.indexOf("skew") !== 0; }), out = [];
  function done() {
    var pre = document.createElement("pre");
    pre.id = "test-results";
    pre.textContent = JSON.stringify(out);
    document.body.append(pre);
  }
  function next(i) {
    if (i >= plan.length) return done();
    var s = plan[i];
    if (s.charAt(0) === "w") return setTimeout(function () { out.push(state(s)); next(i + 1); }, Number(s.slice(1)));
    if (s === "r") return window.FHOnline.refresh().then(function () { out.push(state(s)); next(i + 1); });
    if (s === "p") {
      var g = Math.floor(Date.now() / 1000);
      out.push({ step: s, parsed: window.FHOnline.parse({ version: 1, generated: g, players: 3, worlds: 4, racing: 1 }, g) });
      return next(i + 1);
    }
    if (s === "v") {
      window.__hidden = false;
      document.dispatchEvent(new Event("visibilitychange"));
      return setTimeout(function () { out.push(state(s)); next(i + 1); }, 1500);
    }
    out.push({ step: s, error: "unknown step" });
    next(i + 1);
  }
  next(0);
})();
"""


# A headless window is at least 500px wide, so a phone's page runs in a frame of its size, and
# this page copies the frame's records out for the dump.
FRAME = r"""<!doctype html><meta charset="utf-8"><title>frame</title><style>body{margin:0}iframe{border:0}</style>
<script>
var size = location.search.slice(1).split(","), f = document.createElement("iframe");
f.width = size[0]; f.height = size[1]; f.src = "online-test.html" + location.hash;
document.documentElement.append(f);
(function poll() {
  var pre = f.contentDocument && f.contentDocument.getElementById("test-results");
  if (!pre) return setTimeout(poll, 100);
  var copy = document.createElement("pre");
  copy.id = "test-results";
  copy.textContent = pre.textContent;
  document.documentElement.append(copy);
})();
</script>"""


def assemble(dest):
    subprocess.run([str(TOOLS / "assemble.sh"), str(dest)], check=True)


def headless(browser, profile, extra, url, budget):
    args = [browser, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={tempfile.mkdtemp(dir=profile)}"] + ([f"--virtual-time-budget={budget}"] if budget else []) + extra + [url]
    if "headless-shell" not in browser:
        args.insert(1, "--headless=new")
    return subprocess.run(args, capture_output=True, text=True, timeout=180)


def run(browser, base, profile, answers, plan, budget=15000, real_time=False, size="1440,900"):
    """The plan's records, in a window of size (w,h). Virtual time stands still while a request
    is open, so a test of the page's own timeout runs in real time instead, on a copy of the page
    whose load (and so the dump) waits for a picture the stand-in sends after HOLD seconds."""
    Feed.answers, Feed.calls, Feed.dropped = answers, 0, 0
    page = "online-test-hold.html" if real_time else "online-test.html"
    if int(size.split(",")[0]) < 500:
        page = f"online-frame.html?{size}"
    p = headless(browser, profile, ["--dump-dom", f"--window-size={size}"], f"{base}/{page}#{plan}",
                 None if real_time else budget)
    m = re.search(r'<pre id="test-results">(.*?)</pre>', p.stdout, re.S)
    return json.loads(html.unescape(m.group(1))) if m else []


def test_pages(tmp, screenshot):
    browser = find_browser()
    if not browser:
        if os.environ.get("CI"):
            check(False, "a browser (set CHROME=path)")
        else:
            print("skip: no Chrome or Chromium found (set CHROME=path)")
        return
    site = tmp / "site"
    assemble(site)
    index = (site / "index.html").read_text(encoding="utf-8")
    tag = '<script src="online.js"></script>'
    check(index.count(tag) == 1, "index.html loads online.js")
    page = index.replace(tag, f"<script>{SETUP}</script>{tag}<script>{HARNESS}</script>")
    (site / "online-test.html").write_text(page, encoding="utf-8")
    hold = page.replace("</body>", '<img src="/hold-load.gif" alt="" width="1" height="1"></body>')
    (site / "online-test-hold.html").write_text(hold, encoding="utf-8")
    (site / "online-frame.html").write_text(FRAME, encoding="utf-8")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        print("-- hidden when there is nothing to show")
        for name, skew, answers in (("no route (404)", "", [None]), ("a server error", "", [(500, b"{}", "application/json")]),
                                    ("nobody online", "", [feed(0)]), ("a stale feed (6 minutes old)", "", [feed(7, age=360)]),
                                    ("a feed from the future", "", [feed(7, age=-1000)]),
                                    ("a stale feed to a visitor an hour ahead", "skew3600,", [feed(7, age=360)]),
                                    ("a stale copy from a cache (its Date + Age)", "", [Opts(feed(7, age=360), date=-360, age=360)]),
                                    ("no Date: a stale feed", "", [Opts(feed(7, age=360), date=None)]),
                                    ("no Date: a fresh feed to a visitor an hour ahead (their clock is all there is)", "skew3600,",
                                     [Opts(feed(7), date=None)])):
            r = run(browser, base, tmp, answers, skew + "w1500")
            check(len(r) == 1 and r[0]["hidden"] and r[0]["visibility"] == "hidden" and r[0]["text"] == "",
                  f"hidden: {name}")
        r = run(browser, base, tmp, ["slow"], "w8000", real_time=True)
        check(len(r) == 1 and r[0]["hidden"] and r[0]["text"] == "" and Feed.calls == 1 and Feed.dropped == 1,
              "hidden: a feed slower than the timeout (the page hangs up, and stays hidden)")

        print("-- shown, with the right words")
        r = run(browser, base, tmp, [feed(1)], "w1500")
        check(len(r) == 1 and not r[0]["hidden"] and r[0]["visibility"] == "visible" and r[0]["text"] == "1 player online now",
              "1 player: \"1 player online now\"")
        check(r and r[0]["live"] == "polite", "the badge is in a polite live region")
        for size, where in (("1440,900", "desktop"), ("390,844", "phone")):
            r = run(browser, base, tmp, [feed(0), feed(12, racing=3)], "w1500,r", size=size)
            ok = len(r) == 2 and r[0]["width"] == int(size.split(",")[0]) and r[0]["hidden"] and not r[1]["hidden"]
            # against the step before (the page settled, its fonts in), not the page's first look
            moved = ok and abs(r[1]["at"][0] - r[0]["at"][0]) + abs(r[1]["at"][1] - r[0]["at"][1])
            check(ok and moved < 0.5 and min(r[1]["pill"]) >= 0,
                  f"{where} {size}: nothing moves when the badge appears, and it sits between the buttons and the note")
            check(ok and r[0]["gap"] <= 60, f"{where} {size}: the hidden line leaves little room "
                  f"(buttons to note: {r[0]['gap']:.0f}px)" if ok else f"{where} {size}: the hidden line")
        for name, skew, answers in (("a visitor an hour ahead", "skew3600,", [feed(7)]),
                                    ("a visitor an hour behind", "skew-3600,", [feed(7)]),
                                    ("no Date, a visitor whose clock is right", "", [Opts(feed(7), date=None)]),
                                    ("a copy a cache kept a minute (Date + Age)", "", [Opts(feed(7, age=60), date=-60, age=60)])):
            r = run(browser, base, tmp, answers, skew + "w1500")
            check(len(r) == 1 and not r[0]["hidden"] and r[0]["text"] == "7 players online now", f"shown: {name}")
        r = run(browser, base, tmp, [feed(3, worlds=4)], "w1500,p")
        check(len(r) == 2 and r[0]["text"] == "3 players online now" and r[1]["parsed"] == {"players": 3, "worlds": 3, "racing": 1},
              "more worlds than players (the server does not hold worlds to players): shown, worlds held to players")
        r = run(browser, base, tmp, [feed(1234, racing=5, worlds=200)], "w1500")
        check(r and r[0]["text"] == "1,234 players online now · 5 racing", "many: \"1,234 players online now · 5 racing\"")
        r = run(browser, base, tmp, [feed(1, racing=1)], "w1500")
        check(r and r[0]["text"] == "1 player online now · 1 racing", "1 racing")

        print("-- every malformed feed hides a shown badge")
        bad = [("version 2", feed(3, version=2)), ("no version", {k: v for k, v in feed(3).items() if k != "version"}),
               ("players as text", feed("3")), ("negative players", feed(-1)), ("fractional players", feed(2.5)),
               ("players beyond belief", feed(10 ** 7)), ("racing above players", feed(3, racing=4)),
               ("worlds beyond belief", feed(3, worlds=10 ** 7)), ("no worlds", {k: v for k, v in feed(3).items() if k != "worlds"}),
               ("generated as text", feed(3, generated="now")), ("a list", (200, b"[1,2,3]", "application/json")),
               ("not JSON", (200, b"players: 3", "application/json")), ("not application/json", (200, json.dumps(feed(3)).encode(), "text/plain")),
               ("too big", (200, json.dumps(feed(3, pad="x" * 5000)).encode(), "application/json")),
               ("too big, without a Content-Length", Opts((200, json.dumps(feed(3, pad="x" * 5000)).encode(), "application/json"), length=False))]
        answers = []
        for _, b in bad:
            answers += [feed(3), b]
        r = run(browser, base, tmp, answers, ",".join(["w1000"] + ["r"] * (2 * len(bad) - 1)), budget=30000)
        check(len(r) == 2 * len(bad), "the malformed feeds were all served")
        for i, (name, _) in enumerate(bad):
            shown, after = (r[2 * i], r[2 * i + 1]) if len(r) > 2 * i + 1 else ({}, {})
            check(shown.get("text") == "3 players online now" and after.get("hidden") is True and after.get("text") == "",
                  f"malformed: {name}")

        print("-- refreshes")
        r = run(browser, base, tmp, [feed(3), feed(3), feed(4, racing=2), feed(0), feed(2)], "w1000,r,r,r,r")
        texts = [s.get("text") for s in r]
        check(texts == ["3 players online now", "3 players online now", "4 players online now · 2 racing", "", "2 players online now"],
              "a refresh shows the new count, hides at 0, shows again")
        check(len(r) >= 2 and r[1]["changes"] == r[0]["changes"], "the same count again does not touch the text (no new announcement)")
        r = run(browser, base, tmp, [feed(3), feed(6)], "w1500,w61000", budget=90000)
        check([s.get("text") for s in r] == ["3 players online now", "6 players online now"] and Feed.calls == 2,
              "it refreshes by itself after a minute")
        r = run(browser, base, tmp, [feed(3)], "hiddenTab,w65000,v", budget=90000)
        check(len(r) == 2 and r[0]["hidden"] and Feed.calls == 1 and not r[1]["hidden"]
              and r[1]["text"] == "3 players online now", "a hidden tab does not ask; shown, it asks at once")

        if screenshot:
            Feed.answers, Feed.calls = [feed(12, racing=3, worlds=3)], 0
            out = Path(screenshot).resolve()
            # the picture is taken before the pill's fade-in would end, so this copy shows it at once
            shot = index.replace("</head>", "<style>.hero-live-pill { transition: none; }</style></head>")
            (site / "online-shot.html").write_text(shot, encoding="utf-8")
            headless(browser, tmp, ["--hide-scrollbars", f"--window-size={os.environ.get('SHOT_SIZE', '1440,900')}", f"--screenshot={out}"], base + "/online-shot.html", 5000)
            check(out.is_file() and out.stat().st_size > 0, f"screenshot at {out}")
    finally:
        httpd.shutdown()


def main():
    shot = sys.argv[sys.argv.index("--screenshot") + 1] if "--screenshot" in sys.argv else ""
    tmp = Path(tempfile.mkdtemp(prefix="online-count-"))
    try:
        test_pages(tmp, shot)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("all passed" if not failures else f"{len(failures)} failed"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
