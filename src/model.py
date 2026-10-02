"""Normalize Airtable records into per-menu render models, enforcing the
business data rules in spec §5.

Pricing shapes handled:
  grid      quantity-break matrix (tier x size)      GLSJAR, CONJAR, CHUBJAR,
                                                     MIRON, MYLS2B, MYLDTP, TUBE,
                                                     STKR, (+ CAP add-on)
  devices   deep quantity-break matrix + wrap add-on DEV-O2 (+ O2WRAP)
  terpenes  per-mL schedule + blends + marketing     TERP-*
            flavor list
  infusion  split service tiers + fixed notes        INFUSE
  flat      flat per-item prices                      DSGN-*, PKG-*
"""
import json
import os
import re

from . import airtable

CFG = os.path.join(os.path.dirname(__file__), "..", "config")
TIER_ORDER = {"Standard": 0, "Premium": 1, "Connoisseur": 2, "N/A": 9, None: 9}
TIER_ABBR = {"Standard": "STD", "Premium": "PRM", "Connoisseur": "CNSR"}
PRINT_TIERS = {"Standard", "Premium", "Connoisseur"}


def _tierdefs_section(m):
    """The Sep 30 print-tier definitions block (verbatim owner ruling).

    Shared copy lives in config/disclaimers.json; menus may override the lead
    line (print_tiers_lead). Inserted once per page, only when the page's
    products actually carry Standard/Premium/Connoisseur tiers.
    """
    pt = _load_json("disclaimers.json")["print_tiers"]
    return {"kind": "tierdefs", "heading": pt["heading"],
            "lead": m.get("print_tiers_lead") or pt["lead"], "tiers": pt["tiers"]}


def _load_json(name):
    with open(os.path.join(CFG, name), encoding="utf-8") as fh:
        return json.load(fh)


def money(v, quote_text="Quote"):
    """Format a stored price EXACTLY as $X,XXX.XX. Zero/blank -> quote (spec 5.2).
    Never recomputes or re-rounds (spec §2)."""
    if v is None or v == "":
        return quote_text
    try:
        f = float(v)
    except (TypeError, ValueError):
        return quote_text
    if f == 0:
        return quote_text
    return f"${f:,.2f}"


def money_flat(v, quote_text="Quote"):
    """Like money() but drops a whole-dollar's trailing .00 (the approved menu
    shows the rush fee as '$309', not '$309.00')."""
    s = money(v, quote_text)
    return s[:-3] if s.endswith(".00") else s


class Data:
    """Indexed view over the cached tables."""

    def __init__(self, cache):
        F = airtable.F
        self.products = {}  # sku -> fields dict
        for r in cache["products"]:
            sku = r["f"].get(F["sku"])
            if sku:
                self.products[sku] = r["f"]
        # breaks: sku -> {qty(int): price}
        self.breaks = {}
        for r in cache["price_breaks"]:
            label = r["f"].get(F["break"]) or ""
            if " @" not in label:
                continue
            sku, q = label.rsplit(" @", 1)
            try:
                qty = int(float(q))
            except ValueError:
                continue
            self.breaks.setdefault(sku, {})[qty] = r["f"].get(F["price"])
        self.categories = [r["f"].get(F["cat_name"]) for r in cache["categories"]]

    def family(self, prefix, exclude=()):
        F = airtable.F
        out = []
        for sku, f in self.products.items():
            if not sku.startswith(prefix):
                continue
            if any(sku.startswith(x) for x in exclude):
                continue
            out.append(sku)
        return out

    def tier_of(self, sku):
        return self.products[sku].get(airtable.F["tier"])

    def size_of(self, sku):
        return self.products[sku].get(airtable.F["size"])

    def name_of(self, sku):
        return self.products[sku].get(airtable.F["name"])

    def ref_of(self, sku):
        return self.products[sku].get(airtable.F["ref_price"])

    def qtys(self, skus):
        """Sorted-ascending union of break quantities across skus (spec 8.2:
        columns are ALWAYS ascending, so the historical column-swap is impossible)."""
        s = set()
        for sku in skus:
            s.update(self.breaks.get(sku, {}).keys())
        return sorted(s)


