#!/usr/bin/env python3
"""Give startups added by hand (rows of data/startups.json without "b") a building, without rerunning buildings.py.

buildings.py rebuilds every footprint and needs the BAN, cadastre and tile caches for all ~3,200 startups. For a few
rows added from GitHub "new-startup" issues this script instead:
  1. reindexes data/buildings.json from each row's "b" (inserting rows shifts the startup indices in "s"),
  2. finds each new row's building the way buildings.py does (address parcel -> basemap footprint, else nearest
     footprint, else a small block), and joins an existing coloured building when it is the same one,
  3. updates counts, office counts and lead stage.
Usage: python3 scripts/add_buildings.py && python3 scripts/build.py
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buildings as B  # noqa: E402
from shapely.geometry import Point, shape  # noqa: E402
from shapely import affinity  # noqa: E402

ROOT = B.ROOT


def footprint(p, i):
    """-> (polygon, h, mh) for one startup row, as buildings.py would draw it."""
    B._last.clear()
    lat, lon = p['ll']
    k, how = B.parcel_building(p)
    if k is None and how == 'no-building' and B._last.get('par') is not None and B._last['par'].area * 111320 * 73000 < 6000:
        par = B._last['par']
        poly = B.grow(par, lat, -1.0)
        return (par if poly.is_empty or poly.area < 0.5 * par.area else poly), 12, 0, 'parcel-only'
    if k is None:
        k = B.find(lat, lon)
        how = 'nearest (' + how + ')' if k else 'point (' + how + ')'
    if k is None:
        dlat, dlon = B.metres_to_deg(lat)
        poly = affinity.scale(Point(lon, lat).buffer(7 * dlat, quad_segs=2), xfact=dlon / dlat, yfact=1)
        return poly, 14, 0, how
    polys, meta, _ = B.tile(k[0], k[1])
    h, mh = meta[k[2]]
    return B.grow(polys[k[2]], lat, 0.4), h or 10, mh, how


def main():
    sp, bp = os.path.join(ROOT, 'data', 'startups.json'), os.path.join(ROOT, 'data', 'buildings.json')
    rows = json.load(open(sp))
    bld = json.load(open(bp))
    feats = {ft['id']: ft for ft in bld['features']}
    for ft in feats.values():
        ft['properties']['s'] = []
    new = []
    for i, r in enumerate(rows):
        if r.get('b') in feats:
            feats[r['b']]['properties']['s'].append(i)
        else:
            new.append(i)
    for i in new:
        r = rows[i]
        poly, h, mh, how = footprint(r, i)
        c = poly.representative_point()
        same = next((ft for ft in feats.values() if shape(ft['geometry']).contains(c)), None)
        if same:
            r['b'] = same['id']
            same['properties']['s'].append(i)
            print(f"  {r['n']}: joins building {same['id']} ({how})")
            continue
        bid = max(feats) + 1
        r['b'] = bid
        feats[bid] = {'type': 'Feature', 'id': bid, 'geometry': B.rnd(poly),
                      'properties': {'b': bid, 'h': round(h + 0.6, 1), 'mh': mh, 's': [i]}}
        print(f"  {r['n']}: new building {bid} ({how})")
    out = []
    for ft in feats.values():
        p = ft['properties']
        idx = sorted(p['s'])
        if not idx:
            continue
        offices = [i for i in idx if rows[i].get('k') == 'office']
        lead = max(offices or idx, key=lambda i: (B.RANK.get(rows[i].get('st'), -1), rows[i].get('e') or 0))
        p.update(s=idx, n=len(idx), no=len(offices), o=1 if offices else 0, st=rows[lead]['st'])
        out.append(ft)
    bld['features'] = out
    with open(bp, 'w') as f:
        json.dump(bld, f, separators=(',', ':'))
    with open(sp, 'w') as f:
        json.dump(rows, f, ensure_ascii=False, indent=0)
    print(f'{len(new)} startups placed · {len(rows)} startups in {len(out)} buildings')


if __name__ == '__main__':
    main()
