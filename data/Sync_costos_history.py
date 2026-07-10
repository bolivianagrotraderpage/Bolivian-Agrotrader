"""
Builds/updates processed/costos_history.csv — a persistent, append-only
historical record for the "costos" sheets (Rosario / Lima / Mercado
Exterior), run once per day.

This is the OPPOSITE of sync_form_to_history.py's costos handling:
sync_form_to_history.py only ever stamps *today's* costos snapshot into
history.json (which feeds the "Actual" cards, and gets overwritten if the
sheet's values change before the next run). It has no memory of what
those sheets said yesterday, last week, etc. This script's only job is to
fetch the same sheets and permanently record each day's values as rows in
a CSV, so a genuine time series accumulates even on days when the
combined form/costos sync doesn't run, and even though the sheets
themselves carry no date column.

The two scripts are intentionally independent — this one duplicates the
handful of parsing helpers it needs from sync_form_to_history.py rather
than importing it, so each can be run, scheduled, and reasoned about on
its own. If you change a sheet's layout, update both.

Output format is "long"/tidy CSV, one row per (date, route, path, value):
  date,route,path,value
  2026-07-10,venta_soja_rosario,fob_pto_aguirre.grano,412.50
  2026-07-10,venta_soja_rosario,costo_g_industrial,398.00
  2026-07-10,mercado_exterior,precios.chicago.soya_grano,405.10
  2026-07-10,mercado_exterior,base.rosario_chicago.aceite,12.30

"path" is just the parsed dict's keys dot-joined, so it mirrors whatever
parse_costos_sheet_csv / parse_mercado_exterior_csv return — no schema
changes needed here if a route's internal structure changes. Re-running
this script on the same day replaces that day's rows for the routes it
successfully fetched (safe to re-run / backfill), and leaves other
routes' rows untouched if one route's fetch is skipped or fails to parse.

Publish each sheet via File > Share > Publish to web > (sheet) >
Comma-separated values (.csv) and paste its link in COSTOS_SHEETS below —
same links as sync_form_to_history.py.
"""

import csv
import sys
import urllib.request
from datetime import date
from io import StringIO
from pathlib import Path

# costos.<key> -> published CSV URL for that route/sheet.
# Keep in sync with COSTOS_SHEETS in sync_form_to_history.py.
COSTOS_SHEETS = {
    # tab: "HC Rosario"
    "venta_soja_rosario": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQ39PJM93JRVCt38Ryr_xBQbiDNEGzreH5ydhtmVF3w3ZI3oVHLZBiFtyKmmd3pPHhK4mAOVkW1tvti/pub?gid=62916827&single=true&output=csv",
    # tab: "HC Lima"
    "venta_soja_lima": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQ39PJM93JRVCt38Ryr_xBQbiDNEGzreH5ydhtmVF3w3ZI3oVHLZBiFtyKmmd3pPHhK4mAOVkW1tvti/pub?gid=1317820090&single=true&output=csv",
    # tab: "MercadoExterior"
    "mercado_exterior": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQ39PJM93JRVCt38Ryr_xBQbiDNEGzreH5ydhtmVF3w3ZI3oVHLZBiFtyKmmd3pPHhK4mAOVkW1tvti/pub?gid=1352820681&single=true&output=csv",
    # Add more route sheets here as they're published, e.g.:
    # "venta_soja_callao": "https://...pub?gid=...&single=true&output=csv",
}

OUT_PATH = Path("processed/costos_history.csv")
CSV_HEADER = ["date", "route", "path", "value"]

# --- shared parsing helpers (duplicated from sync_form_to_history.py) ---

COSTOS_PRODUCTO_ROW_LABELS = {
    "fob pto aguirre": "fob_pto_aguirre",
    "fob desaguadero": "fob_desaguadero",
    "fca scz (montero)": "fca_scz_montero",
    "fca scz montero": "fca_scz_montero",
    "costo exp": "costo_exp",
}
COSTOS_PRODUCTO_COLUMNS = {"aceite": "aceite", "solvente": "solvente", "grano": "grano"}
COSTOS_SCALAR_ROW_LABELS = {"precio del grano procesado": "costo_g_industrial"}

MERCADO_EXTERIOR_MARKETS = {
    "rosario": "rosario", "chicago": "chicago",
    "desaguadero": "desaguadero", "lima": "desaguadero",
}
MERCADO_EXTERIOR_PRODUCTS = {"soya grano": "soya_grano", "harina solvente": "harina_solvente", "aceite": "aceite"}
MERCADO_EXTERIOR_BASE_ROWS = {
    "base rosario - chicago": "rosario_chicago", "base rosario chicago": "rosario_chicago",
    "base desaguadero - chicago": "desaguadero_chicago", "base desaguadero chicago": "desaguadero_chicago",
    "base lima - chicago": "desaguadero_chicago", "base lima chicago": "desaguadero_chicago",
}