def _family_section(fam, data, site):
    """One self-contained grid section for a sub-family (e.g. the chubby
    three-way's 'Jar Alone' and 'Label Only' blocks). Untiered families list
    sizes; tiered ones list tier-major rows ('Standard · 2oz'). Prices are
    100%% base-derived, like every grid."""
    skus = data.family(fam["prefix"])
    if not skus:
        raise SystemExit(f"FATAL: sub-family '{fam['prefix']}' has zero products.")
    cols = data.qtys(skus)
    popular = site.get("popular_quantity")
    tiered = any(data.tier_of(s) in PRINT_TIERS for s in skus)

    def cells_for(sku):
        br = data.breaks.get(sku, {})
        out = []
        for q in cols:
            if q in br:
                out.append(money(br[q], site["quote_text"]))
            elif not br and q == cols[0]:
                out.append(money(data.ref_of(sku), site["quote_text"]))
            else:
                out.append("—")
        return out

    rows = []
    if tiered:
        order = sorted(skus, key=lambda s: (TIER_ORDER.get(data.tier_of(s), 5),
                                            float(data.ref_of(s) or 0), s))
        for sku in order:
            rows.append({"label": f"{data.tier_of(sku)} · {data.size_of(sku)}",
                         "cells": cells_for(sku)})
    else:
        for sku in sorted(skus, key=lambda s: (float(data.ref_of(s) or 0), s)):
            rows.append({"label": data.size_of(sku) or data.name_of(sku) or sku,
                         "cells": cells_for(sku)})

    note = fam.get("note", "")
    if 1 in cols:  # singles tier: surface the walk-in hook, price base-derived
        min1 = min(data.breaks[s][1] for s in skus if 1 in data.breaks.get(s, {}))
        note = (note + " · " if note else "") + f"SINGLES FROM {money(min1, site['quote_text'])}"
    return {
        "kind": "grid", "header": fam["header"], "variant_note": note or None,
        "rate_label": fam.get("rate_label"), "media": fam.get("media"),
        "finishes": fam.get("finishes"),
        "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in cols],
        "rows": rows,
    }


def _consolidated_sections(m, data, site):
    """Owner's consolidated shape (Oct 1 v2 feedback): one table per PRINT
    tier, each row a size x way trio (e.g. '2oz — Jar alone / Jar + label /
    Label only'). Untiered ways repeat identically in every tier table.
    Variants of a size (black/clear) auto-collapse into one row while their
    break curves are identical — if a reprice ever un-equalizes them, the
    rows split again on the next regeneration. Reusable via the
    `consolidated_ways` config key (Miron is next in line)."""
    cfg = m["consolidated_ways"]
    ways = []
    all_skus = []
    for w in cfg["ways"]:
        skus = data.family(w["prefix"], exclude=tuple(w.get("exclude", [])))
        if not skus:
            raise SystemExit(f"FATAL: consolidated way '{w['prefix']}' has zero products.")
        ways.append({**w, "skus": skus})
        all_skus += skus
    cols = data.qtys(all_skus)
    popular = site.get("popular_quantity")

    def size_of(sku):
        sz = (data.size_of(sku) or "").split(" / ")[0]
        base = sz.split(" (")[0].strip()
        variant = sz[len(base):].strip()
        return base, variant

    def size_sort(k):
        mt = re.match(r"([\d.]+)", k)
        return float(mt.group(1)) if mt else 999

    def cells(br):
        return [money(br[q], site["quote_text"]) if q in br else "—" for q in cols]

    sizes = sorted({size_of(s)[0] for w in ways for s in w["skus"]}, key=size_sort)
    sections = []
    for tier in ("Standard", "Premium", "Connoisseur"):
        rows = []
        for size in sizes:
            for w in ways:
                cand = [s for s in w["skus"] if size_of(s)[0] == size
                        and (w.get("untiered") or data.tier_of(s) == tier)]
                if not cand:
                    continue
                curves = {tuple(sorted(data.breaks.get(s, {}).items())) for s in cand}
                if len(curves) == 1:
                    rows.append({"label": f"{size} — {w['label']}",
                                 "cells": cells(data.breaks.get(cand[0], {}))})
                else:
                    for s in sorted(cand):
                        _, variant = size_of(s)
                        lbl = f"{size} {variant} — {w['label']}".replace("  ", " ")
                        rows.append({"label": lbl, "cells": cells(data.breaks.get(s, {}))})
        sections.append({
            "kind": "grid", "header": tier.upper(),
            "rate_label": m.get("rate_label"), "media": None, "finishes": None,
            "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in cols],
            "rows": rows,
        })

    # ways legend under the last table; {SINGLES} resolves from the base so the
    # walk-in hook can never go stale
    singles_vals = [data.breaks[s][1] for w in ways if w.get("untiered")
                    for s in w["skus"] if 1 in data.breaks.get(s, {})]
    singles = money(min(singles_vals), site["quote_text"]) if singles_vals else ""
    legend = [l.replace("{SINGLES}", singles) for l in cfg.get("legend", [])]
    if legend:
        sections[-1]["after"] = legend
    return sections


