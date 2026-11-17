#!/usr/bin/env python3
"""Pick ONE office location per company and write data/startups.json.

Evidence (best first). We want where the team works, not the legal seat:
  1. Website address labelled as an office (contact / about / jobs page), not a domiciliation service     -> "office" via website
  2. LinkedIn office address with a street number, not a domiciliation service                           -> "office" via LinkedIn
  3. Website address with neutral context (footer)                                                        -> "office" via website
  4. Register (SIRENE): an open SECONDARY establishment in Paris / inner ring                             -> "office" via register
  5. Register head office or a website/LinkedIn address flagged as legal seat                             -> "seat"  (hidden by default)
Domiciliation addresses (60 rue François 1er, 229 rue Saint-Honoré…) never count as an office.

Also: keeps startups & scale-ups only (founded 2005+, < 10k staff, not funds/agencies/schools), assigns a sector and a stage,
and spreads companies that share one building (Station F!) so every pin can be tapped.
"""
import json, math, os, re, sys, hashlib
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locate as L  # noqa: E402

ROOT = L.ROOT
RAW = os.path.join(ROOT, 'data', 'raw')

ARR = {1: 'Louvre', 2: 'Bourse & Sentier', 3: 'Haut-Marais', 4: 'Marais & islands', 5: 'Latin Quarter', 6: 'Saint-Germain',
       7: 'Invalides', 8: 'Champs-Élysées', 9: 'Opéra & SoPi', 10: 'Canal Saint-Martin', 11: 'Oberkampf & Bastille', 12: 'Bercy & Nation',
       13: 'Paris Rive Gauche', 14: 'Montparnasse', 15: 'Vaugirard & Beaugrenelle', 16: 'Passy & Auteuil', 17: 'Batignolles',
       18: 'Montmartre', 19: 'Buttes-Chaumont & La Villette', 20: 'Belleville'}

CAMPUS = {
    '5 parvis alan turing': 'Station F', '5 rue eugene freyssinet': 'Station F', '55 boulevard vincent auriol': None,
    '10 rue d oradour sur glane': 'PariSanté Campus', '4 place jussieu': 'Sorbonne Université (Jussieu)',
    '189 rue d aubervilliers': 'CENTQUATRE / 104factory', '33 rue la fayette': 'WeWork La Fayette',
}

