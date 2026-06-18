"""Crawl WDFW fishing-location listings into a SQLite database.

Three server-rendered listing tables share an identical column layout:
  - lowland-lakes            (category 'lowland')
  - high-lakes               (category 'high')
  - high-lakes/overabundant  (overpopulation data, a subset of high-lakes)

Each row carries name, acres, elevation, county and inline lat/long. Species are not
shown in the table but are filterable via ?species=<numeric id>, so species membership
is recovered by querying once per species and recording which waterbodies match.

Run:  uv run crawl.py
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path

import requests_cache
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://wdfw.wa.gov/fishing/locations/"
PATH_PREFIX = "/fishing/locations/"
DB_PATH = Path(__file__).parent / "data" / "wdfw.db"
CACHE_PATH = Path(__file__).parent / "data" / "http_cache"
# Committed lake_id -> geo_code map. Geo codes never change, so after the first full
# build the weekly CI crawl fetches ~0 detail pages (it seeds from this file).
GEOCODES_PATH = Path(__file__).parent / "geocodes.json"
# WDFW Fish Plants dataset (Socrata SODA API) on the WA Open Data Portal.
STOCKING_API = "https://data.wa.gov/resource/6fex-3r7d.json"
STOCKING_MONTHS = int(os.environ.get("STOCKING_MONTHS", "6"))  # recency window to keep
USER_AGENT = "fishing-wa-dev/0.1 (personal mapping project; contact via github)"
# Delay between real network hits (not cache hits). Override in CI to be politer.
REQUEST_DELAY_S = float(os.environ.get("CRAWL_DELAY_S", "0.5"))
PER_PAGE = 20

# (url path under /fishing/locations/, category label for the waterbody)
LISTINGS = [
    ("lowland-lakes", "lowland"),
    ("high-lakes", "high"),
]
OVERABUNDANT_PATH = "high-lakes/overabundant"

session = requests_cache.CachedSession(
    str(CACHE_PATH),
    cache_control=False,
    expire_after=requests_cache.NEVER_EXPIRE,
    headers={"User-Agent": USER_AGENT},
)
# Retry transient network/server errors with backoff (important for unattended CI runs).
_retry = Retry(total=5, backoff_factor=1.0,
               status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=frozenset({"GET"}))
session.mount("https://", HTTPAdapter(max_retries=_retry))


def get(path: str, params: dict) -> str:
    """Fetch a listing page; throttle only on real network requests."""
    resp = session.get(BASE + path, params=params, timeout=30)
    resp.raise_for_status()
    if not getattr(resp, "from_cache", False):
        time.sleep(REQUEST_DELAY_S)
    return resp.text


def get_url(url: str) -> str:
    """Fetch an absolute URL (e.g. a lake detail page); throttle real hits."""
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    if not getattr(resp, "from_cache", False):
        time.sleep(REQUEST_DELAY_S)
    return resp.text


# --- parsing ---------------------------------------------------------------

def parse_total(soup: BeautifulSoup) -> int:
    """Read 'Displaying 1 - 20 of 993' -> 993 (0 if no results)."""
    m = re.search(r"of\s+([\d,]+)", soup.get_text())
    return int(m.group(1).replace(",", "")) if m else 0


def _num(text: str) -> float | None:
    m = re.search(r"-?\d[\d,]*\.?\d*", text or "")
    return float(m.group(0).replace(",", "")) if m else None


def parse_table(soup: BeautifulSoup) -> list[dict]:
    """Parse the shared 5-column results table into waterbody dicts."""
    rows: list[dict] = []
    for tr in soup.select("table tbody tr"):
        link = tr.select_one("td.views-field-title a")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        wid = href.replace(PATH_PREFIX, "").strip("/")  # e.g. "high-lakes/airplane"
        lat_el = tr.select_one("span.latlon-lat")
        lon_el = tr.select_one("span.latlon-lon")
        acres = tr.select_one("td.views-field-field-acres")
        elev = tr.select_one("td.views-field-field-elevation")
        county = tr.select_one("td.views-field-name")
        rows.append({
            "id": wid,
            "name": link.get_text(strip=True),
            "url": "https://wdfw.wa.gov" + href,
            "county": county.get_text(strip=True) if county else None,
            "acres": _num(acres.get_text()) if acres else None,
            "elevation": int(_num(elev.get_text())) if elev and _num(elev.get_text()) is not None else None,
            "lat": float(lat_el.get_text(strip=True)) if lat_el else None,
            "lng": float(lon_el.get_text(strip=True)) if lon_el else None,
        })
    return rows


def parse_species_options(soup: BeautifulSoup) -> list[tuple[int, str]]:
    """Return (numeric id, label) for each real species option (skip '- Any -')."""
    out = []
    for opt in soup.select("select[name=species] option"):
        val = (opt.get("value") or "").strip()
        if val.isdigit():
            out.append((int(val), opt.get_text(strip=True)))
    return out


def crawl_pages(path: str, params: dict):
    """Yield (rows, soup, total) page by page until the result set is exhausted.

    Callers may inspect `total` from the first yield and abandon the generator to
    avoid paginating (no further pages are fetched once iteration stops).
    """
    base_params = dict(params)
    first = BeautifulSoup(get(path, {**base_params, "page": 0}), "lxml")
    total = parse_total(first)
    yield parse_table(first), first, total
    pages = math.ceil(total / PER_PAGE)
    for p in range(1, pages):
        soup = BeautifulSoup(get(path, {**base_params, "page": p}), "lxml")
        yield parse_table(soup), soup, total


# --- database --------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS waterbody (
    id TEXT PRIMARY KEY, name TEXT, category TEXT, county TEXT,
    acres REAL, elevation INTEGER, lat REAL, lng REAL, url TEXT,
    geo_code TEXT
);
CREATE TABLE IF NOT EXISTS species (
    id INTEGER PRIMARY KEY, label TEXT
);
CREATE TABLE IF NOT EXISTS waterbody_species (
    waterbody_id TEXT, species_id INTEGER,
    PRIMARY KEY (waterbody_id, species_id)
);
CREATE TABLE IF NOT EXISTS overabundant (
    waterbody_id TEXT, species_id INTEGER,
    PRIMARY KEY (waterbody_id, species_id)
);
CREATE TABLE IF NOT EXISTS stocking (
    waterbody_id TEXT, stock_date TEXT, species TEXT,
    number_released INTEGER, total_pounds REAL, facility TEXT
);
"""