# --------------------------------------------------------------------------
def _grid_menu(m, data, site, warns):
    if m.get("consolidated_ways"):
        sections = _consolidated_sections(m, data, site)
        sections.insert(0, _tierdefs_section(m))
        return _finish_grid(m, data, site, sections)

    prefix = m["prefix"]
    cap_prefix = m.get("cap_prefix")
    exclude = tuple(m.get("exclude_prefixes", [])) + ((cap_prefix,) if cap_prefix else ())
    skus = data.family(prefix, exclude=exclude)
    if not skus:
        raise SystemExit(f"FATAL: menu '{m['slug']}' family '{prefix}' has zero products.")

    cols = data.qtys(skus)
    popular = site.get("popular_quantity")

    # group by tier
    groups = {}
    for sku in skus:
        groups.setdefault(data.tier_of(sku), []).append(sku)

    sections = []
    for tier in sorted(groups, key=lambda t: TIER_ORDER.get(t, 5)):
        rows = []
        for sku in sorted(groups[tier], key=lambda s: (float(data.ref_of(s) or 0), s)):
            br = data.breaks.get(sku, {})
            cells = []
            for q in cols:
                if q in br:
                    cells.append(money(br[q], site["quote_text"]))
                elif not br and q == cols[0]:
                    cells.append(money(data.ref_of(sku), site["quote_text"]))
                else:
                    cells.append("—")
            rows.append({"label": data.size_of(sku) or data.name_of(sku) or sku, "cells": cells})
        # Hoist a qualifier shared by every row (e.g. "Violet glass; sticker
        # applied") out of the labels and into a one-line section note — long
        # repeated labels crowd out the price columns on phones.
        variant_note = None
        splits = [r["label"].split(" / ", 1) for r in rows]
        if rows and all(len(s) == 2 for s in splits) and len({s[1] for s in splits}) == 1:
            variant_note = splits[0][1]
            for r, s in zip(rows, splits):
                r["label"] = s[0]
        label = m.get("section_prefix", "")
        header = (label + " — " if label else "") + (tier if tier and tier != "N/A" else "PRICING")
        sections.append({
            "kind": "grid", "header": header.strip(" —").upper(),
            "variant_note": variant_note,
            "rate_label": m.get("rate_label", "RATE / STICKER"),
            "media": m.get("media"), "finishes": m.get("finishes"),
            "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in cols],
            "rows": rows,
        })

    # group-level note on the first tier section (e.g. what bundled prices include)
    if sections and m.get("family_note"):
        sections[0]["group_note"] = m["family_note"]

    # print-tier definitions, once per page, directly above the tier sections —
    # only when this page's products actually carry S/P/C tiers
    if PRINT_TIERS & set(groups):
        sections.insert(0, _tierdefs_section(m))

    # untiered pre-families (e.g. 'Jar Alone') sit ABOVE the tier definitions:
    # the block must not govern them
    for fam in reversed(m.get("pre_families", [])):
        sections.insert(0, _family_section(fam, data, site))

    # tiered post-families (e.g. 'Label Only') sit below the main tier
    # sections, still under the tier-definitions block
    for fam in m.get("post_families", []):
        sections.append(_family_section(fam, data, site))

    return _finish_grid(m, data, site, sections)


def _finish_grid(m, data, site, sections):
    """Shared grid tail: optional cap-sticker add-on table, then wrap."""
    popular = site.get("popular_quantity")
    cap_prefix = m.get("cap_prefix")
    if cap_prefix:
        caps = data.family(cap_prefix)
        if caps:
            cap_cols = data.qtys(caps) or [100]
            crows = []
            for sku in sorted(caps, key=lambda s: TIER_ORDER.get(data.tier_of(s), 5)):
                br = data.breaks.get(sku, {})
                cells = []
                for q in cap_cols:
                    if q in br:
                        cells.append(money(br[q], site["quote_text"]))
                    elif not br:
                        cells.append(money(data.ref_of(sku), site["quote_text"]))
                    else:
                        cells.append("—")
                crows.append({"label": TIER_ABBR.get(data.tier_of(sku), data.tier_of(sku) or ""), "cells": cells})
            sections.append({
                "kind": "grid", "header": m.get("cap_label", "RATE / CAP STICKER"),
                "rate_label": None, "media": None, "finishes": None, "compact": True,
                "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in cap_cols],
                "rows": crows,
            })
    return _wrap(m, sections, site, disclaimer=False)


