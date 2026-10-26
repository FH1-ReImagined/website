#!/usr/bin/env python3
"""Check an assembled site (tools/assemble.sh OUT): every local href/src of every page names a
file that is there, and the JSON data files parse.
  tools/check_site.py OUT"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

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
for name in ("index.html", "leaderboards.html", "robots.txt", "sitemap.xml", ".nojekyll"):
    if not (root / name).is_file():
        bad.append(f"missing {name}")
print("\n".join(bad) or f"ok: {sum(1 for _ in root.rglob('*') if _.is_file())} files")
sys.exit(1 if bad else 0)
