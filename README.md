# Forza Horizon 1: ReImagined website

The website and the public leaderboards of
[Forza Horizon 1: ReImagined](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined),
published at **[forza.dyotson.dev](https://forza.dyotson.dev)**.

Plain HTML, CSS and JavaScript, no build step. The repository root is the site:

| Path | What |
|------|------|
| `index.html` | the landing page: the sunset first screen with Download and Join the Discord and a "Scroll to know more" hint, the facts, online again, three ways into the site, the latest Photo of the Month winner, the newest photos, and a last call to install |
| `features.html` | single player, co-op, mods, HD textures, measured frame rates |
| `online.html` | how to play online (Horizon World) and the newest player designs; not in the navbar, linked from the landing page, the FAQ and the footer |
| `download.html` | installing with the launcher |
| `nav.js`, `script.js`, `style.css` | the navbar (Features, Online, Community; then Discord, Sign in or Account, and Download, which a visitor who signed in on this browser does not see), the landing page's sunset as it scrolls away, and the look |
| `fonts/` | Bitter for headlines and Saira Semi Condensed for labels and buttons (SIL Open Font License, `fonts/OFL-*.txt`); the text is in the system font. The headline font is one CSS variable, `--font-headline`: `style.css` says how to put another in its place |
| `leaderboards.html`, `leaderboards.js` | the public server's leaderboards on a map of Colorado |
| `gallery.html`, `photos.js` | the community photos (also the landing page's newest three) and the photo viewer for every picture button: it slides through the pictures of its list (arrow buttons and keys, Home and End, swipe or drag), loads the neighbours ahead, and shows a picture only once it has loaded |
| `designs.html`, `designs.js` | the paint jobs and vinyl groups players share, with their share codes (also the Online page's newest three) |
| `account.html`, `account.js` | website accounts: sign in with Discord, GitHub or an emailed code, see the account, link it to the game, sign out everywhere, delete it. The launcher's "Link to website account" opens `account.html#link=<token>` (the page moves the token into `localStorage` for its 10 minutes and out of the address at once, so an email sign-in whose mail link opens another tab still finds it; it is removed once the link is done, declined or refused), or shows a code that is typed on this page; the page claims the link for the signed-in account, and the player confirms the account id in the launcher. A game can be linked once it has uploaded a speed trap to the public server (single player is enough: no lobby or online play), or already has a record on its leaderboards; the page says so next to the link code, and shows the service's own message when it refuses. Signed out, the page shows "How it works" (sign in here, Link in the launcher's Online > Online > Website account, or Settings > Online before launcher 0.2.9, confirm), and says photos and designs come from the game. A linked account shows its game's gamertag and can unlink it, and lists the game's photos and designs in every state (live, waiting for the check, held, hidden) with their likes; the player can delete them there (after a sign-in in the past 10 minutes). It talks to the accounts service at `/api/` on the same site ([forza-live, `accounts/`](https://github.com/FH1-ReImagined/forza-live/tree/main/accounts)); a copy of the site without `/api/` says accounts are not available. The navbar's "Sign in" link says "Account", and its Download goes, once this browser signed in (a `localStorage` hint) |
| `photo-of-the-month.html`, `potm.js` | Photo of the Month: this month's standings (live from the accounts service's public `/api/potm/standings`, sent without cookies, refreshed every minute; the built copy when it cannot be read), with each entry's like button and share buttons (the system share sheet, copy link, X, Bluesky, Reddit, Facebook: plain links, nothing loaded from them), last month (being checked, or its winner) and the past winners. `potm.js` also shows the newest winner on the front page (from the built file only: no API call) and fills the rules page's numbers |
| `photo-of-the-month-rules.html` | the event's rules: free to enter and vote, dates (UTC months), entries, which likes are votes, the tie-break, checks and disqualification, and the prize as a marked placeholder until it is decided |
| `mods.html`, `hub.js` | the mods and texture packs hub (website #2): the list from `data/hub.json` with each item's download (from the hub's own download host) and SHA-256, and for a visitor who signed in the upload form (`POST /api/hub/items`, then the zip as the body of `PUT /api/hub/items/<id>/file`), "Your uploads" and the report button on an item's page. Each live item also gets its own page, `mods/<id>.html`, made by `tools/build_hub.py`. In the navbar as "Mods and Textures", next to Leaderboards. While the accounts service runs no hub the page says it is not open yet, and the build adds no sitemap entries |
| `live.js` | the newest photos and designs before the next build has them: `gallery.html` and `designs.html` read the game server's public feeds through this site at `/live/community/gallery.json` and `designs.json` (a read-only route the site's web server passes on, cached for a few seconds), check each entry as the builds do, and show the feed's items in its order, with the build's copy of each one the build already has; an item the feed no longer lists (hidden or deleted) leaves the page at once. A copy of the site without the route shows the build's data |
| `online.js` | the landing page's "N players online now" under the download button: it reads the game server's public count of players online through this site at `/live/community/online.json` (counts only, no gamertags or ids), on load and every minute while the tab is visible, and keeps the badge hidden with no feed, a malformed or stale one (older than five minutes) or nobody online |
| `likes.js` | the thumbs-up on photos and designs. Everyone sees the totals built into the data files; only a visitor who signed in on this browser (the hint) makes the pages ask `/api/likes/mine` and `/api/likes/counts`, and gets a working button when the account is linked to the game; its likes count in the public totals as soon as the link is made. When the service refuses (not linked, the link on hold, no speed trap uploaded), the thumbs-up says why when pressed. The other pages never call the API for anyone else, except the Photo of the Month page's public standings |
| `profile.html`, `profile.js` | a player's page, `profile.html?p=<player id>`: their photos and designs and the likes they got, from the site's own data files (the player id is the one the server publishes on its leaderboards, never a XUID). Nothing about the web account. Not indexed |
| `press.html` | the press kit: facts, logo, social preview, screenshots and links |
| `faq.html` | common questions (recomp or emulator, your own copy, online, Steam Deck, Mac, requirements, linking a website account to the game), with FAQPage structured data |
| `images/` | screenshots; the logo (`logo-original.png`, and made from it: `logo-mark.webp`/`.png` for the navbar, the landing page and the footer, `logo.png` and `logo.svg` square, `favicon-32.png`, `apple-touch-icon.png`); the social preview (drawn from the logo and plain text, no game assets); the map (`colorado-map.svg`) |
| `data/map_points.json` | the map's pins: the place and in-game name of every speed trap, speed zone and Rivals event |
| `robots.txt`, `sitemap.xml` | for search engines |
| `tools/` | `assemble.sh` (what gets published), `preview_site.sh`, `check_site.py`, `build_gallery.py`, `build_designs.py`, `build_hub.py` and `build_potm.py` and their tests `test_build_gallery.py`, `test_build_designs.py`, `test_build_hub.py` and `test_build_potm.py`, `test_account_page.py`, and `test_live_community.py` (not published), plus `site_hook.py` and `build_community.sh` for a self-hosted builder |

## Preview

    tools/preview_site.sh            # http://127.0.0.1:8000/

The leaderboards page reads `data/leaderboards.json`, which only the Pages workflow writes. To
see the boards locally, open `leaderboards.html?api=http://HOST:PORT` with a Forza Live server's
HTTP port ([forza-live](https://github.com/FH1-ReImagined/forza-live)).

The community photos (`data/gallery.json`, `photos/`) and designs (`data/designs.json`,
`designs/`) are also written only by the workflow. To see the pages with synthetic ones, run a
test and keep its site up (needs Pillow, `pip install -r tools/requirements.txt`):

    tools/test_build_gallery.py --serve 8000   # http://127.0.0.1:8000/
    tools/test_build_designs.py --serve 8000   # the same with synthetic designs

## Publishing

`.github/workflows/pages.yml` publishes the site on every push to `main` and every hour (at
minute 17) with a fresh copy of the public server's `/leaderboards/snapshot`, because the
server speaks plain http and the https page cannot fetch it from the browser. A scheduled run
that cannot reach the server fails and leaves the published site as it was. Pull requests run
`tools/check_site.py` on the assembled site (local links and images, the data files),
`tools/test_build_gallery.py`, `tools/test_build_designs.py` and `tools/test_account_page.py`.

The same workflow copies the community photos: the photos players share from the game's photo
mode, once a maintainer has approved them on the server. Set the repository variable
`GALLERY_FEED_URL` (Settings, Secrets and variables, Actions, Variables) to the server's feed,
`http://HOST:PORT/community/gallery.json`; while it is unset the gallery is empty and the front
page hides its photos section. `tools/build_gallery.py` downloads each new photo, checks its
SHA-256, and re-encodes it with Pillow without any metadata (at most 1920 px wide, plus a 480 px
thumbnail); `actions/cache` keeps the re-encoded copies, so the server sends each photo once.
No photo is kept in this repository, and a photo withdrawn on the server leaves the site with
the next run. Each photo keeps its owner's player id (for the profile pages; only the server's
12-hex-digit id, never a XUID) and gets its like total from the repository variable `LIKES_URL`,
the accounts service's public totals (`https://forza.dyotson.dev/api/likes/counts`). While it is
unset, or when the totals cannot be read, that run publishes no totals and goes on. To publish right after approving photos, run the workflow by hand
(Actions, pages, Run workflow).

The paint jobs and vinyl groups players share from the game's storefront come from the same
server: `tools/build_designs.py` reads `/community/designs.json` next to `GALLERY_FEED_URL`
(only the designs the server screened), checks each preview's SHA-256 and re-encodes it with
Pillow as a PNG without metadata (at most 576 px wide), with its own `actions/cache`. The page
shows each design's share code, which finds it in the game's storefront search; it never shows
a XUID, the design itself or its description. A server without the designs feed (HTTP 404)
gives an empty list, and the Online page hides its designs section. The designs get their owner
ids and like totals as the photos do.

The mods and texture packs hub comes from the same service: `tools/build_hub.py` reads its public
`/api/hub/items` (from `HUB_URL`, or else `LIKES_URL`'s site) and writes `data/hub.json`, one
page per item (`mods/<id>.html`, its text escaped, with its own title and description) and their
sitemap entries. It makes each download address itself, from the service's download host (an
https origin that is not this site) and the item's SHA-256 and folder. While the hub is off
(HTTP 404) the file says `"on": false` and no page is made.

Photo of the Month comes from the accounts service too: `tools/build_potm.py` (after the
gallery) reads its public `/api/potm/winners` and `/api/potm/standings` (this month and the month
before) from the repository variable `POTM_URL`, or else from `LIKES_URL`'s site, and writes
`data/photo-of-the-month.json`: the winners, the two months' standings, and `featured`, the newest
winner with its photo's files from the gallery (null while there is none, or when its photo left
the gallery). The front page shows `featured`; **the launcher can use it as its background**: read
`https://forza.dyotson.dev/data/photo-of-the-month.json`, then `featured.src` (a JPEG of at most
1920 px) on the same site, crediting `featured.gamertag` and `featured.title`. While the service
runs no contest, or cannot be read, the file is empty (`"from": null`) and that build goes on.

A self-hosted copy of the site can publish a change to the photos or designs within a minute
instead of at its next full build: `tools/site_hook.py` takes the game server's signed call
(forza-live's `FORZA_SITE_NOTIFY_URL` and `FORZA_SITE_NOTIFY_SECRET`, an HMAC-SHA256 over the
timestamp and the body) and runs `tools/build_community.sh`, which builds only the photos and
designs into a copy of the release being served and switches to it in one rename, with the same
feeds, caches and like totals as the full build. Photo of the Month is built again after the photos
(it names gallery photos with their files), from the same `POTM_URL` or `LIKES_URL`. The full build stays as it is, as the fallback.
Both scripts describe their settings at the top; the hook belongs on the builder's private network.

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
