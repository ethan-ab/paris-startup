#!/usr/bin/env python3
"""One Exa web search per startup to find the street address of its Paris OFFICE (called through treg.to).

Targets every company in data/startups.json whose office is not verified yet (cf 'u' = registered seat / cohort only,
cf 'l' = a single LinkedIn lead), or everyone with --all. The search asks for pages that state where the team works
(the company's own contact / careers pages, job posts, press about a move); Exa returns query-focused highlights
of each page, and street addresses are pulled from them and scored by the words around them, as in websites.py:
  + "bureaux", "office", "locaux", "nous rejoindre", "based in", job-post wording; own domain; job boards
  - "siège social", "RCS", "capital", "domicili", company registries (pappers, societe.com, ...)
The best candidate per company is written to data/ref/exa_offices.json in the verified.json row format
({n, w, office_street, postcode, source_url, source_type, weight}); scripts/resolve.py reads it as 'research' evidence.

Cost: exa.web.search, $0.007 per company (highlights are included in the base price). Responses are cached in
data/cache/exa_offices/, so reruns are free. Spend is capped with --budget (USD).
Needs TREG_TOKEN. Usage: python3 scripts/exa_offices.py [--budget 25] [--limit N] [--all | --unlocated]
"""
import json, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import locate as L  # noqa: E402

ROOT = L.ROOT
CACHE = os.path.join(ROOT, 'data', 'cache', 'exa_offices')
OUT = os.path.join(ROOT, 'data', 'ref', 'exa_offices.json')
TOKEN = os.environ.get('TREG_TOKEN')
BUDGET = float(sys.argv[sys.argv.index('--budget') + 1]) if '--budget' in sys.argv else 25.0
LIMIT = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else None
spent = [0.0]
lock = threading.Lock()

STREET = r"(?:rue|avenue|av\.|bd|boulevard|place|quai|cours|passage|impasse|all[ée]e|villa|cit[ée]|square|parvis|esplanade|galerie|faubourg|rond-point|cour|promenade|chauss[ée]e)"
ADDR_RE = re.compile(r"(?<!\d)(\d{1,4}(?:\s?(?:bis|ter|b))?(?:\s?[-–]\s?\d{1,4})?[\s,]+" + STREET +
                     r"\b[^\n,;:|<>()]{2,60}?)[\s,–-]+(75\d{3}|9[234]\d{3})\b", re.I)
POS = re.compile(r"bureaux|bureau|office|locaux|nous trouver|venez|retrouvez|hq\b|headquarters|based in|bas[ée]e? (?:à|au|dans)|"
                 r"nous rejoindre|join us|lieu de travail|workplace|on-site|sur site|t[ée]l[ée]travail|hybrid|hybride|"
                 r"d[ée]m[ée]nag|moved|new office|nouveaux locaux|adresse|address|contact|where we are|o[uù] nous", re.I)
NEG = re.compile(r"si[èe]ge\s+social|registered office|\brcs\b|capital\s+(?:social|de)|immatricul|domicili|h[ée]berg|hosted|"
                 r"hosting|directeur de (?:la )?publication|siret|\btva\b|n° tva", re.I)
REGISTRY = re.compile(r"pappers|societe\.com|annuaire|infogreffe|verif\.com|manageo|corporama|bodacc|entreprises\.lefigaro|"
                      r"score3|kompass|dnb\.com|opencorporates|lagazettefrance|b-reputation|pagesjaunes|lesentreprises|"
                      r"wikidata|wikipedia|societeinfo|sirene|siret|rubypayeur|france-entreprises|bilans?-|entreprise\.|"
                      r"openbase|french-companies|companieshouse|dirigeant|infonet|creditsafe|ellisphere|northdata|crunchbase|pitchbook|dealroom|tracxn|cbinsights", re.I)
# legal notices, T&Cs and privacy pages print the registered seat, not the office
LEGAL_URL = re.compile(r"mentions|legal|l[ée]gales?|cgu|cgv|terms|termes|conditions|privacy|confidentialit|politique|"
                       r"imprint|impressum|rgpd|gdpr|cookie|disclaimer", re.I)
JOBS = re.compile(r"welcometothejungle|jobs\.|careers\.|lever\.co|greenhouse\.io|ashbyhq|teamtailor|workable|recruitee|"
                  r"smartrecruiters|indeed|hellowork|jobteaser|apec\.fr|glassdoor|wttj|join\.com|personio|flatchr|"
                  r"/careers|/jobs|/carrieres|/recrutement", re.I)
COWORK = re.compile(r"wework|morning|kwerk|regus|spaces|deskeo|station f|now coworking|wojo|bureaux à partager|cowork", re.I)


