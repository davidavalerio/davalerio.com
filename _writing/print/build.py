"""Build a print map: uv run build.py "<project folder>" [--scripts DIR] [--out DIR] [--no-labels] [--move]

Reads the map's Scripts/map.py and Scripts/settings.json, writes each page as HTML, prints it in the pinned
Chromium, joins the pages, checks the PDF, and draws the PNG from it. Everything is made in the out folder
(default: a folder of this build's own under ~/.local/share/workbench/print/<map>-<mark of the project folder>/); with --move, a build that passes every check is then
moved into the project folder as YYYY-MM-DD-Name-Map.pdf and .png.
"""
import argparse, fcntl, hashlib, importlib.util, os, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import printmap as pm
from export import print_pdf, pdf_checks, png


def scratch(project, name):
    """A map's working folder, its own for each project folder: a copy of a project elsewhere does not share it."""
    return Path.home() / '.local/share/workbench/print' / f'{name}-{hashlib.sha1(str(Path(project).resolve()).encode()).hexdigest()[:8]}'


def load(project, scripts=None, **opts):
    """The map a project's map.py defines, with its saved settings, its pages built."""
    scripts = Path(scripts or Path(project) / 'Scripts')
    spec = importlib.util.spec_from_file_location('map_definition', scripts / 'map.py')
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True                    # no __pycache__ beside a map.py in Drive
    spec.loader.exec_module(mod)
    saved = opts.pop('saved', None)
    m = pm.Map(project, mod.SETTINGS, pm.load_settings(scripts) if saved is None else saved, web=getattr(mod, 'WEB', None), **opts)
    m.name = mod.NAME
    m.output = getattr(mod, 'OUTPUT', None) or mod.NAME.replace(' ', '-') + '-Map'      # the files' name, after the date
    mod.pages(m)
    m.check_web()
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


def build(project, scripts=None, out=None, labels=True, move=False, quiet=False, **opts):
    say = (lambda *a: None) if quiet else print
    t0 = time.time()
    # One build of a project at a time, whoever starts it (the workbench after a save, or this command by hand):
    # a second waits for the first, up to two minutes. The lock is taken before the settings and data are read, so
    # a build that waited builds what is saved when its turn comes. Each build works in a folder of its own.
    root = Path.home() / '.local/share/workbench/print'
    root.mkdir(parents=True, exist_ok=True)
    lock = open(root / f'build-{hashlib.sha1(str(Path(project).resolve()).encode()).hexdigest()[:8]}.lock', 'w')
    waited = time.time()
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() - waited > 120:
                raise pm.BuildError('another build of this map has been running for two minutes; this one was not started')
            time.sleep(0.25)
    m = load(project, scripts, labels=labels, **opts)
    home = scratch(project, m.name)
    home.mkdir(parents=True, exist_ok=True)
    for old in home.glob('build-*'):        # with the lock held, any working folder left here is from a build that has ended
        shutil.rmtree(old, ignore_errors=True)
    own = out is None
    out = Path(out) if out else Path(tempfile.mkdtemp(prefix='build-', dir=home))
    files = write_pages(m, out)
    t1 = time.time()
    stem = f'{m.today}-{m.output}'
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
        strings = [p.title, p.subtitle, *p.credit_lines, *([' '.join(pm.MAKER)] if 'rule' in p.L else [])]
        strings += [l.text.replace('\n', ' ') for f in p.frames if f.label_result for l in f.label_result.placed]
        has_raster = any(L['kind'] == 'relief' for f in p.frames for L in f.layers)
        r = pdf_checks(one, [s for s in strings if s], min_ppi=m.dpi - 2 if has_raster else None, size_in=(p.w_in, p.h_in))
        ok &= r['ok']
        reports.append(f'page {p.n} ({p.size}):\n{r["report"]}')
    dpi = 200 if max(m.pages[0].w_in, m.pages[0].h_in) <= 17 else 100
    if len(pdfs) == 1:
        images = [png(pdf, out / f'{stem}.png', dpi)]
    else:                                     # a picture of every page
        images = [png(one, out / f'{stem}-Page-{p.n}.png', dpi) for p, one in zip(m.pages, pdfs)]
    image = images[0]
    t3 = time.time()
    unplaced = [(f.id, l) for p in m.pages for f in p.frames if f.label_result for l in f.label_result.unplaced]
    placed = sum(len(f.label_result.placed) for p in m.pages for f in p.frames if f.label_result)
    say('\n'.join(reports))
    say(f'labels: {placed} placed, {len(unplaced)} left off' + ''.join(f'\n  left off: {fid} {u}' for fid, u in unplaced))
    say(f'time: pages {t1 - t0:.2f}s, Chromium {t2 - t1:.2f}s, checks and PNG {t3 - t2:.2f}s')
    result = {'ok': ok, 'pdf': pdf, 'png': image, 'pngs': images, 'pages': files, 'map': m}
    if ok and move:
        for f in (pdf, *images):        # beside its place first, under a name of this build's own, then renamed over it: a failed copy leaves the last one whole
            part = Path(project) / f'{f.name}.{os.getpid()}.part'
            try:
                shutil.copyfile(f, part)
                part.replace(Path(project) / f.name)
            finally:
                part.unlink(missing_ok=True)
        if own:                         # the outputs are in the project; this build's working folder is done with
            shutil.rmtree(out, ignore_errors=True)
            pdf, image = Path(project) / pdf.name, Path(project) / image.name
            result.update(pdf=pdf, png=image)
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
