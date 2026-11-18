#!/usr/bin/env python3
"""Scrape every page of stationf.co (sitemap) with Exa through treg and list every startup Station F has named.

1. exa.web.contents.get (via treg) on each sitemap URL, with a structured summary:
   {programme, startups: [{name, description}]} + the page's publication date.
2. Names already in our dataset are matched; the others get a website + LinkedIn page through treg.companies.enrich
   (the company the enrichment returns must carry the same name).
Outputs:
  data/ref/stationf_mentions.json  every startup named on stationf.co: name, domain, programmes [{title, date, url}]
  data/seed/stationf_found.txt     new companies (name | domain) for scripts/seed.py
Cached in data/cache/stationf/. Needs TREG_TOKEN.  Usage: python3 scripts/stationf.py [--budget 4]
"""
import hashlib, json, os, re, sys, threading, unicodedata, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'data', 'cache', 'stationf')
TOKEN = os.environ.get('TREG_TOKEN')
BUDGET = float(sys.argv[sys.argv.index('--budget') + 1]) if '--budget' in sys.argv else 4.0
spent, lock = [0.0], threading.Lock()

SCHEMA = {"type": "object", "properties": {
    "programme": {"type": "string", "description": "Station F programme or cohort this page is about (e.g. Founders Program Fall 2025, Future 40 2024, HEC Incubator), or empty"},
    "startups": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "description": {"type": "string"},
        "programme": {"type": "string", "description": "programme this startup is part of, if stated"}}, "required": ["name"]}}},
    "required": ["startups"]}
NOT_STARTUPS = {'station f', 'stationf', 'hec paris', 'hec', 'lvmh', 'meta', 'microsoft', 'google', 'ubisoft', "l'oreal", 'loreal', 'bpifrance', 'naver',
                'adobe', 'zendesk', 'facebook', 'amazon', 'aws', 'openai', 'anthropic', 'mistral ai', 'hugging face', 'scaleway', 'essec', 'inria',
                'ecole des ponts', 'psg', 'paris saint-germain', 'jetro', 'ocp', 'um6p', 'emlyon', 'organon', 'french tech', 'la french tech', 'xavier niel'}


def fold(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in s if unicodedata.category(c) != 'Mn')).strip()


def treg(endpoint, body):
    with lock:
        if spent[0] >= BUDGET:
            return None
    req = urllib.request.Request(f'https://treg.to/call/{endpoint}', data=json.dumps(body).encode(), method='POST',
                                 headers={'x-treg-token': TOKEN, 'content-type': 'application/json', 'X-Treg-Route-Max-Cost': '0.02'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                cost = int(r.headers.get('x-treg-cost-micro') or 0) / 1e6
                data = json.load(r)
            with lock:
                spent[0] += cost
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get('Retry-After') or 5)); continue
            if e.code in (401, 402, 403):
                print('  treg refused', e.code, file=sys.stderr); return None
            time.sleep(3)
        except Exception:  # noqa: BLE001
            time.sleep(3)
    return None


def cached(key, fn):
    p = os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest()[:16] + '.json')
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    v = fn()
    if v is not None:
        with open(p, 'w') as f:
            json.dump(v, f, ensure_ascii=False)
    return v


def read_pages(urls):
    def one(u):
        def go():
            d = treg('exa.web.contents.get', {'urls': [u],
                                              'summary': {'query': 'List every startup named on this page, with the Station F programme it belongs to.', 'schema': SCHEMA}})
            rs = ((d or {}).get('body', d) or {}).get('results') or []
            if not rs:
                return None
            r = rs[0]
            s = r.get('summary')
            try:
                s = json.loads(s) if isinstance(s, str) else s
            except ValueError:
                s = None
            return {'url': u, 'title': r.get('title'), 'date': (r.get('publishedDate') or '')[:10], 'summary': s}
        return cached('page:' + u, go)
    with ThreadPoolExecutor(8) as ex:
        return [p for p in ex.map(one, urls) if p]


