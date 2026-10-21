"""Automated §8 acceptance checks. Run after a build:  python -m tests.acceptance
Reads the built ./site (HTML + PDFs) and the data model. Exits non-zero if any
REQUIRED check fails. Data-gap checks are reported distinctly.
"""
import json
import os
import re
import sys

import fitz  # pymupdf

from src import airtable, model, build

ROOT = build.ROOT
SITE = build.SITE
results = []  # (ok, name, detail)


def check(name, ok, detail=""):
    results.append((bool(ok), name, detail))


def html(slug):
    return open(os.path.join(SITE, slug + ".html"), encoding="utf-8").read()


def pdf_text(slug):
    d = fitz.open(os.path.join(SITE, "pdf", slug + ".pdf"))
    t = "".join(p.get_text() for p in d)
    d.close()
    return t


def main():
    site = build.load_cfg()
    cache = airtable.load_cache()
    models, warns, data = model.build_models(cache, site)
    mm = {m["slug"]: m for m in models}

    # 1. MIRON-STD-SAT-250 @100 = $4.84
    price = data.breaks.get("MIRON-STD-SAT-250", {}).get(100)
    dressed250 = model.money(round(price + data.breaks["CAP-MIRON-STD"][100]
                                   + data.breaks["CAPW-MIRON-STD"][100] + 1e-9, 2))
    check("1 MIRON-STD-SAT-250 @100 = $4.84 (base; page shows dressed)",
          model.money(price) == "$4.84" and dressed250 in html("miron-jars"),
          f"model={model.money(price)} dressed={dressed250}")

    # 2. MYLS2B-STD-D-1LB @5000=$10.30 & @10000=$9.79 ascending
    b = data.breaks.get("MYLS2B-STD-D-1LB", {})
    qtys = data.qtys(["MYLS2B-STD-D-1LB"])
    i5, i10 = qtys.index(5000), qtys.index(10000)
    check("2 MYLS2B-STD-D-1LB @5000=$10.30 @10000=$9.79 (ascending)",
          model.money(b.get(5000)) == "$10.30" and model.money(b.get(10000)) == "$9.79" and i5 < i10,
          f"5000={model.money(b.get(5000))} 10000={model.money(b.get(10000))} order_ok={i5 < i10}")

    # 3. Design flat prices + PKG-T3-10
    h = html("brand-development")
    want = {"DSGN-T1": "$154.50", "DSGN-T2": "$360.50", "DSGN-T3": "$515.00", "PKG-T3-10": "$3,038.50"}
    ok3 = all(model.money(data.ref_of(s)) == v and v in h for s, v in want.items())
    check("3 Design/Package flat prices", ok3,
          " ".join(f"{s}={model.money(data.ref_of(s))}" for s in want))

    # 3b. Tier definitions + revision terms (Sep 1 owner rulings): the fine
    # print is the customer-facing basis for revision enforcement — its
    # absence creates disputes, so it is gated here.
    need3b = ["FREE CONSULTATION", "PATTERN WORK", "Basic illustrations",
              "Partial illustrations", "Full hand-drawn illustrations",
              "30-day", "60-day", "inquire for pricing on larger orders"]
    check("3b Design tier definitions + revision fine print",
          all(x in h for x in need3b), str([x for x in need3b if x not in h]))

    # 4. Infusion table (split presentation)
    hi = html("terpene-infusion")
    sec = mm["terpene-infusion"]["sections"][0]
    ranges = {t["range"]: t["price"] for t in sec["tiers"]}
    check("4a Infusion 1-4 = $231.75", ranges.get("1-4") == "$231.75", str(ranges.get("1-4")))
    check("4b Infusion '+ terpenes $25.80 / LB' note", "$25.80 / LB" in hi.upper() or "$25.80 / lb" in hi.lower())
    check("4c Infusion own-terpenes line", "Service price only" in hi)
    check("4d Infusion rush = $309", sec.get("rush") == "$309" and "$309" in hi, str(sec.get("rush")))
    check("4e Infusion quantities labeled in lb", "lb" in hi and "PER LB" in hi.upper())
    check("4f Infusion 300-500 = $103.00", ranges.get("300-500") == "$103.00" and "$103.00" in hi,
          str(ranges.get("300-500")))
    check("4g Infusion 500+ = INQUIRE", ranges.get("500+") == "INQUIRE" and "INQUIRE" in hi,
          str(ranges.get("500+")))
    check("4h Infusion shows all 8 tiers", len(sec["tiers"]) == 8, f"{len(sec['tiers'])} tiers")
    # strength options are named, with the price reassurance; the internal mL
    # draw figures (10/20/30 mL per lb) must NEVER appear on the menu
    check("4i Infusion strength note (no mL figures)",
          "CHOOSE YOUR STRENGTH" in hi.upper()
          and "same price at any strength" in hi
          and "mL" not in sec["flavor_note"][0] + sec["flavor_note"][1],
          str(sec.get("flavor_note")))

    # 5. O2 deep grid 50 -> 100,000
    ho = html("o2-devices")
    o2cols = data.qtys(data.family("DEV-O2-"))
    check("5 O2 grid 50→100,000", 50 in o2cols and 100000 in o2cols and "100,000" in ho,
          f"cols={o2cols}")

    # 6. Terpenes: TERP-CDT @5mL=$25.75 + flavors sourced from marketing json only
    cdt = data.breaks.get("TERP-CDT", {}).get(5)
    flavors_cfg = json.load(open(os.path.join(ROOT, "config", "marketing-flavors.json"), encoding="utf-8"))
    ht = html("terpenes")
    # spot-check: an internal SKU flavor token that is NOT a marketing name must be absent
    internal_only = "GOLDENGRAMS"  # internal spelling; marketing uses different names
    check("6a TERP-CDT @5mL = $25.75", model.money(cdt) == "$25.75", f"model={model.money(cdt)}")
    leak6b = internal_only in ht.upper() or "TERP-BOT-" in ht
    check("6b no internal flavor SKU tokens on the terpenes page", not leak6b,
          "internal SKU token leaked" if leak6b else "clean")

    # 6c. Marketing flavor list: confirmed counts (owner sign-offs 2026-07-17
    # and 2026-09-30: +77 reconciled base flavors; the five spec-5.3 pair-names
    # are deliberately withheld from the customer menu)
    counts = {g["name"]: len(g["flavors"]) for g in flavors_cfg["groups"]}
    want_counts = {"FRUIT": 45, "CANDY": 46, "EXPANDED FLAVORS": 155}
    check("6c Flavor group counts 45/46/155", counts == want_counts, str(counts))

    # 6d. Verbatim spellings preserved (intentional; never normalized) on BOTH
    # flavor-bearing menus.
    verbatim = ["Oero Cheesecake", "Krispy Kream", "Cherry Kool-Aide", "Ferro Rocher",
                "Fruit Burst*", "Bubblegum Hubba Bubba*"]
    ok6d = all(v in ht and v in hi for v in verbatim)
    check("6d Verbatim flavor spellings on both menus", ok6d,
          str([v for v in verbatim if v not in ht or v not in hi]))

    # 6e. Approved disclaimer wording, sourced from config, on both menus.
    disc_body = json.load(open(os.path.join(ROOT, "config", "disclaimers.json"),
                               encoding="utf-8"))["terpene_disclaimer"]["body"]
    key_line = "Sprake&#x27;s cannot be liable should any test fail."
    ok6e = ("cannot be liable should any test fail." in disc_body
            and key_line in ht and key_line in hi)
    check("6e Approved disclaimer wording on both menus", ok6e)

    # 7. forbidden strings absent across whole site
    hits = build.forbidden_scan(site, build.forbidden_terms(site, data))
    check("7 no forbidden strings site-wide (HTML + PDF, incl. base supplier names)", not hits, str(hits))

    # 8. DIFF names exactly the changed menu/item/old/new
    snap = build.compute_snapshot(data)
    mutated = json.loads(json.dumps(snap))
    mutated["MIRON-STD-SAT-250"]["breaks"]["100"] = 9.99
    changes = build.diff_snapshots(snap, mutated)  # old=snap, new=mutated
    ok8 = (len(changes) == 1 and changes[0][1] == "MIRON-STD-SAT-250"
           and changes[0][2] == "@100" and "miron" in changes[0][0].lower())
    check("8 DIFF isolates one changed price", ok8, str(changes))

    # 12. Fully-dressed pricing (Owner's Component Architecture, Oct 12):
    # headline price = bundle + cap components at the SAME break. One ordered
    # pin (CG 2oz STD @1,000 = 0.98+0.31+0.31 = $1.60) plus a per-family
    # data-identity probe.
    std_tbl = next(t for t in mm["chubby-jars"]["sections"]
                   if t["kind"] == "grid" and t.get("header") == "STANDARD")
    cols12 = [c["qty"] for c in std_tbl["columns"]]
    cg_cells = {r["label"]: r["cells"] for r in std_tbl["rows"]}["2oz — Jar + label"]
    pin_ok = cg_cells[cols12.index(1000)] == "$1.60"

    def dressed_present(slug, sku, comps, q=1000):
        want = model.money(round(data.breaks[sku][q]
                                 + sum(data.breaks[c][q] for c in comps) + 1e-9, 2))
        return want in html(slug)
    fam_ok = all([
        dressed_present("glass-jars", "GLSJAR-STD-CLR-2OZ", ["CAP-GLSJAR-STD", "CAPW-GLSJAR-STD"]),
        dressed_present("concentrate-jars", "CONJAR-STD-5ML", ["CAP-CONJAR-STD", "CAPW-CONJAR-STD"]),
        dressed_present("preroll-tubes", "TUBE-STD-99-POP", ["CAP-TUBE-STD"]),
        dressed_present("miron-jars", "MIRON-STD-SAT-250", ["CAP-MIRON-STD", "CAPW-MIRON-STD"]),
    ])
    check("12 fully-dressed pricing (CG $1.60 pin + per-family identity)",
          pin_ok and fam_ok, f"pin={pin_ok} cg={cg_cells} fam={fam_ok}")

    # 13. Application wording (verbatim Owner ruling) on every applied page,
    # old -$0.10 framing gone everywhere, miron trio live, box stickers on o2.
    APPW = "APPLICATION — $0.35 PER STICKER APPLIED. APPLY YOUR OWN? NO FEE."
    menus_cfg = json.load(open(os.path.join(ROOT, "config", "menus.json"), encoding="utf-8"))["menus"]
    applied = ["glass-jars", "concentrate-jars", "chubby-jars", "miron-jars",
               "mylar-bags", "preroll-tubes", "o2-devices"]
    word_ok = all(APPW in html(sl) for sl in applied)
    # probe the old PHRASE, not the bare amount — $0.10 is now a legitimate
    # sticker price (STKR-STD-1X1 @1,000 after the Oct 6 reprice)
    old_gone = all("W/O APPLICATION" not in html(mc["slug"])
                   and "$0.10 / STICKER" not in html(mc["slug"]) for mc in menus_cfg)
    hmn = html("miron-jars")
    trio_m = (all(f"— {w}" in hmn for w in ("Jar alone", "Jar + label", "Label only"))
              and -1 < hmn.find("WHAT THE TIERS MEAN") < hmn.find(">STANDARD<")
              < hmn.find(">PREMIUM<") < hmn.find(">CONNOISSEUR<"))
    singles_m = "SINGLES FROM $" in hmn and "EACH" in hmn
    box_ok = "BOX STICKERS" in html("o2-devices")
    check("13 app wording verbatim + old framing gone + miron trio + box stickers",
          word_ok and old_gone and trio_m and singles_m and box_ok,
          f"word={word_ok} old_gone={old_gone} trio={trio_m} singles={singles_m} box={box_ok}")

    # 14. page-header banners (Oct 13 order): every menu page carries its slug's
    # banner band with explicit intrinsic dimensions (no CLS), the referenced
    # variants exist on disk, and the hub embeds all of them
    import re as _re
    ban = json.load(open(os.path.join(ROOT, "assets", "banners", "manifest.json")))
    bad14 = []
    for m in models:
        slug = m["slug"]
        if slug not in ban:
            bad14.append(f"{slug}: no banner in manifest"); continue
        h = html(slug)
        if f'<div class="banner"' not in h or f'assets/banners/{slug}-' not in h:
            bad14.append(f"{slug}: band missing")
        if f'width="{ban[slug]["w"]}" height="{ban[slug]["h"]}"' not in h:
            bad14.append(f"{slug}: intrinsic dims missing")
        for w in ban[slug]["widths"]:
            for ext in ("avif", "webp"):
                p = os.path.join(SITE, "assets", "banners", f"{slug}-{w}.{ext}")
                if not os.path.exists(p):
                    bad14.append(f"{slug}-{w}.{ext} not shipped")
    hub = html("index")
    n_hub = len(_re.findall(r'<div class="banner"', hub))
    if n_hub != len(models):
        bad14.append(f"hub has {n_hub} banners, want {len(models)}")
    # hero treatment (Oct 14 follow-up): full-bleed cover band, scrim confined
    # to the text zone, image-led hub tiles
    css = open(os.path.join(ROOT, "assets", "css", "menu.css")).read()
    if "object-fit:cover" not in css or "linear-gradient(90deg" not in css:
        bad14.append("hero cover/left-scrim CSS missing")
    n_tiles = len(_re.findall(r'<button class="tab" [^>]*background-image:url', hub))
    if n_tiles != len(models):
        bad14.append(f"hub has {n_tiles} image tiles, want {len(models)}")
    check("14 hero banners on all pages + image-led hub tiles (dims, variants shipped)",
          not bad14, "; ".join(bad14[:6]))

    # 15. per-tier MEDIA/FINISHES subtitles (Oct 15 order): wherever tier sections
    # carry a media line, the three tiers must read differently, and
    # "FINISHES : NONE" may never appear under Premium or Connoisseur
    bad15 = []
    pair_re = _re.compile(
        r'<span class="sechead">([^<]+)</span></div>(?:(?!<section).)*?<div class="media">(.*?)</div>',
        _re.S)
    strip_tags = lambda s: _re.sub(r"<[^>]+>", "", s)
    for m in models:
        pairs = [(h, strip_tags(x)) for h, x in pair_re.findall(html(m["slug"]))]
        tiered = [(h, x) for h, x in pairs
                  if any(t in h for t in ("STANDARD", "PREMIUM", "CONNOISSEUR"))]
        if not tiered:
            continue
        if len({x for _, x in tiered}) != len(tiered):
            bad15.append(f"{m['slug']}: tier media lines not distinct")
        for h, x in tiered:
            if ("PREMIUM" in h or "CONNOISSEUR" in h) and "FINISHES : NONE" in x:
                bad15.append(f"{m['slug']}: FINISHES : NONE under {h}")
    # the fix must actually be present: the 5 known pages carry 3 distinct lines
    for slug in ("glass-jars", "concentrate-jars", "stickers", "mylar-bags", "preroll-tubes"):
        h = html(slug)
        if len(_re.findall(r'<div class="media">', h)) != 3:
            bad15.append(f"{slug}: expected 3 tier media lines")
    # page-level sweep (Oct 15 follow-up): on ANY page selling tiered products,
    # the legacy media string must not appear anywhere, and FINISHES : NONE may
    # only ever occur inside the STANDARD tier's own subtitle. Untiered pages
    # (direct-to-bag) keep their line — FINISHES : NONE is true there.
    tiered_slugs = {m["slug"] for m in models
                    if any("tierdefs" == s["kind"] for s in m["sections"])}
    for slug in sorted(tiered_slugs):
        h = html(slug)
        if "MATTE / GLOSS / PAPER (3MM VINYL)" in h:
            bad15.append(f"{slug}: legacy media string present")
        body = _re.sub(r'<div class="media">MEDIA : REGULAR WHITE VINYL.*?</div>',
                       "", h)  # the one legitimate Standard-subtitle occurrence
        if "FINISHES : NONE" in strip_tags(body):
            bad15.append(f"{slug}: FINISHES : NONE outside the Standard subtitle")
    check("15 per-tier media/finishes distinct; no legacy/NONE leaks on tiered pages",
          not bad15, "; ".join(bad15[:6]))

    # 16. guard-first (Oct 20): STKR-CUSTOM-* (one-per-tier forms items, no
    # breaks) must never load or render. Probe strings come from the cached
    # records themselves, so a base-side rename can't silently defeat the check.
    bad16 = []
    if "STKR-CUSTOM-" not in site.get("global_exclude_prefixes", []):
        bad16.append("guard prefix missing from site.json")
    if any(s.startswith("STKR-CUSTOM-") for s in data.products):
        bad16.append("STKR-CUSTOM loaded into model data")
    snap = json.load(open(os.path.join(ROOT, "build-cache", "price-snapshot.json")))
    if any(s.startswith("STKR-CUSTOM-") for s in snap):
        bad16.append("STKR-CUSTOM in price snapshot")
    probes = {"STKR-CUSTOM-"}
    for r in airtable.load_cache()["products"]:
        if (r["f"].get(airtable.F["sku"]) or "").startswith("STKR-CUSTOM-"):
            for k in ("size", "name"):
                if r["f"].get(airtable.F[k]):
                    probes.add(r["f"][airtable.F[k]])
    for m in models:
        h = html(m["slug"]); t = pdf_text(m["slug"])
        for p in probes:
            if p in h or p in t:
                bad16.append(f"{m['slug']}: renders {p!r}")
    check("16 guard: STKR-CUSTOM-* never loads, renders, or enters the snapshot",
          not bad16, "; ".join(bad16[:6]))

    # 17. sticker formula chart (Oct 20): endpoint-priced, same page as before
    from src import sticker_quote
    stm = next(mc for mc in menus_cfg if mc["slug"] == "stickers")
    chart = stm["chart"]
    quotes = sticker_quote.load(chart)
    ver = quotes["version"]
    hs, ps = html("stickers"), pdf_text("stickers")
    bad17 = []
    smodel = next(mm for mm in models if mm["slug"] == "stickers")
    grids = [s for s in smodel["sections"] if s["kind"] == "grid"]
    if [g["header"] for g in grids] != ["STANDARD", "PREMIUM", "CONNOISSEUR"]:
        bad17.append("tier sections wrong")
    for g in grids:
        if [r["label"] for r in g["rows"]] != [sz["label"] for sz in chart["sizes"]]:
            bad17.append(f"{g['header']}: preset sizes/labels changed")
        if [c["qty"] for c in g["columns"]] != [100, 500, 1000, 5000, 10000]:
            bad17.append(f"{g['header']}: columns changed")
        if any(not _re.fullmatch(r"\$\d+\.\d\d", c) for r in g["rows"] for c in r["cells"]):
            bad17.append(f"{g['header']}: non-price cell")
    # grid version visible on page + PDF; any-size line present; no shop names
    if f"Price grid {ver}" not in hs or f"Price grid {ver}" not in " ".join(ps.split()):
        bad17.append("grid version not shown on page/PDF")
    if chart["any_size_note"] not in hs:
        bad17.append("any-size line missing")
    if "240 SQ IN" not in chart["any_size_note"] or "QUOTED" not in chart["any_size_note"]:
        bad17.append("any-size line must state the 240 sq in limit and that larger sizes are quoted")
    for shop in ("StickerApp", "StickerPlug", "SlapHub"):
        if shop not in site["forbidden_strings"]:
            bad17.append(f"{shop} not on the forbidden list")
    # independence: archive every STKR-{TIER}-{SIZE} item (and its breaks) from
    # the cache - the stickers page must render the same chart regardless
    arch = airtable.load_cache()
    isstk = lambda s: (s or "").startswith("STKR-")
    arch["products"] = [r for r in arch["products"] if not isstk(r["f"].get(airtable.F["sku"]))]
    arch["price_breaks"] = [r for r in arch["price_breaks"]
                            if not isstk((r["f"].get(airtable.F["break"]) or "").split(" @")[0])]
    amodels, _, _ = model.build_models(arch, site)
    if next(mm for mm in amodels if mm["slug"] == "stickers")["sections"] != smodel["sections"]:
        bad17.append("stickers chart still depends on the 33 STKR size items")
    # fail loud: a missing or non-numeric price must refuse to publish
    for mutate in ("drop", "text"):
        q2 = json.loads(json.dumps(quotes))
        cards = q2["prices"]["Premium"][sticker_quote._key(chart["sizes"][2])]
        if mutate == "drop":
            cards.pop("1000")
        else:
            cards["1000"] = "0.36"
        try:
            sticker_quote.validate(chart, q2)
            bad17.append(f"validate() accepted a {mutate} price")
        except SystemExit:
            pass
    check("17 sticker chart: endpoint-priced, same layout, version shown, fail-loud, archive-proof",
          not bad17, "; ".join(bad17[:6]))

    # 17b. price pin: while the grid is the cutover version, every cell must
    # equal the chart that was live before the switch (mismatch = wiring bug).
    # A re-survey (new version) turns this into a [DATA] gap, never a freeze.
    import html as _html
    fx = json.load(open(os.path.join(ROOT, "tests", "fixtures", "stickers-live-2026-10-19.json")))
    if ver == fx["grid_version"]:
        mism = [f"{g['header']} {r['label']} {c['label']}: {v} != {fx['tiers'][g['header']][r['label']][c['label']]}"
                for g in grids for r in g["rows"] for c, v in zip(g["columns"], r["cells"])
                if fx["tiers"][g["header"]][_html.unescape(r["label"])][c["label"]] != v]
        n = sum(len(r["cells"]) for g in grids for r in g["rows"])
        check(f"17b sticker chart == live pre-cutover values ({n} cells, grid {ver})",
              not mism and n == 165, "; ".join(mism[:6]))
    else:
        check(f"17b [DATA] sticker grid is {ver}, fixture pins {fx['grid_version']} - "
              f"verify the new live chart, then refresh tests/fixtures", False)

    # 18. archive guard (Oct 21): a product whose Notes begin "ARCHIVED" never
    # loads, renders, or enters a dressed composition; config pointing at one
    # is fatal. Plant a fake archived GLSJAR- row (control: the same row
    # un-archived DOES render) and archive a config-referenced SKU.
    bad18 = []
    import copy as _copy
    FX = airtable.F

    def _plant(notes):
        c = airtable.load_cache()
        src = next(r for r in c["products"] if r["f"].get(FX["sku"]) == "GLSJAR-STD-CLR-2OZ")
        row = _copy.deepcopy(src); row["id"] = "recPLANTED"
        row["f"][FX["sku"]] = "GLSJAR-STD-CLR-9OZ"; row["f"][FX["size"]] = "9oz PLANTED / Clear glass"
        row["f"][FX["notes"]] = notes
        c["products"].append(row)
        for r in [r for r in c["price_breaks"] if (r["f"].get(FX["break"]) or "").startswith("GLSJAR-STD-CLR-2OZ @")]:
            b = _copy.deepcopy(r); b["f"][FX["break"]] = b["f"][FX["break"]].replace("2OZ", "9OZ")
            c["price_breaks"].append(b)
        return c
    def _glass_labels(c):
        ms, _, d = model.build_models(c, site)
        g = next(mm for mm in ms if mm["slug"] == "glass-jars")
        return [r["label"] for sec in g["sections"] for r in sec.get("rows", [])], d
    ctl, _ = _glass_labels(_plant("live row"))
    arc, d18 = _glass_labels(_plant("ARCHIVED Oct 21 2026: planted by check 18"))
    if not any("PLANTED" in l for l in ctl):
        bad18.append("control row did not render (plant broken)")
    if any("PLANTED" in l for l in arc) or "GLSJAR-STD-CLR-9OZ" in d18.products:
        bad18.append("archived GLSJAR row rendered/loaded")
    if "GLSJAR-STD-CLR-9OZ" in d18.breaks:
        bad18.append("archived row's breaks loaded")
    if any(s.startswith("STKR-") and not s.startswith("STKR-CUSTOM-") for s in data.products):
        bad18.append("live archived STKR size items loaded")
    for victim in ("TERP-CDT", "CAPW-GLSJAR-STD"):  # config SKU / dressed component
        c = airtable.load_cache()
        for r in c["products"]:
            if r["f"].get(FX["sku"]) == victim:
                r["f"][FX["notes"]] = "ARCHIVED planted by check 18"
        try:
            model.build_models(c, site)
            bad18.append(f"archived config-referenced {victim} did not fail loud")
        except SystemExit:
            pass
    check("18 archive guard: ARCHIVED notes never render; config refs fail loud",
          not bad18, "; ".join(bad18[:6]))

    # 19. supplier guard (Oct 21): every base Supplier Name is forbidden unless
    # allow-listed as a customer-facing brand; the scan covers PDF text too
    bad19 = []
    terms = build.forbidden_terms(site, data)
    allow = site.get("supplier_brand_allow", {})
    if len(data.suppliers) < 1:
        bad19.append("no suppliers loaded")
    for n in data.suppliers:
        if (n in allow) == (n in terms):
            bad19.append(f"{n}: allow-list/forbidden mismatch")
    for keep in ("StickerApp", "StickerPlug", "SlapHub", "Pacer", "$0.00"):
        if keep not in terms:
            bad19.append(f"static term {keep} dropped")
    if not any(fn.startswith("pdf/") for fn, _ in build._pdf_texts()):
        bad19.append("PDF texts not scanned")
    probe = next(n for n in data.suppliers if n not in allow)
    tmp = os.path.join(SITE, "zz-check19.html")
    open(tmp, "w").write(f"<p>{probe.upper()}</p>")
    try:
        if not any(fn == "zz-check19.html" for fn, _ in build.forbidden_scan(site, terms, pdfs=False)):
            bad19.append(f"planted supplier {probe!r} not caught")
    finally:
        os.remove(tmp)
    check("19 supplier guard: base supplier names forbidden (brand allow-list), HTML + PDF",
          not bad19, "; ".join(bad19[:6]))

    # 20. one-stop-shop copy guard (Oct 21): no in-house / external-production
    # / sourcing language in rendered copy (HTML text + PDF text)
    bad20 = [f"{fn}: {p!r}" for fn, p in build.copy_scan(site)]
    if "IN-HOUSE" in html("direct-to-bag").upper():
        bad20.append("direct-to-bag still says in-house")
    tmp = os.path.join(SITE, "zz-check20.html")
    open(tmp, "w").write("<p>Printed IN HOUSE by our team</p>")
    try:
        if not any(fn == "zz-check20.html" for fn, _ in build.copy_scan(site, pdfs=False)):
            bad20.append("planted in-house phrase not caught")
    finally:
        os.remove(tmp)
    check("20 one-stop-shop copy guard (HTML + PDF)", not bad20, "; ".join(bad20[:6]))

    # 21. flavor UNION (Oct 21): every base TERP-BOT-* flavor is on both flavor
    # menus exactly once (mapped to its marketing entry or appended verbatim)
    # unless it is one of the five held pair-names, which are absent; curated
    # groups unchanged. Planted rows prove a new kiosk flavor appears, an
    # archived one doesn't, and a name colliding with a curated entry shows once.
    bad21 = []
    ucfg = json.load(open(os.path.join(ROOT, "config", "flavor-union.json"), encoding="utf-8"))
    held = set(ucfg["held_names"])
    curated = [(g["name"], g["flavors"]) for g in flavors_cfg["groups"]]

    def _flav(ms, slug):
        mm = next(x for x in ms if x["slug"] == slug)
        return next(sec for sec in mm["sections"] if sec["kind"] == "flavors")
    base_fl = sorted(s for s in data.products if s.startswith("TERP-BOT-"))
    for slug in ("terpenes", "terpene-infusion"):
        sec = _flav(models, slug)
        names = [f for g in sec["groups"] for f in g["flavors"]]
        if [(g["name"], g["flavors"]) for g in sec["groups"][:len(curated)]] != curated:
            bad21.append(f"{slug}: curated groups changed")
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            bad21.append(f"{slug}: shown twice {dup[:4]}")
        rep = sec["union"]
        accounted = (set(rep["mapped"]) | set(rep["held"]) | {k for k, _ in rep["appended"]}
                     | {k for k, _, _ in rep["collisions"]})
        if accounted != set(base_fl):
            bad21.append(f"{slug}: unaccounted base flavors {sorted(set(base_fl) - accounted)[:4]}")
        for k, b in rep["appended"]:
            if names.count(b) != 1:
                bad21.append(f"{slug}: appended {b!r} not shown exactly once")
        upper = {n.upper().rstrip("*").strip() for n in names}
        if held & upper:
            bad21.append(f"{slug}: held pair-name on menu {sorted(held & upper)}")
        if rep["collisions"]:
            bad21.append(f"{slug}: unmapped collisions need mapping {rep['collisions'][:3]}")
        want_total = sum(len(f) for _, f in curated) + len(rep["appended"])
        if len(names) != want_total:
            bad21.append(f"{slug}: {len(names)} flavors, want {want_total}")
    pc = airtable.load_cache()
    tmpl = next(r for r in pc["products"] if r["f"].get(FX["sku"]) == "TERP-BOT-MANGO")
    for sku, bottle, notes in (("TERP-BOT-ZZPLANTED", "ZZ Planted Flavor", ""),
                               ("TERP-BOT-ZZARCHIVED", "ZZ Archived Flavor", "ARCHIVED planted by check 21"),
                               ("TERP-BOT-PEACHCOBBLER2", "PEACH COBBLER", "")):
        row = _copy.deepcopy(tmpl); row["id"] = "rec" + sku
        row["f"][FX["sku"]] = sku; row["f"][FX["size"]] = bottle; row["f"][FX["notes"]] = notes
        pc["products"].append(row)
    pms, _, _ = model.build_models(pc, site)
    psec = _flav(pms, "terpenes")
    pnames = [f for g in psec["groups"] for f in g["flavors"]]
    last = psec["groups"][-1]
    if last["name"] != ucfg["appended_group"] or "ZZ Planted Flavor" not in last["flavors"]:
        bad21.append("planted new flavor not appended verbatim under the appended group")
    if "ZZ Archived Flavor" in pnames:
        bad21.append("planted archived flavor shown")
    if pnames.count("Peach Cobbler") != 1 or "PEACH COBBLER" in pnames:
        bad21.append("colliding planted flavor shown twice")
    check("21 flavor union: every base flavor exactly once, held names absent, curated unchanged",
          not bad21, "; ".join(bad21[:6]))

    # 22. box stickers name the shape (Oct 21): within each tier the rows are
    # distinct and every label carries cart/display/master + top/side/wrap
    bad22 = []
    o2m = next(x for x in models if x["slug"] == "o2-devices")
    box = next(sec for sec in o2m["sections"] if sec.get("header") == "BOX STICKERS")
    labels = [r["label"] for r in box["rows"]]
    for tier in ("Standard", "Premium", "Connoisseur"):
        tl = [l for l in labels if l.startswith(tier + " · ")]
        if len(tl) != 6 or len(set(tl)) != 6:
            bad22.append(f"{tier}: {len(set(tl))} distinct of {len(tl)} rows")
        for l in tl:
            if not (_re.search(r"Cart|Display|Master", l) and _re.search(r"Top|Side|Wrap", l)):
                bad22.append(f"no shape in {l!r}")
    if any(lbl in html("o2-devices") for lbl in ("Standard · ≈3×3 in<", "Premium · ≈3×3 in<")):
        bad22.append("bare size-only label still rendered")
    check("22 box-sticker rows name the shape (6 distinct per tier)", not bad22, "; ".join(bad22[:6]))

    # 9. every page + PDF carries the build date
    import datetime
    date = build.pacific_today().strftime("%B %-d, %Y")
    slugs = [m["slug"] for m in models] + ["index"]
    html_ok = all(date in html(s) for s in slugs)
    pdf_ok = all(date in pdf_text(m["slug"]) for m in models)
    check("9 build date on every page + PDF", html_ok and pdf_ok, f"html={html_ok} pdf={pdf_ok}")

    # 10. Print-tier definitions (Sep 30 owner ruling): the block renders on
    # every page whose products (incl. cap/wrap add-ons) carry Standard/
    # Premium/Connoisseur tiers, and on NONE of the others. Tieredness is
    # derived from the live data, not a hardcoded page list.
    menus_cfg = json.load(open(os.path.join(ROOT, "config", "menus.json"), encoding="utf-8"))["menus"]
    marker = "no finish layers"  # verbatim from the ruling
    bad10 = []
    for mc in menus_cfg:
        tiers = set()
        for p in (mc.get("prefix"), mc.get("cap_prefix"), mc.get("wrap_prefix")):
            if p:
                tiers |= {data.tier_of(s) for s in data.family(p)}
        if mc.get("chart"):  # endpoint-priced (stickers): tiered by construction
            from src import sticker_quote
            tiers |= set(sticker_quote.TIERS)
        tiered = bool(tiers & {"Standard", "Premium", "Connoisseur"})
        present = marker in html(mc["slug"])
        if tiered != present:
            bad10.append(f"{mc['slug']}: tiered={tiered} rendered={present}")
    check("10 print-tier definitions on tiered pages only", not bad10, "; ".join(bad10))

    # 11. Chubby consolidated three-way (Owner's Oct 1 v2): the definitions
    # block, then ONE table per tier (STANDARD -> PREMIUM -> CONNOISSEUR),
    # each row a size x way trio with em-dash column alignment; same-curve
    # variants collapsed; equalization still pinned; contained to this page.
    hc = html("chubby-jars")
    i_defs = hc.find("WHAT THE TIERS MEAN")
    i_std, i_prm, i_cnsr = (hc.find(">STANDARD<"), hc.find(">PREMIUM<"),
                            hc.find(">CONNOISSEUR<"))
    order_ok = -1 < i_defs < i_std < i_prm < i_cnsr
    trio_ok = all(f"{sz} — {way}" in hc for sz in ("2oz", "3oz", "5oz")
                  for way in ("Jar alone", "Jar + label", "Label only"))
    std = next(s for s in mm["chubby-jars"]["sections"]
               if s["kind"] == "grid" and s["header"] == "STANDARD")
    row = {r["label"]: r["cells"] for r in std["rows"]}
    dash_ok = (row["2oz — Jar alone"][0] == "$1.55"
               and row["2oz — Jar + label"][0] == "—"
               and row["2oz — Label only"][0] == "—")
    collapse_ok = hc.count("2oz — Jar + label") == 3  # one per tier table
    repeat_ok = hc.count("2oz — Jar alone") == 3      # untiered way in all three
    defs_once = hc.count("no finish layers") == 1
    singles_ok = "EACH" in hc and "SINGLES FROM $1.55" in hc
    legend_ok = "Bring your own jars" in hc
    eq_ok = all(data.breaks.get(f"CHUBJAR-{t}-2OZBLK") == data.breaks.get(f"CHUBJAR-{t}-2OZCLR")
                for t in ("STD", "PRM", "CNSR"))
    std500 = [data.breaks["CHUBJAR-STD-2OZBLK"].get(q) for q in (500, 1000, 5000, 10000)]
    eq_vals_ok = std500 == [1.18, 0.98, 0.88, 0.82]
    leak = [mc["slug"] for mc in menus_cfg if mc["slug"] not in ("chubby-jars", "miron-jars")
            and ("— Jar alone" in html(mc["slug"]) or "— Label only" in html(mc["slug"]))]
    check("11 chubby consolidated three-way (defs/order/trio/dash/collapse/legend/equalized/contained)",
          order_ok and trio_ok and dash_ok and collapse_ok and repeat_ok
          and defs_once and singles_ok and legend_ok and eq_ok and eq_vals_ok and not leak,
          f"order={order_ok} trio={trio_ok} dash={dash_ok} collapse={collapse_ok} "
          f"repeat={repeat_ok} once={defs_once} singles={singles_ok} legend={legend_ok} "
          f"eq={eq_ok} std500+={std500} leak={leak}")

    # ---- report ----
    print("\n=== §8 ACCEPTANCE ===")
    req_fail = 0
    for ok, name, detail in results:
        tag = "PASS" if ok else ("GAP " if "[DATA]" in name else "FAIL")
        if not ok and "[DATA]" not in name:
            req_fail += 1
        print(f"  [{tag}] {name}" + (f"   — {detail}" if detail and not ok else ""))
    gaps = [n for ok, n, _ in results if not ok and "[DATA]" in n]
    print(f"\n  {sum(1 for ok,_,_ in results if ok)}/{len(results)} passed; "
          f"{req_fail} required failures; {len(gaps)} data-gap(s).")
    if gaps:
        print("  Data gaps (need Airtable data, not code): " + "; ".join(gaps))
    sys.exit(1 if req_fail else 0)


if __name__ == "__main__":
    main()
