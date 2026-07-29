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

from . import airtable

CFG = os.path.join(os.path.dirname(__file__), "..", "config")
TIER_ORDER = {"Standard": 0, "Premium": 1, "Connoisseur": 2, "N/A": 9, None: 9}
TIER_ABBR = {"Standard": "STD", "Premium": "PRM", "Connoisseur": "CNSR"}


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


# --------------------------------------------------------------------------
def _grid_menu(m, data, site, warns):
    prefix = m["prefix"]
    cap_prefix = m.get("cap_prefix")
    exclude = (cap_prefix,) if cap_prefix else ()
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

    # optional cap-sticker add-on table (tier rows)
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
    drows = [{"label": data.name_of(s) or s, "price": money(data.ref_of(s), site["quote_text"])}
             for s in sorted(designs, key=lambda s: float(data.ref_of(s) or 0))]
    prows = [{"label": data.name_of(s) or s, "price": money(data.ref_of(s), site["quote_text"])}
             for s in sorted(packages, key=lambda s: float(data.ref_of(s) or 0))]
    sections = [
        {"kind": "kv", "header": "DESIGN SERVICES", "rows": drows},
        {"kind": "kv", "header": "BRANDING PACKAGES", "rows": prows},
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
    return f"{q:,}"


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
