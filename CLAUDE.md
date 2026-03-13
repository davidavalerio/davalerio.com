# davalerio.com

## What This Is

Professional services hub for David Valerio — AI training and implementation for businesses in the Williston Basin. This is the URL David hands out at presentations and on business cards.

## Stack

Static HTML with inline CSS. Josefin Sans font via Google Fonts. Formspree for contact form. No build step, no framework.

## Files

- `index.html` — Single-page site with all content and styles inline
- `content.md` — Plain-text copy for each section (edit here, then sync to index.html)
- `headshot.jpg` — Professional headshot (used in hero section)
- `CNAME` — Points to davalerio.com

## Brand Identity

**Name:** "David Valerio" is the canonical business name. No middle initial — keep it clean and direct.

**Logo:** Single purple V mark. No monogram or additional initials — the V is distinctive, scales well at all sizes, and works as favicon, card mark, and presentation branding.

## Design System

Purple/gold palette aligned with discern.earth. Josefin Sans typography. CSS custom properties for all color tokens. Key color decisions: nav CTA button is deep purple (`--purple-500`), primary CTA and form buttons use bright gold (`--gold-400`), nav bottom border is gold (`--gold-400`), page background is cream (`--gold-100`).

## Layout

Each section is full-viewport height (`100svh`) with vertically centered content. No separate footer — copyright line lives at the bottom of the contact section.

## Offering

AI training and implementation — not consulting. The value proposition is capability transfer: David teaches teams to build AI solutions themselves using their own workflows as curriculum. Training is the entry point; implementation projects are the natural next step — scoped, fixed-price engagements built around the client's workflow alongside their team. Half-day on-site training sessions ($1,500). No project pricing on the site; each engagement is scoped individually. All copy should reinforce "I teach you" not "I build for you."

## Sections

1. **Hero** — Headshot, headline ("Learn AI on Your Own Problems"), CTA ("Book a Training Session")
2. **Training** — "How It Works" section with single card: on-site training details, included items, bottleneck examples, pricing. Section ID is `#training`; CSS class remains `.services` (structural only).
3. **About** — Bio, "Why Me" credentials sidebar, link to Discern Earth
4. **Contact** — Formspree form, phone, email. Subheading frames the ask around AI adoption, not pain points.

## Contact Form

Uses Formspree endpoint `https://formspree.io/f/xnjnkknw`. Do not change this without explicit direction.

## Deployment

Hosted on GitHub Pages. Use `/deploy` to ship changes (branches, commits, PRs, merges, and pulls back to main in one step).

## Domains

- **davalerio.com** — Primary domain, served from this repo.
- **davidavalerio.com** — Redirects to davalerio.com. Served from a separate GitHub Pages repo ([davidavalerio/davidavalerio.com](https://github.com/davidavalerio/davidavalerio.com)) that contains only a meta-refresh redirect and a matching 404 page.

## Related Properties

- **discern.earth** — David's writing (Ghost). Visual identity is aligned between the two sites (purple/gold palette, Josefin Sans). Discern Earth links back here with "Work with David" nav link.
- **valeriosafety.com** — Redirects to this site. Safety background is mentioned in the About bio but not a separate service offering.
