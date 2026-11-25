"""The proof page: a hand-made page, no engine, that shows what the pinned Chromium writes into a PDF.

Run after any change of Chromium (a Playwright bump): uv run proof.py <out folder>
It prints letter, tabloid and 24 x 36 in pages and checks each with pdffonts, pdftotext and pdfimages.
"""
import math, subprocess, sys, time
from pathlib import Path
import numpy as np
from PIL import Image
import text as T
from export import print_pdf, pdf_checks, png

SIZES = {'letter': (11, 8.5), 'tabloid': (17, 11), 'display': (36, 24)}
INK, STONE, PLUM, BLUE = '#22282B', '#6B7075', '#5E4B8B', '#2f5f8a'
DPI, TILE = 350, 2048


def relief(out, name, w_in, h_in):
    """A synthetic shaded surface at 350 dpi, cut into tiles; returns [(file, x, y, w, h in points)]."""
    W, H = round(w_in * DPI), round(h_in * DPI)
    tiles = []
    for ty in range(0, H, TILE):
        for tx in range(0, W, TILE):
            w, h = min(TILE, W - tx), min(TILE, H - ty)
            y, x = np.mgrid[ty:ty + h, tx:tx + w].astype('float32')
            z = np.sin(x / 190) * np.cos(y / 140) + 0.5 * np.sin((x + y) / 37) + 0.2 * np.sin(x / 3.1) * np.sin(y / 2.9)
            g = (150 + 45 * z).clip(0, 255).astype('uint8')
            rgb = np.stack([g, (g * 0.94).astype('uint8'), (g * 0.82).astype('uint8')], -1)
            f = f'{name}-relief-{ty // TILE}-{tx // TILE}.jpg'
            Image.fromarray(rgb).save(out / f, quality=92, subsampling=0)
            tiles.append((f, tx * 72 / DPI, ty * 72 / DPI, w * 72 / DPI, h * 72 / DPI))
    return tiles


def arc_text(s, style, size, cx, cy, r, a0, fill):
    """One <text> with a position and rotation per glyph, set along a circle."""
    clusters, _ = T.shape(s, style, size, 1.2)
    xs, ys, rs, chars = [], [], [], []
    for ch, x, adv in clusters:
        a = a0 + (x + adv / 2) / r                       # angle at the glyph's middle
        px, py = cx + r * math.sin(a), cy - r * math.cos(a)
        rot = math.degrees(a)
        # the glyph is drawn from its left edge: step back half its advance along its own baseline
        px -= adv / 2 * math.cos(a); py -= adv / 2 * math.sin(a)
        for i, c in enumerate(ch):
            chars.append(c); xs.append(px); ys.append(py); rs.append(rot)   # a ligature's later characters ride with the first
    f = lambda v: ' '.join(f'{n:.2f}' for n in v)
    return (f'<text x="{f(xs)}" y="{f(ys)}" rotate="{f(rs)}" style="{T.css(style)};font-size:{size}px;fill:{fill}">'
            + ''.join(chars).replace('&', '&amp;') + '</text>')


