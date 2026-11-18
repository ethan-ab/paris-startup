#!/usr/bin/env python3
"""Free office finder: open SECONDARY establishments from the French company register (SIRENE, via recherche-entreprises.api.gouv.fr).

No API key, no credits (7 requests/s limit, responses cached in data/cache/). For every startup it lists the company's open establishments in Paris + inner ring that are not the registered seat
(no matter where the current pin is: resolve.py decides what wins). Writes data/ref/register_offices.json ({n, w, office_street, postcode, created,
current, source_type: 'register'}), which resolve.py can read as research evidence.
Usage: python3 scripts/register_offices.py [--limit N] [--workers 6]   (checks every startup; ~13 min cold, instant when cached)
"""
import json, math, os, re, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locate as L  # noqa: E402

OUT = os.path.join(L.ROOT, 'data', 'ref', 'register_offices.json')


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111320, (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])))


# A brand name can match an unrelated company (BOBO the restaurant, MOODWALK for Moodwork): drop matches whose activity is
# a physical trade (farming, food, retail, hairdressing...) or whose creation year is far from the startup's founding year.
NAF_NO = re.compile(r'^(0[123]|10|11|45|47\.(?!91)|55|56|85\.5|93|96)')


def plausible(r, meta):
    if NAF_NO.match(meta.get('naf') or ''):
        return False
    yr = int((meta.get('created') or '0')[:4] or 0)
    return not (r.get('y') and yr and not (r['y'] - 3 <= yr <= r['y'] + 2))


def main():
    rows = json.load(open(os.path.join(L.ROOT, 'data', 'startups.json')))
    todo = rows
    if '--limit' in sys.argv:
        todo = todo[:int(sys.argv[sys.argv.index('--limit') + 1])]

    def one(r):
        dom = (r.get('u') or '').replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0].lower()
        meta = {}
        ets = L.sirene_match_all({'name': r['n'], 'domain': dom, 'founded': r.get('y')}, r.get('pc'), meta)
        if ets and not plausible(r, meta):
            return r, []
        # independent of the current pin, so a rerun gives the same file (resolve.py decides what wins)
        sec = [e for e in ets if not e['siege'] and L.addr_key(e['street']) not in L.DOMICILIATION]
        return r, sec

    out = []
    with ThreadPoolExecutor(int(sys.argv[sys.argv.index('--workers') + 1]) if '--workers' in sys.argv else 6) as ex:
        for r, sec in ex.map(one, todo):
            if sec:
                out.append({'n': r['n'], 'w': (r.get('u') or '').replace('https://', '').replace('www.', '').strip('/'),
                            'cands': [
                                {'office_street': e['street'], 'postcode': e['postcode'], 'll': [e['lat'], e['lng']]} for e in sec]})
    json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=0)
    print(len(todo), 'checked ·', len(out), 'with another open establishment than the pin ->', OUT)


if __name__ == '__main__':
    main()
