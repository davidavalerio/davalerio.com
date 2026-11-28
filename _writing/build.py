#!/usr/bin/env python3
"""Build davalerio.com/writing/ from the sources in _writing/posts/.

Each source is a file with YAML frontmatter (title, date, kind, optional
subtitle / image / image_alt / image_caption, map) followed by the body: .html
bodies are used as they are, .md bodies go through Pandoc. The filename is
the slug. Run with no arguments; output lands in ../writing/, ../maps/ and
../sitemap.xml, and the homepage's Maps, Writing and Podcasts lists (each between
its own markers in ../index.html) are refreshed.

Maps are defined in _writing/maps/<name>.yaml; each gets a page at
/maps/<name>/ when its definition says `page: true`, and a place in the homepage's
Maps list, by its date, with its thumbnail from _writing/thumbs.yaml.
A piece with `map: <name>` in its frontmatter can turn passages into map steps
(`::: {.map-step view=<view>}`) or set in a map (`::: {.map-embed view=<view>}`).

It also writes the two subscription landing pages under ../subscribe/ (Posts by Email;
_writing/send.py sends).

`--drafts` also builds the pieces in _writing/drafts/ into ../_preview/<slug>/,
which is never published, for reading on a local server (_writing/serve.mjs).
"""
import argparse
import datetime
import email.utils
import hashlib
import html
import json
import os
import re
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, "posts")
DRAFTS = os.path.join(HERE, "drafts")
OUT = os.path.join(ROOT, "writing")
MAP_SRC = os.path.join(HERE, "maps")
MAP_OUT = os.path.join(ROOT, "maps")
PREVIEW = os.path.join(ROOT, "_preview")
SITE = "https://davalerio.com"
KINDS = [("essay", "Essays"), ("paper", "Papers"), ("conversation", "Conversations")]
KIND_LABEL = {"essay": "Essay", "paper": "Paper", "conversation": "Conversation"}

FAVICON = open(os.path.join(HERE, "favicon.txt")).read().strip()


def asset(path):
    """A link to one of the site's own stylesheets or scripts, marked with a hash of its contents,
    so a browser fetches it again when it changes instead of keeping its copy for GitHub Pages' 10 minutes."""
    with open(os.path.join(ROOT, path.lstrip("/")), "rb") as f:
        return "{}?v={}".format(path, hashlib.sha256(f.read()).hexdigest()[:10])


HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light">
    <title>{title}</title>
    <meta name="description" content="{description}">
    <link rel="canonical" href="{url}">
    <meta property="og:type" content="{og_type}">
    <meta property="og:title" content="{og_title}">
    <meta property="og:description" content="{description}">
    <meta property="og:url" content="{url}">{og_image}
    <link rel="alternate" type="application/rss+xml" title="David Valerio — Writing" href="/writing/feed.xml">
    <link rel="icon" type="image/svg+xml" href="{favicon}">
    <link rel="icon" type="image/png" sizes="64x64" href="/favicon.png">
    <link rel="apple-touch-icon" href="/apple-touch-icon.png">
    <link rel="preload" href="/fonts/source-serif-4-normal-latin.woff2" as="font" type="font/woff2" crossorigin>
    <link rel="stylesheet" href="__FONTS__">
    <link rel="stylesheet" href="{style}">{extra_head}
</head>
<body{body_class}>
    <header class="site">
        <a href="/" class="home">David Valerio</a>
        <a href="/#writing">Writing</a>
    </header>
