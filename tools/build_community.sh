#!/usr/bin/env bash
# A partial build of a self-hosted copy of the site: the community photos and designs only, for
# the minute after the game server approves, hides or deletes one (tools/site_hook.py runs it).
# The full build (the whole site, every few minutes) stays the source of everything else; this
# copies the release the site serves now, builds the photos and designs into the copy again
# (tools/build_gallery.py and build_designs.py, with the same feeds, caches and like totals as
# the full build), checks it (tools/check_site.py) and switches the site to it at once, the way
# the full build does: a symlink to the release, replaced in one rename. A feed that cannot be
# read, a failed check or a site that is not served through such a symlink changes nothing.
# Photo of the Month is built again after the photos (tools/build_potm.py), since it names gallery
# photos with their files: a photo deleted from the gallery leaves its standings too, and an
# accounts service that cannot be read changes nothing.
# The sitemap is left as the release has it: it lists the pages and the mods hub's items, never
# photos or designs.
#
# Environment (the paths default to a builder that keeps the releases under /www):
#   GALLERY_FEED_URL, LIKES_URL   as for the full build (both builders read them)
#   POTM_URL       as for the full build (else LIKES_URL's site; neither: no contest)
#   SITE_LINK      the symlink the web server serves                       (/www/current)
#   RELEASES       the folder of the releases                              (/www/releases)
#   REPO           the checkout of this repository the full build uses     (the one this file is in)
#   GALLERY_CACHE, DESIGNS_CACHE   the full build's caches of re-encoded files
#                                  (/work/gallery-cache, /work/designs-cache)
#   BUILD_LOCK     a lock file the full build also holds, so one build runs at a time
#                                                                          (/work/build.lock)
#   KEEP           partial releases kept besides the one served            (2)
#   tools/build_community.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-$(dirname "$here")}"
SITE_LINK="${SITE_LINK:-/www/current}"
RELEASES="${RELEASES:-/www/releases}"
GALLERY_CACHE="${GALLERY_CACHE:-/work/gallery-cache}"
DESIGNS_CACHE="${DESIGNS_CACHE:-/work/designs-cache}"
BUILD_LOCK="${BUILD_LOCK:-/work/build.lock}"
KEEP="${KEEP:-2}"

say() { printf 'community build: %s\n' "$*"; }

if [ ! -L "$SITE_LINK" ]; then
    say "$SITE_LINK is not a symlink to a release: nothing done (set SITE_LINK and RELEASES)"
    exit 2
fi
command -v flock >/dev/null || { say "flock is not installed: nothing done"; exit 2; }

exec 9>"$BUILD_LOCK"
if ! flock -w 900 9; then
    say "another build held $BUILD_LOCK for 15 minutes: nothing done"
    exit 1
fi

current="$(readlink -f "$SITE_LINK")"
[ -d "$current" ] || { say "$SITE_LINK points at no folder: nothing done"; exit 2; }
mkdir -p "$RELEASES"
new="$RELEASES/community-$(date -u +%Y%m%dT%H%M%SZ)-$$"
trap 'rm -rf "$new" "$SITE_LINK.next"' EXIT

# The copy shares every unchanged file with the release it comes from (hard links); what the two
# builders write is removed from it first, so they write new files and the served release is
# never touched.
cp -al "$current" "$new"
rm -rf "$new/photos" "$new/designs"
rm -f "$new/data/gallery.json" "$new/data/designs.json" "$new/data/photo-of-the-month.json"

python3 "$REPO/tools/build_gallery.py" "$new" --cache "$GALLERY_CACHE" --require-feed
python3 "$REPO/tools/build_designs.py" "$new" --cache "$DESIGNS_CACHE" --require-feed
POTM_URL="${POTM_URL:-${LIKES_URL:-}}" python3 "$REPO/tools/build_potm.py" "$new" --require
python3 "$REPO/tools/check_site.py" "$new" >/dev/null || { python3 "$REPO/tools/check_site.py" "$new" | head -20; say "the check failed: nothing switched"; exit 1; }

ln -sfn "$new" "$SITE_LINK.next"
mv -T "$SITE_LINK.next" "$SITE_LINK"
trap - EXIT
say "switched to $(basename "$new")"

# Partial releases older than the newest KEEP, never the one served.
served="$(readlink -f "$SITE_LINK")"
ls -1dt "$RELEASES"/community-* 2>/dev/null | tail -n +"$((KEEP + 2))" | while read -r old; do
    [ "$(readlink -f "$old")" = "$served" ] || rm -rf "$old"
done
