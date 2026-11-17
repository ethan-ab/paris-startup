#!/usr/bin/env python3
"""Turn data/seed/known.txt (well-known names Exa under-ranks) into company records shaped like Exa's.

Description comes from the company's homepage (meta description); founding year and team size band from the
company register (SIRENE). Output: data/raw/seed_companies.json. Location is resolved later like every other company.
"""
import html, json, os, re, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locate as L  # noqa: E402
import websites as W  # noqa: E402

ROOT = L.ROOT
BAND = {'00': 0, '01': 2, '02': 4, '03': 8, '11': 15, '12': 35, '21': 75, '22': 150, '31': 225, '32': 375, '41': 750,
        '42': 1500, '51': 3500, '52': 7500, '53': 10000}


def meta_desc(h):
    for pat in (r'<meta[^>]+name="description"[^>]+content="([^"]*)"', r'<meta[^>]+content="([^"]*)"[^>]+name="description"',
                r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"'):
        m = re.search(pat, h or '', re.I)
        if m and len(m.group(1).strip()) > 20:
            return html.unescape(m.group(1)).strip()
    return None


def build(line):
    name, dom = [x.strip() for x in line.split('|')]
    url = 'https://' + dom + '/'
    rec = {'name': name, 'url': url, 'domain': dom, 'description': meta_desc(W.get(url) or W.get('https://www.' + dom + '/')),
           'queries': [], 'seed': True}
    data = L.sirene(name)
    want = {L.core(name), L.fold(name), L.fold(dom.split('.')[0])} - {''}
    for r in (data or {}).get('results', []):
        names = {L.fold(r.get('nom_raison_sociale')), L.core(r.get('nom_raison_sociale')), L.fold(r.get('sigle')), L.core(r.get('nom_complet'))}
        if want & (names - {''}):
            rec['founded'] = int((r.get('date_creation') or '0')[:4]) or None
            rec['employees'] = BAND.get(r.get('tranche_effectif_salarie') or '', None)
            break
    return rec


def main():
    lines = [l for l in open(os.path.join(ROOT, 'data', 'seed', 'known.txt')).read().splitlines() if '|' in l and not l.startswith('#')]
    with ThreadPoolExecutor(8) as ex:
        recs = list(ex.map(build, lines))
    with open(os.path.join(ROOT, 'data', 'raw', 'seed_companies.json'), 'w') as f:
        json.dump(recs, f, ensure_ascii=False, indent=0)
    print(len(recs), 'seeds ·', sum(bool(r.get('description')) for r in recs), 'with description ·', sum(bool(r.get('employees')) for r in recs), 'with team size')


if __name__ == '__main__':
    main()