""".replace("__FONTS__", asset("/fonts/fonts.css"))

# A map's own face, preloaded where the map's text uses it
SANS = '\n    <link rel="preload" href="/fonts/source-sans-3-normal-latin.woff2" as="font" type="font/woff2" crossorigin>'

HEAD = HEAD.replace("{style}", asset("/writing/style.css"))

# The plum band at the foot of every page, carrying the Horizon mark in paper; the homepage has its own copy
# (index.html), as does the signup service's page (_subscribe/worker.js).
FOOT = """    <footer class="band"><a href="/" aria-label="Home"><img src="/mark-paper.svg" alt="Horizon mark" width="48" height="48"></a></footer>
</body>
</html>
"""

# Posts by Email: the signup form at the foot of every essay, paper and map page.
# The homepage carries a hand-made copy below its lists (index.html); keep the two in step,
# and the name in step with _subscribe/worker.js and send.py.
SUBSCRIBE_NAME = "Posts by Email"
SUBSCRIBE = """<form class="subscribe" action="https://subscribe.davalerio.com/" method="post">
            <label for="subscribe-email">{}</label>
            <div class="row"><input id="subscribe-email" type="email" name="email" placeholder="you@example.com" autocomplete="email" required><button type="submit">Subscribe</button></div>
            <input class="trap" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
        </form>""".format(SUBSCRIBE_NAME)

# The pages a subscriber lands on after the signup service (_subscribe/) redirects.
NOTICES = {
    "sent": ("Check Your Inbox", "A confirmation link is on its way. Open it and confirm, and new writing will arrive by email."),
    "confirmed": ("You’re Subscribed", "New writing will arrive by email. Every email carries a link to unsubscribe."),
}


def esc(s):
    return html.escape(s or "", quote=True)


# A map's note and credits are markdown: each new line a line break, a blank line a new
# paragraph, lines starting "- " a list, [text](url) a link, and a full web address (https://…)
# a link. Raw HTML is not let through.
TEXT_MD = "markdown+hard_line_breaks+autolink_bare_uris+lists_without_preceding_blankline-raw_html"


def md(s):
    """A map's note or credits as HTML blocks."""
    if not s:
        return ""
    return subprocess.run(["pandoc", "-f", TEXT_MD, "-t", "html", "--wrap=none"], input=s,
                          capture_output=True, text=True, check=True).stdout.strip()


def md_inline(s):
    """The same text run into one line, for a caption: paragraphs and list items joined, links kept."""
    out = re.sub(r"</(p|li)>\s*", " ", md(s))
    out = re.sub(r"<br\s*/?>", " ", re.sub(r"</?(p|ul|ol|li)>", "", out))
    return re.sub(r"\s+", " ", out).strip()


