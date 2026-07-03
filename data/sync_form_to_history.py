"""
Fetches the published Google Sheet CSV (linked to your Form responses)
and converts it into processed/history.json, matching the schema the
dashboard's chart code already expects.

Set CSV_URL below to the link you copied from
File > Share > Publish to web > (your sheet) > Comma-separated values (.csv)
"""

import csv
import json
import sys
import urllib.request
from io import StringIO
from pathlib import Path

CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRHWIio7FAVHaT8BrxgZaT-SpAxulHLv9NkL_WZwSBOmZRKUy9NIFNiZWiFllj6NiB5COwNxL73LEDr/pub?gid=345760016&single=true&output=csv"

# Google Form question titles -> (group, product, region)
# Use these EXACT strings as your Form question titles so the columns line up.
COLUMN_MAP = {
    "Maíz - Cochabamba":              ("grains", "maiz", "cbba"),
    "Maíz - Santa Cruz":              ("grains", "maiz", "scz"),
    "Maíz - Anapo":                   ("grains", "maiz", "anapo"),
    "Sorgo - Cochabamba":             ("grains", "sorgo", "cbba"),
    "Sorgo - Santa Cruz":             ("grains", "sorgo", "scz"),
    "Sorgo - Anapo":                  ("grains", "sorgo", "anapo"),
    "H. Integral - Cochabamba":       ("grains", "harina_integral", "cbba"),
    "H. Integral - Santa Cruz":       ("grains", "harina_integral", "scz"),
    "H. Integral - Anapo":            ("grains", "harina_integral", "anapo"),
    "H. Solvente - Cochabamba":       ("grains", "harina_solvente", "cbba"),
    "H. Solvente - Santa Cruz":       ("grains", "harina_solvente", "scz"),
    "H. Solvente - Anapo":            ("grains", "harina_solvente", "anapo"),
    "Soya FOB - Perú":                ("soy_complex", "soya_fob", "peru"),
    "Soya FOB - Bolivia":             ("soy_complex", "soya_fob", "bolivia"),
    "Soya FOB - Chicago":             ("soy_complex", "soya_fob", "chicago"),
    "Soya FOB - Argentina":           ("soy_complex", "soya_fob", "argentina"),
    "Aceite FOB - Perú":              ("soy_complex", "aceite_fob", "peru"),
    "Aceite FOB - Bolivia":           ("soy_complex", "aceite_fob", "bolivia"),
    "Aceite FOB - Chicago":           ("soy_complex", "aceite_fob", "chicago"),
    "Aceite FOB - Argentina":         ("soy_complex", "aceite_fob", "argentina"),
    "Torta de soya FOB - Perú":       ("soy_complex", "torta_soya_fob", "peru"),
    "Torta de soya FOB - Bolivia":    ("soy_complex", "torta_soya_fob", "bolivia"),
    "Torta de soya FOB - Chicago":    ("soy_complex", "torta_soya_fob", "chicago"),
    "Torta de soya FOB - Argentina":  ("soy_complex", "torta_soya_fob", "argentina"),
    "Flete - Lima":                   ("freight", None, "lima"),
    "Flete - Arica":                  ("freight", None, "arica"),
}

DATE_COLUMN = "Fecha"


def fetch_csv(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "sync-script"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                sys.exit(f"ERROR: Sheet fetch returned HTTP {resp.status}")
            text = resp.read().decode("utf-8")
    except Exception as e:
        sys.exit(f"ERROR: Could not fetch published sheet CSV: {e}")

    # Guard against committing an error page or empty response as data.
    if DATE_COLUMN not in text or len(text.strip().splitlines()) < 1:
        sys.exit(
            "ERROR: Fetched content doesn't look like the expected CSV "
            f"(missing '{DATE_COLUMN}' header). Aborting without writing."
        )
    return text


def to_float(v):
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def build_snapshot(row: dict) -> dict:
    snapshot = {
        "date": row.get(DATE_COLUMN, "").strip(),
        "source_file": "google-form",
        "grains": {},
        "soy_complex": {},
        "freight": {},
    }
    for column, (group, product, region) in COLUMN_MAP.items():
        val = to_float(row.get(column))
        if val is None:
            continue
        if group == "freight":
            snapshot["freight"][region] = val
        else:
            snapshot[group].setdefault(product, {})[region] = val
    return snapshot


def main():
    csv_text = fetch_csv(CSV_URL)
    reader = csv.DictReader(StringIO(csv_text))

    # Rows are in submission order, so iterating in order and overwriting
    # by date means the LAST submission for a given date wins automatically.
    by_date = {}
    skipped = 0
    for row in reader:
        snap = build_snapshot(row)
        if not snap["date"]:
            skipped += 1
            continue
        by_date[snap["date"]] = snap

    if not by_date:
        sys.exit("ERROR: No valid dated rows found in the sheet. Aborting without writing.")

    history = sorted(by_date.values(), key=lambda s: s["date"])

    out_path = Path("processed/history.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"Wrote {len(history)} snapshot(s) to {out_path} ({skipped} row(s) skipped, no date).")


if __name__ == "__main__":
    main()