def _devices_menu(m, data, site, warns):
    skus = data.family(m["prefix"])
    cols = data.qtys(skus)
    popular = site.get("popular_quantity")
    rows = []
    for sku in sorted(skus, key=lambda s: (float(data.ref_of(s) or 0), s)):
        br = data.breaks.get(sku, {})
        cells = [money(br[q], site["quote_text"]) if q in br else "—" for q in cols]
        rows.append({"label": data.name_of(sku) or sku, "cells": cells})
    sections = [{
        "kind": "grid", "header": "DEVICE PRICING", "rate_label": "RATE / UNIT",
        "media": None, "finishes": None,
        "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in cols],
        "rows": rows,
    }]
    wrap_prefix = m.get("wrap_prefix")
    if wrap_prefix:
        wraps = data.family(wrap_prefix)
        if wraps:
            wcols = data.qtys(wraps) or [100]
            wrows = []
            for sku in sorted(wraps, key=lambda s: TIER_ORDER.get(data.tier_of(s), 5)):
                br = data.breaks.get(sku, {})
                cells = [money(br[q], site["quote_text"]) if q in br else money(data.ref_of(sku), site["quote_text"]) if not br else "—" for q in wcols]
                wrows.append({"label": TIER_ABBR.get(data.tier_of(sku), ""), "cells": cells})
            # the wrap rows are the tiered product here — definitions sit
            # directly above them
            if PRINT_TIERS & {data.tier_of(s) for s in wraps}:
                sections.append(_tierdefs_section(m))
            sections.append({
                "kind": "grid", "header": m.get("wrap_label", "RATE / WRAP"), "compact": True,
                "rate_label": None, "media": None, "finishes": None,
                "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in wcols],
                "rows": wrows,
            })
    return _wrap(m, sections, site, disclaimer=False)


def _terpenes_menu(m, data, site, warns):
    disc = _load_json("disclaimers.json")
    flavors = _load_json("marketing-flavors.json")
    base = m["base_sku"]
    base_br = data.breaks.get(base, {})
    if not base_br:
        warns.append(f"terpenes: base schedule '{base}' has no price breaks.")
    # base schedule (mL band -> $/mL)
    sched_rows = [{"label": _ml(q), "cells": [money(p, site["quote_text"])]}
                  for q, p in sorted(base_br.items())]
    sections = [{
        "kind": "schedule", "header": "BOTANICAL DERIVED TERPENES",
        "subhead": "STRAIN SPECIFIC · PER mL",
        "columns": [{"label": "VOLUME"}, {"label": "PRICE / mL"}],
        "rows": sched_rows,
    }]
    # blends table
    blends = [b for b in m.get("blends", []) if b in data.products]
    if blends:
        bcols = data.qtys(blends)
        brows = []
        for b in blends:
            br = data.breaks.get(b, {})
            cells = [money(br[q], site["quote_text"]) if q in br else "—" for q in bcols]
            nm = data.name_of(b) or b
            brows.append({"label": nm, "cells": cells})
        sections.append({
            "kind": "grid", "header": "SIGNATURE BLENDS", "rate_label": "PRICE / mL",
            "media": None, "finishes": None,
            "columns": [{"qty": q, "label": _ml(q), "popular": False} for q in bcols],
            "rows": brows,
        })
    # marketing flavor list
    sections.append({
        "kind": "flavors", "header": "AVAILABLE FLAVORS",
        "groups": flavors["groups"], "star_note": flavors.get("star_note"),
    })
    return _wrap(m, sections, site, disclaimer=disc["terpene_disclaimer"])


