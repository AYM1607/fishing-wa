# fishing-wa

Interactive map of Washington State fishing lakes — filter by species and surface
**overpopulated** ("overabundant") lakes for a given species. Data is crawled from
[WDFW](https://wdfw.wa.gov/fishing/locations).

Two parts:

1. **Crawler** (`crawler/`, Python via uv) — scrapes the WDFW lowland-lakes, high-lakes,
   and high-lakes/overabundant listings into a SQLite database, then exports static JSON.
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

- `waterbody(id, name, category, county, acres, elevation, lat, lng, url)` — `id` is the
  WDFW URL slug (e.g. `high-lakes/airplane`), which dedups a lake across queries.
- `species(id, label)` — `id` is WDFW's numeric species id.
- `waterbody_species(waterbody_id, species_id)` — which species are present in each lake.
- `overabundant(waterbody_id, species_id)` — lake is overpopulated for that species.

## 2. Run the map

```sh
cd web
pnpm install
pnpm dev        # dev server, opens the map
pnpm build      # static production build -> web/dist/ (deploy anywhere)
```

The map clusters all lakes, lets you filter by species and water type, and — once a species
is selected — a toggle highlights overpopulated lakes (red markers + a density heat layer).

## Refreshing data

Re-run the crawl + export and rebuild. The pipeline is idempotent; cached HTTP keeps it
cheap locally. Set `CRAWL_DELAY_S` to change the per-request delay (default 0.5s).

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
delay, with automatic retries/backoff on transient WDFW errors.

### Deploy on push (`.github/workflows/deploy.yml`)

Pushing site changes under `web/**` to `main` builds and deploys to Pages immediately, so
you don't have to wait for the weekly run. Both workflows share a `pages` concurrency group
so deployments never overlap. (The recrawl's data commit is made with `GITHUB_TOKEN`, which
by design does not trigger this workflow — the recrawl publishes its own fresh data, so
there's no double deploy.)
