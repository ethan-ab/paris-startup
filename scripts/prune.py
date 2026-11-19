#!/usr/bin/env python3
"""Remove the companies listed in data/ref/not_tech.json from the published dataset without rerunning the pipeline.

data/ref/not_tech.json lists companies that are not tech startups (restaurants, consumer brands, agencies, consultancies,
brokers, associations, spam...), keyed by website domain (w) or name (n), with a code (c) and a short reason (r).
resolve.py reads the same file, so a full rerun drops them too. This script applies it to data/startups.json in place
and reindexes data/buildings.json (startup indices in "s", counts, lead stage; buildings left empty are removed).
Usage: python3 scripts/prune.py && python3 scripts/build.py
"""
import json, os, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
RANK = {'unknown': 0, 'early': 1, 'growth': 2, 'scale': 3}


def domain(u):
    try:
        return (urllib.parse.urlparse(u or '').hostname or '').lower().removeprefix('www.')
    except ValueError:
        return ''


def fold(s):
    import locate as L
    return L.fold(s)


def load_not_tech():
    with open(os.path.join(ROOT, 'data', 'ref', 'not_tech.json')) as f:
        rows = json.load(f)
    return {r['w'] for r in rows if r.get('w')}, {fold(r['n']) for r in rows if r.get('n') and not r.get('w')}


def main():
    doms, names = load_not_tech()
    sp, bp = os.path.join(ROOT, 'data', 'startups.json'), os.path.join(ROOT, 'data', 'buildings.json')
    rows = json.load(open(sp))
    keep = [i for i, r in enumerate(rows) if domain(r.get('u')) not in doms and fold(r['n']) not in names]
    new = {old: j for j, old in enumerate(keep)}
    out = [rows[i] for i in keep]
    bld = json.load(open(bp))
    feats = []
    for ft in bld['features']:
        p = ft['properties']
        idx = [new[i] for i in p['s'] if i in new]
        if not idx:
            continue
        offices = [i for i in idx if out[i].get('k') == 'office']
        lead = max(offices or idx, key=lambda i: (RANK.get(out[i].get('st'), 0), out[i].get('e') or 0))
        p.update(s=idx, n=len(idx), no=len(offices), o=1 if offices else 0, st=out[lead]['st'])
        feats.append(ft)
    bld['features'] = feats
    with open(sp, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    with open(bp, 'w') as f:
        json.dump(bld, f, separators=(',', ':'))
    print(f'{len(rows) - len(out)} removed · {len(out)} startups · {len(feats)} buildings')


if __name__ == '__main__':
    main()
