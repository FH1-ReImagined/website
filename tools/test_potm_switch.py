#!/usr/bin/env python3
"""Test tools/potm_switch.py, the one step that shows Photo of the Month on the site or hides it,
and the check in tools/check_site.py that the site is wholly in one state, on a copy of the site
(tools/assemble.sh).

- This repository is wholly on or off, and check_site.py passes on it.
- Off: no navbar or footer links to the contest, the sitemap leaves its two pages out, both pages
  have noindex, every element marked data-potm-switch (the announcement, the winner's credit) is
  hidden, the home page's winner section is marked data-potm-off, and no other page links to the
  contest outside what the switch hides. On: "Photo of the Month" right after Photos in every
  navbar (the current page on its own page) and footer, also on the hub's item pages, both
  sitemap entries, no noindex, the marked elements shown, the winner section left to potm.js.
  check_site.py passes on both. (tools/test_build_potm.py loads the home page in a browser, with
  a winner, in both states.)
- Switching on and back off, or off and back on, gives the same files byte for byte; switching to
  the state the site is in changes nothing; a page added later is switched with the others.
- Half switched (one navbar, or the sitemap): status exits 1 and names it, check_site.py refuses
  the site, and on or off finishes it.
- A page that lost its Photos link, or a hidden written elsewhere in a switched tag: the switch
  writes nothing and exits 2.
- A footer or navbar link, a noindex, a mark or a sitemap entry written in another form than the
  switch's own (other attribute order, spacing, quotes or case): status exits 1, check_site.py
  refuses the site and names the page, and the switch writes nothing and exits 2, on or off.
- An off site whose winner section lost its data-potm-off: check_site.py refuses it (half
  switched, and a link to the contest that the home page would show), and on or off finishes it.
- What is not decided yet (whether there is a prize, the announcement, the credit) is listed by
  status and on, and leaves the list once a maintainer writes it. Each placeholder says "To be
  announced." and that it is not decided, and promises nothing: no prize, no dates, no title.

  python3 tools/test_potm_switch.py   # needs Pillow (tools/requirements.txt), for build_hub's import
"""
import html.parser
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import potm_switch  # noqa: E402

failures = []
LINK = "photo-of-the-month.html"


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def switch(site, action):
    p = subprocess.run([sys.executable, str(TOOLS / "potm_switch.py"), action, "--root", str(site)],
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


def check_site(site):
    p = subprocess.run([sys.executable, str(TOOLS / "check_site.py"), str(site)], capture_output=True, text=True,
                       timeout=120)
    return p.returncode, p.stdout + p.stderr


def files(site):
    return {str(p.relative_to(site)): p.read_bytes() for p in sorted(site.rglob("*")) if p.is_file()}


class Tags(html.parser.HTMLParser):
    """Every start tag of a page, with its attributes and the ids and tags it is inside."""

    def __init__(self):
        super().__init__()
        self.tags, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a, [t for t, _ in self.stack] + [i for _, i in self.stack if i]))
        if tag not in ("meta", "link", "img", "br", "input", "source"):
            self.stack.append((tag, a.get("id")))

    def handle_endtag(self, tag):
        for n in range(len(self.stack) - 1, -1, -1):
            if self.stack[n][0] == tag:
                del self.stack[n:]
                break


def tags_of(path):
    t = Tags()
    t.feed(path.read_text(encoding="utf-8"))
    return t.tags


def links(tags, inside):
    return [a for t, a, stack in tags if t == "a" and a.get("href") == LINK and inside in stack]


