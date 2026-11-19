#!/usr/bin/env python3
"""Apply data/ref/corrections.json to data/startups.json without a full resolve.py run.

resolve.py already lets corrections win, but a full run needs every network cache (Exa, treg, SIRENE, BAN) on disk;
without them it rebuilds a different dataset. This patches only the corrected rows, the way resolve.py would write them:
street, postcode, area, BAN coordinates, office / verified / manual, campus, and the Station F "moved" flag. It drops
their building id, so run scripts/buildings.py next, then scripts/build.py.

A correction row: {"name", "website", "office_street", "postcode", "city", "source_url", "note"} (+ optional "employees").
Usage: python3 scripts/apply_corrections.py
"""
import json, os, sys, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locate as L  # noqa: E402
import resolve as R  # noqa: E402

ROOT = L.ROOT


def host(u):
    try:
        return (urllib.parse.urlparse(u if '//' in (u or '') else '//' + (u or '')).hostname or '').lower().removeprefix('www.')
    except ValueError:
        return ''


def main():
    rows = json.load(open(os.path.join(ROOT, 'data', 'startups.json')))
    cors = [c for c in json.load(open(os.path.join(ROOT, 'data', 'ref', 'corrections.json'))) if c.get('office_street')]
    by_dom = {host(r['u']): r for r in rows if host(r['u'])}
    by_name = {L.fold(r['n']): r for r in rows}
    todo = []
    for i, c in enumerate(cors):
        r = by_dom.get(host(c.get('website'))) or by_name.get(L.fold(c['name']))
        if r is None:
            print('  no startup for correction:', c['name'])
            continue
        todo.append((i, c, r))
    geo = L.ban_batch([(str(i), f"{c['office_street']} {c.get('postcode') or ''} {c.get('city') or ''}".strip(), c.get('postcode')) for i, c, _ in todo])
    changed = 0
    for i, c, r in todo:
        g = geo.get(str(i))
        if not g or (g[6] or 0) < 0.5:
            print(f"  could not geocode {c['name']}: {c['office_street']} {c.get('postcode')}")
            continue
        lat, lon, label, pc, city = g[0], g[1], g[2], g[3] or c.get('postcode'), g[4] or c.get('city')
        new = {'a': R.clean_street(c['office_street'][:1].upper() + c['office_street'][1:]), 'pc': pc, 'area': R.area(pc, city),
               'll': [round(lat, 6), round(lon, 6)], 'k': 'office', 'cf': 'v', 'v': 'manual', 'ev': ['manual']}
        if c.get('employees'):
            new['e'], new['eh'] = c['employees'], 'manual'
        before = (r.get('a'), r.get('pc'))
        if before == (new['a'], new['pc']) and r.get('v') == 'manual':
            continue
        r.update(new)
        cp = R.CAMPUS.get(L.addr_key(r['a']))
        if cp:
            r['cp'] = cp
        else:
            r.pop('cp', None)
        if r.get('sf'):
            r['sf']['s'] = 'here' if L.addr_key(r['a']) in ('5 parvis alan turing', '5 rue eugene freyssinet') else 'moved'
        r.pop('b', None)
        changed += 1
        print(f"  {r['n']}: {before[0]}, {before[1]} -> {r['a']}, {r['pc']}")
    with open(os.path.join(ROOT, 'data', 'startups.json'), 'w') as f:
        json.dump(rows, f, ensure_ascii=False, indent=0)
    print(f'{changed} startups moved to their corrected office')


if __name__ == '__main__':
    main()
