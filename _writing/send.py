#!/usr/bin/env python3
"""Email one essay, paper or map page: first a test to David alone, then, on a
second explicit run, to the Writing segment in Resend. Nothing sends on publish.

    python3 _writing/send.py <name>                 test to david@davalerio.com
    python3 _writing/send.py <name> --subscribers   the real send
    python3 _writing/send.py <name> --preview       print the email's HTML

<name> is a piece's slug (_writing/posts/) or a map page's name (_writing/maps/,
`page: true`). Run by the newsletter workflow's Run button, or from a session with
`gh workflow run newsletter.yml -f name=<name> -f to=test|subscribers`.

The subscriber send runs only in that workflow on main, after a successful test of
the same piece on the same commit (the workflow checks), and only when the live
page is byte for byte the page this checkout's source renders, so what is mailed
is what is published. Resend's broadcast list is the record: a broadcast named
`writing:<slug>` or `map:<name>` that went out means the piece was sent, and it is
never sent again. A failed or uncertain send stops and says so; nothing is
retried. Needs RESEND_API_KEY, and SEGMENT_ID for --subscribers.
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import KIND_LABEL, SITE, SUBSCRIBE_NAME, esc, load, load_maps, map_page, offline, post_page, still_url  # noqa: E402

FROM = "David Valerio <writing@davalerio.com>"
REPLY_TO = "david@davalerio.com"
TEST_TO = "david@davalerio.com"
FONT = "'Source Serif 4', Georgia, 'Times New Roman', serif"
GONE_OUT = ("sent", "queued", "scheduled", "canceled")  # a broadcast in any of these is never sent again
DELIVERING = ("sent", "queued")                         # a send that worked
DRAFT = "draft"                                         # the only status a leftover broadcast is deleted in
FRESH_DRAFT = datetime.timedelta(minutes=10)
# Carried by every email until the first broadcast has gone out, so the readers
# brought over from Discern Earth's newsletter learn of the move in the first issue.
MOVE_NOTE = ("Discern Earth’s newsletter is now Writing by Email, from davalerio.com. You subscribed to "
             "Discern Earth, so new writing will come to you here; the link at the foot of this email "
             "unsubscribes you.")           # a draft younger than this may be a send in progress

# Inline styles (email clients drop stylesheets), matching the site's pages in the
# house email palette. A class style wins over its tag's.
STYLES = {
    "p": "margin: 0 0 1.1em;",
    "a": "color: #5E4B8B; text-decoration: none; font-weight: 600;",
    "h2": "color: #22282B; font-size: 17px; font-weight: 600; letter-spacing: 0.01em; line-height: 1.3; margin: 34px 0 14px;",
    "h3": "color: #22282B; font-size: 15px; font-weight: 600; line-height: 1.4; margin: 28px 0 10px;",
    "h4": "color: #22282B; font-size: 15px; font-weight: 600; line-height: 1.4; margin: 24px 0 8px;",
    "blockquote": "margin: 0 0 1.1em; border-left: 2px solid #5E4B8B; padding-left: 1.2em; font-style: italic; color: #3A4145;",
    "ul": "margin: 0 0 1.1em; padding-left: 1.4em;",
    "ol": "margin: 0 0 1.1em; padding-left: 1.4em;",
    "li": "margin: 0 0 0.4em;",
    "hr": "border: 0; border-top: 1px solid #D8D4CC; width: 80px; margin: 2em auto;",
    "figcaption": "margin: 8px 0 0; color: #6B7075; font-size: 12.5px; line-height: 1.5; text-align: center;",
}
CLASS_STYLES = {
    "map-note": "margin: 0 0 1.6em; color: #6B7075; font-size: 15px;",
    "map-credits": "margin: 1.8em 0 0; color: #6B7075; font-size: 12.5px; line-height: 1.5;",
}


def styled(body, page):
    """Inline styles, and every link and image made absolute against the live page.
    A Ghost download card (two old papers) becomes a plain link, and any inline
    SVG or style block is dropped, since mail clients show them as stray text."""
    body = re.sub(r'<div class="kg-card kg-file-card"><a class="kg-file-card-container" href="([^"]+)".*?'
                  r'kg-file-card-filesize">([^<]*)</div>.*?</a></div>',
                  r'<p><a href="\1">Download the PDF (\2)</a></p>', body, flags=re.S)
    body = re.sub(r"<(svg|style)\b.*?</\1>", "", body, flags=re.S | re.I)
    body = re.sub(r'(href|src)="/', r'\1="' + SITE + "/", body)
    body = re.sub(r'href="#', 'href="' + page + "#", body)

    def tag(m):
        name, attrs = m.group(1).lower(), m.group(2)
        if "style=" in attrs:
            return m.group(0)
        cls = re.search(r'class="([^"]*)"', attrs)
        style = next((CLASS_STYLES[c] for c in (cls.group(1).split() if cls else []) if c in CLASS_STYLES),
                     STYLES.get(name))
        return '<{} style="{}"{}>'.format(m.group(1), style, attrs) if style else m.group(0)

    return re.sub(r"<([a-zA-Z][a-zA-Z0-9]*)((?:\s[^>]*)?)>", tag, body)


def email_html(title, url, meta, subtitle, body, test, note=None):
    sub = ""
    if subtitle:
        sub = ('<p style="margin: 0 0 10px; color: #6B7075; font-size: 17px; font-style: italic; '
               'line-height: 1.45;">{}</p>\n').format(esc(subtitle))
    unsubscribe = ('Test send to you alone' if test else
                   '<a href="{{{RESEND_UNSUBSCRIBE_URL}}}" style="color: #6B7075; text-decoration: none;">Unsubscribe</a>')
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,wght@0,400;0,600;1,400&display=swap');</style>
</head>
<body style="margin: 0; background: #ffffff;">
<div style="max-width: 600px; margin: 0 auto; padding: 20px; font-family: {font}; font-size: 15px; line-height: 1.66; color: #22282B;">
<h1 style="color: #22282B; font-size: 26px; font-weight: 600; line-height: 1.15; margin: 0 0 8px;"><a href="{url}" style="color: #22282B; text-decoration: none;">{title}</a></h1>
{sub}<p style="color: #6B7075; font-size: 12.5px; letter-spacing: 0.05em; margin: 0 0 24px; padding-bottom: 16px; border-bottom: 1px solid #D8D4CC;">{meta}</p>
{note}{body}
<p style="margin: 32px 0 0; padding-top: 16px; border-top: 1px solid #E6E3DC; color: #6B7075; font-size: 12.5px;"><a href="{url}" style="color: #6B7075; text-decoration: none;">Read on davalerio.com</a> · {unsubscribe}<br>You receive this because you subscribed to {name} at davalerio.com.</p>
</div>
</body></html>""".format(font=FONT, url=url, title=esc(title), sub=sub, meta=meta, body=styled(body, url),
                         unsubscribe=unsubscribe, name=esc(SUBSCRIBE_NAME),
                         note=('<p style="margin: 0 0 24px; padding: 12px 16px; background: #F3F1EC; color: #3A4145; '
                               'font-size: 14px;">{}</p>\n'.format(esc(note)) if note else ""))


