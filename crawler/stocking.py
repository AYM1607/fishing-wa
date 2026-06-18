"""Shared helpers for the WDFW Fish Plants (Socrata) stocking dataset.

Used by both the full crawler (into SQLite) and the daily refresh (into JSON).
Hits only the WA Open Data Portal SODA API — no load on wdfw.gov.
"""
from __future__ import annotations

import datetime as dt
import os

import requests

STOCKING_API = "https://data.wa.gov/resource/6fex-3r7d.json"
STOCKING_MONTHS = int(os.environ.get("STOCKING_MONTHS", "6"))  # recency window to keep
PAGE = 5000


def cutoff_date() -> str:
    """ISO date STOCKING_MONTHS months back from today."""
    return (dt.date.today() - dt.timedelta(days=30 * STOCKING_MONTHS)).isoformat()


def fetch_plants(cutoff: str, http=requests) -> list[dict]:
    """Return normalized recent plant events: geo_code, date, species, number,
    total_pounds, facility. Paginates the SODA API."""
    out: list[dict] = []
    offset = 0
    while True:
        page = http.get(STOCKING_API, params={
            "$select": "geo_code,release_end_date,species,number_released,total_pounds,facility",
            "$where": f"release_end_date >= '{cutoff}'",
            "$order": "release_end_date DESC",
            "$limit": PAGE, "$offset": offset,
        }, timeout=60).json()
        if not page:
            break
        for r in page:
            out.append({
                "geo_code": r.get("geo_code"),
                "date": (r.get("release_end_date") or "")[:10],
                "species": r.get("species"),
                "number": int(float(r["number_released"])) if r.get("number_released") else None,
                "total_pounds": float(r["total_pounds"]) if r.get("total_pounds") else None,
                "facility": (r.get("facility") or "").strip() or None,
            })
        offset += len(page)
        if len(page) < PAGE:
            break
    return out
