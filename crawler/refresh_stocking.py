"""Daily stocking-only refresh.

Reads the committed geocodes.json + locations.geojson, pulls recent plants from the
WDFW Fish Plants SODA API, and rewrites just the stocking parts of locations.geojson
and stocks.json. Touches only the Socrata API (no wdfw.gov load) and needs no database,
so it's a cheap daily job alongside the weekly full crawl.

Run:  uv run refresh_stocking.py
"""
from __future__ import annotations

import json
from pathlib import Path

from stocking import cutoff_date, fetch_plants

GEOCODES_PATH = Path(__file__).parent / "geocodes.json"
DATA_DIR = Path(__file__).parent.parent / "web" / "public" / "data"


def main() -> None:
    geocodes = json.loads(GEOCODES_PATH.read_text())  # lake_id -> geo_code
    geo_to_id = {g: i for i, g in geocodes.items() if g}

    geojson = json.loads((DATA_DIR / "locations.geojson").read_text())

    cutoff = cutoff_date()
    by_lake: dict[str, list[dict]] = {}
    for p in fetch_plants(cutoff):
        wid = geo_to_id.get(p["geo_code"])
        if not wid:
            continue
        by_lake.setdefault(wid, []).append(
            {"date": p["date"], "species": p["species"], "number": p["number"]})

    stock_events = []
    for f in geojson["features"]:
        wid = f["properties"]["id"]
        events = sorted(by_lake.get(wid, []), key=lambda e: e["date"], reverse=True)
        f["properties"]["last_stocked"] = events[0]["date"] if events else None
        f["properties"]["recent_stocks"] = events[:12]
        lng, lat = f["geometry"]["coordinates"]
        for e in events:
            stock_events.append({
                "id": wid, "name": f["properties"]["name"],
                "county": f["properties"]["county"], "lat": lat, "lng": lng, **e,
            })

    stock_events.sort(key=lambda e: e["date"], reverse=True)
    (DATA_DIR / "locations.geojson").write_text(json.dumps(geojson))
    (DATA_DIR / "stocks.json").write_text(json.dumps(stock_events))
    print(f"refreshed {len(stock_events)} events across {len(by_lake)} lakes (since {cutoff})")


if __name__ == "__main__":
    main()
