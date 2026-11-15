#!/usr/bin/env python3
"""Show Photo of the Month on the whole site, or hide it again, in one step; or say which it is.

Off (while no contest runs): nothing of the contest is shown or linked outside its own two pages.
No page's navbar or footer links to it, sitemap.xml leaves out its two pages, both pages have
<meta name="robots" content="noindex">, every element marked data-potm-switch (the announcement,
the winner's credit) is hidden, and the home page's winner section (data-potm-featured) is marked
data-potm-off, so potm.js leaves it hidden even when data/photo-of-the-month.json has a winner. On
is all of that undone: "Photo of the Month" under Community in every navbar (the current page on
its own page) and footer, right after Photos; its two sitemap entries after the gallery's; no
noindex; the marked elements shown; and the winner section without data-potm-off, so potm.js shows
it once there is a winner.

  tools/potm_switch.py status            # on or off (exit 0), or what is half switched (exit 1)
  tools/potm_switch.py on                # then lists what is still marked as not decided
  tools/potm_switch.py off
  tools/potm_switch.py on --root DIR     # another copy of the site (default: this repository)

Switching on and then off again (or off and then on) gives back the same files byte for byte, and
switching to the state the site is already in changes nothing. A half switched site is finished in
the state asked for. A page that has lost the line the switch writes next to (its Photos link, its
canonical link), or has a part written in another form than the switch's own (a link, a noindex,
a mark with other attributes, spacing or case), stops the switch before any file is written.
tools/check_site.py uses site_state() to refuse a site that is half switched or has such a part,
and shown_links() to refuse an off site that still shows a link to the contest.

Text still to be decided (the prize, the announcement, the credit) is marked class="potm-placeholder";
`status` and `on` list it. Writing it is the maintainers' job, not this script's."""
import argparse
import html.parser
import re
import sys
from pathlib import Path

PAGE = "photo-of-the-month.html"
RULES = "photo-of-the-month-rules.html"
SITE = "https://forza.dyotson.dev"
NAV_LINK = '<a href="photo-of-the-month.html">Photo of the Month</a>'
NAV_LINK_HERE = '<a href="photo-of-the-month.html" aria-current="page">Photo of the Month</a>'
FOOT_LINK = '<li><a href="photo-of-the-month.html">Photo of the Month</a></li>'
NOINDEX = '<meta name="robots" content="noindex">\n'
SITEMAP_ENTRIES = (
    f"  <url>\n    <loc>{SITE}/{PAGE}</loc>\n    <changefreq>daily</changefreq>\n    <priority>0.8</priority>\n  </url>\n"
    f"  <url>\n    <loc>{SITE}/{RULES}</loc>\n    <changefreq>monthly</changefreq>\n    <priority>0.5</priority>\n  </url>\n")
SITEMAP_AFTER = re.compile(r"^  <url>\n    <loc>" + re.escape(f"{SITE}/gallery.html") + r"</loc>\n(?:(?!  </url>\n).*\n)*?  </url>\n",
                           re.M)
NAV_PHOTOS = re.compile(r'^([ \t]*)<a href="gallery\.html"(?: aria-current="page")?>Photos</a>\n', re.M)
NAV_POTM = re.compile(r'^[ \t]*<a href="photo-of-the-month\.html"[^>]*>Photo of the Month</a>\n', re.M)
FOOT_PHOTOS = re.compile(r'^([ \t]*)<li><a href="gallery\.html">Photos</a></li>\n', re.M)
FOOT_POTM = re.compile(r'^[ \t]*<li><a href="photo-of-the-month\.html"[^>]*>Photo of the Month</a></li>\n', re.M)
SWITCH_ATTR = re.compile(r"\sdata-potm-switch( hidden)?(?=[\s>])")
SWITCH_TAG = re.compile(r"<[a-z][^<>]*\sdata-potm-switch(?=[\s>])[^<>]*>")
FEATURED_TAG = re.compile(r"<[a-z][^<>]*\sdata-potm-featured(?=[\s>])[^<>]*>")
FEATURED, OFF_MARK = " data-potm-featured", " data-potm-off"
OFF_AT = re.compile(re.escape(FEATURED + OFF_MARK) + r"(?=[\s>])")
# Any form a browser reads: other case, other spacing, a value, other quotes.
ANY_ATTR = {name: re.compile(r"<[a-z][^<>]*\s" + name + r"(?=[\s=/>])", re.I)
            for name in ("data-potm-switch", "data-potm-featured", "data-potm-off")}
