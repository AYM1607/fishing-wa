# fishing-wa

Interactive map of Washington State fishing lakes. **Explore** lakes by species and
surface **overpopulated** ("overabundant") lakes for a species; switch to **Recent stocks**
to see which lakes were stocked most recently (color-graded by recency); and **search**
lakes by name. Data is crawled from [WDFW](https://wdfw.wa.gov/fishing/locations) plus the
[WDFW Fish Plants](https://data.wa.gov/dataset/WDFW-Fish-Plants/6fex-3r7d) open dataset.

Two parts:

1. **Crawler** (`crawler/`, Python via uv) — scrapes the WDFW lowland-lakes, high-lakes,
   and high-lakes/overabundant listings into a SQLite database, captures each lake's
   `geo_code` from its detail page, pulls recent stocking from the WDFW Fish Plants SODA
   API (joined by `geo_code`), then exports static JSON.
2. **Web** (`web/`, Vite + TypeScript + Leaflet) — a fully static map that loads the
   exported JSON. No backend.

## Toolchain

Everything runs inside a reproducible Nix dev shell (`uv`, `nodejs`, `pnpm`, `sqlite`):

```sh
nix develop
```

## 1. Crawl + export

```sh
cd crawler
uv run crawl.py            # WDFW -> data/wdfw.db  (re-runnable; HTTP responses are cached)
uv run export_geojson.py   # wdfw.db -> ../web/public/data/{locations.geojson,species.json}
```

The crawler is polite (descriptive User-Agent, 0.5s delay on real requests) and idempotent.
HTTP responses are cached in `crawler/data/http_cache.sqlite`, so re-runs and resumes are
fast and don't re-hit WDFW. Delete that file to force a fresh crawl.

Inspect the database:

```sh
sqlite3 crawler/data/wdfw.db 'SELECT category, COUNT(*) FROM waterbody GROUP BY category;'
```

### Data model (SQLite)

- `waterbody(id, name, category, county, acres, elevation, lat, lng, url, geo_code)` — `id`
  is the WDFW URL slug (e.g. `high-lakes/airplane`); `geo_code` is WDFW's lake id, used to
  join stocking.
- `species(id, label)` — `id` is WDFW's numeric species id.
- `waterbody_species(waterbody_id, species_id)` — which species are present in each lake.
- `overabundant(waterbody_id, species_id)` — lake is overpopulated for that species.
- `stocking(waterbody_id, stock_date, species, number_released, total_pounds, facility)` —
  recent plants from the WDFW Fish Plants dataset (last `STOCKING_MONTHS`, default 6).

#### Geo codes (`crawler/geocodes.json`)

Stocking has no coordinates, only a `geo_code`, so we join it to lakes by `geo_code` — which
lives on each lake's detail page. Capturing it means one fetch per lake (~1,700) the first
time. The resulting `lake_id → geo_code` map is committed as `crawler/geocodes.json`; the
crawler seeds from it and only fetches detail pages for lakes it doesn't yet know, so repeat
runs (including weekly CI) fetch ~0 detail pages. Stocking itself is one SODA API call.

## 2. Run the map

```sh
cd web
pnpm install
pnpm dev        # dev server, opens the map
pnpm build      # static production build -> web/dist/ (deploy anywhere)
```

The map has two tabs and a name search:

- **Explore** — cluster all lakes, filter by species and water type; once a species is
  selected, a toggle highlights overpopulated lakes (red markers + a density heat layer).
- **Recent stocks** — shows only recently-stocked lakes, marker color graded by recency
  (≤7d / ≤30d / ≤90d / older), with a sortable newest-first list; clicking a list row flies
  to the lake.
- **Search** — type a lake name for an autocomplete dropdown; selecting flies to the lake
  and opens its popup. Works from either tab.

## Refreshing data

Full refresh — re-run the crawl + export and rebuild. The pipeline is idempotent; cached
HTTP keeps it cheap locally. Set `CRAWL_DELAY_S` to change the per-request delay (default
0.5s) and `STOCKING_MONTHS` to change the recency window (default 6).

Stocking-only refresh — `uv run refresh_stocking.py` updates just the stocking fields of
`locations.geojson` + `stocks.json` from the committed `geocodes.json`, with one SODA API
call and no wdfw.gov load. Use this for frequent (e.g. daily) updates.

> **Note on the species filter:** WDFW's listing views silently ignore a species filter
> when the species id isn't in that listing's own dropdown and return the *entire* list.
> The crawler therefore queries each listing only with its own dropdown species and also
> skips any response whose row count equals the unfiltered total. Without this, high-lakes
> would be falsely tagged with every warmwater species.

## Automated weekly recrawl (GitHub Actions)

`.github/workflows/recrawl.yml` runs every **Sunday 09:00 UTC** (and on manual dispatch):
it crawls, exports, commits the refreshed `web/public/data/*` back to the repo (only if
changed), then builds and deploys the static site to **GitHub Pages**.

One-time setup after pushing to GitHub:

1. Repo **Settings → Pages → Build and deployment → Source: "GitHub Actions"**.
2. Trigger once from the **Actions** tab (`Run workflow`) to verify, or wait for Sunday.

The CI run does a full fresh crawl (no persisted cache → always current) at a politer 1s
delay, with automatic retries/backoff on transient WDFW errors. It seeds geo_codes from the
committed `geocodes.json`, so it fetches ~0 lake detail pages.

### Daily stocking refresh (`.github/workflows/refresh-stocking.yml`)

Runs every **day at 13:00 UTC** (~6am Pacific): refreshes only the stocking data (one SODA
API call, no wdfw.gov crawl), commits `locations.geojson` + `stocks.json`, and deploys. This
keeps stocking current daily while the heavy lake/species crawl stays weekly.

### Deploy on push (`.github/workflows/deploy.yml`)

Pushing site changes under `web/**` to `main` builds and deploys to Pages immediately, so
you don't have to wait for a scheduled run.

All three workflows share a single `pages` concurrency group, so data commits and Pages
deployments never overlap. (The scheduled jobs commit with `GITHUB_TOKEN`, which by design
does not trigger the push-deploy workflow — each scheduled job deploys its own fresh data,
so there's no double deploy.)
