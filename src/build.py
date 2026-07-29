"""Build the static menu site: fetch -> model -> render -> PDF, with DIFF and
a hard forbidden-string gate. Output is pure static files under ./site.

Usage:
  python -m src.build                 # build from cache (fails if cache absent)
  python -m src.build --fetch         # fetch Airtable first (needs AIRTABLE_TOKEN)
  python -m src.build --no-pdf        # skip PDF export (faster, HTML only)
  python -m src.build --diff          # print price changes vs last snapshot, then build
  python -m src.build --index         # publish indexable (override noindex default)
"""
import argparse
import datetime
import json
import os
import shutil
import sys

from . import airtable, model, pdf, render

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE = os.path.join(ROOT, "site")
CFG = os.path.join(ROOT, "config")
CACHE = os.path.join(ROOT, "build-cache")
SNAP = os.path.join(CACHE, "price-snapshot.json")


def load_cfg():
    with open(os.path.join(CFG, "site.json"), encoding="utf-8") as fh:
        site = json.load(fh)
    with open(os.path.join(CFG, "disclaimers.json"), encoding="utf-8") as fh:
        disc = json.load(fh)
    site["footer_discount"] = disc["footer_discount"]
    return site


def compute_snapshot(data):
    snap = {}
    for sku, f in data.products.items():
        snap[sku] = {
            "ref": f.get(airtable.F["ref_price"]),
            "breaks": {str(q): p for q, p in sorted(data.breaks.get(sku, {}).items())},
        }
    return snap


def sku_to_menu():
    menus = json.load(open(os.path.join(CFG, "menus.json"), encoding="utf-8"))["menus"]
    pairs = []  # (prefix, title)
    for m in menus:
        for p in (m.get("prefix"), m.get("cap_prefix"), m.get("wrap_prefix")):
            if p:
                pairs.append((p, m["title"]))
    pairs.sort(key=lambda x: -len(x[0]))  # longest prefix wins

    def lookup(sku):
        for p, t in pairs:
            if sku.startswith(p):
                return t
        return "(unlisted)"
    return lookup


def diff_snapshots(old, new):
    lookup = sku_to_menu()
    changes = []
    for sku, cur in sorted(new.items()):
        prev = old.get(sku)
        if prev is None:
            changes.append((lookup(sku), sku, "NEW", "", cur.get("ref")))
            continue
        if prev.get("ref") != cur.get("ref"):
            changes.append((lookup(sku), sku, "ref", prev.get("ref"), cur.get("ref")))
        pb, cb = prev.get("breaks", {}), cur.get("breaks", {})
        for q in sorted(set(pb) | set(cb), key=lambda x: int(x)):
            if pb.get(q) != cb.get(q):
                changes.append((lookup(sku), sku, f"@{q}", pb.get(q), cb.get(q)))
    for sku in sorted(set(old) - set(new)):
        changes.append((lookup(sku), sku, "REMOVED", old[sku].get("ref"), ""))
    return changes


def render_index(models, site, build_date):
    """The index is the interactive hub: all 12 menus in one page behind a tab
    bar (hash-routed, no full page loads). Standalone pages + PDFs unchanged."""
    return render.render_hub(models, site, build_date)


def forbidden_scan(site):
    bad = site.get("forbidden_strings", [])
    hits = []
    for fn in os.listdir(SITE):
        if not fn.endswith(".html"):
            continue
        text = open(os.path.join(SITE, fn), encoding="utf-8").read()
        for b in bad:
            if b in text:
                hits.append((fn, b))
    return hits


def copy_assets():
    dst = os.path.join(SITE, "assets")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(ROOT, "assets"), dst)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="fetch Airtable via AIRTABLE_TOKEN first")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--diff", action="store_true", help="print price changes vs last snapshot")
    ap.add_argument("--index", action="store_true", help="publish indexable (override noindex)")
    ap.add_argument("--noindex", action="store_true", help="force noindex")
    args = ap.parse_args(argv)

    site = load_cfg()
    if args.index:
        site["noindex"] = False
    if args.noindex:
        site["noindex"] = True

    if args.fetch:
        token = os.environ.get("AIRTABLE_TOKEN")
        if not token:
            raise SystemExit("FATAL: --fetch requires AIRTABLE_TOKEN in the environment.")
        print("Fetching Airtable ...")
        airtable.fetch_all(token)

    cache = airtable.load_cache()
    models, warns, data = model.build_models(cache, site)

    # content-hash the stylesheet so browsers pick up CSS changes after deploys
    import hashlib
    with open(os.path.join(ROOT, "assets", "css", "menu.css"), "rb") as fh:
        site["asset_v"] = hashlib.md5(fh.read()).hexdigest()[:8]

    build_date = datetime.date.today().strftime("%B %-d, %Y")

    # DIFF against previous snapshot (before overwriting)
    new_snap = compute_snapshot(data)
    if args.diff:
        old_snap = json.load(open(SNAP, encoding="utf-8")) if os.path.exists(SNAP) else {}
        changes = diff_snapshots(old_snap, new_snap)
        print(f"\n=== PRICE DIFF ({len(changes)} change(s) since last build) ===")
        for menu, sku, where, old, new in changes:
            print(f"  [{menu}] {sku} {where}: {old} -> {new}")
        if not changes:
            print("  (no price changes)")
        print()

    # render
    os.makedirs(SITE, exist_ok=True)
    for m in models:
        out = os.path.join(SITE, m["slug"] + ".html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(render.render_menu(m, site, build_date))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(render_index(models, site, build_date))
    copy_assets()
    if site.get("noindex"):
        # no sitemap; add robots.txt disallow
        with open(os.path.join(SITE, "robots.txt"), "w") as fh:
            fh.write("User-agent: *\nDisallow: /\n")
    else:
        base = (site.get("base_url") or "").rstrip("/")
        robots = "User-agent: *\nAllow: /\n"
        if base:
            robots += f"Sitemap: {base}/sitemap.xml\n"
            urls = [f"{base}/"] + [f"{base}/{m['slug']}.html" for m in models]
            today = datetime.date.today().isoformat()
            with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as fh:
                fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                         '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
                for u in urls:
                    fh.write(f"  <url><loc>{u}</loc><lastmod>{today}</lastmod></url>\n")
                fh.write("</urlset>\n")
        with open(os.path.join(SITE, "robots.txt"), "w") as fh:
            fh.write(robots)

    # hard gate: forbidden strings (spec 5.1 / 8.7)
    hits = forbidden_scan(site)
    if hits:
        for fn, b in hits:
            print(f"  FORBIDDEN STRING '{b}' found in {fn}", file=sys.stderr)
        raise SystemExit("FATAL: forbidden strings present — refusing to publish.")

    # PDFs
    if not args.no_pdf:
        chrome = pdf.find_chrome()
        os.makedirs(os.path.join(SITE, "pdf"), exist_ok=True)
        for m in models:
            pdf.html_to_pdf(chrome, os.path.join(SITE, m["slug"] + ".html"),
                            os.path.join(SITE, "pdf", m["slug"] + ".pdf"))
        print(f"  exported {len(models)} PDFs")

    # persist snapshot for next DIFF
    with open(SNAP, "w", encoding="utf-8") as fh:
        json.dump(new_snap, fh, indent=1)

    print(f"\nBuilt {len(models)} menus -> {SITE}  (build date {build_date}, "
          f"noindex={site.get('noindex')})")
    if warns:
        print("\nBuild warnings / data gaps:")
        for w in warns:
            print("  ⚠ " + w)


if __name__ == "__main__":
    main()