def _infusion_menu(m, data, site, warns):
    disc = _load_json("disclaimers.json")
    flavors = _load_json("marketing-flavors.json")
    svc = m["service_sku"]
    ref = data.ref_of(svc)
    br = data.breaks.get(svc, {})
    unit = m.get("unit_label", "lb")
    # Service tiers are derived LIVE from the Airtable breaks (spec 7). Each break
    # @N is the price for "N and up"; ranges close at the next break's floor. The
    # top "500+ = INQUIRE" row has no price and no break row on purpose, so it is
    # supplied from menu config and its floor closes the last priced tier's range.
    inquire = m.get("inquire_tier")
    floor = inquire.get("floor") if inquire else None
    bqs = sorted(br.keys())
    if not bqs or ref is None:
        raise SystemExit(f"FATAL: infusion service SKU '{svc}' has no reference price/breaks.")
    tiers = [{"range": f"1-{bqs[0] - 1}", "price": money(ref, site["quote_text"])}]
    for i, q in enumerate(bqs):
        if i + 1 < len(bqs):
            rng = f"{q}-{bqs[i + 1] - 1}"
        elif floor:
            rng = f"{q}-{floor}"
        else:
            rng = f"{q}+"
        tiers.append({"range": rng, "price": money(br[q], site["quote_text"])})
    if inquire:
        tiers.append({"range": inquire["range"], "price": inquire["price"]})

    rush_ref = data.ref_of(m["rush_sku"])
    sections = [{
        "kind": "infusion",
        "tiers": tiers, "unit": unit,
        "terpene_add": money(m["terpene_add_per_lb"], site["quote_text"]),
        "own_terpenes": disc["infusion_notes"]["own_terpenes"],
        "flavor_note": disc["infusion_notes"]["flavor_note"],
        "rush": money_flat(rush_ref, site["quote_text"]) if rush_ref else None,
    }, {
        "kind": "flavors", "header": "TERPENE FLAVORS",
        "groups": flavors["groups"], "star_note": flavors.get("star_note"),
    }]
    return _wrap(m, sections, site, disclaimer=disc["terpene_disclaimer"])


def _flat_menu(m, data, site, warns):
    designs = data.family(m["design_prefix"])
    packages = data.family(m["package_prefix"])
    tier_notes = m.get("tier_notes", {})  # static tier definitions (config, not catalog)
    drows = []
    for s in sorted(designs, key=lambda s: float(data.ref_of(s) or 0)):
        row = {"label": data.name_of(s) or s, "price": money(data.ref_of(s), site["quote_text"])}
        if s in tier_notes:
            row["sub"] = tier_notes[s]
        drows.append(row)

    def _pkg_key(sku):
        # PKG-T{tier}-{qty}: order tiers ascending, 5-pack before 10-pack —
        # a plain price sort interleaves tiers on equal prices
        mt = re.match(r".*-T(\d+)-(\d+)$", sku)
        if mt:
            return (int(mt.group(1)), int(mt.group(2)))
        return (99, float(data.ref_of(sku) or 0))

    prows = [{"label": data.name_of(s) or s, "price": money(data.ref_of(s), site["quote_text"])}
             for s in sorted(packages, key=_pkg_key)]
    sections = [
        {"kind": "kv", "header": "DESIGN SERVICES", "rows": drows,
         "after": [m["fine_print"]] if m.get("fine_print") else None},
        {"kind": "kv", "header": "BRANDING PACKAGES", "rows": prows,
         "note": m.get("package_note"), "after": m.get("package_after")},
    ]
    return _wrap(m, sections, site, disclaimer=False)


def _wrap(m, sections, site, disclaimer):
    return {
        "slug": m["slug"], "title": m["title"], "order": m["order"],
        "tab_label": m.get("tab_label", m["title"]),
        "subtitle": m.get("subtitle", []),
        "includes": m.get("includes"),
        "sections": sections,
        "disclaimer": disclaimer or None,
    }


def _qty_label(q):
    return "EACH" if q == 1 else f"{q:,}"


def _ml(q):
    return f"{q:,} mL"


BUILDERS = {
    "grid": _grid_menu, "devices": _devices_menu, "terpenes": _terpenes_menu,
    "infusion": _infusion_menu, "flat": _flat_menu,
}


def build_models(cache, site):
    data = Data(cache)
    menus_cfg = _load_json("menus.json")["menus"]
    warns = []
    models = []
    for m in sorted(menus_cfg, key=lambda x: x["order"]):
        builder = BUILDERS[m["layout"]]
        models.append(builder(m, data, site, warns))
    return models, warns, data