def md_plain(s):
    """The same text as plain words, for a page's description."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", md(s)))).strip()


def load(maps=None, folder=SRC, draft=False):
    maps = load_maps() if maps is None else maps
    posts = []
    if not os.path.isdir(folder):
        return posts
    for name in sorted(os.listdir(folder)):
        slug, ext = os.path.splitext(name)
        if ext not in (".html", ".md"):
            continue
        raw = open(os.path.join(folder, name), encoding="utf-8").read()
        _, fm, body = raw.split("---\n", 2)
        meta = yaml.safe_load(fm)
        if ext == ".md":
            body = subprocess.run(["pandoc", "-f", "markdown", "-t", "html"], input=body,
                                  capture_output=True, text=True, check=True).stdout
        date = meta["date"]
        if not isinstance(date, datetime.date):
            date = datetime.date.fromisoformat(str(date)[:10])
        kind = meta.get("kind", "essay")
        assert kind in KIND_LABEL, "{}: unknown kind {}".format(name, kind)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
        map_name = meta.get("map")
        if map_name:
            assert map_name in maps, "{}: no map named {} in _writing/maps/".format(name, map_name)
            body = map_body(body, map_name, maps[map_name], slug, strict=not draft,
                            page="/_preview/{}/".format(slug) if draft else None)
        posts.append({
            "slug": slug, "title": meta["title"], "date": date, "kind": kind,
            "subtitle": meta.get("subtitle"), "image": meta.get("image"),
            "image_alt": meta.get("image_alt"), "image_caption": meta.get("image_caption"),
            "map": map_name, "credits": maps[map_name].get("credits") if map_name else None,
            "share_image": (still_url(map_name, first_view(body)) if map_name and first_view(body) else None) or meta.get("image"),
            "body": body.strip(),
            "description": meta.get("subtitle") or (text[:157] + "…" if len(text) > 158 else text),
        })
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def og_image(path):
    if not path:
        return ""
    return '\n    <meta property="og:image" content="{}{}">\n    <meta name="twitter:card" content="summary_large_image">'.format(SITE, esc(path))


def post_page(p, maps):
    url = "{}/writing/{}/".format(SITE, p["slug"])
    out = HEAD.format(title=esc(p["title"]) + " — David Valerio", og_title=esc(p["title"]),
                      description=esc(p["description"]), url=url, og_type="article",
                      og_image=og_image(p["share_image"]), favicon=FAVICON,
                      extra_head=map_head(p["map"], maps) if p["map"] else "", body_class="")
    out += '    <main>\n        <article>\n            <header class="post">\n'
    out += '                <p class="meta">{} · <time datetime="{}">{}</time></p>\n'.format(
        KIND_LABEL[p["kind"]], p["date"].isoformat(), p["date"].isoformat())
    out += '                <h1>{}</h1>\n'.format(esc(p["title"]))
    if p["subtitle"]:
        out += '                <p class="subtitle">{}</p>\n'.format(esc(p["subtitle"]))
    out += '            </header>\n'
    if p["image"]:
        out += '            <figure class="feature"><img src="{}" alt="{}">'.format(esc(p["image"]), esc(p["image_alt"]))
        if p["image_caption"]:
            out += '<figcaption>{}</figcaption>'.format(p["image_caption"])
        out += '</figure>\n'
    out += '            <div class="body">\n{}\n            </div>\n        </article>\n'.format(p["body"])
    back = ("/#podcasts", "Podcasts") if p["kind"] == "conversation" else ("/#writing", "Writing")
    form = "" if p["kind"] == "conversation" else SUBSCRIBE + "\n        "
    out += '        <footer class="post">\n        {}<a href="{}">← {}</a>\n        </footer>\n    </main>\n'.format(form, *back)
    return out + FOOT


def notice_page(slug, title, text):
    out = HEAD.format(title=esc(title) + " — David Valerio", og_title=esc(title), description=esc(text),
                      url="{}/subscribe/{}/".format(SITE, slug), og_type="website", og_image="", favicon=FAVICON,
                      extra_head='\n    <meta name="robots" content="noindex">', body_class="")
    out += '    <main>\n        <header class="post">\n            <h1>{}</h1>\n        </header>\n'.format(esc(title))
    out += '        <div class="body">\n            <p>{}</p>\n        </div>\n'.format(esc(text))
    out += '        <footer class="post"><a href="/">← Home</a></footer>\n    </main>\n'
    return out + FOOT


def offline(p):
    """A piece's body as it reads away from the site (the feed and email), with a map's
    credits once, at the end."""
    body = re.sub(r'\s*<(div|span) class="map-credits">.*?</\1>', "", p["body"], flags=re.S)
    if p.get("credits"):
        body += '\n<div class="map-credits">{}</div>'.format(md(p["credits"]))
    return body


# Maps

STEP = re.compile(r'<div class="map-step" data-view="([^"]+)">\n?(.*?)</div>', re.S)
EMBED = re.compile(r'<div class="map-embed" data-view="([^"]+)">\n?(.*?)</div>', re.S)
STILL_SIZE = (1200, 800)


def load_maps():
    maps = {}
    if os.path.isdir(MAP_SRC):
        for name in sorted(os.listdir(MAP_SRC)):
            if name.endswith(".yaml"):
                m = yaml.safe_load(open(os.path.join(MAP_SRC, name), encoding="utf-8"))
                d = m["date"]
                m["date"] = d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d))
                m["revised"] = revised(name[:-5], m["date"])
                maps[name[:-5]] = m
    return maps


def revised(name, published):
    """The day a map last changed, from its own files (definition, tile script, tiles, stills, image, model; not its
    generated page): today while any of them has uncommitted changes, otherwise the date of the last commit to touch
    them. None when that is not after the published date, or when git cannot say."""
    paths = ["_writing/maps/{}.yaml".format(name), "_writing/maps/{}.py".format(name), "maps/{}".format(name),
             ":(exclude)maps/{}/index.html".format(name)]
    git = lambda *a: subprocess.run(["git", "-C", ROOT, *a, "--", *paths], capture_output=True, text=True)
    try:
        status = git("status", "--porcelain")
        if status.returncode:
            return None
        if status.stdout.strip():
            day = datetime.date.today()
        else:
            last = git("log", "-1", "--format=%cs").stdout.strip()
            day = datetime.date.fromisoformat(last) if last else None
    except (OSError, ValueError):
        return None
    return day if day and day > published else None


def still_url(map_name, view):
    return "/maps/{}/stills/{}.jpg".format(map_name, view)


def first_view(body):
    m = re.search(r'data-view="([^"]+)"', body)
    return m.group(1) if m else None


def still(map_name, m, view, strict, link=None, caption=None):
    """The image of one view, which is all a reader sees without the live map:
    in email, in the feed, in print, and with JavaScript off. Its inline styles
    are the ones an email client needs."""
    assert view in m.get("views", {}), "map {} has no view named {}".format(map_name, view)
    path = os.path.join(ROOT, still_url(map_name, view).lstrip("/"))
    if not os.path.exists(path):
        assert not strict, "missing still {} (run _writing/stills.mjs {})".format(path, map_name)
        print("note: still not made yet:", still_url(map_name, view))
    v = m["views"][view]
    img = '<img src="{}" alt="{}" width="{}" height="{}" style="display: block; width: 100%; max-width: 100%; height: auto;">'.format(
        still_url(map_name, view), esc(v.get("alt")), STILL_SIZE[0], STILL_SIZE[1])
    if link:
        img = '<a href="{}">{}</a>'.format(link, img)
    caption = caption if caption is not None else esc(v.get("caption", ""))
    cap = "<figcaption>{}</figcaption>".format(caption) if caption else ""
    return '<figure class="still" style="margin: 1.6em 0;">{}{}</figure>'.format(img, cap)


def map_body(body, map_name, m, slug, strict, page=None):
    """Group consecutive map steps around one pinned map, give each change of
    view a still and an anchor, and turn embeds into maps with stills. A step
    holds plain prose: a fenced div or code block inside one would end it early."""
    page = page or "/writing/{}/".format(slug)
    out, pos, group, anchors = [], 0, [], {}
    credits = '<div class="map-credits">{}</div>'.format(md(m["credits"])) if m.get("credits") else ""

    def flush():
        if not group:
            return
        html_steps, previous = [], None
        for view, inner in group:
            ident, fig = "", ""
            if view != previous:
                anchors[view] = anchors.get(view, 0) + 1
                anchor = "view-" + view + ("-{}".format(anchors[view]) if anchors[view] > 1 else "")
                ident = ' id="{}"'.format(anchor)
                fig = still(map_name, m, view, strict, link=page + "#" + anchor)
            html_steps.append('<section class="map-step" data-view="{}"{}>\n{}{}</section>'.format(view, ident, fig, inner))
            previous = view
        out.append('<div class="scrolly" data-map="{}">\n<div class="map-stage"></div>\n<div class="steps">\n{}\n</div>\n</div>\n{}'.format(
            map_name, "\n".join(html_steps), credits))
        group.clear()

    for match in STEP.finditer(body):
        between = body[pos:match.start()]
        if group and between.strip():
            flush()
        if between.strip() or not group:
            out.append(between)
        group.append((match.group(1), match.group(2)))
        pos = match.end()
    flush()
    out.append(body[pos:])
    body = "".join(out)
    def embed(e):
        view, caption = e.group(1), e.group(2).strip()
        img = still(map_name, m, view, strict, caption="")
        img = img.replace('<figure class="still" style="margin: 1.6em 0;">', '<div class="still">').replace("</figure>", "</div>")
        credit = '<span class="map-credits">{}</span>'.format(md_inline(m["credits"])) if m.get("credits") else ""
        cap = " ".join(x for x in (re.sub(r"</?p>", "", caption), credit) if x)
        cap = "<figcaption>{}</figcaption>".format(cap) if cap else ""
        return '<figure style="margin: 1.6em 0;"><div class="map-embed" data-map="{}" data-view="{}">{}</div>{}</figure>'.format(
            map_name, view, img, cap)

    body = EMBED.sub(embed, body)
    note = '<p class="map-note"><em>This piece is told with maps. <a href="{}">Read it on davalerio.com</a> to explore them.</em></p>\n'.format(page)
    return note + body


def map_head(map_name, maps):
    data = json.dumps({k: maps[map_name][k] for k in ("start", "bounds", "exaggeration", "sources", "layers", "labels", "views", "popup")
                       if k in maps[map_name]}, separators=(",", ":"))
    return (SANS +
            '\n    <link rel="stylesheet" href="/maps/lib/maplibre-gl.css">\n    <link rel="stylesheet" href="{}">'
            '\n    <script src="/maps/lib/pmtiles.js" defer></script>\n    <script type="module" src="{}"></script>'
            '\n    <script type="application/json" id="map-def-{}">{}</script>').format(
                asset("/maps/map.css"), asset("/maps/map.js"), map_name, data.replace("</", "<\\/"))


def legend(groups):
    """The key beneath a map page's map: groups of swatches, each a fill, a dot or a line
    (`shape`, with `dash` dashed or dotted), coloured with a hex value or a --map token."""
    out = '        <div class="map-legend">\n'
    for g in groups:
        out += '            <div>\n'
        if g.get("heading"):
            out += '                <h3>{}</h3>\n'.format(esc(g["heading"]))
        out += '                <ul>\n'
        for item in g["items"]:
            color = item["color"]
            color = "var({})".format(color) if color.startswith("--") else color
            shape = item.get("shape", "fill")
            cls = "swatch" + ("" if shape == "fill" else " " + shape) + (" " + item["dash"] if item.get("dash") else "")
            style = "border-top-color: {}".format(color) if shape == "line" else "background: {}".format(color)
            code = '<span class="code">{}</span>'.format(esc(item["code"])) if item.get("code") else ""
            out += '                    <li><span class="{}" style="{}"></span>{}<span>{}</span></li>\n'.format(
                cls, style, code, esc(item["label"]))
        out += '                </ul>\n            </div>\n'
    return out + '        </div>\n'


def map_page(name, m):
    url = "{}/maps/{}/".format(SITE, name)
    interactive = "views" in m
    share = still_url(name, m["start"]) if interactive else "/maps/{}/{}".format(name, m["image"])
    head = (SANS +
            '\n    <link rel="stylesheet" href="{}">'.format(asset("/maps/map.css")))
    if m.get("model"):
        head += '\n    <script type="module" src="{}"></script>'.format(asset("/maps/model.js"))
    out = HEAD.format(title=esc(m["title"]) + " — David Valerio", og_title=esc(m["title"]),
                      description=esc(md_plain(m.get("note"))), url=url, og_type="article", og_image=og_image(share),
                      favicon=FAVICON, extra_head=map_head(name, {name: m}) if interactive else head, body_class=' class="wide"')
    out += '    <main>\n        <header class="post">\n'
    rev = ' · Revised <time datetime="{0}">{0}</time>'.format(m["revised"].isoformat()) if m.get("revised") else ""
    out += '            <p class="meta">Map · <time datetime="{0}">{0}</time>{1}</p>\n'.format(m["date"].isoformat(), rev)
    out += '            <h1>{}</h1>\n'.format(esc(m["title"]))
    if m.get("note"):
        out += '            <div class="subtitle">{}</div>\n'.format(md(m["note"]))
    out += '        </header>\n'
    if interactive:
        out += '        <div class="map-full" data-map="{}" data-view="{}">{}</div>\n'.format(
            name, m["start"], still(name, m, m["start"], strict=False).replace(' style="margin: 1.6em 0;"', ""))
    else:
        # A model (a turnable stack drawn by maps/model.js) takes the image's place once it draws.
        figure = '<figure class="showpiece"><a href="{0}"><img src="{0}" alt="{1}"></a></figure>'.format(
            "/maps/{}/{}".format(name, m["image"]), esc(m.get("alt")))
        if m.get("model"):
            figure = '<div class="model-view" data-model="/maps/{}/{}/">{}</div>'.format(name, m["model"], figure)
        out += '        {}\n'.format(figure)
    if m.get("legend"):
        out += legend(m["legend"])
    if m.get("credits"):
        out += '        <div class="map-credits">{}</div>\n'.format(md(m["credits"]))
    if m.get("pdf"):
        out += '        <p class="map-downloads"><a href="/maps/{}/{}">Download the PDF</a></p>\n'.format(name, m["pdf"])
    out += '        <footer class="post">\n        {}\n        <a href="/#maps">← Maps</a>\n        </footer>\n    </main>\n'.format(SUBSCRIBE)
    return out + FOOT


# The Maps, Writing and Podcasts lists each sit between their own markers in index.html.
MARKERS = {name: ("<!-- {}:start (generated by _writing/build.py — do not edit by hand) -->".format(name),
                  "<!-- {}:end -->".format(name)) for name in ("maps", "writing", "podcasts")}

FORWARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="0; url=/#writing">
  <link rel="canonical" href="{0}/">
  <title>Writing — David Valerio</title>
</head>
<body>
  <p>The writing is on the <a href="/#writing">homepage</a>.</p>
</body>
</html>
""".format(SITE)


