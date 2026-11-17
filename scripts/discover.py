#!/usr/bin/env python3
"""Discover Paris startups & scale-ups with Exa's company search.

Runs many queries (sectors x phrasings, programs, campuses, arrondissements, founding years) against
Exa `category: company`, keeps every result whose headquarters is in Paris / inner ring, and writes
data/raw/exa_companies.json (one record per company domain). Every query response is cached in
data/cache/exa/ so reruns are free.

Usage: EXA_API_KEY=... python3 scripts/discover.py [--max-queries N]
"""
import json, os, re, sys, hashlib, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'data', 'cache', 'exa')
OUT = os.path.join(ROOT, 'data', 'raw', 'exa_companies.json')
KEY = os.environ.get('EXA_API_KEY')

SECTORS = [
    'fintech', 'insurtech', 'crypto and web3', 'regtech and compliance software', 'wealthtech and investing app', 'payments infrastructure',
    'generative AI', 'AI agents', 'machine learning infrastructure', 'LLM and foundation models', 'computer vision', 'data analytics platform',
    'B2B SaaS', 'HR tech and recruiting software', 'sales and marketing software', 'legaltech', 'proptech and real estate tech',
    'construction tech', 'cybersecurity', 'developer tools', 'cloud infrastructure', 'no-code platform', 'productivity and collaboration software',
    'healthtech', 'medtech and medical devices', 'biotech', 'digital health and telemedicine', 'mental health app', 'femtech', 'pharma drug discovery AI',
    'climate tech', 'energy transition and renewables', 'carbon accounting software', 'circular economy and recycling', 'cleantech hardware',
    'foodtech', 'agritech', 'e-commerce brand (DNVB)', 'marketplace', 'retail tech', 'fashion tech and resale', 'beauty and cosmetics brand',
    'edtech', 'travel tech', 'hospitality tech', 'mobility and transportation', 'logistics and supply chain software', 'electric vehicles and micromobility',
    'deeptech', 'quantum computing', 'robotics', 'space tech', 'semiconductors and hardware', 'IoT', 'drones and defense tech',
    'gaming and video games', 'media and creator economy', 'music tech', 'sports tech', 'social app', 'dating app',
    'adtech', 'martech', 'customer support software', 'accounting and finance software for SMEs', 'procurement software',
    'govtech and civic tech', 'impact and social economy', 'silver economy and elderly care', 'kids and parenting', 'pet tech',
    'legal and notary tech', 'insurance broker platform', 'banking as a service', 'API platform', 'open source software company',
    'quick commerce and delivery', 'restaurant tech', 'event tech', 'workplace and office management', 'freelance platform',
    'industry 4.0 and manufacturing software', 'chemistry and materials science', 'water tech', 'battery tech', 'hydrogen',
    'audio and voice AI', 'translation and language AI', 'video AI', 'design software', '3D and AR/VR', 'smart building',
]
PHRASES = [
    '{s} startup headquartered in Paris, France',
    'Paris-based {s} scale-up',
    '{s} company founded in Paris that raised venture capital',
]
EXTRA = [
    'startup based at Station F, 5 Parvis Alan Turing, Paris', 'Station F Founders Program startup', 'HEC Paris incubator startup at Station F',
    'Station F Fighters program startup', 'ESSEC Ventures startup Paris', 'Schoolab Paris startup', 'Agoranov incubated startup Paris',
    'Paris&Co incubated startup', 'Wilco accelerator Paris startup', 'Y Combinator startup founded in Paris', 'French Tech Next40 company',
    'French Tech 120 company Paris', 'French Tech 2030 deeptech startup Paris', 'unicorn startup headquartered in Paris', 'Paris scale-up with more than 200 employees',
    'Series A startup Paris', 'Series B startup Paris', 'seed-stage startup Paris', 'pre-seed startup Paris', 'bootstrapped SaaS company Paris',
    'PariSanté Campus startup', 'Sorbonne University startup', 'PSL Research University spin-off startup', 'Institut Pasteur spin-off startup',
    'Ecole Polytechnique Drahi X-Novation startup Paris', 'Hi! PARIS AI startup', 'Le Cargo Paris incubator startup', 'Willa incubator Paris women founders',
    'Kima Ventures portfolio startup Paris', 'Partech portfolio startup Paris', 'Elaia portfolio startup Paris', 'Frst VC portfolio startup Paris',
    'Alven portfolio startup Paris', 'Eurazeo growth portfolio startup Paris', 'ISAI portfolio startup Paris', 'Serena Capital portfolio startup Paris',
    'Daphni portfolio startup Paris', 'Point Nine portfolio startup Paris', 'Bpifrance backed startup Paris', 'Seedcamp portfolio startup Paris',
    'startup with offices in Le Marais Paris', 'startup with offices in Le Sentier Paris', 'startup office near Opéra Paris',
    'startup office in Paris 13e near Bibliothèque François Mitterrand', 'startup office in Paris 10e Canal Saint-Martin',
    'startup office in Paris 11e Oberkampf', 'startup office in Paris 8e', 'startup office in Paris 9e', 'startup office in Paris 2e',
    'startup office in Paris 17e Batignolles', 'startup office in Paris 18e', 'startup office in Paris 15e', 'startup office in Paris 12e',
    'startup office in Paris 19e', 'startup office in Paris 20e', 'startup office in Paris 3e', 'startup office in Paris 14e', 'startup office in Paris 16e',
    'startup office in Paris 1er', 'startup office in Paris 4e', 'startup office in Paris 5e', 'startup office in Paris 6e', 'startup office in Paris 7e',
] + [f'Paris startup founded in {y}' for y in range(2012, 2027)]


