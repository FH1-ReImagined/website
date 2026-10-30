# Forza Horizon 1: ReImagined website

The landing page and the public leaderboards of
[Forza Horizon 1: ReImagined](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined),
published at **[forza.dyotson.dev](https://forza.dyotson.dev)**.

Plain HTML, CSS and JavaScript, no build step. The repository root is the site:

| Path | What |
|------|------|
| `index.html`, `script.js`, `style.css` | the landing page |
| `leaderboards.html`, `leaderboards.js` | the public server's leaderboards on a map of Colorado |
| `gallery.html`, `photos.js` | the community photos (also the front page's newest six) and the screenshot lightbox |
| `images/` | screenshots, the logo, the social preview, the map (`colorado-map.svg`) |
| `data/map_points.json` | the map's pins: the place and in-game name of every speed trap, speed zone and Rivals event |
| `robots.txt`, `sitemap.xml` | for search engines |
| `tools/` | `assemble.sh` (what gets published), `preview_site.sh`, `check_site.py`, `build_gallery.py` and its test `test_build_gallery.py` (not published) |

## Preview

    tools/preview_site.sh            # http://127.0.0.1:8000/

The leaderboards page reads `data/leaderboards.json`, which only the Pages workflow writes. To
see the boards locally, open `leaderboards.html?api=http://HOST:PORT` with a Forza Live server's
HTTP port ([forza-live](https://github.com/FH1-ReImagined/forza-live)).

The community photos (`data/gallery.json`, `photos/`) are also written only by the workflow. To
see the pages with synthetic photos, run the gallery test and keep its site up (needs Pillow,
`pip install -r tools/requirements.txt`):

    tools/test_build_gallery.py --serve 8000   # http://127.0.0.1:8000/

## Publishing

`.github/workflows/pages.yml` publishes the site on every push to `main` and every hour (at
minute 17) with a fresh copy of the public server's `/leaderboards/snapshot`, because the
server speaks plain http and the https page cannot fetch it from the browser. A scheduled run
that cannot reach the server fails and leaves the published site as it was. Pull requests run
`tools/check_site.py` on the assembled site (local links and images, the data files) and
`tools/test_build_gallery.py`.

The same workflow copies the community photos: the photos players share from the game's photo
mode, once a maintainer has approved them on the server. Set the repository variable
`GALLERY_FEED_URL` (Settings, Secrets and variables, Actions, Variables) to the server's feed,
`http://HOST:PORT/community/gallery.json`; while it is unset the gallery is empty and the front
page hides its photos section. `tools/build_gallery.py` downloads each new photo, checks its
SHA-256, and re-encodes it with Pillow without any metadata (at most 1920 px wide, plus a 480 px
thumbnail); `actions/cache` keeps the re-encoded copies, so the server sends each photo once.
No photo is kept in this repository, and a photo withdrawn on the server leaves the site with
the next run. To publish right after approving photos, run the workflow by hand
(Actions, pages, Run workflow).

The map and its pins are built from a copy of the game in the game's repository
(`tools/build_map_roads.py`, `tools/extract_map_points.py`, described in forza-live's
[`docs/hosting.md`](https://github.com/FH1-ReImagined/forza-live/blob/main/docs/hosting.md))
and committed here; nothing of the game is in this repository.

## History

The site lived in `site/` of the game's repository until October 2026; its history came along
when it moved here.

## License

The website is free software under the
[GNU Affero General Public License, version 3 or later](LICENSE) (AGPL-3.0-or-later). If you run a
modified version for other people over a network, you must offer them its source (section 13).
Releases and commits before this license change were published under the MIT License and stay
available under it; see [`NOTICE.md`](NOTICE.md). The game and its launcher are under the GNU
General Public License, version 3 or later.

Forza Horizon is (c) Microsoft. This project is not affiliated with, endorsed by
or sponsored by Microsoft, Playground Games or Turn 10 Studios.
