#!/usr/bin/env python3
"""Free office finder: open SECONDARY establishments from the French company register (SIRENE, via recherche-entreprises.api.gouv.fr).

No API key, no credits (7 requests/s limit, responses cached in data/cache/). For every startup whose pin is not verified
(or everyone with --all) it lists the company's open establishments in Paris + inner ring that are not the registered seat and
sit more than ~60 m from the current pin. Writes data/ref/register_offices.json ({n, w, office_street, postcode, created,
current, source_type: 'register'}), which resolve.py can read as research evidence.
Usage: python3 scripts/register_offices.py [--all] [--limit N] [--workers 6]
"""
import json, math, os, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locate as L  # noqa: E402

OUT = os.path.join(L.ROOT, 'data', 'ref', 'register_offices.json')


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111320, (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])))


def main():
    rows = json.load(open(os.path.join(L.ROOT, 'data', 'startups.json')))
    todo = rows if '--all' in sys.argv else [r for r in rows if r.get('cf') != 'v']
    if '--limit' in sys.argv:
        todo = todo[:int(sys.argv[sys.argv.index('--limit') + 1])]

    def one(r):
        dom = (r.get('u') or '').replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0].lower()
        ets = L.sirene_match_all({'name': r['n'], 'domain': dom, 'founded': r.get('y')}, r.get('pc'))
        sec = [e for e in ets if not e['siege'] and L.addr_key(e['street']) not in L.DOMICILIATION
               and dist_m((e['lat'], e['lng']), r['ll']) > 60]
        return r, sec

    out = []
    with ThreadPoolExecutor(int(sys.argv[sys.argv.index('--workers') + 1]) if '--workers' in sys.argv else 6) as ex:
        for r, sec in ex.map(one, todo):
            if sec:
                out.append({'n': r['n'], 'w': (r.get('u') or '').replace('https://', '').replace('www.', '').strip('/'),
                            'current': r['a'] + ' ' + r['pc'], 'cf': r['cf'], 'cands': [
                                {'office_street': e['street'], 'postcode': e['postcode'], 'll': [e['lat'], e['lng']]} for e in sec]})
    json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=0)
    print(len(todo), 'checked ·', len(out), 'with another open establishment than the pin ->', OUT)


if __name__ == '__main__':
    main()
