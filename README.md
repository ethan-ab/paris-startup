# Paris Startup Atlas

Every startup and scale-up in Paris on one map, pinned at the **office where the team works**, not the registered seat.
Same stack as [paris-city](https://github.com/ethan-ab/paris-city): vanilla JS + MapLibre GL + OpenFreeMap tiles, static site on Vercel.

## Pipeline

| Step | Script | What it does |
|---|---|---|
| 1. Discover | `scripts/discover.py` | ~360 Exa `category: company` searches (sectors × phrasings, Station F programs, VC portfolios, arrondissements, founding years). Keeps companies headquartered in Paris. Needs `EXA_API_KEY`. |
| 2. Read websites | `scripts/websites.py [--all]` | Fetches each company's homepage + contact / about / jobs / legal pages and extracts street addresses, scored by context ("bureaux", "office" vs "siège social", "RCS", "hébergeur"). |
| 2b. Search offices | `scripts/exa_offices.py [--unlocated]` | One Exa web search per startup (via treg.to, $0.007 each) for pages that state where the team works — careers pages, job posts, press. Addresses are scored by context; legal notices, T&Cs and company registries (registered seats) are ignored. Writes `data/ref/exa_offices.json`, read by `resolve.py` as research evidence. `--unlocated` searches the companies that `resolve.py` could not place at all. Needs `TREG_TOKEN`. |
| 2c. Register offices | `scripts/register_offices.py [--all]` | Free (no key, no credits): asks the French company register (SIRENE, `recherche-entreprises.api.gouv.fr`) for open secondary establishments that differ from a startup's current pin. Writes `data/ref/register_offices.json`, read by `resolve.py` as `register` evidence (weight 2, above a legal-notice seat). |
| 3. Resolve | `scripts/resolve.py` | Picks one office per company: website office → LinkedIn office → SIRENE secondary establishment. Domiciliation addresses (60 rue François 1er, 229 rue Saint-Honoré…) never count as an office. Filters to startups (founded 2005+, not funds/agencies/schools), assigns sector + stage, spreads pins inside shared buildings (Station F uses its OpenStreetMap footprint). |
| 3b. Buildings | `scripts/buildings.py [--context]` | Finds the building each startup sits in and writes `data/buildings.json` (rerun after every `resolve.py`, the footprints index `startups.json`). Free open data: the address's cadastral parcel from BAN (`cad_parcelles`) and the parcel polygon from cadastre.data.gouv.fr; the building is the basemap footprint covering most of the parcel. Falls back to the parcel under BAN's point or the pin, then to the nearest large building within 25 m. |
| 4. Logos | `scripts/logos.py [--retry]` | Self-hosts each startup's logo in `public/logos/` (96 px WebP): the LinkedIn logo Exa recorded, else the company website's icon or header logo (SVGs rendered with Playwright). Placeholders and generic CMS icons are dropped; the card shows a monogram instead. Index in `data/logos.json`. |
| + Recent rounds | `scripts/rounds.py merge <files>` | Exa's round data lags, so rounds announced since mid-2026 are researched from the press (Maddyness, Les Pépites Tech, French Tech Journal…) into `data/ref/rounds.json`, one sourced row each. `build.py` overlays them by website domain: newest round in `rd` (with month), its source in `rs`, amount added to `f` unless Exa may already count it. |
| 5. Build | `scripts/build.py [--artifact]` | Writes `public/` (Vercel) and a self-contained `artifact/` page with an offline basemap. |

`scripts/locate.py` holds the shared helpers (address parsing, BAN batch geocoding, SIRENE matching, domiciliation list).
All network calls are cached in `data/cache/` (git-ignored); `data/startups.json` is the published dataset.

```bash
EXA_API_KEY=... python3 scripts/discover.py
python3 scripts/websites.py --all
TREG_TOKEN=... python3 scripts/exa_offices.py --budget 25
python3 scripts/resolve.py
python3 scripts/logos.py
python3 scripts/build.py --artifact
```

## Add a startup (in the page)
The **+ Add a startup** button opens a form (name, office address with French address-base autocomplete or a pin dropped on the map, sector,
team size → stage, founding year, website, one-liner). The startup lights up its building immediately: it joins a building that already hosts
startups, otherwise the map reads the real footprint of the building under the address from the basemap, otherwise a small block is drawn.
Added startups are saved in the visitor's browser (`localStorage`, key `psa.added.v1`) and can be removed; **Send for review** opens a
pre-filled GitHub issue (label `new-startup`) so it can be checked and added to `data/startups.json`.

## Data fields (`data/startups.json`)
`n` name · `u` website · `d` description · `y` founded · `e` team size · `f` total raised (USD) · `rd` last round · `s` sector · `st` stage
(`early` <50, `growth` 50–249, `scale` 250+) · `a` street · `pc` postcode · `area` · `ll` [lat, lng] · `k` `office` or `seat` (registered address only)
· `v` address source (`website`, `linkedin`, `register`) · `cp` campus (Station F, PariSanté Campus…)
· `rs` source of the last round (press-refreshed rounds) · `lg` logo file key in `public/logos/` (added at build time from `data/logos.json`)
