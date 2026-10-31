#!/usr/bin/env bash
# Assemble the site the way .github/workflows/pages.yml does (tools/assemble.sh) and serve it.
#   tools/preview_site.sh [PORT]     -> http://127.0.0.1:8000/   (the assembled copy is in ${TMPDIR:-/tmp}/forza-site)
# The leaderboards page reads data/leaderboards.json, which only the Pages workflow fetches; for a
# local look at the boards open leaderboards.html?api=http://host:port (a server's HTTP port).
# With GALLERY_FEED_URL set (a server's /community/gallery.json) the community photos and designs
# are built in too (tools/build_gallery.py, tools/build_designs.py, need Pillow);
# tools/test_build_gallery.py --serve PORT and tools/test_build_designs.py --serve PORT serve the
# site with synthetic photos or designs instead.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${TMPDIR:-/tmp}/forza-site"
rm -rf "${OUT:?}"
"$ROOT/tools/assemble.sh" "$OUT"
if [ -n "${GALLERY_FEED_URL:-}" ]; then
    python3 "$ROOT/tools/build_gallery.py" "$OUT"
    python3 "$ROOT/tools/build_designs.py" "$OUT"
fi
echo "serving $OUT on http://127.0.0.1:${1:-8000}/"
exec python3 -m http.server "${1:-8000}" --bind 127.0.0.1 --directory "$OUT"