def _normalize(s: str) -> str:
    return " ".join(s.strip().lower().split())


def to_float(v):
    v = (v or "").strip().replace(",", "")
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fetch_csv(url: str) -> str | None:
    if not url or url.startswith("PASTE_"):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "sync-costos-history"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"WARNING: HTTP {resp.status} for {url}, skipping this route.")
                return None
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"WARNING: Could not fetch {url}: {e}. Skipping this route.")
        return None


def parse_costos_sheet_csv(csv_text: str) -> dict:
    """Aceite/Solvente/Grano layout — same logic as sync_form_to_history.py."""
    rows = list(csv.reader(StringIO(csv_text)))
    result = {}

    for row in rows:
        if not row:
            continue
        matched_key = matched_j = None
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

    col_index_by_product = {}
    header_row_idx = None
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            norm = _normalize(cell)
            if norm in COSTOS_PRODUCTO_COLUMNS:
                col_index_by_product[COSTOS_PRODUCTO_COLUMNS[norm]] = j
        if len(col_index_by_product) >= 2:
            header_row_idx = i
            break

    if header_row_idx is not None and col_index_by_product:
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

    transporte = _extract_costo_total_transporte(rows)
    if transporte:
        result["costo_total_transporte"] = transporte

    return result


def _extract_costo_total_transporte(rows: list) -> dict:
    label_row_idx = label_col_idx = None
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if _normalize(cell) == "costo total transporte":
                label_row_idx, label_col_idx = i, j
                break
        if label_row_idx is not None:
            break
    if label_row_idx is None:
        return {}

    product_col = label_col_idx + 1
    value_col = label_col_idx + 2
    values = {}
    window_start = max(0, label_row_idx - 2)
    window_end = min(len(rows), label_row_idx + 3)
    for row in rows[window_start:window_end]:
        if product_col >= len(row):
            continue
        norm = _normalize(row[product_col])
        if norm not in COSTOS_PRODUCTO_COLUMNS:
            continue
        if value_col < len(row):
            val = to_float(row[value_col])
            if val is not None:
                values[COSTOS_PRODUCTO_COLUMNS[norm]] = val
    return values


def parse_mercado_exterior_csv(csv_text: str) -> dict:
    rows = list(csv.reader(StringIO(csv_text)))
    precios: dict = {}
    base: dict = {}

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
                break
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


COSTOS_SHEET_PARSERS = {"mercado_exterior": parse_mercado_exterior_csv}


# --- flatten + CSV read/write --------------------------------------------

def flatten(prefix: str, value) -> list[tuple[str, float]]:
    """Turns a parsed dict (however deeply nested) into (path, value)
    pairs, dot-joining keys along the way — e.g. {"precios": {"chicago":
    {"aceite": 5.1}}} -> [("precios.chicago.aceite", 5.1)]."""
    rows = []
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else k
            rows.extend(flatten(path, v))
    elif value is not None:
        rows.append((prefix, value))
    return rows


def read_existing_rows() -> list[dict]:
    if not OUT_PATH.exists():
        return []
    with OUT_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(rows: list[dict]) -> None:
    rows_sorted = sorted(rows, key=lambda r: (r["date"], r["route"], r["path"]))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows_sorted)


def main():
    today = date.today().isoformat()
    existing_rows = read_existing_rows()
    fetched_routes = set()
    new_rows_by_route: dict[str, list[dict]] = {}

    for route_key, url in COSTOS_SHEETS.items():
        csv_text = fetch_csv(url)
        if csv_text is None:
            print(f"[{route_key}] URL not set or fetch failed — leaving existing history untouched.")
            continue

        parser = COSTOS_SHEET_PARSERS.get(route_key, parse_costos_sheet_csv)
        parsed = parser(csv_text)
        if not parsed:
            print(f"[{route_key}] fetched sheet but found no matching data — leaving existing history untouched.")
            continue

        rows = [
            {"date": today, "route": route_key, "path": path, "value": value}
            for path, value in flatten("", parsed)
        ]
        new_rows_by_route[route_key] = rows
        fetched_routes.add(route_key)
        print(f"[{route_key}] parsed {len(rows)} value(s) for {today}.")

    if not fetched_routes:
        sys.exit("ERROR: No routes could be fetched/parsed. Aborting without writing.")

    # Drop any pre-existing rows for (today, route) for the routes we just
    # successfully re-fetched, then add the fresh ones — makes the script
    # safe to re-run the same day (corrects rather than duplicates).
    kept_rows = [
        r for r in existing_rows
        if not (r["date"] == today and r["route"] in fetched_routes)
    ]
    for route_rows in new_rows_by_route.values():
        kept_rows.extend(route_rows)

    write_rows(kept_rows)
    print(f"Wrote {len(kept_rows)} total row(s) to {OUT_PATH} ({len(fetched_routes)} route(s) updated for {today}).")


if __name__ == "__main__":
    main()