def check_on(site, what):
    pages = [p for p in sorted(site.glob("*.html")) if '<header class="topbar">' in p.read_text(encoding="utf-8")]
    navs, foots, here = [], [], None
    for page in pages:
        tags = tags_of(page)
        nav = links(tags, "header")
        navs.append(len(nav) == 1)
        foots.append(len(links(tags, "footer")) == 1)
        if page.name == LINK:
            here = nav[0].get("aria-current") if nav else None
        elif nav and "aria-current" in nav[0]:
            here = "elsewhere"
        header = re.search(r'<header class="topbar">.*?</header>', page.read_text(encoding="utf-8"), re.S).group(0)
        navs[-1] = navs[-1] and bool(re.search(r'<a href="gallery\.html"[^>]*>Photos</a>\n *<a href="photo-of-the-month\.html"', header))
    check(pages and all(navs), f"{what}: every navbar links to it, right after Photos")
    check(all(foots), f"{what}: every footer links to it")
    check(here == "page", f"{what}: its own page marks it as the current page, no other page does")
    sm = (site / "sitemap.xml").read_text(encoding="utf-8")
    check(f"/{LINK}</loc>" in sm and "/photo-of-the-month-rules.html</loc>" in sm, f"{what}: both pages in the sitemap")
    check(all('content="noindex"' not in (site / n).read_text(encoding="utf-8") for n in (LINK, "photo-of-the-month-rules.html")),
          f"{what}: no noindex on either page")
    marked = [(p.name, a) for p in pages for t, a, _ in tags_of(p) if "data-potm-switch" in a]
    check(marked and all("hidden" not in a for _, a in marked), f"{what}: the switched elements are shown")
    names = {(n, a.get("id")) for n, a in marked}
    check({("index.html", "potm-news"), ("index.html", "potm-credit"), (LINK, "potm-news"),
           ("photo-of-the-month-rules.html", "potm-credit")} <= names,
          f"{what}: the announcement and the winner's credit are among them")
    potm = winner_section(site)
    check("hidden" in potm and "data-potm-off" not in potm,
          f"{what}: the home page's winner section is left to potm.js (hidden until there is a winner)")


def check_off(site, what):
    pages = sorted(site.glob("*.html"))
    check(all(not links(tags_of(p), "header") and not links(tags_of(p), "footer") for p in pages),
          f"{what}: no navbar or footer links to it")
    sm = (site / "sitemap.xml").read_text(encoding="utf-8")
    check("photo-of-the-month" not in sm, f"{what}: neither page in the sitemap")
    check(all('<meta name="robots" content="noindex">' in (site / n).read_text(encoding="utf-8")
              for n in (LINK, "photo-of-the-month-rules.html")), f"{what}: both pages have noindex")
    marked = [a for p in pages for t, a, _ in tags_of(p) if "data-potm-switch" in a]
    check(len(marked) >= 4 and all("hidden" in a for a in marked), f"{what}: the switched elements are hidden")
    potm = winner_section(site)
    check("hidden" in potm and "data-potm-off" in potm,
          f"{what}: the home page's winner section is marked data-potm-off (potm.js leaves it hidden)")
    leaks = {p.name: potm_switch.shown_links(p.read_text(encoding="utf-8")) for p in pages
             if p.name not in (LINK, "photo-of-the-month-rules.html")}
    check(len(leaks) > 5 and not any(leaks.values()),
          f"{what}: no other page links to the contest outside what the switch hides {[k for k, v in leaks.items() if v]}")
    index = tags_of(site / "index.html")
    inside = [s for t, a, s in index if t == "a" and potm_switch.is_contest(a.get("href"))]
    check(inside and all("potm" in s or "potm-news" in s for s in inside),
          f"{what}: the home page's links to the contest are all in the announcement or the winner section")


def winner_section(site):
    return next((a for t, a, _ in tags_of(site / "index.html") if t == "section" and a.get("id") == "potm"), {})


