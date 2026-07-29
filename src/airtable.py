"""Airtable access: build-time REST fetch (standalone, PAT) + cache loader.

Two entry points:
  * fetch_all(token)  -> hits api.airtable.com, writes REST-shaped JSON to build-cache/
  * load_cache()      -> reads build-cache/, returns normalized records

The loader tolerates BOTH record shapes so the same cache works whether it was
written by fetch_all (REST: {"fields": {...}}) or seeded from the Airtable MCP
({"cellValuesByFieldId": {...}}). Field values are normalized (singleSelect ->
name string) and de-mojibaked.
"""
import json
import os
import time
import urllib.parse
import urllib.request

BASE_ID = "appPAgwMs1Sy1z4v6"

# --- Table + field IDs (from the spec / base schema) ---
T_PRODUCTS = "tblp8UkHqUwEIsNEz"
T_BREAKS = "tblfECeq4Zanuc4FY"
T_CATEGORIES = "tblcp9r1nvsSkRKM8"

F = {
    "sku": "fldYd6kDMtWPxZz8Z",
    "name": "fld2qqIi6HZBE3W1S",
    "tier": "fldSUTX2fzLTcGWG5",
    "size": "fldaVVjw26lQBpCPJ",
    "ref_price": "fldZDzFhIxRDebHsY",
    "unit": "fldHr6ozaLPldU4WV",
    "category": "fld2hEdNlmUa4ip4m",
    # price breaks
    "break": "fldhI64u3nhBh5LmX",
    "qty": "fldAVCZaPO0DVaRrz",
    "price": "fld9tTFDj0exJxj9n",
    "break_product": "fldjYfG7MUPb4Bfy8",
    # categories
    "cat_name": "fldVa5NVDGLD7YDev",
}

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "build-cache")
TABLES = {
    "products": (T_PRODUCTS, "products.raw.json"),
    "price_breaks": (T_BREAKS, "price_breaks.raw.json"),
    "categories": (T_CATEGORIES, "categories.raw.json"),
}


def demojibake(s):
    """Repair double-mojibaked text (e.g. '‚Äî' -> '—').

    Clean ASCII round-trips to itself; genuine UTF-8 punctuation raises on the
    mac_roman/utf-8 round-trip and is returned unchanged.
    """
    if not isinstance(s, str):
        return s
    try:
        return s.encode("mac_roman").decode("utf-8")
    except (UnicodeError, ValueError):
        return s


def _norm_value(v):
    if isinstance(v, dict):  # singleSelect / linked-as-object
        return demojibake(v.get("name"))
    if isinstance(v, list):  # multiple selects / linked records
        return [_norm_value(x) for x in v]
    return demojibake(v)


def _norm_record(rec):
    fields = rec.get("fields")
    if fields is None:
        fields = rec.get("cellValuesByFieldId", {})
    return {"id": rec.get("id"), "f": {k: _norm_value(v) for k, v in fields.items()}}


def load_cache():
    """Return {'products': [...], 'price_breaks': [...], 'categories': [...]}.

    Each item is {'id': recId, 'f': {fieldId: value}}. Raises if a cache file is
    missing or a table is empty (spec 6: fail, do not publish).
    """
    out = {}
    for key, (_tid, fname) in TABLES.items():
        path = os.path.join(CACHE_DIR, fname)
        if not os.path.exists(path):
            raise SystemExit(
                f"FATAL: cache file missing: {fname}. Run a fetch first "
                f"(python -m src.build --fetch) or seed build-cache/."
            )
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        recs = [_norm_record(r) for r in data.get("records", [])]
        if not recs:
            raise SystemExit(f"FATAL: table '{key}' returned zero records — refusing to publish.")
        # Completeness guard (spec §6): a partial fetch must FAIL, never publish.
        # A leftover pagination cursor or a short count means records were dropped.
        if data.get("nextCursor"):
            raise SystemExit(
                f"FATAL: cache '{fname}' is truncated — an unfollowed pagination "
                f"cursor is present ({len(recs)} records loaded). Re-fetch the full table."
            )
        total = (data.get("metadata") or {}).get("totalRecordCount")
        if total is not None and len(recs) < total:
            raise SystemExit(
                f"FATAL: cache '{fname}' is incomplete — {len(recs)} of {total} records. "
                f"A fetch dropped records; refusing to publish."
            )
        out[key] = recs
    return out


# --------------------------------------------------------------------------
# Standalone REST fetch (used when AIRTABLE_TOKEN is set; not needed if the
# cache is already seeded). Respects Airtable's 5 req/s limit.
# --------------------------------------------------------------------------
def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _fetch_table(table_id, token):
    records = []
    offset = None
    while True:
        params = {"pageSize": 100, "returnFieldsByFieldId": "true"}
        if offset:
            params["offset"] = offset
        url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}?" + urllib.parse.urlencode(params)
        data = _get(url, token)
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.25)  # stay under 5 req/s
    return records


def fetch_all(token):
    """Fetch all three tables via REST and write REST-shaped cache files.

    FAILS (raises) if a table returns zero records or the API is unreachable.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    for key, (tid, fname) in TABLES.items():
        records = _fetch_table(tid, token)
        if not records:
            raise SystemExit(f"FATAL: Airtable table '{key}' returned zero records — aborting build.")
        with open(os.path.join(CACHE_DIR, fname), "w", encoding="utf-8") as fh:
            json.dump({"records": records, "metadata": {"totalRecordCount": len(records)}}, fh, indent=1)
        print(f"  fetched {key}: {len(records)} records -> {fname}")
        time.sleep(0.25)
