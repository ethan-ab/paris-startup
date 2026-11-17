# Paris Startup Atlas

Every startup and scale-up in Paris on one map, pinned at the **office where the team works**, not the registered seat.
Same stack as [paris-city](https://github.com/ethan-ab/paris-city): vanilla JS + MapLibre GL + OpenFreeMap tiles, static site on Vercel.

## Pipeline

| Step | Script | What it does |
|---|---|---|
| 1. Discover | `scripts/discover.py` | ~360 Exa `category: company` searches (sectors × phrasings, Station F programs, VC portfolios, arrondissements, founding years). Keeps companies headquartered in Paris. Needs `EXA_API_KEY`. |
| 2. Read websites | `scripts/websites.py [--all]` | Fetches each company's homepage + contact / about / jobs / legal pages and extracts street addresses, scored by context ("bureaux", "office" vs "siège social", "RCS", "hébergeur"). |
| 3. Resolve | `scripts/resolve.py` | Picks one office per company: website office → LinkedIn office → SIRENE secondary establishment. Domiciliation addresses (60 rue François 1er, 229 rue Saint-Honoré…) never count as an office. Filters to startups (founded 2005+, not funds/agencies/schools), assigns sector + stage, spreads pins inside shared buildings (Station F uses its OpenStreetMap footprint). |
| 4. Build | `scripts/build.py [--artifact]` | Writes `public/` (Vercel) and a self-contained `artifact/` page with an offline basemap. |

`scripts/locate.py` holds the shared helpers (address parsing, BAN batch geocoding, SIRENE matching, domiciliation list).
All network calls are cached in `data/cache/` (git-ignored); `data/startups.json` is the published dataset.

```bash
EXA_API_KEY=... python3 scripts/discover.py
python3 scripts/websites.py --all
python3 scripts/resolve.py
python3 scripts/build.py --artifact
```

## Data fields (`data/startups.json`)
`n` name · `u` website · `d` description · `y` founded · `e` team size · `f` total raised (USD) · `rd` last round · `s` sector · `st` stage
(`early` <50, `growth` 50–249, `scale` 250+) · `a` street · `pc` postcode · `area` · `ll` [lat, lng] · `k` `office` or `seat` (registered address only)
· `v` address source (`website`, `linkedin`, `register`) · `cp` campus (Station F, PariSanté Campus…)