def piece(name, test, note=None):
    """(broadcast name, subject, email HTML, page URL, page HTML) for a slug or a map
    page, the email and the page both rendered from the same source."""
    maps = load_maps()
    posts = {p["slug"]: p for p in load(maps)}
    pages = {n: m for n, m in maps.items() if m.get("page")}
    if name in posts and name in pages:
        sys.exit("{} is both a piece and a map page; rename one".format(name))
    if name in posts:
        p = posts[name]
        if p["kind"] == "conversation":
            sys.exit("{} is a conversation; only writing and maps are emailed".format(name))
        url = "{}/writing/{}/".format(SITE, name)
        meta = "{} · {}".format(KIND_LABEL[p["kind"]], p["date"].isoformat())
        return ("writing:" + name, p["title"], email_html(p["title"], url, meta, p["subtitle"], offline(p), test, note), url,
                post_page(p, maps))
    if name in pages:
        m = pages[name]
        url = "{}/maps/{}/".format(SITE, name)
        image = still_url(name, m["start"]) if "views" in m else "/maps/{}/{}".format(name, m["image"])
        alt = m["views"][m["start"]].get("alt") if "views" in m else m.get("alt")
        body = '<figure style="margin: 0 0 1.4em;"><a href="{}"><img src="{}" alt="{}" style="display: block; width: 100%; max-width: 100%; height: auto;"></a></figure>\n'.format(
            url, image, esc(alt))
        if m.get("note"):
            body += "<p>{}</p>\n".format(esc(m["note"]))
        body += '<p><a href="{}">Explore the map on davalerio.com</a></p>\n'.format(url)
        if m.get("credits"):
            body += '<p class="map-credits">{}</p>\n'.format(esc(m["credits"]))
        meta = "Map · {}".format(m["date"].isoformat())
        return ("map:" + name, m["title"], email_html(m["title"], url, meta, None, body, test, note), url,
                map_page(name, m))
    sys.exit("no piece or map page named " + name)


