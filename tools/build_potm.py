#!/usr/bin/env python3
"""Copy Photo of the Month (the website accounts service's standings and winners) into the
assembled site, after tools/build_gallery.py, as OUT/data/photo-of-the-month.json.

  POTM_URL=URL tools/build_potm.py OUT [--require]

POTM_URL is the accounts service: the site's origin (https://forza.dyotson.dev), or any URL on
it, such as LIKES_URL (only its scheme, host and port are used). This reads its public routes
/api/potm/winners and /api/potm/standings (this month, and the month before), keeps only the
fields the pages show, checked, and adds to every photo the files tools/build_gallery.py
published for it in OUT/data/gallery.json (src, thumb and their sizes, and the owner's player
id). The pages insert the text with textContent, so nothing is escaped here.

The file:

  {"version": 1, "generated": <unix>, "from": "YYYY-MM" | null,
   "featured": <the newest winner, with its photo files> | null,
   "winners": [{"month", "id", "title", "gamertag", "votes", "reached", "confirmed", [photo files]}],
   "months": [{"month", "start", "end", "state", "accountDays", "entries",
               "top": [{"rank", "id", "title", "gamertag", "votes", "likes", "reached", [photo files]}],
               "winner": "<photo id>" | null}]}

"featured" is what the home page shows and what the launcher may use as its background:
https://<site>/data/photo-of-the-month.json, then https://<site>/<featured.src> (1920 px at
most). It is null while there is no confirmed winner, or when the newest winner's photo is no
longer in the gallery (an older winner is never featured in its place).

Without POTM_URL, or while the service has no contest (HTTP 404), the file says so ("from": null,
nothing listed) and the pages show that the contest has not started. Standings that fail
the checks are left out (an entry that fails them is left out of its standings), and the winners
are still published. When the service cannot be read, the same empty file is written and the build goes on, unless --require (the scheduled runs) is
given: then it exits 1, so the published site stays as it was.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_gallery import Refused, Unreachable, clean_text, fetch, is_int  # noqa: E402

MONTH_RE = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
ID_RE = re.compile(r"[A-Za-z2-7]{12}")
OWNER_RE = re.compile(r"[0-9a-f]{12}")
STATES = {"upcoming", "open", "verifying", "confirmed", "no winner"}
MAX_BYTES = 1 << 20
MAX_TOP = 50
MAX_WINNERS = 600   # fifty years of months
MAX_COUNT = 10 ** 9
MAX_TIME = 10 ** 11
PHOTO_FILES = ("src", "w", "h", "thumb", "tw", "th", "owner")


def log(msg):
    print(f"photo of the month: {msg}", flush=True)


def warn(msg):
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    print(f"{prefix}photo of the month: {msg}", flush=True)


def base_of(url):
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc or "@" in u.netloc:
        raise Refused(f"POTM_URL must be an http(s) URL, not {url!r}")
    return f"{u.scheme}://{u.netloc}"


def read(url):
    """The JSON document at url; None for HTTP 404 (no contest, or no such month)."""
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


def count(v):
    return is_int(v) and v <= MAX_COUNT


def stamp(v):
    return is_int(v) and v <= MAX_TIME


def text(raw, key, limit, required=False):
    value = clean_text(raw.get(key), limit)
    if required and not value:
        raise Refused(f"no {key}")
    return value or ""


def winner_of(raw):
    if not isinstance(raw, dict):
        raise Refused("a winner that is not an object")
    month, pid = raw.get("month"), raw.get("id")
    if not (isinstance(month, str) and MONTH_RE.fullmatch(month)):
        raise Refused("a winner without a month")
    if not (isinstance(pid, str) and ID_RE.fullmatch(pid)):
        raise Refused(f"winner {month}: bad id")
    if not (count(raw.get("votes")) and stamp(raw.get("reached")) and stamp(raw.get("confirmed"))):
        raise Refused(f"winner {month}: bad votes or times")
    return {"month": month, "id": pid, "title": text(raw, "title", 80), "gamertag": text(raw, "gamertag", 32, True),
            "votes": raw["votes"], "reached": raw["reached"], "confirmed": raw["confirmed"]}


def month_of(raw):
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise Refused("standings that are not version 1")
    month = raw.get("month")
    if not (isinstance(month, str) and MONTH_RE.fullmatch(month)):
        raise Refused("standings without a month")
    if raw.get("state") not in STATES:
        raise Refused(f"{month}: unknown state")
    if not (stamp(raw.get("start")) and stamp(raw.get("end")) and raw["start"] < raw["end"]
            and count(raw.get("entries")) and count(raw.get("accountDays"))):
        raise Refused(f"{month}: bad times or counts")
    top = raw.get("top")
    if not isinstance(top, list):
        raise Refused(f"{month}: no top")
    entries, seen = [], set()
    for i, e in enumerate(top[:MAX_TOP]):
        if not isinstance(e, dict) or not (isinstance(e.get("id"), str) and ID_RE.fullmatch(e["id"])):
            warn(f"{month}: left out entry {i}: no id")
            continue
        if e["id"] in seen or not (count(e.get("votes")) and count(e.get("likes")) and stamp(e.get("reached"))):
            warn(f"{month}: left out entry {i} ({e['id']}): repeated, or bad counts")
            continue
        seen.add(e["id"])
        entries.append({"rank": len(entries) + 1, "id": e["id"], "title": text(e, "title", 80),
                        "gamertag": text(e, "gamertag", 32), "votes": e["votes"], "likes": e["likes"],
                        "reached": e["reached"]})
    winner = raw.get("winner")
    winner_id = winner.get("id") if isinstance(winner, dict) else None
    return {"month": month, "start": raw["start"], "end": raw["end"], "state": raw["state"],
            "accountDays": raw["accountDays"], "entries": raw["entries"], "top": entries,
            "winner": winner_id if isinstance(winner_id, str) and ID_RE.fullmatch(winner_id) else None}


def previous(month):
    y, m = int(month[:4]), int(month[5:])
    return "%04d-%02d" % (y - (m == 1), (m - 2) % 12 + 1)


def gallery(out):
    """{photo id: the photo's files} from the gallery tools/build_gallery.py wrote."""
    try:
        doc = json.loads((out / "data" / "gallery.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    photos = doc.get("photos") if isinstance(doc, dict) else None
    found = {}
    for p in photos if isinstance(photos, list) else []:
        if isinstance(p, dict) and isinstance(p.get("id"), str) and ID_RE.fullmatch(p["id"]) \
                and p.get("src") == f"photos/{p['id']}.jpg" and (out / p["src"]).is_file():
            found[p["id"]] = {k: p[k] for k in PHOTO_FILES if k in p}
            if "owner" in found[p["id"]] and not (isinstance(p["owner"], str) and OWNER_RE.fullmatch(p["owner"])):
                del found[p["id"]]["owner"]
    return found


def write(out, doc):
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    text_ = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    tmp = data / "photo-of-the-month.json.tmp"
    tmp.write_text(text_, encoding="utf-8")
    os.replace(tmp, data / "photo-of-the-month.json")


def empty():
    return {"version": 1, "generated": int(time.time()), "from": None, "featured": None, "winners": [], "months": []}


def build(base, photos):
    winners_doc = read(base + "/api/potm/winners")
    if winners_doc is None:
        log("the accounts service runs no contest (HTTP 404)")
        return empty()
    if not isinstance(winners_doc, dict) or winners_doc.get("version") != 1 or \
            not isinstance(winners_doc.get("winners"), list):
        raise Refused("winners that are not version 1")
    first = winners_doc.get("from")
    if not (isinstance(first, str) and MONTH_RE.fullmatch(first)):
        raise Refused("winners without the first contest month")
    winners, seen = [], set()
    for raw in winners_doc["winners"][:MAX_WINNERS]:
        try:
            w = winner_of(raw)
        except Refused as e:
            warn(f"left out a winner: {e}")
            continue
        if w["month"] in seen:
            warn(f"left out a second winner of {w['month']}")
            continue
        seen.add(w["month"])
        winners.append(w)
    winners.sort(key=lambda w: w["month"], reverse=True)

    months = []
    try:
        now = read(base + "/api/potm/standings")
        if now is not None:
            months.append(month_of(now))
            before = previous(months[0]["month"])
            if before >= first:
                older = read(base + "/api/potm/standings?month=" + before)
                if older is not None:
                    months.append(month_of(older))
    except Refused as e:  # the winners are still worth publishing
        warn(f"the standings are left out: {e}")

    for item in winners + [e for m in months for e in m["top"]]:
        item.update(photos.get(item["id"], {}))
    featured = dict(winners[0]) if winners and "src" in winners[0] else None
    if winners and featured is None:
        log(f"the newest winner ({winners[0]['month']}, {winners[0]['id']}) is not in the gallery: none featured")
    return {"version": 1, "generated": int(time.time()), "from": first, "featured": featured,
            "winners": winners, "months": months}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="the assembled site, with the gallery built into it")
    ap.add_argument("--require", action="store_true",
                    help="exit 1 when POTM_URL is set but the service cannot be read")
    args = ap.parse_args()
    url = os.environ.get("POTM_URL", "").strip()
    if not url:
        log("POTM_URL is not set: no contest on this copy of the site")
        write(args.out, empty())
        return 0
    try:
        doc = build(base_of(url), gallery(args.out))
    except (Refused, Unreachable) as e:
        write(args.out, empty())
        if args.require:
            print(f"::error::photo of the month: {url} could not be read ({e})", flush=True)
            return 1
        warn(f"{url} could not be read ({e}): no contest in this build")
        return 0
    write(args.out, doc)
    log(f"{len(doc['winners'])} winners, featured: {doc['featured']['month'] if doc['featured'] else 'none'}; "
        + ", ".join(f"{m['month']} {m['state']} ({m['entries']} entries)" for m in doc["months"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