HIDDEN_ATTR = re.compile(r"\shidden(?=[\s=/>])", re.I)
PLACEHOLDER = re.compile(r'<(p|div|span|li)\b([^>]*\bclass="[^"]*\bpotm-placeholder\b[^"]*"[^>]*)>(.*?)</\1>', re.S)


class Broken(Exception):
    """The page has lost the line the switch writes next to, or has a part the switch did not write."""


def pages(root):
    return sorted(p for p in root.glob("*.html") if p.is_file())


def is_contest(href):
    """Whether a link goes to one of the contest's two pages, however it is written."""
    href = (href or "").strip().lower()
    if href.startswith("//"):
        href = "https:" + href
    m = re.match(r"^(?:https?://([^/?#]*))?([^?#]*)", href)
    if m.group(1) is not None and m.group(1) != SITE.split("://")[1]:
        return False
    return m.group(2).rsplit("/", 1)[-1] in (PAGE, RULES)


class Links(html.parser.HTMLParser):
    """Every link of a page or part of one (href, whether it is inside the navbar or the footer,
    whether an element the switch hides holds it), and every robots meta."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.robots, self.stack = [], [], []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        frame = self.stack[-1][1:] if self.stack else (False, False)
        edge = frame[0] or tag == "footer" or (tag == "header" and "topbar" in a.get("class", "").split())
        hid = frame[1] or "data-potm-off" in a or ("data-potm-switch" in a and "hidden" in a)
        if tag == "a" and "href" in a:
            self.links.append((a["href"], edge, hid))
        if tag == "meta" and a.get("name", "").strip().lower() in ("robots", "googlebot", "bingbot"):
            self.robots.append(a.get("content", ""))
        if tag not in self.VOID:
            self.stack.append((tag, edge, hid))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_endtag(self, tag):
        for n in range(len(self.stack) - 1, -1, -1):
            if self.stack[n][0] == tag:
                del self.stack[n:]
                break


def links_of(text):
    p = Links()
    p.feed(text)
    p.close()
    return p


def shown_links(text):
    """The links to the contest outside the navbar and the footer that no element the switch hides
    holds: [href]. While the contest is off there must be none outside its own two pages."""
    return [href for href, edge, hid in links_of(text).links if is_contest(href) and not edge and not hid]


def block(text, start, end):
    """The span of the first start...end block of the page, or None."""
    a = text.find(start)
    if a < 0:
        return None
    b = text.find(end, a)
    return None if b < 0 else (a, b + len(end))


# ---- the parts: each says whether it is on or off, and turns one into the other ----
# A part is (name, state, on_text, off_text): its file's text with that part on, and with it off.

def link_part(name, text, span, anchor, line, ours):
    """A link written on its own line right after the anchor line (the Photos link) inside span."""
    a, b = span
    inner = text[a:b]
    found = list(ours.finditer(inner))
    if sum(is_contest(href) for href, _, _ in links_of(inner).links) != len(found):
        raise Broken(f"{name}: has a link to Photo of the Month the switch did not write "
                     "(other attributes, spacing, case or place than its own line after Photos)")
    anchors = list(anchor.finditer(inner))
    if len(anchors) != 1:
        raise Broken(f"{name}: needs exactly one Photos link to write next to ({len(anchors)} found)")
    m = anchors[0]
    mine = m.group(1) + line + "\n"
    if not found:
        on = inner[:m.end()] + mine + inner[m.end():]
        return "off", text[:a] + on + text[b:], text
    if len(found) > 1 or found[0].start() != m.end() or found[0].group(0) != mine:
        raise Broken(f"{name}: has a link to Photo of the Month the switch did not write (not its one line after Photos)")
    off = inner[:m.end()] + inner[found[0].end():]
    return "on", text, text[:a] + off + text[b:]


def page_parts(page, text):
    parts = []
    span = block(text, '<header class="topbar">', "</header>")
    if span:
        line = NAV_LINK_HERE if page.name == PAGE else NAV_LINK
        parts.append((f"{page.name} navbar",) + link_part(f"{page.name} navbar", text, span, NAV_PHOTOS, line, NAV_POTM))
    span = block(text, "<footer>", "</footer>")
    if span:
        parts.append((f"{page.name} footer",) + link_part(f"{page.name} footer", text, span, FOOT_PHOTOS, FOOT_LINK, FOOT_POTM))
    if page.name in (PAGE, RULES):
        canonical = f'<link rel="canonical" href="{SITE}/{page.name}">\n'
        if text.count(canonical) != 1:
            raise Broken(f"{page.name}: needs its one canonical link to write noindex after")
        at = text.index(canonical) + len(canonical)
        # Every robots meta that says noindex, in any form: other attribute order, quotes, case.
        noindex = [c for c in links_of(text).robots if re.search(r"\b(noindex|none)\b", c, re.I)]
        if len(noindex) != text[at:].startswith(NOINDEX):
            raise Broken(f"{page.name}: has a noindex the switch did not write (right after the canonical link)")
        if text[at:].startswith(NOINDEX):
            parts.append((f"{page.name} noindex", "off", text[:at] + text[at + len(NOINDEX):], text))
        else:
            parts.append((f"{page.name} noindex", "on", text, text[:at] + NOINDEX + text[at:]))
    for attr, ours in (("data-potm-switch", SWITCH_TAG), ("data-potm-featured", FEATURED_TAG)):
        if len(ANY_ATTR[attr].findall(text)) != len(ours.findall(text)):
            raise Broken(f"{page.name}: has a {attr} the switch did not write (write it as {attr}, lower case, "
                         "with no value)")
    if len(ANY_ATTR["data-potm-off"].findall(text)) != len(OFF_AT.findall(text)):
        raise Broken(f"{page.name}: has a data-potm-off the switch did not write (right after data-potm-featured)")
    for n, tag in enumerate(SWITCH_TAG.finditer(text)):
        t = tag.group(0)
        m = SWITCH_ATTR.search(t)
        if HIDDEN_ATTR.search(t[:m.start()] + t[m.end():]):
            raise Broken(f"{page.name}: write hidden right after data-potm-switch, nothing else: {t}")
        ident = re.search(r'\sid="([^"]*)"', t)
        name = f"{page.name} " + (f"#{ident.group(1)}" if ident else f"data-potm-switch {n + 1}")
        a, b = tag.start() + m.start(), tag.start() + m.end()
        if m.group(1):
            parts.append((name, "off", text[:a] + " data-potm-switch" + text[b:], text))
        else:
            parts.append((name, "on", text, text[:a] + " data-potm-switch hidden" + text[b:]))
    for n, tag in enumerate(FEATURED_TAG.finditer(text)):
        t = tag.group(0)
        if not HIDDEN_ATTR.search(t):
            raise Broken(f"{page.name}: an element marked data-potm-featured must start hidden (potm.js shows it): {t}")
        ident = re.search(r'\sid="([^"]*)"', t)
        name = f"{page.name} " + (f"#{ident.group(1)}" if ident else f"data-potm-featured {n + 1}")
        a = tag.start() + t.index(FEATURED) + len(FEATURED)
        if OFF_AT.match(text, a - len(FEATURED)):
            parts.append((name, "off", text[:a] + text[a + len(OFF_MARK):], text))
        else:
            parts.append((name, "on", text, text[:a] + OFF_MARK + text[a:]))
    return parts


def sitemap_part(text):
    have = [loc for loc in (PAGE, RULES) if re.search(re.escape(loc), text, re.I)]
    if text.count(SITEMAP_ENTRIES) == 1:
        at = text.index(SITEMAP_ENTRIES)
        anchor = list(SITEMAP_AFTER.finditer(text))
        again = [loc for loc in (PAGE, RULES) if len(re.findall(re.escape(loc), text, re.I)) != 1]
        if again:
            raise Broken(f"sitemap.xml: {', '.join(again)} listed again, in a form the switch did not write")
        if len(anchor) != 1 or anchor[0].end() != at:
            raise Broken("sitemap.xml: the Photo of the Month entries are not right after the gallery's")
        return "on", text, text[:at] + text[at + len(SITEMAP_ENTRIES):]
    if have:
        raise Broken(f"sitemap.xml: {', '.join(have)} listed in a form the switch did not write "
                     "(not its two entries after the gallery's)")
    anchor = list(SITEMAP_AFTER.finditer(text))
    if len(anchor) != 1:
        raise Broken("sitemap.xml: needs the gallery's entry to write after")
    at = anchor[0].end()
    return "off", text[:at] + SITEMAP_ENTRIES + text[at:], text


def read(path):
    return path.read_bytes().decode("utf-8")


def parts_of(root):
    """Every part of the site the switch touches, [(file, name, state)], and the problems."""
    parts, problems = [], []
    for page in pages(root):
        try:
            for name, state, _, _ in page_parts(page, read(page)):
                parts.append((page, name, state))
        except Broken as e:
            problems.append(str(e))
    sitemap = root / "sitemap.xml"
    if sitemap.is_file():
        try:
            parts.append((sitemap, "sitemap.xml", sitemap_part(read(sitemap))[0]))
        except Broken as e:
            problems.append(str(e))
    else:
        problems.append("sitemap.xml: missing")
    return parts, problems


def switched(path, text, want):
    """The file's text with every part of it in the state wanted, one part at a time."""
    while True:
        parts = [("sitemap.xml",) + sitemap_part(text)] if path.name == "sitemap.xml" else page_parts(path, text)
        todo = [p for p in parts if p[1] != want]
        if not todo:
            return text
        _, _, on, off = todo[0]
        text = on if want == "on" else off


