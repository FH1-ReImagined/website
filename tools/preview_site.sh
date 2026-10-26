#!/usr/bin/env bash
# Assemble the site the way .github/workflows/pages.yml does (tools/assemble.sh) and serve it.
#   tools/preview_site.sh [PORT]     -> http://127.0.0.1:8000/   (the assembled copy is in ${TMPDIR:-/tmp}/forza-site)
# The leaderboards page reads data/leaderboards.json, which only the Pages workflow fetches; for a
# local look at the boards open leaderboards.html?api=http://host:port (a server's HTTP port).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${TMPDIR:-/tmp}/forza-site"
rm -rf "${OUT:?}"
"$ROOT/tools/assemble.sh" "$OUT"
echo "serving $OUT on http://127.0.0.1:${1:-8000}/"
exec python3 -m http.server "${1:-8000}" --bind 127.0.0.1 --directory "$OUT"
