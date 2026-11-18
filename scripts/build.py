#!/usr/bin/env python3
"""Build the Paris Startup Atlas from data/startups.json.

Writes public/index.html + public/app.js (static site, live OpenFreeMap tiles) and, with --artifact,
artifact/paris-startup-atlas.html: one self-contained page with an offline basemap baked in (data/basemap.json).
Usage: python3 scripts/build.py [--artifact]
"""
import base64, datetime, json, os, re, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from rounds import apply as apply_rounds  # noqa: E402  recent funding rounds from data/ref/rounds.json


def main():
    with open(os.path.join(ROOT, 'data', 'startups.json')) as f:
        rows = json.load(f)
    attach_logos(rows)
    apply_rounds(rows)
    with open(os.path.join(ROOT, 'src', 'index.template.html')) as f:
        html = f.read()
    data = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    checked = datetime.date.today().strftime('%B %Y')
    with open(os.path.join(ROOT, 'data', 'buildings.json')) as f:
        bld = f.read()
    html = html.replace('/*__DATA__*/[]', data).replace('/*__CHECKED__*/""', json.dumps(checked))
    html = html.replace('/*__BUILDINGS__*/{"type":"FeatureCollection","features":[]}', bld)
    os.makedirs(os.path.join(ROOT, 'public'), exist_ok=True)
    with open(os.path.join(ROOT, 'public', 'index.html'), 'w') as f:
        f.write(html)
    with open(os.path.join(ROOT, 'src', 'app.js')) as f, open(os.path.join(ROOT, 'public', 'app.js'), 'w') as g:
        g.write(f.read())
    print(len(rows), 'startups · public/index.html', len(html) // 1024, 'KB')
    if '--artifact' in sys.argv:
        build_artifact(html, rows)


def attach_logos(rows):
    """lg = file key in public/logos/ (from scripts/logos.py), for startups whose logo was found."""
    path = os.path.join(ROOT, 'data', 'logos.json')
    idx = json.load(open(path)) if os.path.exists(path) else {}
    n = 0
    for r in rows:
        try:
            d = (urllib.parse.urlparse(r.get('u') or '').hostname or '').lower().removeprefix('www.')
        except ValueError:
            d = ''
        k = (idx.get(d) or {}).get('key')
        if k and os.path.exists(os.path.join(ROOT, 'public', 'logos', k + '.webp')):
            r['lg'] = k
            n += 1
    print(f'logos: {n}/{len(rows)} startups')


def inline_logos(rows, budget=4_000_000):
    """The artifact cannot fetch public/logos/, so the logos travel inside the page as data URIs (biggest teams first)."""
    out, size = {}, 0
    for r in sorted(rows, key=lambda r: -(r.get('e') or 0)):
        k = r.get('lg')
        if not k or k in out:
            continue
        with open(os.path.join(ROOT, 'public', 'logos', k + '.webp'), 'rb') as f:
            uri = 'data:image/webp;base64,' + base64.b64encode(f.read()).decode()
        if size + len(uri) > budget:
            break
        out[k] = uri
        size += len(uri) + len(k) + 6
    print(f'artifact logos: {len(out)} inlined, {size // 1024} KB')
    return json.dumps(out, separators=(',', ':'))


def build_artifact(html, rows):
    with open(os.path.join(ROOT, 'data', 'basemap.json')) as f:
        basemap = f.read()
    css_path = os.path.join(ROOT, 'data', 'cache', 'maplibre-gl-4.7.1.css')
    if not os.path.exists(css_path):
        os.makedirs(os.path.dirname(css_path), exist_ok=True)
        with urllib.request.urlopen('https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css') as r, open(css_path, 'wb') as f:
            f.write(r.read())
    with open(css_path) as f:
        mlcss = f.read()
    with open(os.path.join(ROOT, 'src', 'app.js')) as f:
        app = f.read()
    h = re.sub(r'<!doctype html>\s*<html[^>]*>\s*<head>\s*<meta charset="utf-8">\s*', '', html, flags=re.I)
    h = re.sub(r'<meta name="viewport"[^>]*>\n', '', h)
    h = h.replace('</head>\n<body>', '').replace('</body>\n</html>', '')
    h = re.sub(r'<link rel="preconnect" href="https://tiles.openfreemap.org"[^>]*>\n', '', h)
    h = h.replace('<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">', '<style>' + mlcss + '</style>')
    h = h.replace('<script src="app.js"></script>', '<script>window.BASEMAP=' + basemap + ';window.CONTEXT=' + context() + ';window.LOGOS=' + inline_logos(rows) + ';</script>\n<script>' + app.replace('</script', '<\\/script') + '</script>')
    os.makedirs(os.path.join(ROOT, 'artifact'), exist_ok=True)
    out = os.path.join(ROOT, 'artifact', 'paris-startup-atlas.html')
    with open(out, 'w') as f:
        f.write(h)
    print('wrote', out, len(h) // 1024, 'KB')


def context(box=(2.312, 48.829, 2.388, 48.884)):
    """Grey buildings for the offline page, delta-encoded at 1e-5 degrees: {x0, y0, b: [[h, dx, dy, dx, dy, ...], ...]}."""
    with open(os.path.join(ROOT, 'data', 'context_buildings.json')) as f:
        rows = json.load(f)
    x0, y0 = 2.3, 48.8
    out = []
    for h, ring in rows:
        cx = sum(p[0] for p in ring) / len(ring); cy = sum(p[1] for p in ring) / len(ring)
        if not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
            continue
        a, px, py = [h], 0, 0
        for x, y in ring[:-1]:
            qx, qy = round((x - x0) * 1e5), round((y - y0) * 1e5)
            a += [qx - px, qy - py]; px, py = qx, qy
        out.append(a)
    return json.dumps({'x0': x0, 'y0': y0, 'b': out}, separators=(',', ':'))


if __name__ == '__main__':
    main()