def homepage_list(posts, maps):
    """The Writing list (essays and papers) and the Podcasts list (the conversations), each newest
    first, the first seven shown and the rest behind an All toggle."""
    def row(p):
        # A conversation is listed by its guest's name ("Candice Ammori on ..." becomes "Candice Ammori"),
        # an appearance on another show by the show's name, linked to its episode.
        if p["kind"] == "appearance":
            href, title = p["url"], p["show"]
        else:
            href = "/writing/{}/".format(p["slug"])
            title = p["title"].split(" on ")[0] if p["kind"] == "conversation" else p["title"]
        return '            <li><a href="{}">{}</a><time datetime="{}">{}</time></li>\n'.format(
            href, esc(title), p["date"].isoformat(), p["date"].isoformat())

    def column(ident, title, items, shown=7):
        out = '    <section id="{}">\n        <h2>{}</h2>\n        <ul class="list">\n'.format(ident, title)
        out += "".join(row(p) for p in items[:shown]) + '        </ul>\n'
        # The toggle and the Back to Top link share the line below the list.
        out += '        <div class="more">\n'
        if len(items) > shown:
            out += '        <details>\n        <summary>All {}</summary>\n        <ul class="list">\n'.format(title)
            out += "".join(row(p) for p in items[shown:]) + '        </ul>\n        </details>\n'
        return out + '        <a class="top" href="#home">Back to Top</a>\n        </div>\n    </section>\n'

    return {"writing": column("writing", "Writing", [p for p in posts if p["kind"] != "conversation"]),
            "podcasts": column("podcasts", "Podcasts", sorted([p for p in posts if p["kind"] == "conversation"] + appearances(),
                                                    key=lambda p: p["date"], reverse=True))}


