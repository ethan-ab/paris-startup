#!/usr/bin/env python3
"""Find the building footprint each startup sits in, so the map can colour buildings instead of drawing dots.

Reads data/startups.json, decodes OpenFreeMap (OpenMapTiles) z14 vector tiles around every startup, finds the
building polygon that contains the office point (or the nearest one within 25 m) and writes:
  data/buildings.json  {"type":"FeatureCollection"} one feature per occupied building:
                       properties: b (building id), h / mh (height / base, metres), st (stage of its largest startup),
                       n (number of startups), s ([startup indices])
  and adds "b" (building id) to every row of data/startups.json.
Footprints are grown by ~0.4 m and raised by 0.6 m so the coloured walls sit just outside the base map's grey ones.
With --context it also writes data/context_buildings.json: plain buildings around central Paris for the offline build.
"""
import json, math, os, sys, urllib.request
import mapbox_vector_tile
from shapely.geometry import Point, Polygon, MultiPolygon, mapping, shape
from shapely.strtree import STRtree
from shapely import affinity

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'data', 'cache', 'tiles')
UA = 'paris-startup-map/1.0 (building lookup)'
Z = 14
RANK = {'unknown': -1, 'early': 0, 'growth': 1, 'scale': 2}
TMPL = None


def tile_url():
    global TMPL
    if not TMPL:
        with urllib.request.urlopen(urllib.request.Request('https://tiles.openfreemap.org/planet', headers={'User-Agent': UA})) as r:
            TMPL = json.load(r)['tiles'][0]
    return TMPL


def txy(lon, lat):
    n = 2 ** Z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def to_ll(px, py, tx, ty, ext):
    n = 2 ** Z
    lon = (tx + px / ext) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + py / ext) / n))))
    return lon, lat


_tiles = {}


def tile(tx, ty):
    if (tx, ty) in _tiles:
        return _tiles[(tx, ty)]
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f'{Z}_{tx}_{ty}.pbf')
    if not os.path.exists(p):
        u = tile_url().replace('{z}', str(Z)).replace('{x}', str(tx)).replace('{y}', str(ty))
        with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': UA}), timeout=60) as r, open(p, 'wb') as f:
            f.write(r.read())
    with open(p, 'rb') as f:
        t = mapbox_vector_tile.decode(f.read(), default_options={'y_coord_down': True})
    polys, meta = [], []
    lay = t.get('building') or {'features': [], 'extent': 4096}
    ext = lay.get('extent', 4096)
    for feat in lay['features']:
        g = feat['geometry']
        h = feat['properties'].get('render_height') or 0
        mh = feat['properties'].get('render_min_height') or 0
        parts = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates'] if g['type'] == 'MultiPolygon' else []
        for rings in parts:
            try:
                poly = Polygon([to_ll(x, y, tx, ty, ext) for x, y in rings[0]], [[to_ll(x, y, tx, ty, ext) for x, y in r] for r in rings[1:]])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty or poly.area <= 0:
                    continue
            except Exception:  # noqa: BLE001
                continue
            polys.append(poly); meta.append((h, mh))
    _tiles[(tx, ty)] = (polys, meta, STRtree(polys) if polys else None)
    return _tiles[(tx, ty)]


def metres_to_deg(lat):
    return 1 / 111320, 1 / (111320 * math.cos(math.radians(lat)))


def find(lat, lon):
    tx, ty = txy(lon, lat)
    polys, meta, tree = tile(tx, ty)
    if not tree:
        return None
    pt = Point(lon, lat)
    for i in tree.query(pt):
        if polys[i].contains(pt):
            return (tx, ty, int(i))
    dlat, dlon = metres_to_deg(lat)
    i = tree.nearest(pt)
    if i is not None:
        # distance in metres (approx.)
        q = polys[i].exterior.interpolate(polys[i].exterior.project(pt)) if polys[i].geom_type == 'Polygon' else None
        if q is not None and math.hypot((q.x - lon) / dlon, (q.y - lat) / dlat) <= 25:
            return (tx, ty, int(i))
    return None


def rnd(geom):
    def r(coords):
        return [[round(x, 6), round(y, 6)] for x, y in coords]
    if geom.geom_type == 'Polygon':
        return {'type': 'Polygon', 'coordinates': [r(geom.exterior.coords)] + [r(i.coords) for i in geom.interiors]}
    return {'type': 'MultiPolygon', 'coordinates': [[r(p.exterior.coords)] + [r(i.coords) for i in p.interiors] for p in geom.geoms]}