def category_of(wid: str) -> str:
    return "lowland" if wid.startswith("lowland-lakes") else "high"


def upsert_waterbody(db: sqlite3.Connection, row: dict) -> None:
    db.execute(
        """INSERT INTO waterbody (id,name,category,county,acres,elevation,lat,lng,url)
           VALUES (:id,:name,:category,:county,:acres,:elevation,:lat,:lng,:url)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, county=excluded.county, acres=excluded.acres,
             elevation=excluded.elevation, lat=excluded.lat, lng=excluded.lng,
             url=excluded.url""",
        {**row, "category": category_of(row["id"])},
    )


def tag_listing(db, path, species_opts, all_total, table, insert_sql):
    """Filter `path` by each species and record memberships in `table`.

    WDFW quirk: filtering a listing by a species id that is NOT in that listing's
    own dropdown makes the view ignore the filter and return the *entire* list.
    So we (a) only query each listing's own dropdown species, and (b) guard by
    skipping any response whose total equals the unfiltered total — belt and
    suspenders against that silent-no-op behaviour producing false memberships.
    """
    for sid, label in species_opts.items():
        gen = crawl_pages(path, {"county": "All", "species": str(sid)})
        rows0, _, total = next(gen)
        if total == 0:
            continue
        if all_total and total >= all_total:
            print(f"[skip] {table} / {label}: filter not honored ({total} rows)")
            continue  # abandon generator -> no further pages fetched
        tagged = 0
        for rows in (rows0, *(r for r, _, _ in gen)):
            for r in rows:
                upsert_waterbody(db, r)
                db.execute(insert_sql, (r["id"], sid))
                tagged += 1
        db.commit()
        if tagged:
            print(f"[{table}] {label}: {tagged}")


GEO_CODE_RE = re.compile(r"Geo Code:\s*(?:</strong>)?\s*([A-Z]?[0-9][0-9.]*)")


def capture_geocodes(db) -> None:
    """Fill waterbody.geo_code from each lake's detail page.

    Seeds from the committed geocodes.json so only lakes with an unknown geo_code
    are fetched (≈0 after the first full build). Geo codes join lakes to the WDFW
    Fish Plants stocking dataset.
    """
    cache = json.loads(GEOCODES_PATH.read_text()) if GEOCODES_PATH.exists() else {}
    rows = db.execute("SELECT id, url FROM waterbody ORDER BY id").fetchall()
    fetched = 0
    for wid, url in rows:
        code = cache.get(wid)
        if code is None and wid not in cache:
            m = GEO_CODE_RE.search(get_url(url))
            code = m.group(1) if m else None
            cache[wid] = code
            fetched += 1
            if fetched % 100 == 0:
                print(f"[geocode] fetched {fetched} detail pages ...")
        if code:
            db.execute("UPDATE waterbody SET geo_code=? WHERE id=?", (code, wid))
    db.commit()
    GEOCODES_PATH.write_text(json.dumps(cache, indent=0, sort_keys=True))
    have = db.execute("SELECT COUNT(*) FROM waterbody WHERE geo_code IS NOT NULL").fetchone()[0]
    print(f"[geocode] {have}/{len(rows)} lakes have a geo_code ({fetched} newly fetched)")


