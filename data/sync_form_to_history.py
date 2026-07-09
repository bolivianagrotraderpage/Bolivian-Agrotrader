"""
Fetches published Google Sheet CSVs and merges them into
processed/history.json:

1. FORM_CSV_URL   - the Form-responses sheet (one row per submission,
                     has a "Fecha" column). Feeds grains / soy_complex /
                     freight.

2. COSTOS_SHEETS  - one or more per-product cost sheets (Aceite / Solvente
                     / Grano columns), keyed by the name they'll be stored
                     under in costos.*. These sheets have no date column -
                     they just hold today's already-computed values - so
                     their numbers are stamped with today's date and
                     merged into (or used to create) that day's entry.
                     Rosario is the first one; add more entries here as
                     new route sheets (Callao, Arica, etc.) get published -
                     no other code changes needed as long as they follow
                     the same "Aceite/Solvente/Grano columns + labeled
                     rows" layout.

Nothing is computed in Python: every source is read as-is. The
"declaracion de variables" sheet that feeds these sheets' formulas is
internal to the spreadsheet and is never fetched here.

Publish each sheet via File > Share > Publish to web > (sheet) >
Comma-separated values (.csv) and paste its link below. Leave an entry
as the placeholder to skip that source without breaking the sync.
"""

import csv
import json
import sys
import urllib.request
from datetime import date
from io import StringIO
from pathlib import Path

FORM_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRHWIio7FAVHaT8BrxgZaT-SpAxulHLv9NkL_WZwSBOmZRKUy9NIFNiZWiFllj6NiB5COwNxL73LEDr/pub?gid=345760016&single=true&output=csv"

# costos.<key> -> published CSV URL for that route/sheet.
# Add more route sheets here as they're published
# (e.g. "venta_soja_callao": "https://...pub?gid=...&single=true&output=csv").
# Most sheets follow the "Aceite/Solvente/Grano columns + labeled rows"
# layout, parsed by parse_costos_sheet_csv. "mercado_exterior" is a
# different layout (see parse_mercado_exterior_csv) - which parser runs
# for which key is set in COSTOS_SHEET_PARSERS below.
COSTOS_SHEETS = {
    # tab: "HC Rosario"
    "venta_soja_rosario": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQ39PJM93JRVCt38Ryr_xBQbiDNEGzreH5ydhtmVF3w3ZI3oVHLZBiFtyKmmd3pPHhK4mAOVkW1tvti/pub?gid=62916827&single=true&output=csv",
    # tab: "HC Lima"
    "venta_soja_lima": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQ39PJM93JRVCt38Ryr_xBQbiDNEGzreH5ydhtmVF3w3ZI3oVHLZBiFtyKmmd3pPHhK4mAOVkW1tvti/pub?gid=1317820090&single=true&output=csv",
    # tab: "MercadoExterior"
    "mercado_exterior": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQ39PJM93JRVCt38Ryr_xBQbiDNEGzreH5ydhtmVF3w3ZI3oVHLZBiFtyKmmd3pPHhK4mAOVkW1tvti/pub?gid=1352820681&single=true&output=csv",
}

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

# --- Per-product cost tables (FOB / FCA values) ---
# Row-label (normalized) -> key in costos.<route>.
# These rows have one value per product column (Aceite/Solvente/Grano).
# Shared across all COSTOS_SHEETS entries: Rosario uses "FOB Pto Aguirre",
# Lima uses "FOB Desaguadero"; both routes share the "FCA SCZ (montero)"
# label for their final stage.
COSTOS_PRODUCTO_ROW_LABELS = {
    "fob pto aguirre": "fob_pto_aguirre",
    "fob desaguadero": "fob_desaguadero",
    "fca scz (montero)": "fca_scz_montero",
    "fca scz montero": "fca_scz_montero",
}
# Column header (normalized) -> product key
COSTOS_PRODUCTO_COLUMNS = {
    "aceite": "aceite",
    "solvente": "solvente",
    "grano": "grano",
}

# Rows that hold a single value (not split across Aceite/Solvente/Grano
# columns) - e.g. "Precio del grano procesado FCA SCZ para Rosario", which
# is the reference cost subtracted from the FCA Grano value to decide
# INDUSTRIAL vs GRANOS on the webpage (comparison done in JS, not here).
# Only applies to the FCA SCZ (Montero) stage, not FOB Pto Aguirre.
# Matched as a substring (see parse_costos_sheet_csv) so trailing wording
# like "FCA SCZ para Rosario" can vary without breaking the match.
COSTOS_SCALAR_ROW_LABELS = {
    "precio del grano procesado": "costo_g_industrial",
}