def site_state(root):
    """("on" or "off", []) for a site wholly in one state, else (None, [what is wrong])."""
    parts, problems = parts_of(Path(root))
    on = [name for _, name, state in parts if state == "on"]
    off = [name for _, name, state in parts if state == "off"]
    if problems:
        return None, problems
    if on and off:
        few, state = (on, "on") if len(on) <= len(off) else (off, "off")
        shown = ", ".join(few[:8]) + (f" and {len(few) - 8} more" if len(few) > 8 else "")
        return None, [f"half switched: {len(on)} parts on, {len(off)} off; {state}: {shown} "
                      "(tools/potm_switch.py on or off finishes it)"]
    return ("on" if on else "off"), []


def undecided(root):
    """What the pages still mark as not decided (class="potm-placeholder"): [(page, its text)]."""
    out = []
    for page in pages(Path(root)):
        for m in PLACEHOLDER.finditer(read(page)):
            words = " ".join(re.sub(r"<[^>]+>", "", m.group(3)).split())
            out.append((page.name, words if len(words) <= 90 else words[:87] + "..."))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("on", "off", "status"))
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                    help="the site's folder (default: this repository)")
    args = ap.parse_args()
    root = args.root
    state, problems = site_state(root)
    if args.action == "status":
        print(state or "\n".join(["half or broken:"] + problems))
        todo = undecided(root)
        if todo:
            print("marked as not decided (class potm-placeholder):")
            for name, words in todo:
                print(f"  {name}: {words}")
        return 0 if state else 1
    if problems and not problems[0].startswith("half switched"):
        print("\n".join(problems) + "\nnothing was changed", file=sys.stderr)
        return 2
    if state == args.action:
        print(f"already {state}: nothing changed")
        return 0
    paths = pages(root) + [root / "sitemap.xml"]
    new = {}
    try:
        for path in paths:
            old = read(path)
            text = switched(path, old, args.action)
            if text != old:
                new[path] = text
    except Broken as e:
        print(f"{e}\nnothing was changed", file=sys.stderr)
        return 2
    for path, text in new.items():
        path.write_bytes(text.encode("utf-8"))
    after, problems = site_state(root)
    print(f"{args.action}: {len(new)} files changed")
    if after != args.action:
        print("\n".join(problems), file=sys.stderr)
        return 2
    todo = undecided(root)
    if args.action == "on" and todo:
        print("still marked as not decided (class potm-placeholder): write these before the contest starts")
        for name, words in todo:
            print(f"  {name}: {words}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