# ---------- sectors (filter + label; pins are coloured by stage) ----------
SECTOR_OF_QUERY = {
    'AI & Data': ['generative AI', 'AI agents', 'machine learning infrastructure', 'LLM and foundation models', 'computer vision', 'data analytics platform',
                  'audio and voice AI', 'translation and language AI', 'video AI'],
    'Fintech & Insurance': ['fintech', 'insurtech', 'crypto and web3', 'regtech and compliance software', 'wealthtech and investing app', 'payments infrastructure',
                            'insurance broker platform', 'banking as a service', 'accounting and finance software for SMEs'],
    'Health & Bio': ['healthtech', 'medtech and medical devices', 'biotech', 'digital health and telemedicine', 'mental health app', 'femtech',
                     'pharma drug discovery AI', 'silver economy and elderly care'],
    'Climate & Energy': ['climate tech', 'energy transition and renewables', 'carbon accounting software', 'circular economy and recycling', 'cleantech hardware',
                         'water tech', 'battery tech', 'hydrogen', 'impact and social economy'],
    'Software & SaaS': ['B2B SaaS', 'HR tech and recruiting software', 'sales and marketing software', 'legaltech', 'cybersecurity', 'developer tools',
                        'cloud infrastructure', 'no-code platform', 'productivity and collaboration software', 'adtech', 'martech', 'customer support software',
                        'procurement software', 'govtech and civic tech', 'legal and notary tech', 'API platform', 'open source software company',
                        'workplace and office management', 'freelance platform', 'industry 4.0 and manufacturing software', 'event tech', 'design software'],
    'Consumer & Commerce': ['e-commerce brand (DNVB)', 'marketplace', 'retail tech', 'fashion tech and resale', 'beauty and cosmetics brand', 'social app',
                            'dating app', 'kids and parenting', 'pet tech', 'quick commerce and delivery'],
    'Food & Agri': ['foodtech', 'agritech', 'restaurant tech'],
    'Mobility & Logistics': ['mobility and transportation', 'logistics and supply chain software', 'electric vehicles and micromobility'],
    'Deeptech & Hardware': ['deeptech', 'quantum computing', 'robotics', 'space tech', 'semiconductors and hardware', 'IoT', 'drones and defense tech',
                            'chemistry and materials science', '3D and AR/VR'],
    'Real Estate & Construction': ['proptech and real estate tech', 'construction tech', 'smart building'],
    'Media, Gaming & Sport': ['gaming and video games', 'media and creator economy', 'music tech', 'sports tech'],
    'Education & Travel': ['edtech', 'travel tech', 'hospitality tech'],
}
Q2S = {q: s for s, qs in SECTOR_OF_QUERY.items() for q in qs}
KW = [  # description fallback, first match wins
    ('Health & Bio', r'\b(health|santé|medical|médical|patient|clinic|biotech|pharma|therapy|thérap|hospital|hôpital|care\b|diagnos|genom|drug)'),
    ('Fintech & Insurance', r'\b(fintech|bank|banque|payment|paiement|insurance|assurance|crypto|blockchain|invest|credit|crédit|lending|wealth|épargne|neobank)'),
    ('Climate & Energy', r'\b(climate|climat|carbon|carbone|energy|énergie|solar|solaire|renewable|recycl|circular|circulaire|decarbon|décarbon|hydrogen|hydrogène|battery|batter)'),
    ('AI & Data', r'\b(artificial intelligence|intelligence artificielle|\bai\b|\bia\b|machine learning|llm|data platform|computer vision|generative)'),
    ('Food & Agri', r'\b(food|aliment|restaurant|agri|farm|cuisine|meal|repas|boisson|beverage)'),
    ('Mobility & Logistics', r'\b(mobility|mobilité|logisti|delivery|livraison|transport|fleet|flotte|vehicle|véhicule|bike|vélo|scooter|freight)'),
    ('Real Estate & Construction', r'\b(real estate|immobili|construction|building|bâtiment|property|propert|rent|location|housing|logement)'),
    ('Deeptech & Hardware', r'\b(robot|quantum|quantique|space|spatial|satellite|semiconductor|chip|hardware|drone|defense|défense|sensor|capteur|photonic|laser)'),
    ('Education & Travel', r'\b(education|éducation|learning|formation|school|école|student|étudiant|travel|voyage|hotel|hôtel|tourism|tourisme)'),
    ('Media, Gaming & Sport', r'\b(game|gaming|jeu|media|média|music|musique|sport|creator|créateur|video|vidéo|podcast|film)'),
    ('Consumer & Commerce', r'\b(e-commerce|ecommerce|brand|marque|fashion|mode|beauty|beauté|cosmetic|cosmét|marketplace|shop|retail|consumer|app for)'),
    ('Software & SaaS', r'\b(saas|software|logiciel|platform|plateforme|api|b2b|automation|automatis|crm|erp|hr\b|rh\b|recruit|recrut|legal|juridique|security|sécurité|cyber)'),
]
# What the company says it IS (first sentences only), not who it sells to: "an AI assistant for law firms" stays in.
NOT_STARTUP = re.compile(r"\b(venture capital (firm|fund)|vc firm|investment fund|private equity|fonds d'investissement|"
                         r"business school|école de commerce|grande école|university|université|faculté|consulting firm|cabinet de conseil|conseil en|"
                         r"digital agency|agence (web|digitale|de communication|marketing|créative|de design)|creative agency|marketing agency|web agency|"
                         r"design studio|studio de design|accounting firm|cabinet d'expertise|non-profit|nonprofit|loi 1901|think tank|ministry|ministère|"
                         r"public (agency|body|institution)|établissement public)", re.I)
NOT_STARTUP_WEAK = re.compile(r"\b(société de gestion|investment (firm|company)|asset manager|law firm|cabinet d'avocats|avocats?\b|family office|cowork|incubat(or|eur)|accelerat(or|eur)|expert[- ]comptable|"
                              r"\bong\b|\bngo\b|association|agence immobilière|real estate agency|recruitment agency|cabinet de recrutement)", re.I)
TECH = re.compile(r"\b(saas|software|logiciel|platform|plateforme|app\b|application|api\b|ai\b|ia\b|artificial intelligence|intelligence artificielle|"
                  r"automat|algorithm|digital solution|solution digitale|online|en ligne|marketplace|startup|start-up)", re.I)


