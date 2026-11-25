"""Build a print map: uv run build.py "<project folder>" [--scripts DIR] [--out DIR] [--no-labels] [--move]

Reads the map's Scripts/map.py and Scripts/settings.json, writes each page as HTML, prints it in the pinned
Chromium, joins the pages, checks the PDF, and draws the PNG from it. Everything is made in the out folder
(default ~/.local/share/workbench/print/<map>/build); with --move, a build that passes every check is then
moved into the project folder as YYYY-MM-DD-Name-Map.pdf and .png.
"""
import argparse, hashlib, importlib.util, json, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import printmap as pm
from export import print_pdf, pdf_checks, png


def load(project, scripts=None, **opts):
    """The map a project's map.py defines, with its saved settings, its pages built."""
    scripts = Path(scripts or Path(project) / 'Scripts')
    spec = importlib.util.spec_from_file_location('map_definition', scripts / 'map.py')
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True                    # no __pycache__ beside a map.py in Drive
    spec.loader.exec_module(mod)
    saved = opts.pop('saved', None)
    m = pm.Map(project, mod.SETTINGS, pm.load_settings(scripts) if saved is None else saved, **opts)
    m.name = mod.NAME
    mod.pages(m)
    return m


def write_pages(m, out):
    """Each page's HTML in `out`, with its rasters beside it. Returns the files."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    files = []
    for p in m.pages:
        f = out / f'page-{p.n}.html'
        f.write_text(p.html(out))
        files.append(f)
    return files


def label_strings(m):
    out = []
    for p in m.pages:
        for f in p.frames:
            if f.label_result:
                out += [l.text.replace('\n', ' ') for l in f.label_result.placed]
    return out


def build(project, scripts=None, out=None, labels=True, move=False, quiet=False, **opts):
    say = (lambda *a: None) if quiet else print
    t0 = time.time()
    m = load(project, scripts, labels=labels, **opts)
    out = Path(out or Path.home() / '.local/share/workbench/print' / m.name / 'build')
    files = write_pages(m, out)
    t1 = time.time()
    stem = f'{m.today}-{m.name.replace(" ", "-")}-Map'
    pdfs = []
    for p, f in zip(m.pages, files):
        one = out / f'page-{p.n}.pdf'
        print_pdf(f, one, p.w_in, p.h_in)
        pdfs.append(one)
    pdf = out / f'{stem}.pdf'
    if len(pdfs) == 1:
        shutil.copyfile(pdfs[0], pdf)
    else:
        from pypdf import PdfWriter
        w = PdfWriter()
        for one in pdfs:
            w.append(str(one))
        w.write(str(pdf))
    t2 = time.time()
    ok, reports = True, []
    for p, one in zip(m.pages, pdfs):
        strings = [p.title, p.subtitle, *p.credit_lines]
        strings += [l.text.replace('\n', ' ') for f in p.frames if f.label_result for l in f.label_result.placed]
        has_raster = any(L['kind'] == 'relief' for f in p.frames for L in f.layers)
        r = pdf_checks(one, [s for s in strings if s], min_ppi=m.dpi - 2 if has_raster else None, size_in=(p.w_in, p.h_in))
        ok &= r['ok']
        reports.append(f'page {p.n} ({p.size}):\n{r["report"]}')
    image = png(pdf, out / f'{stem}.png', 200 if max(m.pages[0].w_in, m.pages[0].h_in) <= 17 else 100)
    t3 = time.time()
    unplaced = [(f.id, l) for p in m.pages for f in p.frames if f.label_result for l in f.label_result.unplaced]
    placed = sum(len(f.label_result.placed) for p in m.pages for f in p.frames if f.label_result)
    say('\n'.join(reports))
    say(f'labels: {placed} placed, {len(unplaced)} left off' + ''.join(f'\n  left off: {fid} {u}' for fid, u in unplaced))
    say(f'time: pages {t1 - t0:.2f}s, Chromium {t2 - t1:.2f}s, checks and PNG {t3 - t2:.2f}s')
    result = {'ok': ok, 'pdf': pdf, 'png': image, 'pages': files, 'map': m}
    if ok and move:
        for f in (pdf, image):
            shutil.copyfile(f, Path(project) / f.name)
        say('moved into', project)
    say('BUILD', 'PASSED' if ok else 'FAILED', pdf)
    return result


def main():
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument('project'); a.add_argument('--scripts'); a.add_argument('--out')
    a.add_argument('--no-labels', action='store_true'); a.add_argument('--move', action='store_true')
    a.add_argument('--write-settings', action='store_true', help='print the settings file the map declares, with saved values, and stop')
    o = a.parse_args()
    if o.write_settings:
        m = load(o.project, o.scripts)
        print(pm.settings_file(m.name, m.settings), end='')
        return 0
    try:
        return 0 if build(o.project, o.scripts, o.out, labels=not o.no_labels, move=o.move)['ok'] else 1
    except pm.BuildError as e:
        print('BUILD STOPPED:', e)
        return 1


if __name__ == '__main__':
    sys.exit(main())