def fetch_stocking(db) -> None:
    """Pull recent plants from the WDFW Fish Plants dataset and join them to lakes
    by geo_code. Only plants whose geo_code matches a crawled lake are kept."""
    geo_to_id = {g: i for i, g in db.execute(
        "SELECT id, geo_code FROM waterbody WHERE geo_code IS NOT NULL")}
    cutoff = (dt.date.today() - dt.timedelta(days=30 * STOCKING_MONTHS)).isoformat()
    kept = 0
    offset = 0
    db.execute("DELETE FROM stocking")
    while True:
        rows = session.get(STOCKING_API, params={
            "$select": "geo_code,release_end_date,species,number_released,total_pounds,facility",
            "$where": f"release_end_date >= '{cutoff}'",
            "$order": "release_end_date DESC",
            "$limit": 5000, "$offset": offset,
        }, timeout=60).json()
        if not rows:
            break
        for r in rows:
            wid = geo_to_id.get(r.get("geo_code"))
            if not wid:
                continue
            db.execute(
                "INSERT INTO stocking VALUES (?,?,?,?,?,?)",
                (wid, (r.get("release_end_date") or "")[:10], r.get("species"),
                 int(float(r["number_released"])) if r.get("number_released") else None,
                 float(r["total_pounds"]) if r.get("total_pounds") else None,
                 (r.get("facility") or "").strip() or None),
            )
            kept += 1
        offset += len(rows)
        if len(rows) < 5000:
            break
    db.commit()
    lakes = db.execute("SELECT COUNT(DISTINCT waterbody_id) FROM stocking").fetchone()[0]
    print(f"[stocking] kept {kept} plant events across {lakes} lakes (since {cutoff})")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    catalog: dict[int, str] = {}          # union of species, for the species table
    listing_species: dict[str, dict] = {}  # path -> that listing's own dropdown species
    listing_total: dict[str, int] = {}     # path -> unfiltered (species=All) total

    # 1-2. Full lists + per-listing species discovery.
    for path, category in LISTINGS:
        count, total, opts = 0, 0, {}
        for rows, soup, t in crawl_pages(path, {"county": "All", "species": "All"}):
            total = t
            for r in rows:
                upsert_waterbody(db, r)
            count += len(rows)
            for sid, label in parse_species_options(soup):
                opts.setdefault(sid, label)
        listing_species[path] = opts
        listing_total[path] = total
        catalog.update(opts)
        db.commit()
        print(f"[full] {path}: {count} waterbodies, {len(opts)} filter species")

    for sid, label in catalog.items():
        db.execute("INSERT OR IGNORE INTO species (id,label) VALUES (?,?)", (sid, label))
    db.commit()
    print(f"[species] {len(catalog)} species in catalog")

    # 3. Tag species membership — only each listing's own dropdown species.
    for path, category in LISTINGS:
        tag_listing(db, path, listing_species[path], listing_total[path],
                    "waterbody_species", "INSERT OR IGNORE INTO waterbody_species VALUES (?,?)")

    # 4. Overabundance tags from the overabundant listing.
    over_first = BeautifulSoup(
        get(OVERABUNDANT_PATH, {"county": "All", "species": "All", "page": 0}), "lxml")
    over_opts = dict(parse_species_options(over_first))
    over_total = parse_total(over_first)
    for sid, label in over_opts.items():
        db.execute("INSERT OR IGNORE INTO species (id,label) VALUES (?,?)", (sid, label))
    tag_listing(db, OVERABUNDANT_PATH, over_opts, over_total,
                "overabundant", "INSERT OR IGNORE INTO overabundant VALUES (?,?)")

    # 5. Geo codes (detail pages) + recent stocking (Socrata), joined by geo_code.
    capture_geocodes(db)
    fetch_stocking(db)

    # Summary.
    for cat, n in db.execute("SELECT category, COUNT(*) FROM waterbody GROUP BY category"):
        print(f"  waterbody[{cat}] = {n}")
    n_over = db.execute("SELECT COUNT(DISTINCT waterbody_id) FROM overabundant").fetchone()[0]
    print(f"  overabundant lakes = {n_over}")
    db.close()


if __name__ == "__main__":
    main()
