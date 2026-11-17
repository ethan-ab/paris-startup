#!/usr/bin/env python3
"""Fetch every startup's logo and self-host it as a small WebP plate in public/logos/.

Sources, best first:
  1. the company's LinkedIn logo, as recorded by Exa in data/raw/exa_companies.json (square, 200 px);
  2. its own website: apple-touch-icon / <link rel=icon> / web manifest icons, or the header <img> tagged "logo"
     (SVGs rendered with Playwright's Chromium via scripts/rasterize.js), then Google's favicon service.
Each image becomes a 96 px tall plate: an opaque square logo fills it edge to edge, a transparent logo is centred with
padding on white (or on ink when the artwork is light); wordmarks get up to 2:1. Icons under 48 px are rejected, and
the same image on 3+ domains (LinkedIn's placeholder, site-builder defaults) counts as no logo. The card then shows a
monogram instead.

Writes public/logos/<key>.webp and data/logos.json ({domain: {key, from, ...} or {key: null, why}}).
Downloads are cached by domain in data/logos.json, so reruns only try new domains (--retry retries the misses).
Usage: python3 scripts/logos.py [--retry] [--limit N]
"""
import hashlib, html.parser, io, json, os, re, sys, threading, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'public', 'logos')
INDEX = os.path.join(ROOT, 'data', 'logos.json')
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
SIZE, MIN_SRC = 96, 48
# hosting / CMS defaults that a site serves as its own favicon (found by eye in a contact sheet of the website logos)
GENERIC = {'julesetshim.com': 'WordPress default icon', 'pc3.org': 'GoDaddy default icon'}


def host(u):
    try:
        return (urllib.parse.urlparse(u).hostname or '').lower().removeprefix('www.') or None
    except ValueError:
        return None


def key_for(domain):
    return re.sub(r'[^a-z0-9]+', '-', domain.lower().removeprefix('www.')).strip('-')


def get(url, limit=4_000_000):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*', 'Accept-Language': 'fr,en;q=0.8'})
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.geturl(), r.headers.get('Content-Type', ''), r.read(limit)


