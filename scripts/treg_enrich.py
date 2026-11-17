#!/usr/bin/env python3
"""LinkedIn headcount + LinkedIn office locations for every company, through treg (treg.to).

Step 1  treg.companies.enrich      {domain}        -> linkedin_url (+ a headcount estimate)     ~$0.001
Step 2  treg.linkedin.company.profile {linkedin_url} -> employees_in_linkedin, locations[]      ~$0.001
Every response is cached in data/cache/treg/<domain>.json, so reruns cost nothing.
Spend is capped per call (X-Treg-Route-Max-Cost) and per run (--budget, USD).
Output: data/ref/linkedin.json  [{domain, linkedin_url, li_employees, locations, enrich_employees}]
Needs TREG_TOKEN.  Usage: python3 scripts/treg_enrich.py [--budget 15] [--limit N]
"""
import json, os, re, sys, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'data', 'cache', 'treg')
TOKEN = os.environ.get('TREG_TOKEN')
BUDGET = float(sys.argv[sys.argv.index('--budget') + 1]) if '--budget' in sys.argv else 15.0
spent = [0.0]
HAS_NUM = re.compile(r'\d+,? ')
lock = threading.Lock()


def call(endpoint, body):
    with lock:
        if spent[0] >= BUDGET:
            return None, 'budget'
    req = urllib.request.Request(f'https://treg.to/call/{endpoint}', data=json.dumps(body).encode(), method='POST',
                                 headers={'x-treg-token': TOKEN, 'content-type': 'application/json', 'X-Treg-Route-Max-Cost': '0.01'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                cost = int(r.headers.get('x-treg-cost-micro') or 0) / 1e6
                data = json.load(r)
            with lock:
                spent[0] += cost
            return data, r.headers.get('x-treg-route-outcome')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get('Retry-After') or 5)); continue
            if e.code in (401, 402, 403):
                return None, f'http_{e.code}'
            if e.code >= 500 and attempt < 1:
                time.sleep(3); continue
            return None, f'http_{e.code}'
        except Exception:  # noqa: BLE001
            time.sleep(3)
    return None, 'error'


def li_handle(url):
    m = re.search(r'linkedin\.com/company/([^/?#]+)', url or '')
    return m.group(1) if m else None


def work(dom):
    p = os.path.join(CACHE, re.sub(r'[^a-z0-9.-]', '_', dom) + '.json')
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    rec = {'domain': dom}
    d, out = call('treg.companies.enrich', {'domain': dom})
    if d is None and out in ('budget', 'http_401', 'http_402', 'http_403'):
        return {'domain': dom, '_stop': out}
    o = (d or {}).get('output') or {}
    o = o if isinstance(o, dict) else {}
    rec['enrich_employees'] = o.get('employees')
    raw1 = ((d or {}).get('raw') or {}).get('data') or {}
    rec['linkedin_url'] = o.get('linkedin_url') or (raw1.get('socials') or {}).get('linkedin') if isinstance(raw1, dict) else o.get('linkedin_url')
    h = li_handle(rec['linkedin_url'])
    if h:
        d2, out2 = call('treg.linkedin.company.profile', {'linkedin_url': f'https://www.linkedin.com/company/{h}'})
        if d2 is None and out2 in ('budget', 'http_401', 'http_402', 'http_403'):
            return {'domain': dom, '_stop': out2}
        raw = ((d2 or {}).get('raw') or {}).get('data') or {}
        raw = raw if isinstance(raw, dict) else {}
        o2 = (d2 or {}).get('output') or {}
        o2 = o2 if isinstance(o2, dict) else {}
        rec['li_employees'] = raw.get('employees_in_linkedin') or raw.get('employee_count') or (o2.get('employees') if isinstance(o2.get('employees'), int) else None)
        rec['li_size_band'] = raw.get('company_size') or (o2.get('employees') if isinstance(o2.get('employees'), str) else None)
        locs = raw.get('locations') or o2.get('locations') or []
        rec['locations'] = [l if isinstance(l, str) else ', '.join(str(v) for v in l.values() if v) for l in locs]
        rec['li_website'] = raw.get('website')
    os.makedirs(CACHE, exist_ok=True)
    with open(p, 'w') as f:
        json.dump(rec, f, ensure_ascii=False)
    return rec


def main():
    if not TOKEN:
        sys.exit('TREG_TOKEN missing')
    with open(os.path.join(ROOT, 'data', 'startups.json')) as f:
        rows = json.load(f)
    doms = []
    for r in sorted(rows, key=lambda r: -(r.get('e') or 0)):
        d = re.sub(r'^https?://(www\.)?', '', r.get('u') or '').split('/')[0].lower()
        if d and 'linkedin.com' not in d and d not in doms:
            doms.append(d)
    if '--limit' in sys.argv:
        doms = doms[:int(sys.argv[sys.argv.index('--limit') + 1])]
    print(len(doms), 'domains · budget $', BUDGET, flush=True)
    out = []
    with ThreadPoolExecutor(32) as ex:
        for k, rec in enumerate(ex.map(work, doms)):
            if rec.get('_stop'):
                continue
            out.append(rec)
            if k % 250 == 0:
                print(f'  {k}/{len(doms)} · spent ${spent[0]:.3f}', flush=True)
    with open(os.path.join(ROOT, 'data', 'ref', 'linkedin.json'), 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print(f'done: {len(out)} companies · {sum(1 for r in out if r.get("li_employees"))} LinkedIn headcounts · '
          f'{sum(1 for r in out if any(HAS_NUM.search(l) for l in r.get("locations") or []))} with a street on LinkedIn · spent ${spent[0]:.3f}')


if __name__ == '__main__':
    main()
