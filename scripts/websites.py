#!/usr/bin/env python3
"""Read each company's own website to find its OFFICE address (not its registered seat).

For every company whose office is not already known from LinkedIn, fetch the homepage, follow up to 5 links
that look like contact / about / jobs / legal pages, and pull every Paris-area street address out of the text.
Each candidate is scored by the words around it:
  + "bureaux", "office", "locaux", "nous trouver", "venez", "HQ", "headquarters", "adresse", "contact"
  - "siège social", "RCS", "capital", "immatriculée", "domicili", "hébergeur", "hosted by"
Output: data/raw/websites.json {domain: [{"addr":..., "postcode":..., "score":..., "page":..., "kind": "office"|"seat"}]}
Pages are cached in data/cache/web/ (gzip) so reruns are free.
"""
import sys as _s; _s.path.insert(0, __import__('os').path.dirname(__file__))
import gzip, hashlib, html, json, os, re, sys, urllib.parse, urllib.request, ssl
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'data', 'cache', 'web')
OUT = os.path.join(ROOT, 'data', 'raw', 'websites.json')
UA = 'Mozilla/5.0 (compatible; paris-startup-map/1.0; office address lookup)'
CTX = ssl.create_default_context()

LINK_RE = re.compile(r'href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
WANT = re.compile(r'contact|nous-contacter|contactez|about|a-propos|qui-sommes|equipe|team|company|entreprise|jobs|careers|carrieres|recrutement|join|rejoindre|bureaux|office|mentions|legal|imprint|cgu|cgv', re.I)
STREET = r"(?:rue|avenue|av\.|bd|boulevard|place|quai|cours|passage|impasse|all[ée]e|villa|cit[ée]|square|parvis|esplanade|galerie|faubourg|rond-point|cour|promenade|chauss[ée]e)"
ADDR_RE = re.compile(r"(\d{1,4}(?:\s?(?:bis|ter|b))?(?:\s?[-–]\s?\d{1,4})?[\s,]+" + STREET + r"\b[^\n,;:|<>()]{2,60}?)[\s,–-]+(75\d{3}|9[234]\d{3})\s+([A-Za-zÀ-ÿ' -]{3,30})", re.I)
POS = re.compile(r"bureaux|bureau|office|locaux|nous trouver|venez|retrouvez|hq\b|headquarters|adresse|address|contact|where we are|o[uù] nous", re.I)
NEG = re.compile(r"si[èe]ge\s+social|registered office|rcs|capital\s+(?:social|de)|immatricul|domicili|h[ée]berg|hosted|hosting|directeur de (?:la )?publication|siret|tva", re.I)


def get(url):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest()[:20] + '.gz')
    if os.path.exists(p):
        with gzip.open(p, 'rt', encoding='utf-8') as f:
            return f.read()
    text = ''
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'fr,en'})
        with urllib.request.urlopen(req, timeout=8, context=CTX) as r:
            if 'html' in (r.headers.get('content-type') or ''):
                text = r.read(1_500_000).decode(r.headers.get_content_charset() or 'utf-8', 'replace')
    except Exception:  # noqa: BLE001
        text = ''
    with gzip.open(p, 'wt', encoding='utf-8') as f:
        f.write(text)
    return text


def visible(h):
    h = re.sub(r'<(script|style|noscript|svg)[^>]*>.*?</\1>', ' ', h, flags=re.S | re.I)
    h = re.sub(r'<br\s*/?>|</(p|div|li|h\d|span|address|td)>', '\n', h, flags=re.I)
    h = re.sub(r'<[^>]+>', ' ', h)
    return re.sub(r'[ \t\xa0]+', ' ', html.unescape(h))


def candidates(text, page):
    out = []
    for m in ADDR_RE.finditer(text):
        ctx = text[max(0, m.start() - 160):m.start()] + ' ' + text[m.end():m.end() + 60]
        near = text[max(0, m.start() - 70):m.start()]
        score = (2 if POS.search(ctx) else 0) - (3 if NEG.search(near) else (1 if NEG.search(ctx) else 0))
        if re.search(r'mentions|legal|cgu|cgv|imprint', page, re.I):
            score -= 1
        addr = re.sub(r'\s+', ' ', m.group(1)).strip(' ,-')
        out.append({'addr': addr, 'postcode': m.group(2), 'city': m.group(3).strip(), 'score': score, 'page': page,
                    'kind': 'seat' if NEG.search(near) else 'office'})
    return out


def crawl(c):
    base = c['url'] if c['url'].startswith('http') else 'https://' + c['url']
    if 'linkedin.com' in base:
        return c['domain'], []
    home = get(base)
    if not home:
        return c['domain'], []
    host = urllib.parse.urlparse(base).netloc.replace('www.', '')
    links = []
    for href, label in LINK_RE.findall(home):
        u = urllib.parse.urljoin(base, href)
        pu = urllib.parse.urlparse(u)
        if pu.netloc.replace('www.', '') != host or not WANT.search(pu.path + ' ' + re.sub('<[^>]+>', '', label)):
            continue
        u = pu._replace(query='', fragment='').geturl()
        if u not in links and u.rstrip('/') != base.rstrip('/'):
            links.append(u)
    links.sort(key=lambda u: (0 if re.search('contact|bureau|office', u, re.I) else 1 if re.search('about|propos|jobs|career|carri', u, re.I) else 2))
    cands = candidates(visible(home), base)
    for u in links[:5]:
        cands += candidates(visible(get(u)), u)
    seen, uniq = set(), []
    for x in sorted(cands, key=lambda x: -x['score']):
        k = re.sub(r'\W+', '', x['addr'].lower())[:30]
        if k not in seen:
            seen.add(k); uniq.append(x)
    return c['domain'], uniq[:6]


def main():
    with open(os.path.join(ROOT, 'data', 'raw', 'exa_companies.json')) as f:
        companies = json.load(f)
    # Companies without a numbered LinkedIn office address first (or on a domiciliation address), then everyone else.
    import locate
    dom = locate.DOMICILIATION
    def has_office(c):
        num, _, _ = locate.parse_exa(c.get('hq_address'))
        return bool(num) and locate.addr_key(num) not in dom
    first = [c for c in companies if not has_office(c)]
    rest = [c for c in companies if has_office(c)]
    todo = first + (rest if '--all' in sys.argv else [])
    if '--seed' in sys.argv:
        with open(os.path.join(ROOT, 'data', 'raw', 'seed_companies.json')) as f:
            todo = json.load(f)
    print(len(todo), 'websites to read')
    res = {}
    if os.path.exists(OUT):  # merge with earlier runs
        with open(OUT) as f:
            res = json.load(f)
    with ThreadPoolExecutor(64) as ex:
        for i, (d, cands) in enumerate(ex.map(crawl, todo)):
            res[d] = cands
            if i % 250 == 0:
                print(' ', i, sum(bool(v) for v in res.values()), 'with an address', flush=True)
    with open(OUT, 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=0)
    print('done', sum(bool(v) for v in res.values()), 'of', len(res), 'sites gave an address')


if __name__ == '__main__':
    main()
