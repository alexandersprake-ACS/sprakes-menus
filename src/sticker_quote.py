"""Sticker chart prices from the forms app's public quote endpoint (Oct 20 order).

Sticker pricing is area-based and lives in the forms app as a versioned grid
behind POST /api/stickers/quote (prices + grid version only — the grid itself
never leaves that server, and is never copied into this repo). The stickers
menu calls it once per size x tier at fetch time; each response carries all
five quantity cards.

Fail loud, like the Airtable guards (spec 6): unreachable endpoint, an error
envelope, a missing or non-numeric price, an oversize size, or mixed grid
versions inside one fetch all raise SystemExit, so CI never publishes and the
previous menus stay live. The cache is written only after the whole chart
validates, so a failed fetch can never leave a partial file behind.
"""
import json
import os
import time
import urllib.error
import urllib.request

CACHE = os.path.join(os.path.dirname(__file__), "..", "build-cache", "sticker_quotes.raw.json")
TIERS = ("Standard", "Premium", "Connoisseur")

# The endpoint rate-limits at 30 requests/min per IP; a full chart is 33 calls.
PACE_SECONDS = 2.2
MAX_RETRIES = 4


def _key(size):
    return f'{size["w"]}x{size["h"]}'


def _post(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", "user-agent": "sprakes-menus-build"})
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES:
                wait = float(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
                print(f"  sticker quote rate-limited; retrying in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise SystemExit(f"FATAL: sticker quote endpoint returned HTTP {e.code} "
                             f"for {body} — refusing to publish.")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < MAX_RETRIES:
                time.sleep(5 * (attempt + 1))
                continue
            raise SystemExit(f"FATAL: sticker quote endpoint unreachable ({e}) — "
                             f"refusing to publish.")
        except json.JSONDecodeError:
            raise SystemExit(f"FATAL: sticker quote endpoint returned non-JSON for {body} "
                             f"— refusing to publish.")
    raise SystemExit("FATAL: sticker quote endpoint kept rate-limiting — refusing to publish.")


def validate(chart, quotes):
    """Every configured size x tier x qty present, numeric and positive; one
    grid version throughout. Returns the version. Raises on any gap."""
    if not isinstance(quotes, dict) or not quotes.get("version"):
        raise SystemExit("FATAL: sticker quote cache has no grid version — refusing to publish.")
    qtys = [str(q) for q in chart["qtys"]]
    for tier in TIERS:
        for size in chart["sizes"]:
            got = (quotes.get("prices", {}).get(tier) or {}).get(_key(size))
            if not isinstance(got, dict):
                raise SystemExit(f"FATAL: no sticker quote for {tier} {_key(size)} — "
                                 f"refusing to publish.")
            for q in qtys:
                p = got.get(q)
                if isinstance(p, bool) or not isinstance(p, (int, float)) or p <= 0:
                    raise SystemExit(f"FATAL: sticker quote {tier} {_key(size)} @{q} is "
                                     f"{p!r} (missing or non-numeric) — refusing to publish.")
    return quotes["version"]


def fetch(chart):
    """Query the endpoint for the whole chart, validate, then write the cache."""
    url = chart["endpoint"]
    prices, versions = {}, set()
    print(f"Fetching sticker quotes from {url} ...")
    first = True
    for tier in TIERS:
        for size in chart["sizes"]:
            if not first:
                time.sleep(PACE_SECONDS)
            first = False
            body = {"tier": tier, "w": size["w"], "h": size["h"], "qty": chart["qtys"][0]}
            res = _post(url, body)
            if not res.get("ok"):
                raise SystemExit(f"FATAL: sticker quote error for {body}: "
                                 f"{res.get('error')!r} — refusing to publish.")
            d = res.get("data") or {}
            if d.get("oversize"):
                raise SystemExit(f"FATAL: sticker size {_key(size)} is oversize per the "
                                 f"price grid — refusing to publish.")
            versions.add(d.get("version"))
            prices.setdefault(tier, {})[_key(size)] = d.get("cards") or {}
    if len(versions) != 1 or None in versions:
        raise SystemExit(f"FATAL: sticker quotes returned grid versions {sorted(map(str, versions))} "
                         f"in one fetch — refusing to publish.")
    quotes = {"version": versions.pop(), "endpoint": url, "prices": prices}
    validate(chart, quotes)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(quotes, fh, indent=1)
    os.replace(tmp, CACHE)
    print(f"  sticker quotes OK: {sum(len(v) for v in prices.values())} size x tier rows, "
          f"price grid {quotes['version']}")
    return quotes


def load(chart):
    if not os.path.exists(CACHE):
        raise SystemExit("FATAL: sticker quote cache missing — run a fetch first "
                         "(python -m src.build --fetch or --fetch-quotes).")
    with open(CACHE, encoding="utf-8") as fh:
        quotes = json.load(fh)
    validate(chart, quotes)
    return quotes