def grow(poly, lat, m):
    dlat, dlon = metres_to_deg(lat)
    # buffer in a locally isotropic frame, then scale back
    s = affinity.scale(poly, xfact=dlat / dlon, yfact=1, origin=(poly.centroid.x, poly.centroid.y))
    s = s.buffer(m * dlat, join_style=2).simplify(0.2 * dlat)
    return affinity.scale(s, xfact=dlon / dlat, yfact=1, origin=(poly.centroid.x, poly.centroid.y))


def main():
    path = os.path.join(ROOT, 'data', 'startups.json')
    with open(path) as f:
        rows = json.load(f)
    groups, solo = {}, 0
    for i, p in enumerate(rows):
        if p.get('cp') == 'Station F':  # one campus, one building: all its halls together
            groups.setdefault(('stationf', 0, 0), []).append(i); continue
        k = find(p['ll'][0], p['ll'][1])
        if k is None:
            k = ('pt', i, 0); solo += 1
        groups.setdefault(k, []).append(i)
    feats = []
    for bid, (k, idx) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        lat0, lon0 = rows[idx[0]]['ll']
        if k[0] == 'stationf':
            with open(os.path.join(ROOT, 'data', 'ref', 'stationf.geojson')) as f:
                fp = shape(json.load(f))
            tx, ty = txy(fp.centroid.x, fp.centroid.y)
            polys, meta, tree = tile(tx, ty)
            parts = [(polys[j], meta[j]) for j in tree.query(fp) if polys[j].intersection(fp).area > 0.5 * polys[j].area]
            from shapely.ops import unary_union
            poly = grow(unary_union([q for q, _ in parts]) if parts else fp, lat0, 0.4)
            h, mh = max([m[0] for _, m in parts] or [20]), 0
        elif k[0] == 'pt':  # no footprint nearby: a small 7 m block on the point
            dlat, dlon = metres_to_deg(lat0)
            poly = Point(lon0, lat0).buffer(7 * dlat, quad_segs=2)
            poly = affinity.scale(poly, xfact=dlon / dlat, yfact=1)
            h, mh = 14, 0
        else:
            polys, meta, _ = tile(k[0], k[1])
            poly, (h, mh) = polys[k[2]], meta[k[2]]
            h = h or 10
            poly = grow(poly, lat0, 0.4)
        offices = [i for i in idx if rows[i].get('k') == 'office']
        lead = max(offices or idx, key=lambda i: (RANK[rows[i]['st']], rows[i].get('e') or 0))
        for i in idx:
            rows[i]['b'] = bid
        feats.append({'type': 'Feature', 'id': bid, 'geometry': rnd(poly),
                      'properties': {'b': bid, 'h': round(h + 0.6, 1), 'mh': mh, 'st': rows[lead]['st'],
                                     'o': 1 if offices else 0, 'n': len(idx), 'no': len(offices), 's': idx}})
    with open(os.path.join(ROOT, 'data', 'buildings.json'), 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': feats}, f, separators=(',', ':'))
    with open(path, 'w') as f:
        json.dump(rows, f, ensure_ascii=False, indent=0)
    print(len(rows), 'startups in', len(feats), 'buildings ·', solo, 'without a footprint nearby ·',
          max(len(v) for v in groups.values()), 'in the busiest building ·',
          os.path.getsize(os.path.join(ROOT, 'data', 'buildings.json')) // 1024, 'KB')
    if '--context' in sys.argv:
        context(feats)


def context(feats):
    """Plain (grey) buildings for the offline build: central Paris only, compact."""
    lon0, lat0, lon1, lat1 = 2.300, 48.835, 2.395, 48.885
    x0, y1 = txy(lon0, lat0); x1, y0 = txy(lon1, lat1)
    out = []
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            polys, meta, _ = tile(tx, ty)
            for poly, (h, mh) in zip(polys, meta):
                c = poly.centroid
                if not (lon0 <= c.x <= lon1 and lat0 <= c.y <= lat1) or poly.area < 2e-9:
                    continue
                g = poly.simplify(0.000006)
                if g.is_empty:
                    continue
                g = g if g.geom_type == 'Polygon' else max(g.geoms, key=lambda q: q.area) if hasattr(g, 'geoms') else None
                if g is None or g.geom_type != 'Polygon':
                    continue
                ring = [[round(x, 5), round(y, 5)] for x, y in g.exterior.coords]
                out.append([int(h or 8), ring])
    with open(os.path.join(ROOT, 'data', 'context_buildings.json'), 'w') as f:
        json.dump(out, f, separators=(',', ':'))
    print('context buildings:', len(out), os.path.getsize(os.path.join(ROOT, 'data', 'context_buildings.json')) // 1024, 'KB')


if __name__ == '__main__':
    main()