def maps_list(maps, shown=3):
    """The Maps list: every map with a page, newest first (maps of the same day in the order posts take), each with
    its thumbnail from _writing/thumbs.yaml, the first three shown and the rest behind All Maps."""
    thumbs = yaml.safe_load(open(os.path.join(HERE, "thumbs.yaml"), encoding="utf-8")) or {}
    items = sorted(((n, m) for n, m in maps.items() if m.get("page")), key=lambda x: (x[1]["date"], x[0]), reverse=True)
    missing = [n for n, _ in items if n not in thumbs]
    if missing:
        sys.exit("no thumbnail in _writing/thumbs.yaml for: " + ", ".join(missing))

    def row(i, name, m):
        t, href = thumbs[name], "/maps/{}/".format(name)
        extra = ' loading="lazy"' if i else ""
        if t.get("position"):
            extra += ' style="object-position: {}"'.format(esc(t["position"]))
        return ('            <li><div><a href="{0}">{1}</a><time datetime="{2}">{2}</time></div><a class="thumb" href="{0}">'
                '<img src="{3}" alt="{4}" width="1200" height="800"{5}></a></li>\n').format(
            href, esc(m["title"]), m["date"].isoformat(), esc(t["image"]), esc(t["alt"]), extra)

    rows = [row(i, n, m) for i, (n, m) in enumerate(items)]
    out = '    <section id="maps">\n        <h2>Maps</h2>\n        <ul class="maplist">\n'
    out += "".join(rows[:shown]) + '        </ul>\n        <div class="more">\n'
    if len(rows) > shown:
        out += '        <details>\n        <summary>All Maps</summary>\n        <ul class="maplist">\n'
        out += "".join(rows[shown:]) + '        </ul>\n        </details>\n'
    return out + '        <a class="top" href="#home">Back to Top</a>\n        </div>\n    </section>\n'


