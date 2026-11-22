#!/usr/bin/env python3
"""Build davalerio.com/writing/ from the sources in _writing/posts/.

Each source is a file with YAML frontmatter (title, date, kind, optional
subtitle / image / image_alt / image_caption, map) followed by the body: .html
bodies are used as they are, .md bodies go through Pandoc. The filename is
the slug. Run with no arguments; output lands in ../writing/, ../maps/ and
../sitemap.xml, and the grouped list on the homepage (between the writing
markers in ../index.html) is refreshed.

Maps are defined in _writing/maps/<name>.yaml; each gets a page at
/maps/<name>/, listed on the homepage when its definition says `page: true`.
A piece with `map: <name>` in its frontmatter can turn passages into map steps
(`::: {.map-step view=<view>}`) or set in a map (`::: {.map-embed view=<view>}`).

`--drafts` also builds the pieces in _writing/drafts/ into ../_preview/<slug>/,
which is never published, for reading on a local server (_writing/serve.mjs).
"""
import argparse
import datetime
import email.utils
import html
import json
import os
import re
import subprocess

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

HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light dark">
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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400;1,8..60,600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/writing/style.css">{extra_head}
</head>
<body{body_class}>
    <header class="site">
        <a href="/" class="home">David Valerio</a>
        <a href="/#writing">Writing</a>
    </header>
