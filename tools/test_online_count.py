#!/usr/bin/env python3
"""Test the landing page's "N players online now" (online.js) in a headless Chrome or Chromium
(CHROME=path, else one on PATH or Playwright's), against a stand-in for the site's
/live/community/online.json route: hidden with no route, an error, a timeout, a malformed, stale
or future feed, or nobody online; "1 player online now" and "1,234 players online now · 5 racing"
when there are players; a refresh that changes the count changes the text, one that does not
leaves it alone (no new announcement); a refresh every minute, none while the tab is hidden and
one as soon as it is shown; and nothing under the badge moves when it appears.

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


class Feed:
    """What /live/community/online.json answers, one entry per request (the last one repeats):
    None for a 404 page (a copy of the site without the route), "slow" for an answer after the
    page's timeout, (status, body, content type), or a dict sent as JSON."""
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
        if a is None:
            return self.reply(404, b"<html>Not Found</html>", "text/html")
        if a == "slow":
            time.sleep(SLOW)
            a = feed(5)
        if isinstance(a, dict):
            return self.reply(200, json.dumps(a).encode(), "application/json")
        return self.reply(*a)

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            Feed.dropped += 1  # the page gave up on the request


# Before online.js: with "hiddenTab" in the plan, the tab starts hidden until a "v" step.
SETUP = r"""
(function () {
  if (location.hash.indexOf("hiddenTab") < 0) return;
  window.__hidden = true;
  Object.defineProperty(document, "hidden", { configurable: true, get: function () { return window.__hidden; } });
})();
"""
# After online.js: runs the plan in the hash (comma-separated): w<ms> waits, r refreshes,
# v shows a hidden tab; each step records the badge into <pre id="test-results">.
HARNESS = r"""
(function () {
  var b = document.getElementById("online-now"), t = b.querySelector(".live-text");
  var note = document.querySelector(".hero-note"), down = document.querySelector(".hero .cta");
  var top0 = note.getBoundingClientRect().top, cta0 = down.getBoundingClientRect().top, changes = 0;
  new MutationObserver(function (l) { changes += l.length; }).observe(t, { childList: true, characterData: true, subtree: true });
  function state(step) {
    return { step: step, hidden: b.hidden, text: t.textContent, visibility: getComputedStyle(b).visibility,
             moved: Math.abs(note.getBoundingClientRect().top - top0) + Math.abs(down.getBoundingClientRect().top - cta0),
             changes: changes, live: b.parentNode.getAttribute("aria-live") };
  }
  var plan = location.hash.slice(1).split(",").filter(function (s) { return s && s !== "hiddenTab"; }), out = [];
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


def assemble(dest):
    subprocess.run([str(TOOLS / "assemble.sh"), str(dest)], check=True)


def headless(browser, profile, extra, url, budget):
    args = [browser, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={tempfile.mkdtemp(dir=profile)}"] + ([f"--virtual-time-budget={budget}"] if budget else []) + extra + [url]
    if "headless-shell" not in browser:
        args.insert(1, "--headless=new")
    return subprocess.run(args, capture_output=True, text=True, timeout=180)


def run(browser, base, profile, answers, plan, budget=15000, real_time=False):
    """The plan's records. Virtual time stands still while a request is open, so a test of the
    page's own timeout runs in real time instead, on a copy of the page whose load (and so the
    dump) waits for a picture the stand-in sends after HOLD seconds."""
    Feed.answers, Feed.calls, Feed.dropped = answers, 0, 0
    page = "online-test-hold.html" if real_time else "online-test.html"
    p = headless(browser, profile, ["--dump-dom"], f"{base}/{page}#{plan}", None if real_time else budget)
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
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        print("-- hidden when there is nothing to show")
        for name, answers in (("no route (404)", [None]), ("a server error", [(500, b"{}", "application/json")]),
                              ("nobody online", [feed(0)]), ("a stale feed (6 minutes old)", [feed(7, age=360)]),
                              ("a feed from the future", [feed(7, age=-1000)])):
            r = run(browser, base, tmp, answers, "w1500")
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
        check(r and r[0]["moved"] < 0.5, "nothing moves when the badge appears")
        r = run(browser, base, tmp, [feed(1234, racing=5, worlds=200)], "w1500")
        check(r and r[0]["text"] == "1,234 players online now · 5 racing", "many: \"1,234 players online now · 5 racing\"")
        r = run(browser, base, tmp, [feed(1, racing=1)], "w1500")
        check(r and r[0]["text"] == "1 player online now · 1 racing", "1 racing")

        print("-- every malformed feed hides a shown badge")
        bad = [("version 2", feed(3, version=2)), ("no version", {k: v for k, v in feed(3).items() if k != "version"}),
               ("players as text", feed("3")), ("negative players", feed(-1)), ("fractional players", feed(2.5)),
               ("players beyond belief", feed(10 ** 7)), ("racing above players", feed(3, racing=4)),
               ("worlds above players", feed(3, worlds=4)), ("no worlds", {k: v for k, v in feed(3).items() if k != "worlds"}),
               ("generated as text", feed(3, generated="now")), ("a list", (200, b"[1,2,3]", "application/json")),
               ("not JSON", (200, b"players: 3", "application/json")), ("not application/json", (200, json.dumps(feed(3)).encode(), "text/plain")),
               ("too big", (200, json.dumps(feed(3, pad="x" * 5000)).encode(), "application/json"))]
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
            headless(browser, tmp, ["--hide-scrollbars", f"--window-size={os.environ.get('SHOT_SIZE', '1440,900')}", f"--screenshot={out}"], base + "/index.html", 5000)
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