def call(body, key):
    p = os.path.join(CACHE, re.sub(r'[^a-z0-9.-]', '_', key.lower())[:120] + '.json')
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    with lock:
        if spent[0] >= BUDGET:
            return None
    req = urllib.request.Request('https://treg.to/call/exa.web.search', data=json.dumps(body).encode(), method='POST',
                                 headers={'x-treg-token': TOKEN, 'content-type': 'application/json'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                cost = int(r.headers.get('x-treg-cost-micro') or 0) / 1e6
                data = json.load(r)
            with lock:
                spent[0] += cost
            os.makedirs(CACHE, exist_ok=True)
            with open(p, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get('Retry-After') or 5)); continue
            if e.code in (401, 402, 403):
                print(f'  treg {e.code}: {e.read()[:200]!r}', file=sys.stderr)
                return None
            time.sleep(3)
        except Exception:  # noqa: BLE001
            time.sleep(3)
    return None


def host(u):
    return re.sub(r'^www\.', '', urllib.parse.urlparse(u or '').netloc.lower())


def root(h):
    parts = h.split('.')
    return '.'.join(parts[-3:]) if len(parts) > 2 and parts[-2] in ('co', 'com') else '.'.join(parts[-2:])


def candidates(c, results):
    name = L.fold(c['n'])
    core = L.core(c['n']) or name
    dom = root(host(c.get('u')))
    out = []
    for r in results or []:
        url = r.get('url') or ''
        h = host(url)
        own = bool(dom) and root(h) == dom
        if 'linkedin.com' in h:
            continue  # LinkedIn office lists are already used (treg_enrich.py)
        texts = list(r.get('highlights') or []) + ([r['text']] if r.get('text') else [])
        title = L.fold(r.get('title') or '')
        for t in texts:
            for m in ADDR_RE.finditer(t):
                near = t[max(0, m.start() - 80):m.start()]
                ctx = t[max(0, m.start() - 220):m.start()] + ' ' + t[m.end():m.end() + 80]
                mentioned = own or (core and re.search(r'\b' + re.escape(core) + r'\b', L.fold(ctx) + ' ' + title))
                if not mentioned:
                    continue  # an address on a third-party page that does not name the company is someone else's
                if re.search(r'\b(cs|bp|cedex|tsa)\s*$', m.group(1), re.I):
                    continue  # "CS 92459" is a mail-routing code, not a postcode
                seat = bool(NEG.search(near)) or bool(REGISTRY.search(h)) or bool(LEGAL_URL.search(urllib.parse.urlparse(url).path))
                score = (2 if POS.search(ctx) else 0) - (3 if seat else 1 if NEG.search(ctx) else 0)
                score += 2 if own else 1 if JOBS.search(url) else 0
                addr = re.sub(r'\s+', ' ', m.group(1)).strip(' ,-')
                if L.addr_key(addr) in L.DOMICILIATION:
                    continue
                kind = 'legal' if seat else 'website' if own else 'jobs' if JOBS.search(url) else 'press'
                out.append({'addr': addr, 'postcode': m.group(2), 'score': score, 'url': url, 'kind': kind,
                            'cowork': bool(COWORK.search(ctx))})
    # the same street seen on several pages is stronger
    agg = {}
    for x in out:
        k = L.addr_key(x['addr'])
        if k in agg:
            agg[k]['score'] += 1 if x['url'] != agg[k]['url'] else 0
            if x['score'] > agg[k]['score']:
                agg[k].update({kk: x[kk] for kk in ('url', 'kind')})
        else:
            agg[k] = dict(x)
    return sorted(agg.values(), key=lambda x: -x['score'])


def work(c):
    dom = host(c.get('u'))
    q = (f'{c["n"]} ({dom}) — page giving the street address of the {c["n"]} office in Paris where the team works '
         f'(contact page, careers page or job post with the office location)')
    data = call({'query': q, 'numResults': 8, 'userLocation': 'FR', 'type': 'auto',
                 'contents': {'highlights': {'query': f'{c["n"]} office address bureaux adresse Paris', 'maxCharacters': 1200}}},
                f'{dom or L.fold(c["n"])}')
    if data is None:
        return c, None
    cands = candidates(c, data.get('results'))
    return c, cands


def main():
    if not TOKEN:
        sys.exit('TREG_TOKEN is not set')
    with open(os.path.join(ROOT, 'data', 'startups.json')) as f:
        startups = json.load(f)
    todo = startups if '--all' in sys.argv else [s for s in startups if s.get('cf') in ('u', 'l')]
    if '--unlocated' in sys.argv:   # companies resolve.py could not place at all (not on the map yet)
        with open(os.path.join(ROOT, 'data', 'raw', 'unlocated.json')) as f:
            todo = [dict(r, cf='u') for r in json.load(f)]
    todo.sort(key=lambda s: ('ulv'.index(s.get('cf', 'v')), -(s.get('e') or 0)))   # weakest first, then biggest teams
    if LIMIT:
        todo = todo[:LIMIT]
    print(len(todo), 'startups to search · budget $%.2f' % BUDGET)
    rows = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            rows = {(r['n'], r['w']): r for r in json.load(f)}
    found = 0
    with ThreadPoolExecutor(8) as ex:
        for i, (c, cands) in enumerate(ex.map(work, todo)):
            if cands:
                b = cands[0]
                if b['score'] >= 1 and b['kind'] != 'legal':
                    found += 1
                    rows[(c['n'], host(c.get('u')))] = {
                        'n': c['n'], 'w': host(c.get('u')), 'office_street': b['addr'], 'postcode': b['postcode'],
                        'source_url': b['url'], 'source_type': b['kind'], 'is_startup': True,
                        # own site / job post with office wording = strong; a press mention or a coworking = medium
                        'weight': 3 if b['kind'] in ('website', 'jobs') and b['score'] >= 2 and not b['cowork'] else 2,
                        'via': 'exa'}
            if i % 200 == 0:
                print(f'  {i} searched · {found} offices · ${spent[0]:.2f}', flush=True)
    with open(OUT, 'w') as f:
        json.dump(sorted(rows.values(), key=lambda r: r['n'].lower()), f, ensure_ascii=False, indent=0)
    print(f'done: {found} of {len(todo)} with an office address · spent ${spent[0]:.2f} · {OUT}')


if __name__ == '__main__':
    main()