"""

FOOT = """</body>
</html>
"""


def esc(s):
    return html.escape(s or "", quote=True)


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
        map_name = meta.get("map")
        if map_name:
            assert map_name in maps, "{}: no map named {} in _writing/maps/".format(name, map_name)
            body = map_body(body, map_name, maps[map_name], slug, strict=not draft)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
        posts.append({
            "slug": slug, "title": meta["title"], "date": date, "kind": kind,
            "subtitle": meta.get("subtitle"), "image": meta.get("image"),
            "image_alt": meta.get("image_alt"), "image_caption": meta.get("image_caption"),
            "map": map_name,
            "share_image": meta.get("image") or (still_url(map_name, first_view(body)) if map_name else None),
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
    out += '        <footer class="post"><a href="/#writing">← Writing</a></footer>\n    </main>\n'
    return out + FOOT


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
                maps[name[:-5]] = m
    return maps


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


def map_body(body, map_name, m, slug, strict):
    """Group consecutive map steps around one pinned map, give each change of
    view a still and an anchor, and turn embeds into maps with stills."""
    page = "/writing/{}/".format(slug)
    out, pos, group, last_end = [], 0, [], None

    def flush():
        if not group:
            return
        html_steps, previous, anchors = [], None, set()
        for view, inner in group:
            anchor = "view-" + view
            ident = ' id="{}"'.format(anchor) if anchor not in anchors else ""
            anchors.add(anchor)
            fig = still(map_name, m, view, strict, link=page + "#" + anchor) if view != previous else ""
            html_steps.append('<section class="map-step" data-view="{}"{}>\n{}{}</section>'.format(view, ident, fig, inner))
            previous = view
        out.append('<div class="scrolly" data-map="{}">\n<div class="map-stage"></div>\n<div class="steps">\n{}\n</div>\n</div>'.format(
            map_name, "\n".join(html_steps)))
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
        cap = "<figcaption>{}</figcaption>".format(re.sub(r"</?p>", "", caption)) if caption else ""
        return '<figure style="margin: 1.6em 0;"><div class="map-embed" data-map="{}" data-view="{}">{}</div>{}</figure>'.format(
            map_name, view, img, cap)

    body = EMBED.sub(embed, body)
    note = '<p class="map-note"><em>This piece is told with maps. <a href="{}">Read it on davalerio.com</a> to explore them.</em></p>\n'.format(page)
    return note + body


def map_head(map_name, maps):
    data = json.dumps({k: maps[map_name][k] for k in ("start", "bounds", "exaggeration", "sources", "layers", "labels", "views")
                       if k in maps[map_name]}, separators=(",", ":"))
    return ('\n    <link rel="stylesheet" href="/maps/lib/maplibre-gl.css">\n    <link rel="stylesheet" href="/maps/map.css">'
            '\n    <script src="/maps/lib/pmtiles.js" defer></script>\n    <script type="module" src="/maps/map.js"></script>'
            '\n    <script type="application/json" id="map-def-{}">{}</script>').format(map_name, data.replace("</", "<\\/"))


def map_page(name, m):
    url = "{}/maps/{}/".format(SITE, name)
    interactive = "views" in m
    share = still_url(name, m["start"]) if interactive else "/maps/{}/{}".format(name, m["image"])
    out = HEAD.format(title=esc(m["title"]) + " — David Valerio", og_title=esc(m["title"]),
                      description=esc(m.get("note", "")), url=url, og_type="article", og_image=og_image(share),
                      favicon=FAVICON, extra_head=map_head(name, {name: m}) if interactive else
                      '\n    <link rel="stylesheet" href="/maps/map.css">', body_class=' class="wide"')
    out += '    <main>\n        <header class="post">\n'
    out += '            <p class="meta">Map · <time datetime="{0}">{0}</time></p>\n'.format(m["date"].isoformat())
    out += '            <h1>{}</h1>\n'.format(esc(m["title"]))
    if m.get("note"):
        out += '            <p class="subtitle">{}</p>\n'.format(esc(m["note"]))
    out += '        </header>\n'
    if interactive:
        out += '        <div class="map-full" data-map="{}" data-view="{}">{}</div>\n'.format(
            name, m["start"], still(name, m, m["start"], strict=False).replace(' style="margin: 1.6em 0;"', ""))
    else:
        out += '        <figure class="showpiece"><a href="{0}"><img src="{0}" alt="{1}"></a></figure>\n'.format(
            "/maps/{}/{}".format(name, m["image"]), esc(m.get("alt")))
    if m.get("credits"):
        out += '        <p class="map-credits">{}</p>\n'.format(esc(m["credits"]))
    if m.get("pdf"):
        out += '        <p class="map-downloads"><a href="/maps/{}/{}">Download the PDF</a></p>\n'.format(name, m["pdf"])
    out += '        <footer class="post"><a href="/#maps">← Maps</a></footer>\n    </main>\n'
    return out + FOOT


START = "<!-- writing:start (generated by _writing/build.py — do not edit by hand) -->"
END = "<!-- writing:end -->"

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
    listed = sorted(((n, m) for n, m in maps.items() if m.get("page")), key=lambda x: (x[1]["date"], x[0]), reverse=True)
    groups = [label for _, label in KINDS] + (["Maps"] if listed else [])
    out = '    <section id="writing">\n        <h2>Writing</h2>\n        <nav class="kinds">'
    out += " ".join('<a href="#{}">{}</a>'.format(label.lower(), label) for label in groups)
    out += '</nav>\n'
    for kind, label in KINDS:
        out += '        <section id="{}">\n            <h3>{}</h3>\n            <ul>\n'.format(label.lower(), label)
        for p in (x for x in posts if x["kind"] == kind):
            out += '                <li><a href="/writing/{}/">{}</a><time datetime="{}">{}</time></li>\n'.format(
                p["slug"], esc(p["title"]), p["date"].isoformat(), p["date"].isoformat())
        out += '            </ul>\n        </section>\n'
    if listed:
        out += '        <section id="maps">\n            <h3>Maps</h3>\n            <ul>\n'
        for n, m in listed:
            out += '                <li><a href="/maps/{}/">{}</a><time datetime="{}">{}</time></li>\n'.format(
                n, esc(m["title"]), m["date"].isoformat(), m["date"].isoformat())
        out += '            </ul>\n        </section>\n'
    return out + '    </section>\n'


def update_homepage(posts, maps):
    path = os.path.join(ROOT, "index.html")
    page = open(path, encoding="utf-8").read()
    a, b = page.index(START) + len(START), page.index(END)
    write(path, page[:a] + "\n" + homepage_list(posts, maps) + "    " + page[b:])


def feed(posts):
    items = ""
    for p in posts[:20]:
        url = "{}/writing/{}/".format(SITE, p["slug"])
        body = re.sub(r'(href|src)="/', r'\1="' + SITE + "/", p["body"]).replace("]]>", "]]&gt;")
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
    urls += [("{}/maps/{}/".format(SITE, n), m["date"]) for n, m in sorted(maps.items()) if m.get("page")]
    rows = "".join("  <url><loc>{}</loc>{}</url>\n".format(
        u, "<lastmod>{}</lastmod>".format(d.isoformat()) if d else "") for u, d in urls)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + '</urlset>\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drafts", action="store_true", help="also build _writing/drafts/ into _preview/")
    args = parser.parse_args()
    maps = load_maps()
    posts = load(maps)
    for p in posts:
        write(os.path.join(OUT, p["slug"], "index.html"), post_page(p, maps))
    for name, m in maps.items():
        write(os.path.join(MAP_OUT, name, "index.html"), map_page(name, m))
    write(os.path.join(OUT, "index.html"), FORWARD)
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