def check_hub_page(site):
    import build_hub
    header = build_hub.template_of(site / "mods.html")["header"]
    check(f'href="../{LINK}"' in header, "on: the hub's item pages get the link too (from mods.html)")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="potm-switch-"))
    try:
        site = tmp / "site"
        subprocess.run([str(TOOLS / "assemble.sh"), str(site)], check=True)
        print("-- this repository")
        state, problems = potm_switch.site_state(TOOLS.parent)
        check(state in ("on", "off") and not problems, f"wholly {state or 'half: ' + '; '.join(problems)}")
        rc, log = check_site(site)
        check(rc == 0, "check_site.py passes" + ("" if rc == 0 else ": " + log))
        start = state or "off"
        other = "off" if start == "on" else "on"
        rc, log = switch(site, "status")
        check(rc == 0 and log.splitlines()[0] == start, f"status says {start}")

        print(f"-- {start} to {other} and back")
        first = files(site)
        rc, log = switch(site, other)
        check(rc == 0 and f"{other}:" in log, f"{other}: exits 0 ({log.strip().splitlines()[0]})")
        second = files(site)
        check(second != first and set(second) == set(first), f"{other}: files changed, none added or removed")
        rc, log = check_site(site)
        check(rc == 0, f"check_site.py passes {other}" + ("" if rc == 0 else ": " + log))
        if other == "on":
            check_on(site, "on")
            check_hub_page(site)
        else:
            check_off(site, "off")
        rc, log = switch(site, other)
        check(rc == 0 and "already" in log and files(site) == second, f"{other} again: nothing changed")
        rc, log = switch(site, start)
        check(rc == 0 and files(site) == first, f"{start} again: the same files as before, byte for byte")
        if start == "on":
            check_on(site, "on")
        else:
            check_off(site, "off")
        rc, log = switch(site, start)
        check(rc == 0 and "already" in log and files(site) == first, f"{start} again: nothing changed")
        switch(site, other)
        switch(site, start)
        switch(site, other)
        check(files(site) == second, f"{other}, {start}, {other}: the same files as the first {other}")
        switch(site, start)
        on, off = (second, first) if other == "on" else (first, second)

        print("-- a page added later")
        (site / "new.html").write_text((site / "press.html").read_text(encoding="utf-8"), encoding="utf-8")
        added = files(site)
        switch(site, "on")
        check(len(links(tags_of(site / "new.html"), "header")) == 1, "on: the new page gets the link too")
        switch(site, "off")
        switch(site, start)
        check(files(site) == added, "and loses it again")
        (site / "new.html").unlink()

        print("-- half switched")
        def restore(snap):
            for name, data in snap.items():
                (site / name).write_bytes(data)
        restore(on)
        press = site / "press.html"
        press.write_text(press.read_text(encoding="utf-8").replace(f'          <a href="{LINK}">Photo of the Month</a>\n', "", 1),
                         encoding="utf-8")
        rc, log = switch(site, "status")
        check(rc == 1 and "half switched" in log and "press.html navbar" in log, "status: exits 1 and names press.html's navbar")
        rc, log = check_site(site)
        check(rc == 1 and "Photo of the Month: half switched" in log, "check_site.py refuses it")
        half = files(site)
        rc, log = switch(site, "on")
        check(rc == 0 and files(site) == on, "on finishes it")
        restore(half)
        rc, log = switch(site, "off")
        check(rc == 0 and files(site) == off, "off from there gives the off site")
        sm = site / "sitemap.xml"
        sm.write_bytes(on["sitemap.xml"])
        rc, log = check_site(site)
        check(rc == 1 and "half switched" in log and "sitemap.xml" in log, "off with the sitemap entries back: refused")

        print("-- what the switch cannot do")
        restore(off)
        faq = site / "faq.html"
        faq.write_text(faq.read_text(encoding="utf-8").replace('<a href="gallery.html">Photos</a>', "", 1), encoding="utf-8")
        broken = files(site)
        rc, log = switch(site, "on")
        check(rc == 2 and "faq.html navbar" in log and files(site) == broken, "a navbar without Photos: nothing written, exit 2")
        rc, log = check_site(site)
        check(rc == 1 and "faq.html navbar" in log, "check_site.py names it")
        restore(off)
        index = site / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace(
            'id="potm-credit" data-potm-switch hidden>', 'id="potm-credit" hidden data-potm-switch>', 1), encoding="utf-8")
        broken = files(site)
        rc, log = switch(site, "on")
        check(rc == 2 and "right after data-potm-switch" in log and files(site) == broken,
              "hidden written elsewhere in a switched tag: nothing written, exit 2")
        restore(off)
        rules = site / "photo-of-the-month-rules.html"
        rules.write_text(rules.read_text(encoding="utf-8").replace('<meta name="robots" content="noindex">\n', "", 1).replace(
            "</head>", '<meta name="robots" content="noindex">\n</head>', 1), encoding="utf-8")
        broken = files(site)
        rc, log = switch(site, "on")
        check(rc == 2 and "noindex the switch did not write" in log and files(site) == broken,
              "a noindex somewhere else in the head: nothing written, exit 2")

        print("-- written in another form than the switch's own")
        def refused(what, page, find, put, where, start=off):
            restore(start)
            path = site / page
            text = path.read_text(encoding="utf-8")
            if find not in text:
                check(False, f"{what}: the test's text is in {page}")
                return
            path.write_text(text.replace(find, put, 1), encoding="utf-8")
            broken = files(site)
            rc, log = switch(site, "status")
            ok = rc == 1 and where in log and "did not write" in log
            rc, clog = check_site(site)
            ok = ok and rc == 1 and where in clog
            results = [switch(site, action) for action in ("on", "off")]
            ok = ok and all(r == 2 and where in out and "nothing was changed" in out for r, out in results)
            check(ok and files(site) == broken, f"{what}: status names {where}, check_site.py refuses, on and off write nothing")
        foot = f'<li><a href="{LINK}">Photo of the Month</a></li>'
        photos = '<li><a href="gallery.html">Photos</a></li>\n'
        for what, link in (("footer link with another attribute first", f'<li><a class="x" href="{LINK}">Photo of the Month</a></li>'),
                           ("footer link with a space before >", f'<li><a href="{LINK}" >Photo of the Month</a></li>'),
                           ("footer link in upper case", f'<LI><A HREF="{LINK.upper()}">Photo of the Month</A></LI>'),
                           ("footer link in single quotes", f"<li><a href='{LINK}'>Photo of the Month</a></li>"),
                           ("footer link to the site's address", f'<li><a href="https://forza.dyotson.dev/{LINK}">Photo of the Month</a></li>'),
                           ("footer link on the same line as Photos", None)):
            if link is None:
                refused(f"off, {what}", "press.html", photos, photos[:-1] + foot + "\n", "press.html footer")
                continue
            refused(f"off, {what}", "press.html", photos, photos + "          " + link + "\n", "press.html footer")
            refused(f"on, {what}", "press.html", foot, link, "press.html footer", start=on)
        refused("off, navbar link with another attribute first", "faq.html", '<a href="gallery.html">Photos</a>\n',
                f'<a href="gallery.html">Photos</a>\n          <a class="x" href="{LINK}">Photo of the Month</a>\n', "faq.html navbar")
        noindex = '<meta name="robots" content="noindex">\n'
        canonical = f'<link rel="canonical" href="https://forza.dyotson.dev/{LINK}">\n'
        for what, meta in (("content first", '<meta content="noindex" name="robots">'),
                           ("single quotes", "<meta name='robots' content='noindex'>"),
                           ("upper case", '<META NAME="ROBOTS" CONTENT="NOINDEX">'),
                           ("two spaces", '<meta name="robots"  content="noindex">'),
                           ("noindex, follow", '<meta name="robots" content="noindex, follow">')):
            refused(f"off, noindex with {what}", LINK, noindex, meta + "\n", f"{LINK}: has a noindex")
            refused(f"on, noindex with {what}", LINK, canonical, canonical + meta + "\n", f"{LINK}: has a noindex", start=on)
        featured = '<section id="potm" class="stop" data-potm-featured data-potm-off hidden'
        for what, tag in (("data-potm-off with a value", '<section id="potm" class="stop" data-potm-featured data-potm-off="" hidden'),
                          ("data-potm-off in upper case", '<section id="potm" class="stop" data-potm-featured DATA-POTM-OFF hidden'),
                          ("data-potm-off before data-potm-featured", '<section id="potm" class="stop" data-potm-off data-potm-featured hidden'),
                          ("data-potm-featured in upper case", '<section id="potm" class="stop" DATA-POTM-FEATURED data-potm-off hidden')):
            refused(f"off, {what}", "index.html", featured, tag, "index.html: has a data-potm-")
        refused("off, data-potm-switch with a value", "index.html", 'id="potm-credit" data-potm-switch hidden',
                'id="potm-credit" data-potm-switch="" hidden', "index.html: has a data-potm-switch")
        refused("off, a sitemap entry in upper case", "sitemap.xml", "</urlset>",
                f"  <url>\n    <loc>https://forza.dyotson.dev/{LINK.upper()}</loc>\n  </url>\n</urlset>", "sitemap.xml")

        print("-- off, but the home page's winner section would show")
        restore(off)
        index.write_text(index.read_text(encoding="utf-8").replace(" data-potm-featured data-potm-off hidden",
                                                                   " data-potm-featured hidden", 1), encoding="utf-8")
        rc, log = switch(site, "status")
        check(rc == 1 and "index.html #potm" in log, "status: half switched, names index.html #potm")
        rc, log = check_site(site)
        check(rc == 1 and "half switched" in log and "index.html: links to Photo of the Month while it is off" in log
              and LINK in log, "check_site.py refuses it: half switched, and the winner section's link would show")
        half = files(site)
        rc, log = switch(site, "off")
        check(rc == 0 and files(site) == off, "off puts data-potm-off back")
        restore(half)
        rc, log = switch(site, "on")
        check(rc == 0 and files(site) == on, "on finishes it")
        restore(off)
        index.write_text(index.read_text(encoding="utf-8").replace(
            "</main>", f'<p><a href="./{LINK}#now">Vote</a></p>\n</main>', 1), encoding="utf-8")
        rc, log = check_site(site)
        check(rc == 1 and "index.html: links to Photo of the Month while it is off" in log,
              "off, a link to the contest anywhere else on the home page: check_site.py refuses it")

        print("-- not decided yet")
        restore(off)
        for name, words in potm_switch.undecided(site):
            loose = re.findall(r"\b(will|prize is|wins|gets|receive\w*|role|Photographer|Discord|starts?|before|after|"
                               r"January|February|March|April|May|June|July|August|September|October|November|"
                               r"December|\d+)\b", words, re.I)
            check(words.startswith("To be announced.") and "has not been" in words and not loose,
                  f"placeholder in {name} only says it is to be announced: {words!r}" + (f" (promises: {loose})" if loose else ""))
        rc, log = switch(site, "on")
        todo = [line for line in log.splitlines() if line.startswith("  ")]
        check(len(todo) >= 5 and any("prize" in line for line in todo) and any("announce" in line for line in todo)
              and any("credited" in line for line in todo), "on lists the prize, the announcement and the credit")
        index.write_text(index.read_text(encoding="utf-8").replace(
            '<p class="potm-placeholder" id="potm-credit"', '<p class="potm-credit" id="potm-credit"', 1), encoding="utf-8")
        rc, log = switch(site, "status")
        check(rc == 0 and len([line for line in log.splitlines() if line.startswith("  ")]) == len(todo) - 1,
              "a placeholder a maintainer wrote leaves the list")
        rc, log = switch(site, "off")
        check(rc == 0 and log.startswith("off:") and "not decided" not in log, "off does not list them")
        print(f"\n{len(failures)} failed" if failures else "\nall passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