def api(method, path, body=None, after=""):
    """One Resend call; a refusal stops the run, with `after` added to the message."""
    req = urllib.request.Request("https://api.resend.com" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"],
                                          "Content-Type": "application/json",
                                          "User-Agent": "davalerio-newsletter"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit("Resend refused {} {}: {} {}{}".format(method, path, e.code, e.read().decode(errors="replace")[:500], after))


def broadcasts():
    """Every broadcast in the account, through all pages of the list."""
    out, after = [], None
    while True:
        page = api("GET", "/broadcasts?limit=100" + ("&after=" + after if after else ""))
        data = page.get("data") or []
        out += data
        if not page.get("has_more") or not data:
            return out
        after = data[-1]["id"]


def check_published(url, page):
    """The live page must be the page the email's source renders, so an unpublished
    edit, or a source edit whose page was never rebuilt, is never mailed."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}),
                                    timeout=30) as r:
            live = r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        sys.exit("{} did not answer ({}); nothing sent".format(url, e))
    if page.encode("utf-8") != live:
        sys.exit("the live page at {} is not the page this commit's source renders; rebuild and deploy, "
                 "then test and send again. Nothing sent".format(url))


def check_live(url, email):
    """The page and every image the email shows must answer before anything is sent."""
    images = sorted(set(re.findall(r'<img[^>]+src="({}/[^"]+)"'.format(re.escape(SITE)), email)))
    for u in [url] + images:
        try:
            with urllib.request.urlopen(urllib.request.Request(u, method="HEAD"), timeout=30) as r:
                ok = r.status == 200
        except (urllib.error.URLError, TimeoutError):
            ok = False
        if not ok:
            sys.exit("{} is not live yet; nothing sent".format(u))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    name, subscribers = args[0], "--subscribers" in sys.argv
    first = "--preview" not in sys.argv and not any(b.get("status") in DELIVERING for b in broadcasts())
    key, subject, email, url, page = piece(name, test=not subscribers, note=MOVE_NOTE if first else None)
    if "--preview" in sys.argv:
        print(email)
        return
    if subscribers and not (os.environ.get("GITHUB_ACTIONS") == "true"
                            and os.environ.get("GITHUB_REF") == "refs/heads/main"):
        sys.exit("the subscriber send runs only in the newsletter workflow on main; nothing sent")
    check_live(url, email)
    if subscribers:
        check_published(url, page)
    if not subscribers:
        sent = api("POST", "/emails", {"from": FROM, "reply_to": REPLY_TO, "to": [TEST_TO],
                                       "subject": "Test: " + subject, "html": email})
        print("test of {} sent to {} (email {})".format(name, TEST_TO, sent.get("id")))
        return
    mine = [b for b in broadcasts() if b.get("name") == key]
    done = [b for b in mine if b.get("status") in GONE_OUT]
    if done:
        b = done[0]
        if b["status"] == "canceled":
            sys.exit("{} (broadcast {}) was stopped partway, so some readers may have it; check it in Resend. "
                     "Nothing sent".format(name, b["id"]))
        print("{} already went out (broadcast {}, {}); nothing sent".format(name, b["id"], b["status"]))
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    for b in mine:  # drafts left by a run that stopped before sending
        if b.get("status") != DRAFT:
            sys.exit("{} has broadcast {} in status {}; check it in Resend. Nothing sent".format(
                name, b["id"], b.get("status")))
        made_at = datetime.datetime.fromisoformat(b["created_at"].replace(" ", "T").replace("Z", "+00:00"))
        if made_at.tzinfo is None:
            made_at = made_at.replace(tzinfo=datetime.timezone.utc)
        if now - made_at < FRESH_DRAFT:
            sys.exit("{} has a draft broadcast {} from the last few minutes that may still be sending; "
                     "check it in Resend. Nothing sent".format(name, b["id"]))
        api("DELETE", "/broadcasts/" + b["id"])
    made = api("POST", "/broadcasts", {"segment_id": os.environ["SEGMENT_ID"], "name": key, "from": FROM,
                                       "reply_to": REPLY_TO, "subject": subject, "html": email})
    check = "; check broadcast {} in Resend before running again".format(made["id"])
    try:
        api("POST", "/broadcasts/{}/send".format(made["id"]), {}, after=check)
    except (urllib.error.URLError, TimeoutError) as e:
        sys.exit("the send of {} (broadcast {}) did not answer ({}); check it in Resend before running again".format(
            name, made["id"], e))
    for _ in range(12):  # Resend may take a few seconds to move the broadcast out of draft
        status = api("GET", "/broadcasts/" + made["id"], after=check).get("status")
        if status in GONE_OUT:
            break
        time.sleep(5)
    if status not in DELIVERING:
        sys.exit("{} (broadcast {}) is {} after sending; check it in Resend".format(name, made["id"], status))
    print("{} {} as broadcast {}".format(name, "sent to subscribers" if status == "sent"
                                         else "accepted by Resend, which is delivering it to subscribers", made["id"]))


if __name__ == "__main__":
    main()
