# Sprake's — Menu Generator

Generates the 12 branded menus as **web pages + print PDFs** from Airtable, so a
price change in Airtable propagates to every menu with one rebuild — no manual
re-typesetting. Built to `Sprakes-Menu-Generator-Spec.md`; §8 acceptance checks
are automated (`tests/acceptance.py`).

## What it produces

`site/` — deployable static output:
- `index.html` + 12 menu pages (`glass-jars.html`, `terpenes.html`, …)
- `pdf/*.pdf` — one Letter-portrait print PDF per menu
- `assets/` (fonts, background, CSS), `robots.txt`

Every page and PDF footer carries **"Prices current as of {build date}."**

## How it works

```
Airtable ──fetch──▶ build-cache/*.raw.json ──model──▶ render ──▶ site/*.html ──Chrome──▶ site/pdf/*.pdf
         (REST, PAT)                        (§5 rules)          (mobile-first)   (print)
```

- **Data:** base `appPAgwMs1Sy1z4v6` — Products, Price Breaks, Categories. Prices
  are shown **exactly as stored** (`$X,XXX.XX`), never recomputed. Break `@N` = price
  at quantity N and up; columns are always sorted ascending.
- **Pricing shapes:** break grids (jars/bags/tubes/stickers, +cap add-ons), the O2
  deep grid (+wrap), the terpene per-mL schedule + blends + marketing flavor list,
  the split Infusion presentation, and flat Design/Package prices.
