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
METHOD = {}
PARCEL = {}


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
    # The point is usually on the street side of the address, not inside the footprint. Take every building within 25 m, then among
    # those about as close as the nearest one (+6 m) prefer the biggest tall one: sheds and courtyard annexes are rarely the office.
    dlat, dlon = metres_to_deg(lat)
    near = []
    for i in tree.query(pt.buffer(26 * dlat)):
        q = polys[i].exterior.interpolate(polys[i].exterior.project(pt)) if polys[i].geom_type == 'Polygon' else None
        if q is None:
            continue
        d = math.hypot((q.x - lon) / dlon, (q.y - lat) / dlat)
        if d <= 25:
            near.append((d, i))
    if not near:
        return None
    d0 = min(near)[0]
    d, i = max((c for c in near if c[0] <= d0 + 6), key=lambda c: polys[c[1]].area * (meta[c[1]][0] or 10))
    return (tx, ty, int(i))


# ---------- cadastre: the building an address really belongs to ----------
# BAN (adresse.data.gouv.fr) lists the cadastral parcel of every address; the cadastre (cadastre.data.gouv.fr) gives the parcel polygon.
# Both are free open data. The building of a startup = the basemap footprint that covers most of its parcel, not "nearest to a point
# that often sits in the street or a courtyard".
import csv, gzip, re, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locate as L  # noqa: E402

_parcels = {}
_last = {}


def _fetch(url, path):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import time
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=120) as r:
                    data = r.read()
                with open(path, 'wb') as f:
                    f.write(data)
                break
            except Exception:  # noqa: BLE001  (connection resets happen; back off and retry)
                if attempt == 5:
                    raise
                time.sleep(3 * (attempt + 1))
    return path



ban_index = L.ban_index
_communes = L._communes   # filled by ban_index()


def parcels(insee):
    if insee not in _parcels:
        import gzip as gz
        p = _fetch(f'https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/communes/{insee[:2]}/{insee}/cadastre-{insee}-parcelles.json.gz',
                   os.path.join(ROOT, 'data', 'cache', 'cadastre', f'{insee}-parcelles.json.gz'))
        with gz.open(p, 'rt', encoding='utf-8') as f:
            pm = {ft['properties']['id']: shape(ft['geometry']) for ft in json.load(f)['features']}
        _parcels[insee] = (pm, STRtree(list(pm.values())), list(pm.values()))
    return _parcels[insee]


def parcel_at(insees, lat, lon):
    pt = Point(lon, lat)
    for insee in insees:
        pm, tree, geoms = parcels(insee)
        for i in tree.query(pt):
            if geoms[i].contains(pt):
                return geoms[i]
    return None


def parcel_building(p):
    """-> ((tx, ty, i), how) for a startup row: the basemap building covering most of the parcel of its address; or (None, why).
    The parcel comes from BAN (cad_parcelles), else from the parcel under BAN's coordinate for that address, else under the pin."""
    hit = ban_index().get((L.addr_key(p.get('a')), p.get('pc')))
    par, how = None, 'parcel'
    if hit and hit[1]:
        pm = parcels(hit[0])[0]
        geoms = [pm[i] for i in hit[1] if i in pm]
        if geoms:
            from shapely.ops import unary_union
            par = unary_union(geoms)
    if par is None and hit:
        par, how = parcel_at([hit[0]], hit[2], hit[3]), 'parcel-from-ban-point'
    if par is None:
        par, how = parcel_at(sorted(_communes.get(p.get('pc'), [])), p['ll'][0], p['ll'][1]), 'parcel-from-pin'
    if par is None:
        return None, 'no-parcel'
    _last['par'] = par
    c = par.centroid
    tx, ty = txy(c.x, c.y)
    polys, meta, tree = tile(tx, ty)
    if not tree:
        return None, 'no-building'
    best = None
    for i in tree.query(par):
        inter = polys[i].intersection(par).area
        if inter <= 0:
            continue
        if inter / polys[i].area < 0.4 and inter / par.area < 0.25:   # the neighbour's building clipping the parcel edge
            continue
        score = (inter, meta[i][0] or 10)
        if not best or score > best[0]:
            best = (score, i)
    if not best:
        return None, 'no-building'
    return (tx, ty, int(best[1])), how


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
    METHOD.clear()
    for i, p in enumerate(rows):
        if p.get('cp') == 'Station F':  # one campus, one building: all its halls together
            groups.setdefault(('stationf', 0, 0), []).append(i); continue
        _last.clear()
        k, how = parcel_building(p)
        if k is None and how == 'no-building' and _last.get('par') is not None and _last['par'].area * 111320 * 73000 < 6000:
            PARCEL[i] = _last['par']; k, how = ('parcel', i, 0), 'parcel-only'   # address found, building missing from the basemap: draw the parcel
        if k is None:
            k = find(p['ll'][0], p['ll'][1]); how = 'nearest (' + how + ')' if k else 'point (' + how + ')'
        METHOD[how] = METHOD.get(how, 0) + 1
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
        elif k[0] == 'parcel':  # building not on the basemap yet: the parcel itself, a modest 12 m block
            poly, h, mh = grow(PARCEL[k[1]], lat0, -1.0), 12, 0
            if poly.is_empty or poly.area < 0.5 * PARCEL[k[1]].area:   # tiny parcel: shrinking it would erase it
                poly = PARCEL[k[1]]
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
    print('building found by:', METHOD)
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