TRADITIONAL = re.compile(r"\b(mutuelle|mutual insurer|maisons? de retraite|nursing homes?|hôpital|hospitalier|hospital group|aide à domicile|services à la personne|"
                         r"courtage|réseau d'agences|agences en france|entreprise publique|public company|filiale (du|de la) groupe|subsidiary of|department store|"
                         r"grand magasin|alliance of|higher education|enseignement supérieur|formation (gratuite|diplômante)|family-owned|maison de couture|"
                         r"cabinet d'audit|advisory firm|interim|intérim|travail temporaire|esn\b|it services company|société de services)", re.I)
BIGTECH = re.compile(r"(fintech|finance|néobanque|neobank|insur|tech|digital|numérique|data|cloud|mobile|web|internet|e-commerce|ecommerce|crypto|games?\b|SaaS|AI-|IA )", re.I)
NOT_A_COMPANY = {'station f', '42 paris', '42', 'hec paris', 'essec', 'schoolab'} | {
    L.fold(l) for l in open(os.path.join(ROOT, 'data', 'ref', 'exclude.txt')).read().splitlines() if l.strip() and not l.startswith('#')}


def not_startup(c):
    head = ((c.get('name') or '') + '. ' + (c.get('description') or ''))[:220]
    if L.fold(c.get('name')) in NOT_A_COMPANY or NOT_STARTUP.search(head) or TRADITIONAL.search((c.get('description') or '')[:400]):
        return True
    # big organisations only count as scale-ups when they are clearly tech companies
    if (c.get('employees') or 0) >= 500 and not (TECH.search((c.get('description') or '')[:600]) or BIGTECH.search((c.get('description') or '')[:600])):
        return True
    return bool(NOT_STARTUP_WEAK.search(head)) and not TECH.search((c.get('description') or '')[:600])


def clean_street(st):
    st = re.sub(r'\s+', ' ', st or '').strip()
    m = list(re.finditer(r"\b\d{1,4}(?:\s?(?:bis|ter|b))?\s+(?:rue|avenue|av|boulevard|bd|place|quai|cours|passage|impasse|all[ée]e|villa|cit[ée]|square|parvis|esplanade|galerie|faubourg|rond-point|cour|promenade|chauss[ée]e|port|route|chemin)\b.*", st, re.I))
    return m[-1].group(0) if m else st


SEEDNAME = {}  # curated names win over Exa's (e.g. mirakl.com is "Mirakl", not "Mirakl Target2Sell")


def sector(c):
    votes = {}
    for q in c.get('queries', []):
        for base in Q2S:
            if q.startswith(base + ' ') or q.startswith('Paris-based ' + base + ' ') or (' ' + base + ' ') in (' ' + q + ' '):
                s = Q2S[base]
                votes[s] = votes.get(s, 0) + 1
    if votes:
        return max(votes.items(), key=lambda kv: (kv[1], -list(SECTOR_OF_QUERY).index(kv[0])))[0]
    d = (c.get('description') or '').lower()
    for s, rx in KW:
        if re.search(rx, d):
            return s
    return 'Software & SaaS'


def stage(n):
    if not n:
        return 'unknown'  # no reliable headcount: say so rather than guess "early"
    if n < 50:
        return 'early'
    return 'growth' if n < 250 else 'scale'


def area(pc, city):
    m = re.match(r'^75(\d{3})$', str(pc or ''))
    if m:
        n = int(m.group(1))
        n = 16 if n == 116 else n
        if 1 <= n <= 20:
            return f"{n}{'er' if n == 1 else 'e'} · {ARR[n]}"
    return (city or '').title() or None


# ---------- geometry ----------
def pip(x, y, poly):
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]; x2, y2 = poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def station_f_slots(n):
    """n points on a grid inside the Station F footprint (OpenStreetMap), lat/lng."""
    with open(os.path.join(ROOT, 'data', 'ref', 'stationf.geojson')) as f:
        g = json.load(f)
    polys = [p[0] for p in g['coordinates']] if g['type'] == 'MultiPolygon' else [g['coordinates'][0]]
    xs = [p[0] for poly in polys for p in poly]; ys = [p[1] for poly in polys for p in poly]
    step = 0.00012
    while True:
        pts = []
        y = max(ys) - step / 2
        while y > min(ys):
            x = min(xs) + step / 2
            while x < max(xs):
                if any(pip(x, y, poly) for poly in polys):
                    pts.append((y, x))
                x += step * 1.5
            y -= step
        if len(pts) >= n or step < 0.00001:
            return pts[:n]
        step *= 0.85


