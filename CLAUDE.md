# davalerio.com

## What This Is

Personal calling card for David Valerio. Single-screen landing page with headshot, name, tagline, and a small set of links (Discern Earth, email, phone, LinkedIn, X). This is the URL David hands out at presentations and on business cards.

This is a personal site, not a services site. Don't propose services sections, contact forms, pricing, or training-related copy for it; "update the homepage" means extending the personal page.

The AI consulting offer is not running. Its page is archived at `consulting.html`, reachable at `/consulting.html`, and stays as filed — it represents real positioning, so don't delete or rewrite it speculatively. Bringing it back to the homepage happens only if David asks for it. Earlier narrative is in `HISTORY.md`.

## Stack

Static HTML with inline CSS. Source Serif 4 via Google Fonts. No build step, no framework, no JavaScript on the homepage.

## Files

- `index.html` — single-screen calling card with all content and styles inline
- `consulting.html` — archived consulting page (former homepage), reachable at `/consulting.html`
- `content.md` — plain-text copy for the calling card (edit here, then sync to index.html)
- `headshot.jpg` — professional headshot
- `favicon.png`, `apple-touch-icon.png` — DV favicon fallbacks
- `CNAME` — points to davalerio.com

## Restoring the Consulting Page

Mechanically it is one move: copy `consulting.html` over `index.html`, and pull the consulting-era CLAUDE.md sections (`Sections`, `Offering`, `Contact Form`) back from git history. Only on an explicit ask from David.

## Brand Identity

**Name:** "David Valerio" is the canonical name. No middle initial.

**Favicon:** the initials DV in Source Serif bold, iron oxide (`#97592F`), drawn as outlines inline in `index.html`; `favicon.png` and `apple-touch-icon.png` (same mark on the paper color) cover Safari and the iOS home screen. The archived consulting page keeps its purple V mark.

## Design System

David's house style, shared with his documents and utility emails and separate from the Discern Earth brand: Source Serif 4 alone (regular text, semibold name), slate ink (`--ink`), stone gray tagline (`--stone`), barely warm paper (`--paper`), and one iron-oxide accent (`--accent`, `#97592F`) on the links and their hover underline. Square headshot with a hairline border. The archived `consulting.html` keeps its original purple and gold.

## Layout

Calling card is centered both vertically and horizontally within `100svh`. No nav, no footer, no scroll. Single `<main>` with headshot, name, tagline, and a wrapping row of link items.

## Contact Form

The Formspree endpoint `https://formspree.io/f/xnjnkknw` is wired up inside `consulting.html`. The calling card has no form — just plain `mailto:` and `tel:` links. Do not change the Formspree endpoint without explicit direction.

## Deployment

Hosted on GitHub Pages. Use `/deploy` to ship changes (branches, commits, PRs, merges, and pulls back to main in one step).

## Domains

- **davalerio.com** — primary domain, served from this repo.
- **davidavalerio.com** — redirects to davalerio.com. Served from a separate GitHub Pages repo ([davidavalerio/davidavalerio.com](https://github.com/davidavalerio/davidavalerio.com)) that contains only a meta-refresh redirect and a matching 404 page.
- **valeriosafety.com** — redirects to this site.

## Related Properties

- **discern.earth** — David's writing (Ghost). It keeps its own purple-and-gold brand; this site does not share it. The calling card links to it directly.