def appearances():
    """Episodes of other shows David was a guest on (_writing/appearances.yaml)."""
    path = os.path.join(HERE, "appearances.yaml")
    if not os.path.exists(path):
        return []
    out = []
    for a in yaml.safe_load(open(path, encoding="utf-8")) or []:
        d = a["date"]
        out.append(dict(a, kind="appearance", date=d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d))))
    return out


def update_homepage(posts, maps):
    path = os.path.join(ROOT, "index.html")
    page = open(path, encoding="utf-8").read()
    lists = {"maps": maps_list(maps)}
    if posts is not None:
        lists.update(homepage_list(posts, maps))
    for name, html in lists.items():
        start, end = MARKERS[name]
        a, b = page.index(start) + len(start), page.index(end)
        page = page[:a] + "\n" + html + "    " + page[b:]
    write(path, page)


def feed(posts):
    items = ""
    for p in posts[:20]:
        url = "{}/writing/{}/".format(SITE, p["slug"])
        body = re.sub(r'(href|src)="/', r'\1="' + SITE + "/", offline(p)).replace("]]>", "]]&gt;")
        stamp = datetime.datetime.combine(p["date"], datetime.time(12), datetime.timezone.utc)
        items += ("    <item>\n      <title>{}</title>\n      <link>{}</link>\n      <guid>{}</guid>\n"
                  "      <pubDate>{}</pubDate>\n      <description>{}</description>\n"
                  "      <content:encoded><![CDATA[{}]]></content:encoded>\n    </item>\n").format(
            esc(p["title"]), url, url, email.utils.format_datetime(stamp), esc(p["description"]), body)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">\n'
            '  <channel>\n    <title>David Valerio — Writing</title>\n    <link>{0}/#writing</link>\n'
            '    <atom:link href="{0}/writing/feed.xml" rel="self" type="application/rss+xml"/>\n'
            '    <description>Essays, papers, and conversations by David Valerio.</description>\n'
            '    <language>en</language>\n{1}  </channel>\n</rss>\n').format(SITE, items)


