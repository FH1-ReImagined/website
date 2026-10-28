#!/usr/bin/env bash
# Copy the published part of this repository into OUT: every tracked file except the repository's
# own files (.github/, tools/, README.md, LICENSE, NOTICE.md, .gitignore). Used by .github/workflows/pages.yml
# and tools/preview_site.sh, so the preview is what Pages serves.
#   tools/assemble.sh OUT
set -euo pipefail
OUT="${1:?usage: tools/assemble.sh OUT}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$OUT"
cd "$ROOT"
git ls-files -z | grep -z -v -E '^(\.github/|tools/|README\.md$|LICENSE$|NOTICE\.md$|\.gitignore$)' \
    | xargs -0 cp --parents -t "$OUT"
touch "$OUT/.nojekyll"
