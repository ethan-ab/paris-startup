#!/usr/bin/env python3
"""Find exactly where each discovered company sits.

Input:  data/raw/exa_companies.json (scripts/discover.py)
Output: data/raw/located.json  (same records + lat/lng, street, postcode, precision, address source)

Order of evidence, best first:
  1. Exa / LinkedIn office address with a street number  -> geocoded with the national address base (BAN)   precision "office"
  2. "Station F" anywhere in the address                 -> Station F, 5 parvis Alan Turing                   precision "office"
  3. Company register (SIRENE, recherche-entreprises.api.gouv.fr): the open establishment in Paris that
     matches the brand (same postcode as the office first, then head office)                                precision "registry"
  4. Street only (Exa address without number)             -> BAN street centroid                              precision "street"
Companies with nothing better than a postcode are kept with precision "area" and left off the map by default.
All network calls are cached in data/cache/.
"""
import csv, io, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request, hashlib, threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'data', 'cache')
IN = os.path.join(ROOT, 'data', 'raw', 'exa_companies.json')
OUT = os.path.join(ROOT, 'data', 'raw', 'located.json')
UA = 'paris-startup-map/1.0 (static map build)'
BBOX = (2.17, 48.79, 2.53, 48.93)  # Paris + inner ring
STATION_F = (48.83410, 2.37110, '5 Parvis Alan Turing', '75013')  # inside the hall, not on the parvis