- **Design:** one shared layout + brand background (recovered from the approved
  July-14 menu). Poppins is bundled in `assets/fonts` (SIL OFL). PDF export is
  headless **Chrome** (WeasyPrint's native libs aren't available on this machine;
  Chrome is the spec's §1.3 primary path anyway).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Chrome or Chromium must be installed (used for PDF export).
```

## Rebuild

```bash
# From live Airtable (needs a read-only PAT):
export AIRTABLE_TOKEN=pat_xxx
python -m src.build --fetch

# From the committed cache (no token needed — offline):
python -m src.build

# Faster HTML-only (skip PDFs):
python -m src.build --no-pdf

# Refresh only the sticker chart prices (no Airtable token needed):
python -m src.build --fetch-quotes
```

The build **fails, does not publish**, if Airtable is unreachable, a family returns
zero products, or a cached fetch is incomplete (a completeness guard catches
dropped/paginated records — see `src/airtable.py`).

**Sticker chart (Oct 20 ruling).** The stickers page is NOT priced from Airtable.
Sticker pricing is area-based and lives in the forms app as a versioned grid; the
build calls its public price-only endpoint (`POST /api/stickers/quote` on
order.sprakes-usa.com, one call per size × tier, paced under its 30/min limit)
for the preset sizes listed in `config/menus.json` → stickers → `chart` (display
labels only — the grid is never copied here). `--fetch` implies `--fetch-quotes`.
Unreachable endpoint, an error, a missing/non-numeric price, an oversize size, or
mixed grid versions → **FATAL, nothing publishes** (`src/sticker_quote.py`). The
grid version prints in the stickers footer ("Price grid 2026-10-19") and rides
the DIFF audit as `STKR-GRID` (version) + `STKR-GRID-{TIER}-{SIZE}` (cells).
The old 33 `STKR-{TIER}-{SIZE}` items no longer feed any page (they are archived
and dropped by the archive guard below). `STKR-CUSTOM-*` (one-per-tier forms
items) are excluded site-wide by `global_exclude_prefixes` in `config/site.json`.
The page states the honest range: any size up to 240 sq in priced by area
(the endpoint returns call-for-quote above that), larger sizes quoted.

**Guards (all fail loud, all pinned by acceptance checks).**
- *Archive guard* (check 18): a product whose Notes begin `ARCHIVED` is dropped
  at load with its breaks — never on a page, PDF, dressed price, or the DIFF
  snapshot. Config that still points at an archived SKU (an exact SKU string, or
  an archived SKU under a `dressed_components` prefix) is FATAL. The menus never
  read On Form, by design: jar-alone, CAP and CAPW are Off Form and must render.
- *Supplier guard* (checks 7, 19): the Suppliers table is fetched and
  cursor/count-checked like Products; every Supplier Name is forbidden unless
  allow-listed as a customer-facing brand (`supplier_brand_allow`: Miron, O2 VAPE,
  Puffco). The static `forbidden_strings` stay. Scanned case-insensitively over
  every HTML page AND every PDF's text (the PDFs are what get forwarded).
- *One-stop-shop copy guard* (check 20): `copy_forbidden_patterns` (in-house,
  outsourced, external production, sourced locally, vendor, supplier, …) never
  appear in rendered copy, HTML text or PDF text.
- The price stamp ("Prices current as of …") and sitemap dates use the shop's
  day, America/Los_Angeles — not the runner's UTC date.

## Price-change audit (DIFF)

```bash
python -m src.build --diff        # prints [menu] SKU @qty: old -> new since last build
```

Compares current data to `build-cache/price-snapshot.json` (updated each build).

## Publishing / indexing

Menus publish **publicly and indexable** (owner decision, 2026-07-17).
`config/site.json` sets `"noindex": false` and `robots.txt` allows
crawling with a sitemap. To publish with noindex instead:

```bash
python -m src.build --noindex    # or set "noindex": true in config/site.json
```

## Deploy (GitHub Pages)

`.github/workflows/deploy.yml` fetches Airtable, builds, runs acceptance, and
deploys `site/` to Pages — on manual dispatch, on push to `main`, and nightly
(cron 08:00 UTC; GitHub queues scheduled runs, so they actually start around
11:45–15:05 UTC — ruled fine as is). Set repo secret **`AIRTABLE_TOKEN`** and enable Pages (source:
GitHub Actions). Add the 12 page URLs (or the single index URL) to Linktree.

## Editing content

- **Flavor list** (Terpenes + Infusion) = a UNION (R, Oct 21): the curated
  marketing groups in `config/marketing-flavors.json` (Fruit 45 / Candy 46 /
  Expanded 155 = 246, names **verbatim**, never normalize) + every `TERP-BOT-*`
  flavor in the base (stocked or not — stock never matters on the menu) that is
  not already represented, appended under its bottle spelling (Size/Type) in a
  final **MORE FLAVORS** group. `config/flavor-union.json` maps base SKU →
  marketing entry (23 exact + 24 riff + 77 Owner-styled, all from the Sep 30
  reconciliation) and holds the five §5.3 pair-names (CANDY, SNACKS, STRAWBERRY
  FRUIT, CHERRY VANILLA SKUNK, TROPICAL FRUIT) — the only base flavors skipped,
  by name. A flavor staff add at the kiosk is on the menu the next night. Map a
  new flavor to a marketing entry only to restyle it; an unmapped flavor whose
  name equals an entry already on the menu is shown once and warned (check 21).
- **Disclaimers / footer**: `config/disclaimers.json` (sanitized terpene
  disclaimer — no third-party names; the "3% cash/Zelle discount" line is verbatim).
- **Menu structure / boilerplate**: `config/menus.json`. The Infusion `500+ =
  INQUIRE` row lives here (it has no price and no Airtable break, by design); all
  priced tiers come live from Airtable.
- **Forbidden strings / site settings**: `config/site.json` (static forbidden
  strings + base supplier names + copy patterns, see Guards above).
- **Three-way pages** (Chubby, Miron): `consolidated_ways` in `config/menus.json`
  (one table per tier; rows = size × way trios; Miron matches on SKU suffix).
  `pre_families` / `post_families` are separate add-on sections — today only the
  O2 box stickers (`post_families`, rows name the shape: check 22).

## Acceptance checks

```bash
python -m tests.acceptance        # runs the acceptance suite against the built site
```

36 checks as of Oct 21, 2026. A check whose name carries `[DATA]` is a
non-blocking data gap (only 17b can become one, after a sticker-grid re-survey).

## Standing rulings (do not re-flag)

- **No links on the menus, ever** (R, Oct 21). The menus serve the smoke-shop
  supply trade and stay separate from the website and the order site — no order,
  quote, /stickers or staff links. Only DOWNLOAD PDF.
- **Media gates** (no tubes/vapes/droppers) govern the public website's assets,
  not the menus: the menus are where those products are marketed (Oct 14).
- **Cap stickers are $0.35 per touch**; "UP TO 3 STICKERS" on jars is correct.
- **SEAL-MIRON stays off the page** (chosen in the order flow; not in dressed prices).
- **"colour"** in the tier definitions is the Owner's verbatim spelling.
- **Nightly timing** as observed (see Deploy) is fine.

## Layout / project map

```
config/     menus, marketing flavors, disclaimers, site settings
src/        airtable (fetch+cache+guards) · model (§5 rules) · render · pdf · build
assets/     fonts (Poppins), bg (brand background), css
reference/  the 12 source PDFs (visual target)
build-cache/ raw Airtable fetch + price snapshot
scripts/    recover_bg.py (optional per-menu background recovery)
tests/      acceptance.py (§8)
site/       generated output (git-ignored)
```
