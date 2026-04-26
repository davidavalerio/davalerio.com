# davalerio.com

## What This Is

Personal calling card for David Valerio. Single-screen landing page with headshot, name, tagline, and a small set of links (Discern Earth, email, phone, LinkedIn, X). This is the URL David hands out at presentations and on business cards.

The consulting offer (AI training and implementation) is currently paused. The full consulting page is preserved at `consulting.html` and can be promoted back to the homepage when training relaunches.

## Stack

Static HTML with inline CSS. Josefin Sans font via Google Fonts. No build step, no framework, no JavaScript on the homepage.

## Files

- `index.html` — Single-screen calling card with all content and styles inline
- `consulting.html` — Archived consulting page (former homepage). Reachable at `/consulting.html`. Restore as homepage by copying back to `index.html`.
- `content.md` — Plain-text copy for the calling card (edit here, then sync to index.html)
- `headshot.jpg` — Professional headshot
- `CNAME` — Points to davalerio.com

## Brand Identity

**Name:** "David Valerio" is the canonical name. No middle initial.

**Logo:** Single purple V mark. Used as favicon and on the consulting page; the calling card leans on the headshot instead.

## Design System

Purple/gold palette aligned with discern.earth. Josefin Sans typography. CSS custom properties for all color tokens. Calling card uses cream background (`--gold-100`), purple headings (`--purple-500`), gold accent border on the headshot and link hover underline (`--gold-400`).

## Layout

Calling card is centered both vertically and horizontally within `100svh`. No nav, no footer, no scroll. Single `<main>` with headshot, name, tagline, and a wrapping row of link items.

## Restoring the Consulting Page

When training relaunches, copy `consulting.html` over `index.html` (overwriting the calling card) and restore the consulting-era CLAUDE.md sections from git history (`Sections`, `Offering`, `Contact Form`). The Formspree endpoint and consulting copy in `consulting.html` stay live in the meantime.

## Contact Form

The Formspree endpoint `https://formspree.io/f/xnjnkknw` is wired up inside `consulting.html`. The calling card has no form — just plain `mailto:` and `tel:` links. Do not change the Formspree endpoint without explicit direction.

## Deployment

Hosted on GitHub Pages. Use `/deploy` to ship changes (branches, commits, PRs, merges, and pulls back to main in one step).

## Domains

- **davalerio.com** — Primary domain, served from this repo.
- **davidavalerio.com** — Redirects to davalerio.com. Served from a separate GitHub Pages repo ([davidavalerio/davidavalerio.com](https://github.com/davidavalerio/davidavalerio.com)) that contains only a meta-refresh redirect and a matching 404 page.

## Related Properties

- **discern.earth** — David's writing (Ghost). Visual identity is aligned between the two sites (purple/gold palette, Josefin Sans). The calling card links to it directly.
- **valeriosafety.com** — Redirects to this site.