# Which parsed keys each route sheet is expected to produce - used only
# for the "expected but missing" diagnostic below. New sheets not listed
# here fall back to checking against every known key.
COSTOS_SHEET_EXPECTED_KEYS = {
    "venta_soja_rosario": {"fob_pto_aguirre", "fca_scz_montero", "costo_g_industrial"},
    "venta_soja_lima": {"fob_desaguadero", "fca_scz_montero", "costo_g_industrial"},
    "mercado_exterior": {"precios", "base"},
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


def parse_costos_sheet_csv(csv_text: str) -> dict:
    """Parses a per-product cost sheet (Aceite/Solvente/Grano columns):
    finds the header row with the product columns, then pulls the target
    rows (FOB Pto Aguirre, FCA SCZ Montero) by label, regardless of exact
    row/column position. Also scans the whole sheet for scalar rows
    (COSTOS_SCALAR_ROW_LABELS), which live outside that table and hold a
    single value rather than one per product. Values are read as-is;
    nothing is computed here. Shared across all COSTOS_SHEETS entries -
    they're expected to follow the same "Hoja de costos" layout."""
    rows = list(csv.reader(StringIO(csv_text)))
    result = {}

    # --- scalar rows (single value, anywhere in the sheet) ---
    for row in rows:
        if not row:
            continue
        matched_key = None
        matched_j = None
        for j, cell in enumerate(row):
            norm = _normalize(cell)
            if not norm:
                continue
            for phrase, key in COSTOS_SCALAR_ROW_LABELS.items():
                if phrase in norm:
                    matched_key, matched_j = key, j
                    break
            if matched_key:
                break
        if not matched_key:
            continue
        for later_cell in row[matched_j + 1:]:
            val = to_float(later_cell)
            if val is not None:
                result[matched_key] = val
                break

    # --- per-product table (Aceite / Solvente / Grano columns) ---
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
        return result

    label_col_limit = min(col_index_by_product.values())

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


# --- "Mercado Exterior" sheet: two small tables with an inverted layout
# compared to the FOB/FCA sheets (see parse_mercado_exterior_csv). ---
# Table 1: markets are columns, products are rows. The third market column
# was renamed Lima -> Desaguadero in the sheet; both map to the same
# "desaguadero" key so either wording still works.
MERCADO_EXTERIOR_MARKETS = {
    "rosario": "rosario",
    "chicago": "chicago",
    "desaguadero": "desaguadero",
    "lima": "desaguadero",
}
# Table 2: products are columns, "Base X - Chicago" rows are the base type.
MERCADO_EXTERIOR_PRODUCTS = {
    "soya grano": "soya_grano",
    "harina solvente": "harina_solvente",
    "aceite": "aceite",
}
MERCADO_EXTERIOR_BASE_ROWS = {
    "base rosario - chicago": "rosario_chicago",
    "base rosario chicago": "rosario_chicago",
    "base desaguadero - chicago": "desaguadero_chicago",
    "base desaguadero chicago": "desaguadero_chicago",
    "base lima - chicago": "desaguadero_chicago",
    "base lima chicago": "desaguadero_chicago",
}


def parse_mercado_exterior_csv(csv_text: str) -> dict:
    """Parses the 'Mercado Exterior' sheet, which has two small tables
    stacked vertically with an inverted layout relative to the FOB/FCA
    sheets:
      - Table 1: header row is markets (Rosario/Chicago/Lima), each
        following row is a product (Soya grano/Harina Solvente/Aceite).
        -> result["precios"][market][product] = value
      - Table 2: header row is products, each following row is a base
        type ("Base Rosario - Chicago", "Base Lima - Chicago").
        -> result["base"][base_type][product] = value
    Values are read as-is; nothing is computed here."""
    rows = list(csv.reader(StringIO(csv_text)))
    precios: dict = {}
    base: dict = {}

    # --- Table 1: markets as columns, products as rows ---
    market_cols = {}
    header1_idx = None
    for i, row in enumerate(rows):
        cols_found = {}
        for j, cell in enumerate(row):
            norm = _normalize(cell)
            if norm in MERCADO_EXTERIOR_MARKETS:
                cols_found[MERCADO_EXTERIOR_MARKETS[norm]] = j
        if len(cols_found) >= 2:
            market_cols = cols_found
            header1_idx = i
            break

    if header1_idx is not None:
        for row in rows[header1_idx + 1:]:
            if not row or all(not c.strip() for c in row):
                break  # blank row ends table 1
            product_key = None
            for cell in row:
                norm = _normalize(cell)
                if norm in MERCADO_EXTERIOR_PRODUCTS:
                    product_key = MERCADO_EXTERIOR_PRODUCTS[norm]
                    break
            if not product_key:
                continue
            for market_key, col_idx in market_cols.items():
                if col_idx < len(row):
                    val = to_float(row[col_idx])
                    if val is not None:
                        precios.setdefault(market_key, {})[product_key] = val

    # --- Table 2: products as columns, base-type as rows ---
    product_cols = {}
    header2_idx = None
    start = (header1_idx + 1) if header1_idx is not None else 0
    for i in range(start, len(rows)):
        row = rows[i]
        cols_found = {}
        for j, cell in enumerate(row):
            norm = _normalize(cell)
            if norm in MERCADO_EXTERIOR_PRODUCTS:
                cols_found[MERCADO_EXTERIOR_PRODUCTS[norm]] = j
        if len(cols_found) >= 2:
            product_cols = cols_found
            header2_idx = i
            break

    if header2_idx is not None:
        for row in rows[header2_idx + 1:]:
            if not row:
                continue
            base_key = None
            for cell in row:
                norm = _normalize(cell)
                for phrase, key in MERCADO_EXTERIOR_BASE_ROWS.items():
                    if phrase in norm:
                        base_key = key
                        break
                if base_key:
                    break
            if not base_key:
                continue
            for product_key, col_idx in product_cols.items():
                if col_idx < len(row):
                    val = to_float(row[col_idx])
                    if val is not None:
                        base.setdefault(base_key, {})[product_key] = val

    result = {}
    if precios:
        result["precios"] = precios
    if base:
        result["base"] = base
    return result


# Which parser handles which COSTOS_SHEETS entry. Anything not listed
# falls back to parse_costos_sheet_csv (the Aceite/Solvente/Grano layout).
COSTOS_SHEET_PARSERS = {
    "mercado_exterior": parse_mercado_exterior_csv,
}


def _print_label_hints(csv_text: str, missing_keys: set) -> None:
    """When an expected row wasn't matched, scan the sheet for label cells
    that share key words with what we're looking for (e.g. 'grano',
    'industrial') and print them verbatim - the fastest way to spot a
    wording mismatch without round-tripping through chat."""
    keywords_by_key = {
        "costo_g_industrial": ("grano", "industrial"),
        "fob_pto_aguirre": ("fob", "aguirre"),
        "fob_desaguadero": ("fob", "desaguadero"),
        "fca_scz_montero": ("fca", "montero"),
    }
    needed_keywords = set()
    for key in missing_keys:
        needed_keywords.update(keywords_by_key.get(key, ()))
    if not needed_keywords:
        return

    candidates = []
    for row in csv.reader(StringIO(csv_text)):
        for cell in row:
            norm = _normalize(cell)
            if len(norm) > 3 and any(kw in norm for kw in needed_keywords):
                candidates.append(cell.strip())
    if candidates:
        unique = list(dict.fromkeys(candidates))[:10]
        print(f"[costos]   possible label cells found in the sheet: {unique}")
    else:
        print("[costos]   no cells with matching keywords found at all - check the sheet/tab is the right one.")


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

    # Cost sheets: today's per-product FOB / FCA values (and Mercado
    # Exterior prices/bases), read as-is from each already-computed
    # sheet.
    today_costos = {}
    for costos_key, url in COSTOS_SHEETS.items():
        csv_text = fetch_csv(url)
        if csv_text is None:
            print(f"[costos] '{costos_key}': URL not set, skipping.")
            continue

        row_count = len(csv_text.strip().splitlines())
        parser = COSTOS_SHEET_PARSERS.get(costos_key, parse_costos_sheet_csv)
        parsed = parser(csv_text)

        if parsed:
            today_costos[costos_key] = parsed
            found_rows = ", ".join(parsed.keys())
            print(f"[costos] '{costos_key}': fetched {row_count} CSV line(s), matched rows: {found_rows}")

            if costos_key in COSTOS_SHEET_EXPECTED_KEYS:
                expected_keys = COSTOS_SHEET_EXPECTED_KEYS[costos_key]
                missing_keys = expected_keys - set(parsed.keys())
                if missing_keys:
                    print(f"[costos] '{costos_key}': WARNING - expected but missing: {', '.join(sorted(missing_keys))}")
                    _print_label_hints(csv_text, missing_keys)
        else:
            print(
                f"[costos] '{costos_key}': fetched {row_count} CSV line(s) but found NO matching data. "
                "Check that the published link points at the right tab and that row/column labels match."
                f"\nFirst 3 lines of what was fetched:\n" + "\n".join(csv_text.strip().splitlines()[:3])
            )

    if today_costos:
        today = date.today().isoformat()
        if today not in by_date:
            by_date[today] = {
                "date": today,
                "source_file": "costos-sheets",
                "grains": {},
                "soy_complex": {},
                "freight": {},
                "costos": {},
            }
        by_date[today].setdefault("costos", {}).update(today_costos)

    history = sorted(by_date.values(), key=lambda s: s["date"])

    out_path = Path("processed/history.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"Wrote {len(history)} snapshot(s) to {out_path} ({skipped} row(s) skipped, no date).")


if __name__ == "__main__":
    main()