def fold(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = s.replace('’', "'").replace('&', ' and ')
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


LEGAL = re.compile(r'\b(sas|sasu|sa|sarl|eurl|sci|scop|inc|ltd|gmbh|group|groupe|france|paris|technologies|technology|tech|labs?|io|ai|app|hq)\b')


def core(s):
    return re.sub(r'\s+', ' ', LEGAL.sub(' ', fold(s))).strip()


# Addresses of domiciliation / mail-forwarding services: a company "registered" here almost never works here.
DOMICILIATION = {
    '60 rue francois 1er', '10 rue de penthievre', '128 rue la boetie', '128 rue de la boetie', '66 avenue des champs elysees',
    '229 rue saint honore', '231 rue saint honore', '49 rue de ponthieu', '25 rue de ponthieu', '59 rue de ponthieu', '6 rue d armaille',
    '78 avenue des champs elysees', '34 avenue des champs elysees', '50 avenue des champs elysees', '102 avenue des champs elysees',
    '58 rue de monceau', '10 rue de la paix', '61 rue de lyon', '91 rue du faubourg saint honore',
    '200 rue de la croix nivert', '55 rue la boetie', '60 avenue des champs elysees', '1 rue de stockholm', '122 rue amelot',
    '23 rue du roule', '7 rue meyerbeer', '36 rue de ponthieu', '43 rue de ponthieu', '5 rue de castiglione', '26 rue vignon',
    '75 boulevard haussmann', '2 rue de la paix', '8 rue de l hotel de ville', '17 rue du colisee', '22 rue de la paix',
    '1 place boieldieu', '9 rue anatole de la forge', '11 rue magellan', '37 avenue des champs elysees', '74 avenue des champs elysees',
    '15 rue de turbigo', '19 boulevard malesherbes', '27 rue de la ville l eveque', '38 rue de berri', '5 avenue de l opera',
}


def addr_key(s):
    k = fold(s)
    k = re.sub(r'^(\d+)\s*(?:(?:bis|ter|b)\b)?\s*', r'\1 ', k)   # drop a bis/ter/b suffix (not the "b" of "boulevard")
    k = re.sub(r'\b(av|ave)\b', 'avenue', k)
    k = re.sub(r'\b(bd|blvd)\b', 'boulevard', k)
    k = re.sub(r'\bfrancois i(er)?\b', 'francois 1er', k)
    k = re.sub(r'\bst\b', 'saint', k)
    return re.sub(r'\s+', ' ', k).strip()


def in_bbox(lat, lon):
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def cached(key, fn):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + '.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    data = fn()
    if data is not None:
        with open(path, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    return data


# ---------- 1. parse Exa addresses ----------
STREET_WORDS = r"(rue|avenue|av\.?|boulevard|bd|blvd|place|quai|cours|passage|impasse|allée|allee|villa|cité|cite|square|chemin|route|parvis|esplanade|galerie|faubourg|rond[- ]point|port|cour|hameau|sentier|promenade|voie)"
NUM_STREET = re.compile(r"(\d{1,4}(?:\s?(?:bis|ter|b|t))?)[\s,]*(?:-\s*\d{1,4}\s*)?,?\s*(" + STREET_WORDS + r"\b[^,;]*)", re.I)
STREET_ONLY = re.compile(r"\b(" + STREET_WORDS + r"\s+[^,;\d]{3,})", re.I)


def parse_exa(addr):
    """-> (housenumber street or None, street-only or None, postcode or None)"""
    a = (addr or '').replace('\n', ' ')
    pc = None
    m = re.search(r'\b(75\d{3}|9[234]\d{3})\b', a)
    if m:
        pc = m.group(1)
        if pc == '75000':
            pc = None
    m = NUM_STREET.search(a)
    if m:
        return f"{m.group(1)} {m.group(2).strip()}", None, pc
    m = STREET_ONLY.search(a)
    if m and not re.match(r'(?i)^(route|port|cour)\b', m.group(1)):
        return None, m.group(1).strip(), pc
    return None, None, pc


# ---------- BAN batch geocoding (CSV endpoint) ----------
def ban_batch(rows):
    """rows: list of (id, q, postcode). Returns {id: (lat, lon, label, postcode, city, type, score)}"""
    out = {}
    todo = []
    for rid, q, pc in rows:
        k = 'ban2_' + hashlib.sha1(f'{q}|{pc}'.encode()).hexdigest()[:16]
        p = os.path.join(CACHE, k + '.json')
        if os.path.exists(p):
            with open(p) as f:
                v = json.load(f)
            if v:
                out[rid] = v
        else:
            todo.append((rid, q, pc, p))
    for i in range(0, len(todo), 800):
        chunk = todo[i:i + 800]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['rid', 'q', 'postcode'])
        for rid, q, pc, _ in chunk:
            w.writerow([rid, q, pc or ''])
        boundary = 'xxBOUNDARYxx'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="a.csv"\r\nContent-Type: text/csv\r\n\r\n' + buf.getvalue() +
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="columns"\r\n\r\nq\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="postcode"\r\n\r\npostcode\r\n--{boundary}--\r\n').encode()
        for attempt in range(4):
            try:
                req = urllib.request.Request('https://api-adresse.data.gouv.fr/search/csv/', data=body,
                                             headers={'Content-Type': f'multipart/form-data; boundary={boundary}', 'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=180) as r:
                    text = r.read().decode('utf-8')
                break
            except Exception as e:  # noqa: BLE001
                print('  BAN retry', attempt + 1, e, file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        else:
            continue
        for row in csv.DictReader(io.StringIO(text)):
            rid = row['rid']
            path = next(p for r_, _, _, p in chunk if r_ == rid)
            v = None
            try:
                if row.get('latitude') and float(row.get('result_score') or 0) >= 0.5:
                    v = [float(row['latitude']), float(row['longitude']), row.get('result_name'), row.get('result_postcode'),
                         row.get('result_city'), row.get('result_type'), float(row['result_score'])]
            except ValueError:
                v = None
            with open(path, 'w') as f:
                json.dump(v, f)
            if v:
                out[rid] = v
        print(f'  BAN {min(i + 800, len(todo))}/{len(todo)}')
    return out


# ---------- SIRENE ----------
_lock = threading.Lock()
_last = [0.0]


def sirene(q):
    def go():
        url = 'https://recherche-entreprises.api.gouv.fr/search?' + urllib.parse.urlencode(
            {'q': q, 'per_page': 5, 'etat_administratif': 'A', 'departement': '75,92,93,94'})
        for attempt in range(6):
            with _lock:  # API limit: 7 requests/second
                wait = _last[0] + 0.16 - time.time()
                if wait > 0:
                    time.sleep(wait)
                _last[0] = time.time()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    return {'results': []}
                time.sleep(1.5 * (attempt + 1))
            except Exception:  # noqa: BLE001
                time.sleep(1.5 * (attempt + 1))
        return None
    return cached('sirene_' + hashlib.sha1(q.encode()).hexdigest()[:16], go)


def sirene_match(c, office_pc):
    name = c['name'] or ''
    stem = (c.get('domain') or '').split('.')[0]
    data = sirene(name)
    if not data:
        return None
    want = {core(name), fold(name), fold(stem), core(stem)} - {''}
    best = None
    for r in data.get('results', []):
        names = {fold(r.get('nom_raison_sociale')), core(r.get('nom_raison_sociale')), fold(r.get('sigle')), core(r.get('nom_complet'))}
        ets = [r.get('siege') or {}] + (r.get('matching_etablissements') or [])
        for e in ets:
            for n in (e.get('liste_enseignes') or []) + [e.get('nom_commercial')]:
                names |= {fold(n), core(n)}
        names -= {''}
        if not (want & names):
            continue
        yr = int((r.get('date_creation') or '0')[:4] or 0)
        if c.get('founded') and yr and not (c['founded'] - 4 <= yr <= c['founded'] + 2):
            continue
        cands = []
        for e in ets:
            try:
                lat, lon = float(e.get('latitude')), float(e.get('longitude'))
            except (TypeError, ValueError):
                continue
            if e.get('etat_administratif', 'A') != 'A' or not in_bbox(lat, lon):
                continue
            score = (3 if office_pc and e.get('code_postal') == office_pc else 0) + (1 if e.get('est_siege') or e is ets[0] else 0)
            cands.append((score, lat, lon, e))
        if not cands:
            continue
        cands.sort(key=lambda x: -x[0])
        s, lat, lon, e = cands[0]
        street = re.sub(r'\s+\d{5}\s.*$', '', e.get('adresse') or '').title()
        cand = {'lat': lat, 'lng': lon, 'street': street, 'postcode': e.get('code_postal'), 'city': (e.get('libelle_commune') or '').title(),
                'siren': r.get('siren'), 'legal': r.get('nom_complet'), 'naf': r.get('activite_principale'), 'created': r.get('date_creation'),
                'headcount_band': r.get('tranche_effectif_salarie'), '_s': s}
        if not best or cand['_s'] > best['_s']:
            best = cand
    if best:
        best.pop('_s')
    return best


def main():
    with open(IN) as f:
        companies = json.load(f)
    print(len(companies), 'companies')
    rows_num, rows_street = [], []
    for i, c in enumerate(companies):
        num, street, pc = parse_exa(c.get('hq_address'))
        c['_num'], c['_street'], c['_pc'] = num, street, pc or c.get('hq_postcode')
        if num:
            rows_num.append((str(i), f'{num} Paris' if not pc else num, pc or ''))
        elif street:
            rows_street.append((str(i), street, pc or ''))
    print('with street number:', len(rows_num), '· street only:', len(rows_street))
    geo_num = ban_batch(rows_num)
    geo_street = ban_batch(rows_street)

    # SIRENE for everything without a precise office address (and as legal metadata for all)
    with ThreadPoolExecutor(6) as ex:
        sir = list(ex.map(lambda c: sirene_match(c, c['_pc']), companies))

    stats = {}
    for i, c in enumerate(companies):
        loc = None
        g = geo_num.get(str(i))
        addr = c.get('hq_address') or ''
        if re.search(r'station\s*f|parvis alan turing|5,? rue eug[eè]ne freyssinet', addr, re.I):
            loc = {'lat': STATION_F[0], 'lng': STATION_F[1], 'street': STATION_F[2], 'postcode': STATION_F[3], 'city': 'Paris', 'precision': 'office', 'via': 'linkedin'}
        elif g and g[5] == 'housenumber' and in_bbox(g[0], g[1]):
            loc = {'lat': g[0], 'lng': g[1], 'street': g[2], 'postcode': g[3], 'city': g[4], 'precision': 'office', 'via': 'linkedin'}
        elif sir[i]:
            s = sir[i]
            loc = {k: s[k] for k in ('lat', 'lng', 'street', 'postcode', 'city')} | {'precision': 'registry', 'via': 'sirene'}
        else:
            g = g or geo_street.get(str(i))
            if g and g[5] in ('housenumber', 'street') and in_bbox(g[0], g[1]):
                loc = {'lat': g[0], 'lng': g[1], 'street': g[2], 'postcode': g[3], 'city': g[4], 'precision': 'street', 'via': 'linkedin'}
        if sir[i]:
            c['legal'] = {k: sir[i][k] for k in ('siren', 'legal', 'naf', 'created', 'headcount_band')}
        if loc and loc['street'] and re.match(r'(?i)^\d', loc['street']) and loc['precision'] == 'office':
            pass
        c['loc'] = loc or ({'postcode': c['_pc'], 'precision': 'area'} if c['_pc'] else None)
        for k in ('_num', '_street', '_pc'):
            c.pop(k)
        p = (c['loc'] or {}).get('precision', 'none')
        stats[p] = stats.get(p, 0) + 1
    with open(OUT, 'w') as f:
        json.dump(companies, f, ensure_ascii=False, indent=0)
    print('precision:', stats)


if __name__ == '__main__':
    main()


def sirene_match_all(c, office_pc, meta=None):
    """All open establishments (Paris + inner ring) of the legal entity matching this brand.
    -> [{'lat','lng','street','postcode','city','siege'}] with establishments in the office postcode first."""
    name = c['name'] or ''
    stem = (c.get('domain') or '').split('.')[0]
    data = sirene(name)
    if not data:
        return []
    want = {core(name), fold(name), fold(stem), core(stem)} - {''}
    for r in data.get('results', []):
        names = {fold(r.get('nom_raison_sociale')), core(r.get('nom_raison_sociale')), fold(r.get('sigle')), core(r.get('nom_complet'))}
        siege = r.get('siege') or {}
        ets = [siege] + [e for e in (r.get('matching_etablissements') or []) if e.get('siret') != siege.get('siret')]
        for e in ets:
            for n in (e.get('liste_enseignes') or []) + [e.get('nom_commercial')]:
                names |= {fold(n), core(n)}
        if not (want & (names - {''})):
            continue
        yr = int((r.get('date_creation') or '0')[:4] or 0)
        if c.get('founded') and yr and not (c['founded'] - 4 <= yr <= c['founded'] + 2):
            continue
        out = []
        for e in ets:
            try:
                lat, lon = float(e.get('latitude')), float(e.get('longitude'))
            except (TypeError, ValueError):
                continue
            if e.get('etat_administratif', 'A') != 'A' or not in_bbox(lat, lon):
                continue
            out.append({'lat': lat, 'lng': lon, 'street': re.sub(r'\s+\d{5}\s.*$', '', e.get('adresse') or '').title(),
                        'postcode': e.get('code_postal'), 'city': (e.get('libelle_commune') or '').title(),
                        'siege': e is siege or bool(e.get('est_siege'))})
        out.sort(key=lambda e: (0 if office_pc and e['postcode'] == office_pc else 1, e['siege']))
        if meta is not None:
            meta.update({'legal': r.get('nom_complet'), 'naf': r.get('activite_principale'), 'created': r.get('date_creation'), 'siren': r.get('siren')})
        return out
    return []


# ---------- BAN address dump: coordinates + cadastral parcel of every address (free open data) ----------
import csv, gzip  # noqa: E402

_ban, _communes = {}, {}


def _fetch_ban(url, path):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=120) as r:
                    data = r.read()
                with open(path, 'wb') as f:
                    f.write(data)
                break
            except Exception:  # noqa: BLE001
                if attempt == 5:
                    raise
                time.sleep(3 * (attempt + 1))
    return path


def ban_index():
    """{(addr_key, postcode): (insee, [parcel ids], lat, lon)} for Paris and the inner ring; also fills _communes {postcode: {insee}}."""
    if _ban:
        return _ban
    for d in ('75', '92', '93', '94'):
        p = _fetch_ban(f'https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/adresses-{d}.csv.gz', os.path.join(ROOT, 'data', 'cache', 'ban', f'adresses-{d}.csv.gz'))
        with gzip.open(p, 'rt', encoding='utf-8') as f:
            for r in csv.DictReader(f, delimiter=';'):
                if not r['numero']:
                    continue
                _communes.setdefault(r['code_postal'], set()).add(r['code_insee'])
                k = (addr_key(f"{r['numero']} {r['rep']} {r['nom_voie']}"), r['code_postal'])
                if k not in _ban or (r['cad_parcelles'] and not _ban[k][1]):
                    _ban[k] = (r['code_insee'], r['cad_parcelles'].split('|') if r['cad_parcelles'] else [], float(r['lat']), float(r['lon']))
    return _ban
