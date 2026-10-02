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
    check("1 MIRON-STD-SAT-250 @100 = $4.84",
          model.money(price) == "$4.84" and "$4.84" in html("miron-jars"),
          f"model={model.money(price)}")

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
    check("6b Terpenes flavors from marketing list only", internal_only not in ht.upper(),
          "internal SKU token leaked" if internal_only in ht.upper() else "clean")

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
    hits = build.forbidden_scan(site)
    check("7 no forbidden strings site-wide", not hits, str(hits))

    # 8. DIFF names exactly the changed menu/item/old/new
    snap = build.compute_snapshot(data)
    mutated = json.loads(json.dumps(snap))
    mutated["MIRON-STD-SAT-250"]["breaks"]["100"] = 9.99
    changes = build.diff_snapshots(snap, mutated)  # old=snap, new=mutated
    ok8 = (len(changes) == 1 and changes[0][1] == "MIRON-STD-SAT-250"
           and changes[0][2] == "@100" and "miron" in changes[0][0].lower())
    check("8 DIFF isolates one changed price", ok8, str(changes))

    # 9. every page + PDF carries the build date
    import datetime
    date = datetime.date.today().strftime("%B %-d, %Y")
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
    leak = [mc["slug"] for mc in menus_cfg if mc["slug"] != "chubby-jars"
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