def sitemap(posts, maps):
    urls = [(SITE + "/", posts[0]["date"])]
    urls += [("{}/writing/{}/".format(SITE, p["slug"]), p["date"]) for p in posts]
    urls += [("{}/maps/{}/".format(SITE, n), m.get("revised") or m["date"]) for n, m in sorted(maps.items()) if m.get("page")]
    rows = "".join("  <url><loc>{}</loc>{}</url>\n".format(
        u, "<lastmod>{}</lastmod>".format(d.isoformat()) if d else "") for u, d in urls)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + '</urlset>\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drafts", action="store_true", help="also build _writing/drafts/ into _preview/")
    parser.add_argument("--maps-only", action="store_true", help="build only the map pages (used by stills.mjs)")
    parser.add_argument("--md", action="store_true", help="print a map note or credits read from stdin as HTML (used by the workbench)")
    args = parser.parse_args()
    if args.md:
        print(md(sys.stdin.read()))
        return
    maps = load_maps()
    if args.maps_only:
        for name, m in maps.items():
            write(os.path.join(MAP_OUT, name, "index.html"), map_page(name, m))
        # the sitemap's map entries take the same dates, without rebuilding the writing
        path = os.path.join(ROOT, "sitemap.xml")
        if os.path.exists(path):
            text = open(path, encoding="utf-8").read()
            for name, m in maps.items():
                if m.get("page"):
                    text = re.sub(r"(<loc>{}/maps/{}/</loc><lastmod>)[0-9-]+(</lastmod>)".format(re.escape(SITE), re.escape(name)),
                                  lambda x: x.group(1) + (m.get("revised") or m["date"]).isoformat() + x.group(2), text)
            write(path, text)
        update_homepage(None, maps)
        return
    posts = load(maps)
    for p in posts:
        write(os.path.join(OUT, p["slug"], "index.html"), post_page(p, maps))
    for name, m in maps.items():
        write(os.path.join(MAP_OUT, name, "index.html"), map_page(name, m))
    write(os.path.join(OUT, "index.html"), FORWARD)
    for slug, (title, text) in NOTICES.items():
        write(os.path.join(ROOT, "subscribe", slug, "index.html"), notice_page(slug, title, text))
    update_homepage(posts, maps)
    write(os.path.join(OUT, "feed.xml"), feed(posts))
    write(os.path.join(ROOT, "sitemap.xml"), sitemap(posts, maps))
    print("built {} pieces and {} maps".format(len(posts), len(maps)))
    if args.drafts:
        for p in load(maps, DRAFTS, draft=True):
            write(os.path.join(PREVIEW, p["slug"], "index.html"), post_page(p, maps))
            print("draft: /_preview/{}/".format(p["slug"]))


if __name__ == "__main__":
    main()
