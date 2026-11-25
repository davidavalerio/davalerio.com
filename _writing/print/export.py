"""From the page to the PDF and the PNG: pinned Chromium prints, poppler checks and draws."""
import re, subprocess
from pathlib import Path


def print_pdf(html, pdf, w_in, h_in, probe=None):
    """Print one HTML page to a one-page PDF at its own size with no margins, in Playwright's own Chromium.
    Returns what the browser reported: its version, and the width in CSS pixels of the element `probe` selects."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        errors = []
        pg.on('requestfailed', lambda r: errors.append(r.url))
        pg.goto(Path(html).resolve().as_uri())
        pg.evaluate('document.fonts.ready')
        missing = pg.evaluate('[...document.fonts].filter(f => f.status !== "loaded").map(f => f.family + " " + f.weight + " " + f.style)')
        info = {'version': b.version, 'unloaded_fonts': missing,
                'probe': pg.evaluate(f'document.querySelector({probe!r}).getBoundingClientRect().width') if probe else None}
        pg.pdf(path=str(pdf), width=f'{w_in}in', height=f'{h_in}in', print_background=True, prefer_css_page_size=True,
               margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'})
        b.close()
    if errors:
        raise RuntimeError('the page could not load: ' + ', '.join(errors))
    return info


def run(*a):
    return subprocess.run(a, capture_output=True, text=True, check=True).stdout


def pdf_checks(pdf, strings, min_ppi=None, pages=1, size_in=None):
    """Check a PDF the way a printer would see it. Every font embedded TrueType; every string in `strings`
    readable as text; every image at `min_ppi` or better; the page count and size right."""
    problems = []
    fonts = [l for l in run('pdffonts', str(pdf)).splitlines()[2:] if l.strip()]
    if not fonts:
        problems.append('no fonts in the PDF: the text is not text')
    for l in fonts:
        if 'TrueType' not in l or not re.search(r'\byes\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$', l):
            problems.append('font not embedded TrueType: ' + l.strip())
    txt = run('pdftotext', '-layout', str(pdf), '-')
    raw = run('pdftotext', '-raw', str(pdf), '-')
    squeezed = re.sub(r'\s+', '', txt) + re.sub(r'\s+', '', raw)
    lost = [s for s in strings if re.sub(r'\s+', '', s) not in squeezed]
    if lost:
        problems.append('text not found: ' + '; '.join(lost))
    images = [l.split() for l in run('pdfimages', '-list', str(pdf)).splitlines()[2:]]
    ppis = [(float(l[12]), float(l[13])) for l in images if l[2] == 'image']
    if min_ppi and ppis and min(min(p) for p in ppis) < min_ppi:
        problems.append(f'an image is below {min_ppi} ppi: lowest {min(min(p) for p in ppis):.0f}')
    info = run('pdfinfo', str(pdf))
    n = int(re.search(r'Pages:\s+(\d+)', info).group(1))
    if n != pages:
        problems.append(f'{n} pages, expected {pages}')
    w, h = map(float, re.search(r'Page size:\s+([\d.]+) x ([\d.]+) pts', info).groups())
    if size_in and (abs(w - size_in[0] * 72) > 0.5 or abs(h - size_in[1] * 72) > 0.5):
        problems.append(f'page is {w} x {h} pt, expected {size_in[0] * 72} x {size_in[1] * 72}')
    encs = sorted({l[8] for l in images if l[2] == 'image'})
    report = (f'fonts: {len(fonts)} ' + ', '.join(sorted({l.split()[0].split("+")[-1] for l in fonts})) +
              f'\nimages: {len(ppis)}, ppi {min(min(p) for p in ppis):.0f} to {max(max(p) for p in ppis):.0f}, encoding {",".join(encs)}' * bool(ppis) +
              f'\npage: {w} x {h} pt, {n} page(s)\n' + ('problems: ' + ' | '.join(problems) if problems else 'all checks passed'))
    return {'ok': not problems, 'problems': problems, 'report': report, 'text': txt}


def png(pdf, out, dpi=200):
    """The PNG, drawn from the finished PDF's first page by poppler, a renderer unrelated to the one that wrote
    it. (MuPDF 1.28.6 was the first choice and is not used: given two rotated figures set glyph by glyph it
    draws one of them hundreds of times too large, on a PDF that poppler and Chromium both draw correctly.)"""
    out = Path(out)
    subprocess.run(['pdftoppm', '-png', '-singlefile', '-f', '1', '-l', '1', '-r', str(dpi), str(pdf), str(out.with_suffix(''))], check=True)
    return out