def spread(group):
    """Phyllotaxis spiral around a shared address: ~8 m between neighbours."""
    lat0, lng0 = group[0]['ll']
    k = 8 / 111320
    for i, p in enumerate(group):
        if i == 0:
            continue
        r = k * math.sqrt(i) * 1.15
        a = i * 2.39996
        p['ll'] = [round(lat0 + r * math.sin(a), 6), round(lng0 + r * math.cos(a) / math.cos(math.radians(lat0)), 6)]


# ---------- main ----------
def main():
    with open(os.path.join(RAW, 'exa_companies.json')) as f:
        companies = json.load(f)
    try:
        with open(os.path.join(RAW, 'websites.json')) as f:
            web = json.load(f)
    except FileNotFoundError:
        web = {}

    # well-known names Exa under-ranks (data/seed/known.txt via scripts/seed.py), unless Exa already found them
    sp = os.path.join(RAW, 'seed_companies.json')
    if os.path.exists(sp):
        with open(sp) as f:
            seeds = json.load(f)
        have = {c['domain'] for c in companies} | {L.fold(c['name']) for c in companies}
        names = {L.fold(c['name']) for c in companies}
        SEEDNAME.update({s['domain']: s['name'] for s in seeds})
        add = [s for s in seeds if L.fold(s['name']) not in names]
        companies += add
        print(len(add), 'seed companies added')

    # researched office addresses (Exa Agent / manual checks): data/ref/office_overrides.json, matched by website or name
    ov = {}
    op = os.path.join(ROOT, 'data', 'ref', 'office_overrides.json')
    if os.path.exists(op):
        with open(op) as f:
            for o in json.load(f):
                ov[L.fold(o['name'])] = o
                if o.get('website'):
                    ov[o['website'].replace('https://', '').replace('www.', '').strip('/').lower()] = o
    def load_list(name):
        p_ = os.path.join(ROOT, 'data', 'ref', name)
        idx = {}
        if os.path.exists(p_):
            with open(p_) as f:
                for o in json.load(f):
                    w = (o.get('website') or o.get('w') or '').replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0].lower()
                    for k in {L.fold(o.get('name') or o.get('n')), w} - {''}:
                        idx.setdefault(k, []).append(o)
        return idx
    VER = load_list('verified.json')       # Exa Agent verification: LinkedIn headcount, is_startup, sourced office
    COR = load_list('corrections.json')    # manual corrections: always win

    def _get(idx, c):
        seen, out = set(), []
        for k in ((c.get('domain') or '').replace('www.', '').lower(), L.fold(c.get('name'))):
            for o in idx.get(k, []):
                if id(o) not in seen:
                    seen.add(id(o)); out.append(o)
        return out
    def verified(c):
        return _get(VER, c)
    def corrections(c):
        return _get(COR, c)

    def override(c):
        return ov.get((c.get('domain') or '').replace('www.', '').lower()) or ov.get(L.fold(c.get('name')))

    # keep startups & scale-ups
    kept = []
    drop = {'old': 0, 'big': 0, 'not_startup': 0}
    for c in companies:
        if c.get('seed') or override(c) or corrections(c):
            kept.append(c); continue
        if c.get('founded') and c['founded'] < 2005:
            drop['old'] += 1; continue
        if (c.get('employees') or 0) > 10000:
            drop['big'] += 1; continue
        if not_startup(c):
            drop['not_startup'] += 1; continue
        kept.append(c)
    print(len(companies), '->', len(kept), 'startups', drop)

    # geocode every address candidate in one BAN batch
    rows = []
    for i, c in enumerate(kept):
        num, street, pc = L.parse_exa(c.get('hq_address'))
        c['_li'] = (num, street, pc or c.get('hq_postcode'))
        if num:
            rows.append((f'li{i}', num, c['_li'][2] or ''))
        for j, w in enumerate(web.get(c['domain'], [])):
            rows.append((f'w{i}_{j}', w['addr'], w['postcode']))
    geo = L.ban_batch(rows)

    def good(g):
        return g and g[5] == 'housenumber' and L.in_bbox(g[0], g[1])

    # ---------- evidence: every address we hold for a company, weighted by how much it says about the OFFICE ----------
    # families: manual (your corrections), research (Exa Agent: careers/job posts/press/legal), website (own pages),
    # linkedin (company page HQ), cohort (Station F programme lists), register (SIRENE secondary establishment).
    LEGALISH = re.compile(r'legal|mentions|cgu|cgv|terms|privacy|conditions|pappers|societe\.com|infogreffe', re.I)
    SF = [L.STATION_F[0], L.STATION_F[1], L.STATION_F[2], '75013', 'Paris', 'housenumber', 1]

    def research_entries(c):
        out = []
        for o in [override(c)] + verified(c):
            if o and o.get('office_street'):
                out.append(o)
        return out

    rrows = []
    for i, c in enumerate(kept):
        for j, o in enumerate(research_entries(c) + corrections(c)):
            rrows.append((f'r{i}_{j}', o['office_street'], o.get('postcode') or ''))
    rgeo = L.ban_batch(rrows)

    sfp = os.path.join(ROOT, 'data', 'ref', 'stationf_residents.json')
    sfres = set()
    if os.path.exists(sfp):
        with open(sfp) as f:
            for r in json.load(f):
                sfres.add(L.fold(r['name']))
                sfres.add((r.get('website') or '').replace('https://', '').replace('http://', '').replace('www.', '').strip('/').lower())

    def evidence(i, c):
        ev = []  # (geo, family, weight, is_seat)
        dom = lambda g: L.addr_key(g[2]) in L.DOMICILIATION
        rs = research_entries(c)
        for j, o in enumerate(rs + corrections(c)):
            g = rgeo.get(f'r{i}_{j}')
            if not good(g):
                continue
            if j >= len(rs):
                ev.append((g, 'manual', 100, False)); continue
            st = (o.get('source_type') or '').lower()
            legal = st == 'legal' or LEGALISH.search(o.get('source_url') or '')
            ev.append((g, 'research', 1.5 if legal else 2 if st == 'linkedin' else 3, dom(g)))
        for j, w in enumerate(web.get(c['domain'], [])):
            g = geo.get(f'w{i}_{j}')
            if good(g):
                ev.append((g, 'website', 3 if w['score'] > 0 else 1.5, w['kind'] == 'seat' or dom(g)))
        addr = c.get('hq_address') or ''
        if re.search(r'station\s*f\b|parvis alan turing|eug[eè]ne freyssinet', addr, re.I):
            ev.append((SF, 'linkedin', 2, False))
        else:
            g = geo.get(f'li{i}')
            if good(g):
                ev.append((g, 'linkedin', 2, dom(g)))
        if L.fold(c['name']) in sfres or (c.get('domain') or '').replace('www.', '').lower() in sfres:
            ev.append((SF, 'cohort', 1, False))
        return ev

    def near(g, h):
        return abs(g[0] - h[0]) < 0.00036 and abs(g[1] - h[1]) < 0.00055 or L.addr_key(g[2]) == L.addr_key(h[2])

    def decide(ev):
        """-> (geo, confidence 'v'|'l'|'u', families) or None"""
        offices = [e for e in ev if not e[3]]
        if not offices:
            return None
        man = [e for e in offices if e[1] == 'manual']
        if man:
            return man[0][0], 'v', ['manual']
        best = None
        for e in offices:
            fam = {}
            for f in offices:
                if near(e[0], f[0]):
                    fam[f[1]] = max(fam.get(f[1], 0), f[2])
            score = sum(fam.values())
            top = max(fam.values())
            cand = (score, top, e)
            if not best or cand[:2] > best[:2]:
                best = (score, top, e, fam)
        score, top, e, fam = best
        # pick the strongest single piece of evidence inside the winning cluster for the exact point
        g = max((f for f in offices if near(e[0], f[0])), key=lambda f: f[2])[0]
        conf = 'v' if top >= 3 or (score >= 3.5 and len(fam) >= 2) else 'l' if score >= 1.5 else 'u'
        return g, conf, sorted(fam, key=lambda k: -fam[k])

    decisions = [decide(evidence(i, c)) for i, c in enumerate(kept)]

    outside = {i for i, c in enumerate(kept) if (override(c) or {}).get('in_paris_area') is False and not corrections(c)}
    # the research flag alone is too strict (it calls Hugging Face or Lucca "not a startup"): drop only when the company
    # also has no venture funding and no tech product in its own description
    notstart = {i for i, c in enumerate(kept) if any(v.get('is_startup') is False for v in verified(c)) and not corrections(c)
                and not c.get('seed') and not c.get('funding_total') and not TECH.search((c.get('description') or '')[:600])}
    print(len(outside), 'outside Paris ·', len(notstart), 'not startups (research)')

    # register lookup only where nothing better than "unconfirmed" was found
    need = [i for i, d in enumerate(decisions) if not d or d[1] == 'u']
    print(len(need), 'companies need the company register')
    with ThreadPoolExecutor(8) as ex:
        sir = dict(zip(need, ex.map(lambda i: L.sirene_match_all(kept[i], kept[i]['_li'][2]), need)))

    out, stats = [], {}
    for i, c in enumerate(kept):
        if i in outside or i in notstart:
            continue
        d = decisions[i]
        loc = None
        if d:
            g, conf, fams = d
            loc = {'ll': [g[0], g[1]], 'street': g[2], 'pc': g[3], 'city': g[4], 'via': fams[0], 'cf': conf, 'ev': fams}
        if not loc or loc['cf'] == 'u':
            pc0 = c['_li'][2]
            ets = [e for e in (sir.get(i) or []) if ((e['postcode'] == pc0) if pc0 else (e['postcode'] or '').startswith('75'))]
            sec = [e for e in ets if not e['siege'] and L.addr_key(e['street']) not in L.DOMICILIATION]
            seat = [e for e in ets if e['siege'] and L.addr_key(e['street']) not in L.DOMICILIATION]
            pick_ = sec[0] if sec else None
            if pick_ and loc and abs(pick_['lat'] - loc['ll'][0]) < 0.0004 and abs(pick_['lng'] - loc['ll'][1]) < 0.0006:
                loc['cf'] = 'l'; loc['ev'] = loc['ev'] + ['register']   # register corroborates the weak lead
            elif not loc and (pick_ or seat):
                e = pick_ or seat[0]
                loc = {'ll': [e['lat'], e['lng']], 'street': e['street'], 'pc': e['postcode'], 'city': e['city'], 'via': 'register', 'cf': 'u', 'ev': ['register']}
        key = (loc or {}).get('cf', 'none') + '/' + (loc or {}).get('via', '')
        stats[key] = stats.get(key, 0) + 1
        if not loc:
            continue
        ve = [v.get('linkedin_employees') for v in verified(c) if v.get('linkedin_employees')]
        emp = ve[0] if ve else c.get('employees')
        rd = c.get('last_round') or {}
        rec = {
            'n': SEEDNAME.get((c.get('domain') or '').replace('www.', ''), c['name']).strip(), 'u': c['url'], 'd': re.sub(r'\s+', ' ', (c.get('description') or '')).strip()[:260],
            'y': c.get('founded'), 'e': emp, 'f': c.get('funding_total'),
            'rd': ' · '.join(x for x in (rd.get('name'), (rd.get('date') or '')[:4]) if x) or None,
            's': sector(c), 'st': stage(emp), 'eh': 'linkedin' if ve else ('register' if c.get('seed') and emp else 'exa' if emp else None),
            'a': clean_street(loc['street']), 'pc': loc['pc'], 'area': area(loc['pc'], loc['city']),
            'll': [round(loc['ll'][0], 6), round(loc['ll'][1], 6)], 'k': 'office' if loc['cf'] in 'vl' else 'seat',
            'cf': loc['cf'], 'v': loc['via'], 'ev': loc['ev'],
        }
        cp = CAMPUS.get(L.addr_key(rec['a']))
        if cp:
            rec['cp'] = cp
        out.append({k: v for k, v in rec.items() if v not in (None, '')})
    print('locations:', dict(sorted(stats.items())))

    # de-duplicate: same brand name twice, or the same website twice (Exa + seed). Keep the better-located, bigger record.
    seen, uniq = set(), []
    def dom(r):
        return re.sub(r'^https?://(www\.)?', '', r.get('u') or '').split('/')[0].lower()
    for r in sorted(out, key=lambda r: ('vlu'.index(r['cf']), -(r.get('e') or 0))):
        keys = {'n:' + L.fold(r['n']), 'd:' + dom(r)} - {'d:'}
        if keys & seen:
            continue
        seen |= keys; uniq.append(r)
    out = uniq

    sf = [r for r in out if r.get('cp') == 'Station F']
    out.sort(key=lambda r: r['n'].lower())
    with open(os.path.join(ROOT, 'data', 'startups.json'), 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    from collections import Counter
    print(len(out), 'on file ·', Counter(r['k'] for r in out), '· Station F:', len(sf))
    print(Counter(r['s'] for r in out).most_common())
    print(Counter(r['st'] for r in out))


if __name__ == '__main__':
    main()
