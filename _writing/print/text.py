"""Type: the eight static TrueType faces, two display instances of the semibold serif for titles, and text measured
with HarfBuzz against the same files the page loads."""
from functools import lru_cache
from pathlib import Path
import uharfbuzz as hb

FONTS = Path(__file__).parent / 'fonts'
# style name -> (CSS family, weight, italic, file)
STYLES = {
    'serif': ('Source Serif 4', 400, False, 'SourceSerif4-Regular.ttf'),
    'serif-italic': ('Source Serif 4', 400, True, 'SourceSerif4-It.ttf'),
    'serif-semibold': ('Source Serif 4', 600, False, 'SourceSerif4-Semibold.ttf'),
    'serif-semibold-italic': ('Source Serif 4', 600, True, 'SourceSerif4-SemiboldIt.ttf'),
    'sans': ('Source Sans 3', 400, False, 'SourceSans3-Regular.ttf'),
    'sans-italic': ('Source Sans 3', 400, True, 'SourceSans3-It.ttf'),
    'sans-semibold': ('Source Sans 3', 600, False, 'SourceSans3-Semibold.ttf'),
    'sans-semibold-italic': ('Source Sans 3', 600, True, 'SourceSans3-SemiboldIt.ttf'),
    'serif-display-48': ('Display Serif Opsz48', 600, False, 'DisplaySerif-Semibold-Opsz48.ttf'),
    'serif-display-60': ('Display Serif Opsz60', 600, False, 'DisplaySerif-Semibold-Opsz60.ttf'),
}
# The semibold serif at each optical size the engine has, in px: the static semibold is the text size
OPTICAL = {20: 'serif-semibold', 48: 'serif-display-48', 60: 'serif-display-60'}


def title_style(points):
    """The semibold serif for a title set at `points` on the page: the face whose optical size is nearest the size in
    px, as a browser picks from the variable font (optical size is px, up to 60)."""
    px = min(60.0, points * 4 / 3)
    return OPTICAL[min(OPTICAL, key=lambda o: abs(o - px))]


def font_css(base):
    """The @font-face rules, the files addressed under `base` (a URL prefix ending in a slash)."""
    out = []
    for fam, weight, italic, file in STYLES.values():
        out.append(f"@font-face{{font-family:'{fam}';font-weight:{weight};font-style:{'italic' if italic else 'normal'};"
                   f"src:url('{base}{file}') format('truetype')}}")
    return '\n'.join(out)


def css(style):
    """The CSS declarations that select a style."""
    fam, weight, italic, _ = STYLES[style]
    return f"font-family:'{fam}';font-weight:{weight};font-style:{'italic' if italic else 'normal'}"


@lru_cache(None)
def _font(style):
    face = hb.Face(hb.Blob.from_file_path(str(FONTS / STYLES[style][3])))
    font = hb.Font(face)
    ext = font.get_font_extents('ltr')
    os2 = None
    from fontTools.ttLib import TTFont
    tt = TTFont(str(FONTS / STYLES[style][3]), lazy=True)
    os2 = tt['OS/2']
    return font, face.upem, {'ascender': ext.ascender, 'descender': ext.descender,
                             'cap': getattr(os2, 'sCapHeight', 0) or ext.ascender * 0.66,
                             'x': getattr(os2, 'sxHeight', 0) or ext.ascender * 0.48}


def metrics(style, size):
    """Cap height, x-height, ascender and descender (negative) in points."""
    _, upem, m = _font(style)
    return {k: v * size / upem for k, v in m.items()}


@lru_cache(100000)
def shape(text, style, size, spacing=0.0):
    """Shape one line. Returns (clusters, width): clusters is a tuple of (characters, x, advance) in points, one
    per run of characters that HarfBuzz draws as a unit (a ligature stays whole), in reading order; `spacing` is
    added after every cluster, as CSS letter-spacing does (ligatures are turned off when it is set, as browsers do)."""
    if not text:
        return (), 0.0
    font, upem, _ = _font(style)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    feats = {'liga': False, 'clig': False} if spacing else {}
    hb.shape(font, buf, feats)
    k = size / upem
    out, x = [], 0.0
    infos, poss = buf.glyph_infos, buf.glyph_positions
    starts = sorted({i.cluster for i in infos}) + [len(text)]
    adv = {}
    for info, pos in zip(infos, poss):
        adv[info.cluster] = adv.get(info.cluster, 0.0) + pos.x_advance * k
    for a, b in zip(starts, starts[1:]):
        w = adv[a]
        out.append((text[a:b], x, w))
        x += w + spacing
    return tuple(out), (x - spacing if out else 0.0)


def width(text, style, size, spacing=0.0):
    return shape(text, style, size, spacing)[1]
