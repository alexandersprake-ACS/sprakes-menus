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
```

The build **fails, does not publish**, if Airtable is unreachable, a family returns
zero products, or a cached fetch is incomplete (a completeness guard catches
dropped/paginated records — see `src/airtable.py`).

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
(08:00 UTC). Set repo secret **`AIRTABLE_TOKEN`** and enable Pages (source:
GitHub Actions). Add the 12 page URLs (or the single index URL) to Linktree.

## Editing content

- **Marketing flavor list** (Terpenes + Infusion): `config/marketing-flavors.json`
  — grouped Fruit / Candy / Expanded, names **verbatim** (never normalize). Build
  fails loudly if the file is missing.
- **Disclaimers / footer**: `config/disclaimers.json` (sanitized terpene
  disclaimer — no third-party names; the "3% cash/Zelle discount" line is verbatim).
- **Menu structure / boilerplate**: `config/menus.json`. The Infusion `500+ =
  INQUIRE` row lives here (it has no price and no Airtable break, by design); all
  priced tiers come live from Airtable.
- **Forbidden strings / site settings**: `config/site.json` (the build scans the
  whole site and fails if any supplier/sourcing name or `$0.00` appears).

## Acceptance checks

```bash
python -m tests.acceptance        # runs the §8 checks against the built site
```

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
