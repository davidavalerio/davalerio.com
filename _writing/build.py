#!/usr/bin/env python3
"""Build davalerio.com/writing/ from the sources in _writing/posts/.

Each source is a file with YAML frontmatter (title, date, kind, optional
subtitle / image / image_alt / image_caption) followed by the body: .html
bodies are used as they are, .md bodies go through Pandoc. The filename is
the slug. Run with no arguments; output lands in ../writing/ and ../sitemap.xml.
"""
import datetime
import email.utils
import html
import os
import re
import subprocess

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, "posts")
OUT = os.path.join(ROOT, "writing")
SITE = "https://davalerio.com"
KINDS = [("essay", "Essays"), ("paper", "Papers"), ("conversation", "Conversations")]
KIND_LABEL = {"essay": "Essay", "paper": "Paper", "conversation": "Conversation"}

FAVICON = open(os.path.join(HERE, "favicon.txt")).read().strip()

HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
    <link rel="stylesheet" href="/writing/style.css">
</head>
<body>
    <header class="site">
        <a href="/" class="home">David Valerio</a>
        <a href="/writing/">Writing</a>
    </header>
"""

FOOT = """</body>
</html>
"""


def esc(s):
    return html.escape(s or "", quote=True)


def long_date(d):
    return "{} {}, {}".format(d.strftime("%B"), d.day, d.year)


def load():
    posts = []
    for name in sorted(os.listdir(SRC)):
        slug, ext = os.path.splitext(name)
        if ext not in (".html", ".md"):
            continue
        raw = open(os.path.join(SRC, name), encoding="utf-8").read()
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
        posts.append({
            "slug": slug, "title": meta["title"], "date": date, "kind": kind,
            "subtitle": meta.get("subtitle"), "image": meta.get("image"),
            "image_alt": meta.get("image_alt"), "image_caption": meta.get("image_caption"),
            "body": body.strip(),
            "description": meta.get("subtitle") or (text[:157] + "…" if len(text) > 158 else text),
        })
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def post_page(p):
    url = "{}/writing/{}/".format(SITE, p["slug"])
    og_image = ""
    if p["image"]:
        og_image = '\n    <meta property="og:image" content="{}{}">\n    <meta name="twitter:card" content="summary_large_image">'.format(SITE, esc(p["image"]))
    out = HEAD.format(title=esc(p["title"]) + " — David Valerio", og_title=esc(p["title"]),
                      description=esc(p["description"]), url=url, og_type="article",
                      og_image=og_image, favicon=FAVICON)
    out += '    <main>\n        <article>\n            <header class="post">\n'
    out += '                <p class="meta">{} · <time datetime="{}">{}</time></p>\n'.format(
        KIND_LABEL[p["kind"]], p["date"].isoformat(), long_date(p["date"]))
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
    out += '        <footer class="post"><a href="/writing/">← Writing</a></footer>\n    </main>\n'
    return out + FOOT


def index_page(posts):
    desc = "Essays, papers, and conversations by David Valerio."
    out = HEAD.format(title="Writing — David Valerio", og_title="Writing", description=desc,
                      url=SITE + "/writing/", og_type="website", og_image="", favicon=FAVICON)
    out += '    <main class="index">\n        <h1>Writing</h1>\n        <nav class="kinds">'
    out += " ".join('<a href="#{}">{}</a>'.format(label.lower(), label) for _, label in KINDS)
    out += '</nav>\n'
    for kind, label in KINDS:
        out += '        <section id="{}">\n            <h2>{}</h2>\n            <ul>\n'.format(label.lower(), label)
        for p in (x for x in posts if x["kind"] == kind):
            out += '                <li><a href="/writing/{}/">{}</a><time datetime="{}">{}</time></li>\n'.format(
                p["slug"], esc(p["title"]), p["date"].isoformat(),
                "{} {}".format(p["date"].strftime("%b"), p["date"].year))
        out += '            </ul>\n        </section>\n'
    out += '    </main>\n'
    return out + FOOT


def feed(posts):
    items = ""
    for p in posts[:20]:
        url = "{}/writing/{}/".format(SITE, p["slug"])
        body = p["body"].replace('"/writing/', '"' + SITE + '/writing/').replace("]]>", "]]&gt;")
        stamp = datetime.datetime.combine(p["date"], datetime.time(12), datetime.timezone.utc)
        items += ("    <item>\n      <title>{}</title>\n      <link>{}</link>\n      <guid>{}</guid>\n"
                  "      <pubDate>{}</pubDate>\n      <description>{}</description>\n"
                  "      <content:encoded><![CDATA[{}]]></content:encoded>\n    </item>\n").format(
            esc(p["title"]), url, url, email.utils.format_datetime(stamp), esc(p["description"]), body)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">\n'
            '  <channel>\n    <title>David Valerio — Writing</title>\n    <link>{0}/writing/</link>\n'
            '    <atom:link href="{0}/writing/feed.xml" rel="self" type="application/rss+xml"/>\n'
            '    <description>Essays, papers, and conversations by David Valerio.</description>\n'
            '    <language>en</language>\n{1}  </channel>\n</rss>\n').format(SITE, items)


def sitemap(posts):
    urls = [(SITE + "/", None), (SITE + "/writing/", posts[0]["date"])]
    urls += [("{}/writing/{}/".format(SITE, p["slug"]), p["date"]) for p in posts]
    rows = "".join("  <url><loc>{}</loc>{}</url>\n".format(
        u, "<lastmod>{}</lastmod>".format(d.isoformat()) if d else "") for u, d in urls)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + '</urlset>\n'


def main():
    posts = load()
    for p in posts:
        write(os.path.join(OUT, p["slug"], "index.html"), post_page(p))
    write(os.path.join(OUT, "index.html"), index_page(posts))
    write(os.path.join(OUT, "feed.xml"), feed(posts))
    write(os.path.join(ROOT, "sitemap.xml"), sitemap(posts))
    print("built {} pieces".format(len(posts)))


if __name__ == "__main__":
    main()
