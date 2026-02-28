# davalerio.com

## What This Is

Professional services hub for David A. Valerio — applied AI consulting for businesses in the Williston Basin. This is the URL David hands out at presentations and on business cards.

## Stack

Static HTML with inline CSS. Josefin Sans font via Google Fonts. Formspree for contact form. No build step, no framework.

## Files

- `index.html` — Single-page site with all content and styles inline
- `content.md` — Plain-text copy for each section (edit here, then sync to index.html)
- `headshot.jpg` — Professional headshot (used in hero section)
- `CNAME` — Points to davalerio.com

## Brand Identity

**Name:** "David A. Valerio" is the canonical business name. Never abbreviate to "D.A. Valerio" — the full first name keeps it personal and approachable.

**Logo:** Single purple V mark. No monogram or additional initials — the V is distinctive, scales well at all sizes, and works as favicon, card mark, and presentation branding.

## Design System

Purple/gold palette aligned with discern.earth. Josefin Sans typography. CSS custom properties for all color tokens. Key color decisions: nav CTA button is deep purple (`--purple-500`), primary CTA and form buttons use bright gold (`--gold-400`), nav bottom border is gold (`--gold-400`), page background is cream (`--gold-100`).

## Layout

Each section is full-viewport height (`100svh`) with vertically centered content. No separate footer — copyright line lives at the bottom of the contact section.

## Sections

1. **Hero** — Headshot, name, positioning statement, CTA
2. **Services** — Single card with AI consulting details and pricing
3. **About** — Bio, "Why Me" credentials sidebar, link to Discern Earth
4. **Contact** — Formspree form, phone, email, copyright line

## Contact Form

Uses Formspree endpoint `https://formspree.io/f/xnjnkknw`. Do not change this without explicit direction.

## Deployment

Hosted on GitHub Pages. Use `/deploy` to ship changes (branches, commits, PRs, merges, and pulls back to main in one step).

## Domains

- **davalerio.com** — Primary domain, served from this repo.
- **davidavalerio.com** — Redirects to davalerio.com. Served from a separate GitHub Pages repo ([davidavalerio/davidavalerio.com](https://github.com/davidavalerio/davidavalerio.com)) that contains only a meta-refresh redirect and a matching 404 page.

## Related Properties

- **discern.earth** — David's writing (Ghost). Visual identity is aligned between the two sites (purple/gold palette, Josefin Sans). Discern Earth links back here with "Work with David" nav link.
- **valeriosafety.com** — Redirects to this site. Safety consulting is mentioned in the About bio but not a separate service offering.
