"""
Fetches two published Google Sheet CSVs and merges them into
processed/history.json:

1. FORM_CSV_URL   - the Form-responses sheet (one row per submission,
                     has a "Fecha" column). Feeds grains / soy_complex /
                     freight.

2. COSTOS_PRODUCTO_CSV_URL - the "Hoja de costos Rosario" sheet (Aceite /
                     Solvente / Grano columns). This sheet has no date
                     column - it just holds today's already-computed
                     values - so its numbers are stamped with today's date
                     and merged into (or used to create) that day's entry.

Nothing is computed in Python: both sources are read as-is. The
"declaracion de variables" sheet that feeds the Hoja de costos Rosario
formulas is internal to the spreadsheet and is never fetched here.

Set both URLs below via File > Share > Publish to web > (sheet) >
Comma-separated values (.csv). Leave COSTOS_PRODUCTO_CSV_URL as the
placeholder to skip that source without breaking the sync.
"""

import csv
import json
import sys
import urllib.request
from datetime import date
from io import StringIO
from pathlib import Path

FORM_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRHWIio7FAVHaT8BrxgZaT-SpAxulHLv9NkL_WZwSBOmZRKUy9NIFNiZWiFllj6NiB5COwNxL73LEDr/pub?gid=345760016&single=true&output=csv"

# "Hoja de costos Rosario" - publish this sheet separately and paste its
# CSV link here. Left as a placeholder, this source is skipped.
COSTOS_PRODUCTO_CSV_URL = "PASTE_HOJA_DE_COSTOS_ROSARIO_CSV_URL_HERE"

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

# --- "Hoja de costos Rosario" (per-product FOB / FCA values) ---
# Row-label (normalized) -> key in costos.venta_soja_rosario
COSTOS_PRODUCTO_ROW_LABELS = {
    "fob pto aguirre": "fob_pto_aguirre",
    "fca scz (montero)": "fca_scz_montero",
    "fca scz montero": "fca_scz_montero",
}
# Column header (normalized) -> product key
COSTOS_PRODUCTO_COLUMNS = {
    "aceite": "aceite",
    "solvente": "solvente",
    "grano": "grano",
}


def _normalize(s: str) -> str:
    return " ".join(s.strip().lower().split())


def fetch_csv(url: str, required_marker: str | None = None) -> str | None:
    """Fetches a published-CSV URL. Returns None (without raising) if the
    URL is still the placeholder, so optional sources can be skipped."""
    if not url or url.startswith("PASTE_"):
        return None

    req = urllib.request.Request(url, headers={"User-Agent": "sync-script"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                sys.exit(f"ERROR: Sheet fetch returned HTTP {resp.status} for {url}")
            text = resp.read().decode("utf-8")
    except Exception as e:
        sys.exit(f"ERROR: Could not fetch published sheet CSV ({url}): {e}")

    if required_marker and (required_marker not in text or len(text.strip().splitlines()) < 1):
        sys.exit(
            "ERROR: Fetched content doesn't look like the expected CSV "
            f"(missing '{required_marker}' header) for {url}. Aborting without writing."
        )
    return text


def to_float(v):
    v = (v or "").strip().replace(",", "")
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
        "costos": {},
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


def parse_costos_producto_csv(csv_text: str) -> dict:
    """Parses the 'Hoja de costos Rosario' sheet: finds the header row
    with the product columns, then pulls the target rows (FOB Pto Aguirre,
    FCA SCZ Montero) by label, regardless of exact row/column position.
    Values are read as-is; nothing is computed here."""
    rows = list(csv.reader(StringIO(csv_text)))

    col_index_by_product = {}
    header_row_idx = None
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            norm = _normalize(cell)
            if norm in COSTOS_PRODUCTO_COLUMNS:
                col_index_by_product[COSTOS_PRODUCTO_COLUMNS[norm]] = j
        if len(col_index_by_product) >= 2:  # found at least 2 of the 3 product columns
            header_row_idx = i
            break

    if header_row_idx is None or not col_index_by_product:
        return {}

    label_col_limit = min(col_index_by_product.values())

    result = {}
    for row in rows[header_row_idx:]:
        if not row:
            continue
        key = None
        for cell in row[:label_col_limit]:
            norm = _normalize(cell)
            if norm in COSTOS_PRODUCTO_ROW_LABELS:
                key = COSTOS_PRODUCTO_ROW_LABELS[norm]
                break
        if not key:
            continue
        values = {}
        for product, col_idx in col_index_by_product.items():
            if col_idx < len(row):
                val = to_float(row[col_idx])
                if val is not None:
                    values[product] = val
        if values:
            result[key] = values

    return result


def main():
    form_csv = fetch_csv(FORM_CSV_URL, required_marker=DATE_COLUMN)
    if form_csv is None:
        sys.exit("ERROR: FORM_CSV_URL is not set. Aborting without writing.")

    reader = csv.DictReader(StringIO(form_csv))

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
        sys.exit("ERROR: No valid dated rows found in the Form sheet. Aborting without writing.")

    # Optional second source: today's per-product FOB / FCA values,
    # read as-is from the already-computed sheet.
    costos_producto_csv = fetch_csv(COSTOS_PRODUCTO_CSV_URL)
    if costos_producto_csv is not None:
        venta_soja = parse_costos_producto_csv(costos_producto_csv)
        if venta_soja:
            today = date.today().isoformat()
            if today not in by_date:
                by_date[today] = {
                    "date": today,
                    "source_file": "hoja-costos-rosario",
                    "grains": {},
                    "soy_complex": {},
                    "freight": {},
                    "costos": {},
                }
            by_date[today].setdefault("costos", {})["venta_soja_rosario"] = venta_soja

    history = sorted(by_date.values(), key=lambda s: s["date"])

    out_path = Path("processed/history.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"Wrote {len(history)} snapshot(s) to {out_path} ({skipped} row(s) skipped, no date).")


if __name__ == "__main__":
    main()