def queries():
    qs = [p.format(s=s) for s in SECTORS for p in PHRASES] + EXTRA
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q); out.append(q)
    return out


def search(q):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(q.encode()).hexdigest()[:16] + '.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    body = json.dumps({'query': q, 'category': 'company', 'numResults': 100}).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request('https://api.exa.ai/search', data=body, headers={'x-api-key': KEY, 'content-type': 'application/json'})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            data['_q'] = q
            with open(path, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
            return data
        except Exception as e:  # noqa: BLE001
            print('  retry', attempt + 1, q, e, file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return {'results': [], '_q': q}


PARIS_RE = re.compile(r'\bparis\b', re.I)


def domain(url):
    m = re.match(r'https?://(?:www\.)?([^/]+)', url or '')
    return m.group(1).lower() if m else url


def main():
    qs = queries()
    if '--max-queries' in sys.argv:
        qs = qs[:int(sys.argv[sys.argv.index('--max-queries') + 1])]
    print(len(qs), 'queries')
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(search, qs))
    cost, companies = 0.0, {}
    for data in results:
        cost += ((data.get('costDollars') or {}).get('total') or 0)
        for r in data.get('results', []):
            ents = r.get('entities') or []
            e = ents[0].get('properties', {}) if ents else {}
            hq = e.get('headquarters') or {}
            city = (hq.get('city') or '')
            if not PARIS_RE.search(city + ' ' + (hq.get('address') or '')):
                continue
            d = domain(r.get('url'))
            rec = companies.get(d)
            if rec:
                rec['queries'].append(data['_q'])
                continue
            fin = e.get('financials') or {}
            companies[d] = {
                'name': e.get('name') or r.get('title'), 'url': r.get('url'), 'domain': d, 'exa_id': r.get('id'),
                'description': e.get('description'), 'founded': e.get('foundedYear'),
                'employees': (e.get('workforce') or {}).get('total'),
                'hq_address': hq.get('address'), 'hq_city': hq.get('city'), 'hq_postcode': hq.get('postalCode'),
                'funding_total': fin.get('fundingTotal'), 'last_round': fin.get('fundingLatestRound'),
                'traffic': (e.get('webTraffic') or {}).get('visitsMonthly'), 'logo': r.get('image'),
                'queries': [data['_q']],
            }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(list(companies.values()), f, ensure_ascii=False, indent=0)
    print(f'{len(companies)} unique Paris companies · new spend this run ≈ ${cost:.2f} (cached queries report their original cost)')


if __name__ == '__main__':
    main()