def main():
    os.makedirs(CACHE, exist_ok=True)
    sm = urllib.request.urlopen(urllib.request.Request('https://stationf.co/sitemap.xml', headers={'User-Agent': 'paris-startup-map/1.0'})).read().decode()
    urls = [u for u in re.findall(r'<loc>([^<]+)</loc>', sm) if re.search(r'/(news|programs)/', u)]
    print(len(urls), 'Station F pages')
    pages = read_pages(urls)
    print(len(pages), 'pages read · spent $%.3f' % spent[0])

    # Only cohort / programme pages say a startup was ON CAMPUS; yearly recaps and ecosystem news also name outsiders
    COHORT = re.compile(r'program|programme|batch|cohort|future ?40|meet the|selected|welcom|fellowship|incubat|accelerat|fighters|founders|startups? (of|at|joining)|new startups', re.I)
    mentions = {}
    for p in pages:
        s = p.get('summary') or {}
        page_is_cohort = bool(COHORT.search((p.get('title') or '') + ' ' + p['url'].split('/')[-1]))
        for st in s.get('startups') or []:
            sp = (st.get('programme') or '').strip()
            if sp.lower() in ('null', 'none', 'n/a'):
                sp = ''
            if not (page_is_cohort or (sp and COHORT.search(sp))):
                continue
            if re.search(r'partner|mentor|investor|sponsor|corporate|jury|speaker|judge|expert|coach|ecosystem|context|alumni network', sp + ' ' + (s.get('programme') or ''), re.I):
                continue  # named as a partner or mentor, not as a resident startup
            n = (st.get('name') or '').strip()
            k = fold(n)
            if not k or k in NOT_STARTUPS or len(k) < 2:
                continue
            m = mentions.setdefault(k, {'name': n, 'description': st.get('description'), 'programmes': []})
            label = sp or (s.get('programme') if (s.get('programme') or '').lower() not in ('', 'null') else '') or re.sub(r'\s*\|\s*STATION F$', '', p.get('title') or '')
            m['programmes'].append({'programme': label, 'date': p['date'], 'url': p['url']})
            if not m.get('description') and st.get('description'):
                m['description'] = st['description']
    print(len(mentions), 'distinct startups named on stationf.co')

    # match against what we already have
    # compare with the base sources (Exa sweep + hand-curated list), not with the map, which already holds earlier finds
    have = {}
    with open(os.path.join(ROOT, 'data', 'raw', 'exa_companies.json')) as f:
        for r in json.load(f):
            have.setdefault(fold(r['name']), r['domain'].replace('www.', ''))
    for l in open(os.path.join(ROOT, 'data', 'seed', 'known.txt')).read().splitlines():
        if '|' in l and not l.startswith('#'):
            n_, d_ = [x.strip() for x in l.rsplit('|', 1)]
            have.setdefault(fold(n_), d_)
    for k, m in mentions.items():
        if k in have:
            m['domain'] = have[k]

    # websites for the new ones (treg routed company enrichment by name; the returned company must carry the same name)
    new = [m for m in mentions.values() if not m.get('domain')]
    print(len(new), 'not on the map yet: looking up their websites')

    def find(m):
        def go():
            d = treg('treg.companies.enrich', {'name': m['name']})
            o = ((d or {}).get('output') or {}) if isinstance((d or {}).get('output'), dict) else {}
            return {'name': o.get('name'), 'domain': o.get('domain'), 'linkedin_url': o.get('linkedin_url')}
        r = cached('name:' + fold(m['name']), go) or {}
        same = r.get('name') and (fold(r['name']) == fold(m['name']) or fold(r['name']).startswith(fold(m['name']) + ' ') or fold(m['name']).startswith(fold(r['name']) + ' '))
        if same and r.get('domain'):
            m['domain'] = r['domain'].lower().replace('www.', '')
        return m
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(find, new))
    found = [m for m in new if m.get('domain')]
    for m in mentions.values():
        m['programmes'].sort(key=lambda x: x['date'] or '', reverse=True)
    with open(os.path.join(ROOT, 'data', 'ref', 'stationf_mentions.json'), 'w') as f:
        json.dump(sorted(mentions.values(), key=lambda m: m['name'].lower()), f, ensure_ascii=False, indent=0)
    with open(os.path.join(ROOT, 'data', 'seed', 'stationf_found.txt'), 'w') as f:
        f.write('# Startups named on stationf.co (scripts/stationf.py) that were not on the map. name | website\n')
        for m in sorted(found, key=lambda m: m['name'].lower()):
            f.write(f"{m['name']} | {m['domain']}\n")
    print(f'{len(found)} new startups with a website · {len(new) - len(found)} without one · spent ${spent[0]:.3f}')


if __name__ == '__main__':
    main()
