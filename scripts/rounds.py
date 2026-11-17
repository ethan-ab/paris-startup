#!/usr/bin/env python3
"""Recent funding rounds, overlaid on data/startups.json at build time.

Exa's company records lag (its round data thins out after mid-2026 and misses some later rounds), so rounds announced
since then are collected from the press into data/ref/rounds.json, one sourced row per round:
  {"n", "domain", "round", "amount", "currency", "date": "YYYY-MM-DD", "src"}
A startup matches a round by website domain (or, failing that, by exact folded name). For each startup the newest
round wins: `rd` becomes "Series B · Oct 2026", `rs` its source URL, and `f` (total raised, USD) grows by the round's
amount unless the dataset may already count it (it already lists a round that year, and
the new one is not clearly a later stage).

Usage:
  python3 scripts/rounds.py merge a.json b.json ...   add researched rounds to data/ref/rounds.json (dedup) and report matches
  (build.py calls apply(rows))
"""
import json, os, re, sys, unicodedata, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'data', 'ref', 'rounds.json')
USD = {'USD': 1.0, 'EUR': 1.13, 'GBP': 1.32, 'CHF': 1.2}  # approximate 2026 rates, to keep `f` in one currency
MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
LABELS = {'pre seed': 'Pre-seed', 'post ipo equity': 'Post-IPO equity', 'post ipo debt': 'Post-IPO debt', 'initial coin offering': 'ICO',
          'unspecified': 'Undisclosed round', 'non equity assistance': 'Non-equity support'}


def fold(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    return re.sub(r'[^a-z0-9]+', '', ''.join(c for c in s if unicodedata.category(c) != 'Mn'))


def host(u):
    try:
        return (urllib.parse.urlparse(u if '//' in (u or '') else '//' + (u or '')).hostname or '').lower().removeprefix('www.') or None
    except ValueError:
        return None


def label(name):
    """'Series a' -> 'Series A', 'Pre seed' -> 'Pre-seed'."""
    n = (name or '').strip()
    low = n.lower()
    if low in LABELS:
        return LABELS[low]
    m = re.match(r'series\s+([a-z])\b(.*)', low)
    if m:
        return f'Series {m.group(1).upper()}{m.group(2)}'
    return n[:1].upper() + n[1:]


def stage(name):
    """Pre-seed 0, Seed 1, Series A 2, Series B 3 ...; None when the label says nothing about stage."""
    low = (name or '').lower()
    if 'pre' in low and 'seed' in low:
        return 0
    if 'seed' in low:
        return 1
    m = re.match(r'series\s+([a-z])\b', low)
    return 2 + ord(m.group(1)) - ord('a') if m else None


def tidy_rd(rd):
    if not rd:
        return rd
    head, *rest = rd.split(' · ')
    return ' · '.join([label(head), *rest])


def load():
    return json.load(open(PATH)) if os.path.exists(PATH) else []


def index(rows):
    by_dom, by_name = {}, {}
    for r in rows:
        d = host(r.get('u'))
        if d:
            by_dom.setdefault(d, r)
        by_name.setdefault(fold(r['n']), r)
    return by_dom, by_name


def match(rd, by_dom, by_name):
    d = host(rd.get('domain'))
    return (by_dom.get(d) if d else None) or by_name.get(fold(rd.get('n')))


def apply(rows):
    for r in rows:
        r['rd'] = tidy_rd(r.get('rd'))
    by_dom, by_name = index(rows)
    latest = {}
    for rd in sorted(load(), key=lambda x: x['date']):
        r = match(rd, by_dom, by_name)
        if r is not None:
            latest.setdefault(id(r), [r, []])[1].append(rd)
    n = 0
    for r, rds in latest.values():
        for rd in rds:  # oldest first, so `f` accumulates every new round and `rd` ends on the newest
            y, mo = rd['date'][:4], int(rd['date'][5:7])
            name = label(rd['round'])
            old = (r.get('rd') or '').split(' · ')
            # Exa may already hold this round (its rd only keeps the year): in the same year, only a clearly later
            # stage (Series B after Series A) counts as new money; anything ambiguous is not added twice.
            counted = old[-1].endswith(y) and not (stage(name) is not None and stage(old[0]) is not None and stage(name) > stage(old[0]))
            amt = rd.get('amount')
            if amt and not counted:
                r['f'] = round((r.get('f') or 0) + float(amt) * USD.get((rd.get('currency') or 'USD').upper(), 1.0))
            r['rd'] = f'{name} · {MONTHS[mo - 1]} {y}'
            r['rs'] = rd['src']
        n += 1
    print(f'rounds: {n} startups updated from {len(load())} recent rounds')
    return rows


def dedup(rows):
    """Weekly recaps report a round again a week or two later: same company + same round type within 60 days is one round
    (the first report is kept)."""
    import datetime
    day = lambda x: datetime.date.fromisoformat(x['date'])
    kept = []
    for x in sorted(rows, key=lambda x: x['date']):
        who = host(x.get('domain')) or fold(x['n'])
        if any((host(k.get('domain')) or fold(k['n'])) in (who,) or fold(k['n']) == fold(x['n'])
               for k in kept if label(k['round']).lower() == label(x['round']).lower() and (day(x) - day(k)).days <= 60):
            continue
        kept.append(x)
    return kept


def merge(files):
    have = load()
    seen = {(host(x.get('domain')) or fold(x['n']), x['round'].lower(), x['date'][:7]) for x in have}
    added = 0
    for f in files:
        for x in json.load(open(f)):
            if not (x.get('n') and x.get('round') and re.match(r'\d{4}-\d{2}-\d{2}$', x.get('date') or '') and x.get('src')):
                continue
            if re.search(r'debt|grant|loan|subvention', x['round'], re.I):
                continue
            x = {k: x.get(k) for k in ('n', 'domain', 'round', 'amount', 'currency', 'date', 'src')}
            x['domain'] = host(x['domain'])
            k = (x['domain'] or fold(x['n']), x['round'].lower(), x['date'][:7])
            if k in seen:
                continue
            seen.add(k)
            have.append(x)
            added += 1
    have.sort(key=lambda x: (x['date'], x['n'].lower()))
    have = dedup(have)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, 'w') as f:
        json.dump(have, f, ensure_ascii=False, indent=0)
    rows = json.load(open(os.path.join(ROOT, 'data', 'startups.json')))
    by_dom, by_name = index(rows)
    hits = [x for x in have if match(x, by_dom, by_name)]
    print(f'{added} rounds added, {len(have)} total, {len(hits)} match a startup on the map')


if __name__ == '__main__':
    if sys.argv[1:2] == ['merge']:
        merge(sys.argv[2:])
