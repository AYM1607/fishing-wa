"""Export the crawl database to static JSON the frontend loads directly.

Writes:
  web/public/data/locations.geojson  — FeatureCollection of waterbody points
  web/public/data/species.json       — species list with counts for the filter UI
  web/public/data/stocks.json        — recent stocking events, newest first

Run:  uv run export_geojson.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "wdfw.db"
OUT_DIR = Path(__file__).parent.parent / "web" / "public" / "data"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    species_by_wb: dict[str, list[str]] = {}
    for r in db.execute(
        """SELECT ws.waterbody_id AS wid, s.label AS label
           FROM waterbody_species ws JOIN species s ON s.id = ws.species_id"""
    ):
        species_by_wb.setdefault(r["wid"], []).append(r["label"])

    over_by_wb: dict[str, list[str]] = {}
    for r in db.execute(
        """SELECT o.waterbody_id AS wid, s.label AS label
           FROM overabundant o JOIN species s ON s.id = o.species_id"""
    ):
        over_by_wb.setdefault(r["wid"], []).append(r["label"])

    # Stocking events per lake, newest first.
    stocks_by_wb: dict[str, list[dict]] = {}
    for r in db.execute(
        """SELECT waterbody_id AS wid, stock_date, species, number_released
           FROM stocking ORDER BY stock_date DESC"""
    ):
        stocks_by_wb.setdefault(r["wid"], []).append({
            "date": r["stock_date"], "species": r["species"],
            "number": r["number_released"],
        })

    features = []
    stock_events = []  # flat list for the Recent stocks tab
    for w in db.execute(
        "SELECT * FROM waterbody WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ):
        stocks = stocks_by_wb.get(w["id"], [])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [w["lng"], w["lat"]]},
            "properties": {
                "id": w["id"],
                "name": w["name"],
                "category": w["category"],
                "county": w["county"],
                "acres": w["acres"],
                "elevation": w["elevation"],
                "url": w["url"],
                "species": sorted(species_by_wb.get(w["id"], [])),
                "overabundant_species": sorted(over_by_wb.get(w["id"], [])),
                "last_stocked": stocks[0]["date"] if stocks else None,
                "recent_stocks": stocks[:12],
            },
        })
        for s in stocks:
            stock_events.append({
                "id": w["id"], "name": w["name"], "county": w["county"],
                "lat": w["lat"], "lng": w["lng"],
                "date": s["date"], "species": s["species"], "number": s["number"],
            })

    geojson = {"type": "FeatureCollection", "features": features}
    (OUT_DIR / "locations.geojson").write_text(json.dumps(geojson))

    stock_events.sort(key=lambda e: e["date"], reverse=True)
    (OUT_DIR / "stocks.json").write_text(json.dumps(stock_events))

    # Species list: label, present-count, overabundant-count.
    present_counts: dict[str, int] = {}
    for labels in species_by_wb.values():
        for label in labels:
            present_counts[label] = present_counts.get(label, 0) + 1
    over_counts: dict[str, int] = {}
    for labels in over_by_wb.values():
        for label in labels:
            over_counts[label] = over_counts.get(label, 0) + 1

    species_list = [
        {"label": label, "count": present_counts[label],
         "overabundant_count": over_counts.get(label, 0)}
        for label in sorted(present_counts)
    ]
    (OUT_DIR / "species.json").write_text(json.dumps(species_list, indent=2))

    print(f"wrote {len(features)} features, {len(species_list)} species, "
          f"{len(stock_events)} stock events to {OUT_DIR}")


if __name__ == "__main__":
    main()
