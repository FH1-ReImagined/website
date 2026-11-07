#!/usr/bin/env python3
"""The site builder's webhook: the game server (forza-live, content/site_notify.py) calls it when
the photos or designs its public feeds list change (a photo or design screened and released,
hidden, deleted, a player banned), and it runs the partial build, tools/build_community.sh, so
the change is on the site within a minute instead of at the next full build.

  SITE_HOOK_SECRET=... tools/site_hook.py      (or SITE_HOOK_SECRET_FILE=path)

  POST /hook/community   body {"version": 1, "changed": ["gallery", "designs"]}, at most 4 KiB, with
                         X-Forza-Timestamp: <unix seconds> and
                         X-Forza-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + body,
                         keyed with the shared secret>.
                         202 when accepted; 401 for a bad or missing signature, a timestamp more
                         than SITE_HOOK_MAX_SKEW seconds off, or a signature seen before.
  GET  /hook/health      200 {"ok": true, "builds": n, "last": <unix>, "result": <exit code>}

A call only asks for a build: nothing it sends reaches the build, which reads the feeds itself.
Calls while a build runs make one more build after it, and builds start at most every
SITE_HOOK_MIN_GAP seconds, so a burst of screening decisions costs one or two builds. It is
meant for the builder's private network only: never publish it through the site's proxy.

Environment: SITE_HOOK_SECRET or SITE_HOOK_SECRET_FILE (at least 32 characters), SITE_HOOK_BIND
(0.0.0.0), SITE_HOOK_PORT (8099), SITE_HOOK_COMMAND (tools/build_community.sh next to this file),
SITE_HOOK_MIN_GAP (20), SITE_HOOK_MAX_SKEW (300); the build's own variables pass through.
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 4096
KINDS = {"gallery", "designs"}


def log(msg):
    print(f"site hook: {msg}", flush=True)


def secret_from_env():
    path = os.environ.get("SITE_HOOK_SECRET_FILE", "").strip()
    value = Path(path).read_text(encoding="utf-8").strip() if path else os.environ.get("SITE_HOOK_SECRET", "").strip()
    if len(value) < 32:
        raise SystemExit("site hook: SITE_HOOK_SECRET (or SITE_HOOK_SECRET_FILE) must hold at least 32 characters")
    return value.encode()


def sign(secret, timestamp, body):
    return "sha256=" + hmac.new(secret, str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()


class Builds:
    """Runs the build command on request: one at a time, once more when asked during a run, at
    most every min_gap seconds."""

    def __init__(self, command, min_gap):
        self.command, self.min_gap = command, min_gap
        self.wanted = threading.Event()
        self.count, self.last, self.result = 0, 0, None
        threading.Thread(target=self.run, daemon=True).start()

    def ask(self):
        self.wanted.set()

    def run(self):
        while True:
            self.wanted.wait()
            wait = self.last + self.min_gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self.wanted.clear()
            self.last = time.time()
            try:
                p = subprocess.run(self.command, capture_output=True, text=True, timeout=1800)
                self.result = p.returncode
                for line in (p.stdout + p.stderr).strip().splitlines()[-6:]:
                    log("  " + line[:300])
            except (OSError, subprocess.SubprocessError) as e:
                self.result = -1
                log(f"the build could not run: {e}")
            self.count += 1
            log(f"build {self.count} ended with {self.result}")


class Seen:
    """Signatures accepted in the last max_skew seconds: each is accepted once."""

    def __init__(self, max_skew):
        self.max_skew, self.items, self.lock = max_skew, {}, threading.Lock()

    def first(self, sig, now):
        with self.lock:
            for k in [k for k, t in self.items.items() if t < now - 2 * self.max_skew]:
                del self.items[k]
            if sig in self.items:
                return False
            self.items[sig] = now
            return True


def make_handler(secret, builds, seen, max_skew):
    class Handler(BaseHTTPRequestHandler):
        server_version = "site-hook"
        sys_version = ""

        def log_message(self, *a):
            pass

        def reply(self, code, doc):
            body = json.dumps(doc).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/hook/health":
                return self.reply(200, {"ok": True, "builds": builds.count, "last": int(builds.last), "result": builds.result})
            return self.reply(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/hook/community":
                return self.reply(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or "x")
            except ValueError:
                return self.reply(411, {"error": "a Content-Length"})
            if n < 0 or n > MAX_BODY:
                return self.reply(413, {"error": "too big"})
            body = self.rfile.read(n)
            now = time.time()
            try:
                ts = int(self.headers.get("X-Forza-Timestamp") or "x")
            except ValueError:
                return self.reply(401, {"error": "no timestamp"})
            sig = self.headers.get("X-Forza-Signature") or ""
            if abs(now - ts) > max_skew or not hmac.compare_digest(sig, sign(secret, ts, body)):
                log("refused a call with a bad signature or timestamp")
                return self.reply(401, {"error": "bad signature"})
            if not seen.first(sig, now):
                return self.reply(401, {"error": "replayed"})
            try:
                doc = json.loads(body)
                changed = doc.get("changed") if isinstance(doc, dict) and doc.get("version") == 1 else None
                if not (isinstance(changed, list) and changed and set(changed) <= KINDS):
                    raise ValueError
            except ValueError:
                return self.reply(400, {"error": "a version 1 body with changed"})
            log(f"changed: {', '.join(sorted(set(changed)))}")
            builds.ask()
            return self.reply(202, {"queued": True})

    return Handler


def main():
    secret = secret_from_env()
    command = os.environ.get("SITE_HOOK_COMMAND", "").split() or [str(Path(__file__).resolve().parent / "build_community.sh")]
    min_gap = float(os.environ.get("SITE_HOOK_MIN_GAP", "20"))
    max_skew = int(os.environ.get("SITE_HOOK_MAX_SKEW", "300"))
    bind, port = os.environ.get("SITE_HOOK_BIND", "0.0.0.0"), int(os.environ.get("SITE_HOOK_PORT", "8099"))
    builds = Builds(command, min_gap)
    httpd = ThreadingHTTPServer((bind, port), make_handler(secret, builds, Seen(max_skew), max_skew))
    log(f"listening on {bind}:{port}, runs {' '.join(command)}")
    httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