def page(out, name, w_in, h_in):
    W, H = w_in * 72, h_in * 72
    m = 34
    fx, fy, fw, fh = m, 190, W - 2 * m, H - 190 - m
    tiles = relief(out, name, fw / 72, fh / 72)
    imgs = ''.join(f'<image href="{f}" x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" preserveAspectRatio="none"/>'
                   for f, x, y, w, h in tiles)
    styles = ''.join(
        f'<div style="{T.css(k)};font-size:11pt;color:{INK};position:absolute;left:{m + (i % 4) * 180}pt;top:{60 + (i // 4) * 18}pt">'
        f'{T.STYLES[k][0]} {k.split("-", 1)[1].replace("-", " ").title() if "-" in k else "Regular"} 0123</div>'
        for i, k in enumerate(T.STYLES))
    # the measured width of a string, drawn as a bar under the browser's own setting of it
    probe = 'Painted Woods Creek and the Little Muddy River'
    pw = T.width(probe, 'sans-italic', 9)
    cx, cy = fw * 0.5, fh * 1.25
    html = f'''<!doctype html><html><head><meta charset="utf-8"><title>Print Proof, {name}</title><style>
{T.font_css(T.FONTS.as_uri() + '/')}
@page{{size:{w_in}in {h_in}in;margin:0}}
html,body{{margin:0;padding:0}} body{{width:{w_in}in;height:{h_in}in;position:relative;overflow:hidden;background:#fff}}
svg text{{white-space:pre}}
</style></head><body>
<div style="{T.css('serif-semibold')};font-size:18pt;color:{INK};position:absolute;left:{m}pt;top:22pt">Print Proof Page</div>
{styles}
<div style="position:absolute;left:{m}pt;top:110pt;width:{W - 2 * m}pt;height:1pt;background:{PLUM}"></div>
<div id="probe" style="{T.css('sans-italic')};font-size:9pt;color:{BLUE};position:absolute;left:{m}pt;top:125pt;white-space:nowrap">{probe}</div>
<div style="position:absolute;left:{m}pt;top:140pt;width:{pw:.3f}pt;height:0.5pt;background:{INK}"></div>
<svg xmlns="http://www.w3.org/2000/svg" style="position:absolute;left:{fx}pt;top:{fy}pt;width:{fw}pt;height:{fh}pt" viewBox="0 0 {fw} {fh}">
<defs><clipPath id="clip"><rect width="{fw}" height="{fh}"/></clipPath></defs>
<g clip-path="url(#clip)">
{imgs}
<path d="M{fw * .1},{fh * .2} L{fw * .6},{fh * .1} L{fw * .7},{fh * .7} L{fw * .2},{fh * .8}Z" fill="#b7d2e6" style="mix-blend-mode:multiply"/>
<circle cx="{fw * .75}" cy="{fh * .45}" r="{fh * .35}" fill="#cfe0c0" style="mix-blend-mode:multiply"/>
<circle cx="{fw}" cy="{fh}" r="{fh * .3}" fill="#e6c9c0" stroke="{INK}" stroke-width="0.5"/>
{''.join(f'<path d="M0,{20 + i * 6} C{fw * .3},{60 + i * 6} {fw * .6},{-20 + i * 6} {fw},{30 + i * 6}" fill="none" stroke="#8a6646" stroke-width="0.1"/>' for i in range(8))}
<path d="M0,{fh * .5} L{fw},{fh * .55}" fill="none" stroke="{INK}" stroke-width="0.35" stroke-dasharray="3 1.5"/>
<path d="M0,{fh * .6} L{fw},{fh * .65}" fill="none" stroke="#4a7fae" stroke-width="0.1" stroke-dasharray="1 1"/>
<text x="{fw * .3}" y="{fh * .4}" style="{T.css('sans-semibold')};font-size:11px;fill:{INK}">Williston Straight</text>
<text x="{fw * .3}" y="{fh * .46}" style="{T.css('sans-italic')};font-size:9px;fill:{BLUE}">Lake Sakakawea Italic</text>
{arc_text('Missouri River Curved', 'sans-italic', 9, cx, cy, fh * .95, -0.35, BLUE)}
{arc_text('2100', 'sans', 5.5, cx, cy, fh * .8, 0.1, '#7a5a3c')}
</g>
<rect width="{fw}" height="{fh}" fill="none" stroke="{INK}" stroke-width="0.5"/>
</svg></body></html>'''
    f = out / f'proof-{name}.html'
    f.write_text(html)
    return f, pw


def main(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, (w, h) in SIZES.items():
        f, pw = page(out, name, w, h)
        pdf = out / f'proof-{name}.pdf'
        t = time.time()
        info = print_pdf(f, pdf, w, h, probe='#probe')
        dt = time.time() - t
        strings = ['Print Proof Page', 'Williston Straight', 'Lake Sakakawea Italic', 'Missouri River Curved', '2100',
                   'Source Serif 4 Semibold Italic', 'Source Sans 3 Regular']
        res = pdf_checks(pdf, strings, min_ppi=DPI - 2)
        print(f'--- {name}: printed in {dt:.1f}s; HarfBuzz width {pw:.2f}pt, browser width {info["probe"] * 0.75:.2f}pt')
        print(res['report'])
        ok &= res['ok']
        png(pdf, out / f'proof-{name}.png', 600 if name == 'letter' else 150)
    print('PROOF', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
