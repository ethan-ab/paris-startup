#!/usr/bin/env python3
"""Merge an Exa Agent verification result (saved JSON) into data/ref/verified.json. Usage: ingest_verify.py <result.json> [...]"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(ROOT, 'data', 'ref', 'verified.json')
cur = json.load(open(p)) if os.path.exists(p) else []
have = {(r['n'], r['w']) for r in cur}
for f in sys.argv[1:]:
    d = json.load(open(f))
    rows = d['output']['structured']['rows']
    new = [r for r in rows if (r['n'], r['w']) not in have]
    cur += new; have |= {(r['n'], r['w']) for r in new}
    print(f"{os.path.basename(f)}: {len(rows)} rows · {sum(1 for r in rows if r.get('linkedin_employees'))} headcounts · "
          f"{sum(1 for r in rows if r.get('office_street'))} offices · {sum(1 for r in rows if r.get('is_startup') is False)} not startups · ${d['costDollars']['total']}")
json.dump(cur, open(p, 'w'), ensure_ascii=False, indent=0)
print(len(cur), 'verified rows total')