class IconLinks(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons, self.manifest, self.logos, self._in_logo = [], None, [], 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        tagged = 'logo' in (a.get('class', '') + ' ' + a.get('id', '')).lower()
        if tag == 'img' and len(self.logos) < 3:
            src = a.get('src') or a.get('data-src') or (a.get('srcset') or a.get('data-srcset') or '').split(' ')[0]
            hint = ' '.join([a.get('alt', ''), a.get('class', ''), a.get('id', ''), src]).lower()
            if src and (self._in_logo or 'logo' in hint) and not src.startswith('data:'):
                self.logos.append((src, re.sub(r'[^a-z0-9]', '', urllib.parse.unquote(hint))))
        if tag != 'img' and tagged:
            self._in_logo = 4  # the next few tags sit inside a "logo" wrapper
        elif self._in_logo:
            self._in_logo -= 1
        if tag != 'link':
            return
        rel = a.get('rel', '').lower().split()
        if not a.get('href') or 'mask-icon' in rel:
            return
        if 'manifest' in rel:
            self.manifest = a['href']
        elif 'icon' in rel or 'apple-touch-icon' in rel or 'apple-touch-icon-precomposed' in rel:
            m = re.match(r'(\d+)x(\d+)', a.get('sizes', ''))
            self.icons.append((a['href'], int(m.group(1)) if m else 0, 'apple' in ' '.join(rel)))


def candidates(domain):
    """Icon URLs worth trying, best guesses first."""
    out = []
    page = None
    for base in (f'https://{domain}/', f'https://www.{domain}/' if not domain.startswith('www.') else None):
        if not base:
            continue
        try:
            page, ctype, body = get(base, 1_500_000)
            if 'html' not in ctype:
                continue
            p = IconLinks()
            p.feed(body.decode('utf-8', 'replace'))
            for href, size, apple in sorted(p.icons, key=lambda x: (-x[1], not x[2])):
                out.append(('icon', urllib.parse.urljoin(page, href)))
            # a header logo whose file name or alt text names the brand beats one that doesn't (press / partner logos)
            toks = [t for t in re.split(r'[^a-z0-9]+', domain.lower().removeprefix('www.').rsplit('.', 1)[0]) if len(t) >= 4]
            named = [(src, any(t in hint for t in toks)) for src, hint in p.logos]
            for src, ok in sorted(named, key=lambda x: not x[1]):
                if ok or src == p.logos[0][0]:
                    out.append(('logo' if ok else 'logo?', urllib.parse.urljoin(page, src)))
            if p.manifest:
                try:
                    murl = urllib.parse.urljoin(page, p.manifest)
                    _, _, mb = get(murl, 300_000)
                    for ic in sorted(json.loads(mb).get('icons', []), key=lambda i: -int((re.match(r'\d+', i.get('sizes', '0')) or ['0'])[0])):
                        if 'monochrome' not in ic.get('purpose', ''):
                            out.append(('icon', urllib.parse.urljoin(murl, ic['src'])))
                except Exception:  # noqa: BLE001
                    pass
            break
        except Exception:  # noqa: BLE001
            continue
    root = page or f'https://{domain}/'
    out += [('icon', urllib.parse.urljoin(root, '/apple-touch-icon.png')), ('icon', urllib.parse.urljoin(root, '/favicon.ico')),
            ('google', f'https://www.google.com/s2/favicons?domain={urllib.parse.quote(domain)}&sz=256')]
    seen, uniq = set(), []
    for k, u in out:
        if u not in seen and not u.startswith('data:'):
            seen.add(u)
            uniq.append((k, u))
    return uniq[:12]


_rast = {'proc': None, 'lock': threading.Lock()}


def rasterize_svg(data):
    """SVG -> PNG bytes through scripts/rasterize.js (one long-lived Chromium)."""
    import subprocess, tempfile
    with _rast['lock']:
        if _rast['proc'] is None:
            _rast['proc'] = subprocess.Popen(['node', os.path.join(ROOT, 'scripts', 'rasterize.js')], cwd=ROOT, text=True,
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        with tempfile.TemporaryDirectory() as td:
            src, out = os.path.join(td, 'in.svg'), os.path.join(td, 'out.png')
            with open(src, 'wb') as f:
                f.write(data)
            _rast['proc'].stdin.write(f'{src}\t{out}\n')
            _rast['proc'].stdin.flush()
            if _rast['proc'].stdout.readline().strip() != 'ok':
                raise ValueError('svg render failed')
            with open(out, 'rb') as f:
                return f.read()


def open_image(data, is_svg=False):
    from PIL import Image
    if is_svg:
        data = rasterize_svg(data)
    im = Image.open(io.BytesIO(data))
    if im.format == 'ICO':
        sizes = sorted(im.info.get('sizes') or [im.size], key=lambda s: -s[0])
        im.size = sizes[0]
    im.load()
    return im.convert('RGBA')


def to_plate(im):
    """128 px tall plate (square for icons, up to 2:1 for wordmarks) with the background baked in."""
    from PIL import Image
    w, h = im.size
    alpha = im.getchannel('A')
    corners = [alpha.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if min(corners) > 240 and 0.8 <= w / h <= 1.25:  # an opaque app icon is already a designed tile
        return im.resize((SIZE, SIZE), Image.LANCZOS), 'tile'
    bbox = alpha.point(lambda a: 255 if a > 16 else 0).getbbox()
    if bbox:
        im = im.crop(bbox)
    if min(corners) > 240:  # opaque but not square: trim a uniform background colour
        bg_px = im.getpixel((0, 0))
        diff = Image.new('RGBA', im.size, bg_px)
        from PIL import ImageChops
        box = ImageChops.difference(im.convert('RGB'), diff.convert('RGB')).convert('L').point(lambda v: 255 if v > 24 else 0).getbbox()
        if box:
            im = im.crop(box)
        bg = '#%02x%02x%02x' % bg_px[:3]
    else:
        px = [p for p in im.resize((48, 48)).get_flattened_data() if p[3] > 128] if hasattr(im, 'get_flattened_data') else \
             [p for p in im.resize((48, 48)).getdata() if p[3] > 128]
        lum = sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b, _ in px) / max(1, len(px)) / 255
        bg = '#1b1a18' if lum > 0.8 else '#ffffff'  # white artwork needs a dark plate
    ratio = im.width / max(1, im.height)
    pad = 22 if ratio < 1.6 else 16
    inner_h = SIZE - 2 * pad
    iw = min(2 * SIZE - 2 * pad, round(inner_h * ratio))
    ih = min(inner_h, round(iw / ratio))
    im = im.resize((max(1, iw), max(1, ih)), Image.LANCZOS)
    pw = max(SIZE, iw + 2 * pad)
    plate = Image.new('RGBA', (pw, SIZE), bg)
    plate.alpha_composite(im, ((pw - im.width) // 2, (SIZE - im.height) // 2))
    return plate, 'plate'


def fetch_logo(domain):
    """Returns (webp bytes, meta) or (None, reason)."""
    best = None
    for kind, url in candidates(domain):
        try:
            final, ctype, data = get(url)
            is_svg = 'svg' in ctype or final.lower().split('?')[0].endswith('.svg') or data[:200].lstrip().startswith((b'<svg', b'<?xml'))
            if 'html' in ctype and not is_svg:
                continue
            im = open_image(data, is_svg)
        except Exception:  # noqa: BLE001
            continue
        w, h = im.size
        if kind.startswith('logo'):
            if h < 32 or w / h > 5 or (not is_svg and min(w, h) > 900):
                continue
            score = 170 if is_svg else min(165, h * 1.6)
            if kind == 'logo?':
                score = min(score, 120)
        else:
            if min(w, h) < MIN_SRC:
                continue
            score = min(min(w, h), 256) if kind == 'icon' else min(min(w, h), 100)
        if not best or score > best[0]:
            best = (score, im, final, kind)
        if score >= 180:
            break
    if not best:
        return None, 'no usable logo'
    plate, shape = to_plate(best[1])
    buf = io.BytesIO()
    plate.convert('RGB').save(buf, 'WEBP', quality=88, method=6)
    return buf.getvalue(), {'from': best[2], 'kind': best[3], 'score': round(best[0]), 'shape': shape,
                            'w': plate.width, 'sha': hashlib.sha1(best[1].tobytes()).hexdigest()[:16]}


def encode(im):
    plate, shape = to_plate(im)
    buf = io.BytesIO()
    plate.convert('RGB').save(buf, 'WEBP', quality=84, method=6)
    return buf.getvalue(), shape


def from_linkedin(url):
    if not url or 'static.licdn.com' in url:  # static.licdn.com serves LinkedIn's grey placeholder
        return None
    try:
        _, ctype, data = get(url)
        im = open_image(data)
    except Exception:  # noqa: BLE001
        return None
    return im if min(im.size) >= MIN_SRC else None


def work(job):
    domain, li = job
    try:
        im = from_linkedin(li)
        if im is not None:
            data, shape = encode(im)
            return domain, data, {'from': 'linkedin', 'shape': shape, 'sha': hashlib.sha1(im.tobytes()).hexdigest()[:16]}
        data, meta = fetch_logo(domain)
        if data:
            meta['from'] = 'website:' + meta['from']
        return domain, data, meta
    except Exception as e:  # noqa: BLE001
        return domain, None, f'error: {e}'


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = json.load(open(os.path.join(ROOT, 'data', 'startups.json')))
    li = {}
    for x in json.load(open(os.path.join(ROOT, 'data', 'raw', 'exa_companies.json'))):
        d = host(x.get('url')) or (x.get('domain') or '').lower().removeprefix('www.')
        if d and x.get('logo') not in (None, '', 'None'):
            li.setdefault(d, x['logo'])
    idx = json.load(open(INDEX)) if os.path.exists(INDEX) else {}
    domains = sorted({host(r.get('u')) for r in rows} - {None})
    todo = [d for d in domains if d not in idx or ('--retry' in sys.argv and not idx[d].get('key'))]
    if '--limit' in sys.argv:
        todo = todo[:int(sys.argv[sys.argv.index('--limit') + 1])]
    print(f'{len(domains)} domains, {len(todo)} to fetch')
    done = 0
    with ThreadPoolExecutor(24) as ex:
        for d, data, meta in ex.map(work, [(d, li.get(d)) for d in todo]):
            done += 1
            if data:
                k = key_for(d)
                with open(os.path.join(OUT, k + '.webp'), 'wb') as f:
                    f.write(data)
                idx[d] = {'key': k, **meta}
            else:
                idx[d] = {'key': None, 'why': meta}
            if done % 200 == 0:
                print(f'  {done}/{len(todo)}', flush=True)
                json.dump(dict(sorted(idx.items())), open(INDEX, 'w'), indent=0)
    # the same image on 3+ domains is a placeholder, not a brand mark
    by_sha = {}
    for d, v in idx.items():
        if v.get('sha'):
            by_sha.setdefault(v['sha'], []).append(d)
    for ds in by_sha.values():
        if len(ds) >= 3:
            for d in ds:
                try:
                    os.remove(os.path.join(OUT, idx[d]['key'] + '.webp'))
                except OSError:
                    pass
                idx[d] = {'key': None, 'why': f'placeholder shared by {len(ds)} domains'}
    for d, why in GENERIC.items():
        if (idx.get(d) or {}).get('key'):
            idx[d] = {'key': None, 'why': why}
    # drop files no startup points to any more
    keep = {v['key'] for d, v in idx.items() if v.get('key') and d in set(domains)}
    for f in os.listdir(OUT):
        if f.endswith('.webp') and f[:-5] not in keep:
            os.remove(os.path.join(OUT, f))
    with open(INDEX, 'w') as f:
        json.dump(dict(sorted(idx.items())), f, indent=0)
    n = sum(1 for d in domains if (idx.get(d) or {}).get('key'))
    print(f'logos: {n}/{len(domains)} domains', '·', sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT)) // 1024, 'KB')


if __name__ == '__main__':
    main()
