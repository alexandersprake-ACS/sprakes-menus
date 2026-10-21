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

    def __init__(self, cache, exclude=()):
        F = airtable.F
        # exclude: site-wide guard prefixes (site.json global_exclude_prefixes) —
        # matching SKUs never reach any family, page, PDF, or the price snapshot
        exclude = tuple(exclude)
        self.products = {}  # sku -> fields dict
        # archive guard (Oct 21): a product whose Notes begin "ARCHIVED" is
        # dropped like an excluded prefix. The menus never read On Form —
        # jar-alone, CAP and CAPW are Off Form by ruling and must render.
        self.archived = {}  # sku -> notes
        for r in cache["products"]:
            sku = r["f"].get(F["sku"])
            if not sku or sku.startswith(exclude):
                continue
            notes = (r["f"].get(F["notes"]) or "").lstrip()
            if notes.startswith("ARCHIVED"):
                self.archived[sku] = notes
                continue
            self.products[sku] = r["f"]
        # breaks: sku -> {qty(int): price}
        self.breaks = {}
        for r in cache["price_breaks"]:
            label = r["f"].get(F["break"]) or ""
            if " @" not in label:
                continue
            sku, q = label.rsplit(" @", 1)
            if (exclude and sku.startswith(exclude)) or sku in self.archived:
                continue
            try:
                qty = int(float(q))
            except ValueError:
                continue
            self.breaks.setdefault(sku, {})[qty] = r["f"].get(F["price"])
        self.categories = [r["f"].get(F["cat_name"]) for r in cache["categories"]]
        self.suppliers = [r["f"].get(F["supplier_name"]) for r in cache.get("suppliers", [])
                          if r["f"].get(F["supplier_name"])]

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
            part = data.size_of(sku)
            if fam.get("label_shape"):
                # the shape lives only in Product Name ("O2 Box Sticker — Cart
                # Box Top"); without it same-size shapes read as twin rows
                shape = (data.name_of(sku) or "").split(" — ", 1)[-1]
                part = f"{shape} · {part}"
            rows.append({"label": f"{data.tier_of(sku)} · {part}",
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


def _dressed_breaks(data, sku, component_prefixes):
    """FULLY-DRESSED price ladder (Owner's Component Architecture, Oct 12):
    bundle + every cap/wrap sticker the container takes, summed at the SAME
    break. Component SKU = prefix + the bundle's tier abbreviation. A break
    where any component is missing yields no dressed price (em-dash) rather
    than a silently partial sum. Purely base-derived."""
    base = data.breaks.get(sku, {})
    abbr = TIER_ABBR.get(data.tier_of(sku))
    if not abbr:
        return base
    out = {}
    for q, p in base.items():
        total = p
        for pref in component_prefixes:
            cp = data.breaks.get(pref + abbr, {}).get(q)
            if cp is None:
                total = None
                break
            total += cp
        if total is not None:
            out[q] = round(total + 1e-9, 2)
    return out


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

    def way_breaks(w, sku):
        if w.get("dressed_components"):
            return _dressed_breaks(data, sku, w["dressed_components"])
        return data.breaks.get(sku, {})

    def cells(br):
        return [money(br[q], site["quote_text"]) if q in br else "—" for q in cols]

    # Two matching modes: "size" groups ways by the Size/Type string (chubby);
    # "sku_suffix" groups by the SKU tail after way-prefix + tier segment
    # (miron: MIRON-STD-SAT-100 / MIRON-LBL-STD-SAT-100 / JAR-MIRON-SAT-100
    # all key to SAT-100 even though their Size/Type strings differ).
    if cfg.get("match") == "sku_suffix":
        seg = {"Standard": "STD-", "Premium": "PRM-", "Connoisseur": "CNSR-"}

        def key_of(w, sku):
            tail = sku[len(w["prefix"]):]
            if not w.get("untiered"):
                for sg in seg.values():
                    if tail.startswith(sg):
                        tail = tail[len(sg):]
                        break
            return tail

        display, sortkey = {}, {}
        for w in ways:
            for sku in w["skus"]:
                k = key_of(w, sku)
                rp = float(data.ref_of(sku) or 0)
                if w.get("untiered"):
                    # jar-alone price anchors the ordering (small -> large);
                    # sizes with no jar-alone (big bundle-only formats) sort last
                    sortkey[k] = min(sortkey.get(k, 1e9), rp)
                else:
                    sortkey.setdefault(k, 1000 + rp)
                    display[k] = size_of(sku)[0] or k  # tiered ways name the size
                display.setdefault(k, size_of(sku)[0] or k)
        sizes = sorted(display, key=lambda k: sortkey[k])

        def cands(w, key, tier):
            return [s for s in w["skus"] if key_of(w, s) == key
                    and (w.get("untiered") or data.tier_of(s) == tier)]
        size_name = lambda k: display[k]
    else:
        sizes = sorted({size_of(s)[0] for w in ways for s in w["skus"]}, key=size_sort)

        def cands(w, key, tier):
            return [s for s in w["skus"] if size_of(s)[0] == key
                    and (w.get("untiered") or data.tier_of(s) == tier)]
        size_name = lambda k: k

    sections = []
    for tier in ("Standard", "Premium", "Connoisseur"):
        rows = []
        for size in sizes:
            for w in ways:
                cand = cands(w, size, tier)
                if not cand:
                    continue
                curves = {tuple(sorted(way_breaks(w, s).items())) for s in cand}
                if len(curves) == 1:
                    rows.append({"label": f"{size_name(size)} — {w['label']}",
                                 "cells": cells(way_breaks(w, cand[0]))})
                else:
                    for s in sorted(cand):
                        _, variant = size_of(s)
                        lbl = f"{size_name(size)} {variant} — {w['label']}".replace("  ", " ")
                        rows.append({"label": lbl, "cells": cells(way_breaks(w, s))})
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

    dressed = m.get("dressed_components")
    label_map = m.get("label_map", {})
    sections = []
    for tier in sorted(groups, key=lambda t: TIER_ORDER.get(t, 5)):
        rows = []
        for sku in sorted(groups[tier], key=lambda s: (float(data.ref_of(s) or 0), s)):
            br = _dressed_breaks(data, sku, dressed) if dressed else data.breaks.get(sku, {})
            cells = []
            for q in cols:
                if q in br:
                    cells.append(money(br[q], site["quote_text"]))
                elif not br and q == cols[0]:
                    cells.append(money(data.ref_of(sku), site["quote_text"]))
                else:
                    cells.append("—")
            label = data.size_of(sku) or data.name_of(sku) or sku
            for k, v in label_map.items():  # e.g. "Single-sided" -> "Single-sided · 1 sticker"
                label = label.replace(k, v)
            rows.append({"label": label, "cells": cells})
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
        # per-tier MEDIA/FINISHES from the shared tier canon (config/disclaimers.json
        # print_tiers.section_specs); untiered pages keep their own menu-level line
        spec = _load_json("disclaimers.json")["print_tiers"]["section_specs"].get(tier)
        media = spec["media"] if spec else m.get("media")
        finishes = spec["finishes"] if spec else m.get("finishes")
        sections.append({
            "kind": "grid", "header": header.strip(" —").upper(),
            "variant_note": variant_note,
            "rate_label": m.get("rate_label", "RATE / STICKER"),
            "media": media, "finishes": finishes,
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
            for fam in m.get("post_families", []):  # e.g. box stickers
                sections.append(_family_section(fam, data, site))
            sections.append({
                "kind": "grid", "header": m.get("wrap_label", "RATE / WRAP"), "compact": True,
                "rate_label": None, "media": None, "finishes": None,
                "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in wcols],
                "rows": wrows,
            })
    return _wrap(m, sections, site, disclaimer=False)


def _flavor_key(name):
    """Comparison key only (collision detection) - never used for display."""
    return re.sub(r"[^A-Z0-9]+", "", (name or "").upper().replace("*", ""))


def flavor_union(data, warns):
    """Curated marketing groups + every base TERP-BOT-* flavor not already
    represented (config/flavor-union.json), appended under its bottle
    spelling. Returns (groups, report)."""
    cur = _load_json("marketing-flavors.json")
    cfg = _load_json("flavor-union.json")
    groups = [{"name": g["name"], "flavors": list(g["flavors"])} for g in cur["groups"]]
    entries = {f for g in groups for f in g["flavors"]}
    mapping = {**cfg["map_exact"], **cfg["map_riff"], **cfg["map_styled"]}
    held = set(cfg["held_names"])
    missing = sorted({v for v in mapping.values() if v not in entries})
    if missing:
        raise SystemExit(f"FATAL: flavor-union.json maps to entries missing from "
                         f"marketing-flavors.json: {missing} — refusing to publish.")
    seen = {_flavor_key(f): f for f in entries}
    report = {"mapped": [], "held": [], "appended": [], "collisions": []}
    appended = []
    for sku in sorted(s for s in data.products if s.startswith("TERP-BOT-")):
        bottle = data.size_of(sku)
        if not bottle:
            raise SystemExit(f"FATAL: base flavor {sku} has no Size/Type (bottle "
                             f"spelling) — refusing to publish.")
        if sku in mapping:
            if bottle in held:
                raise SystemExit(f"FATAL: {sku} is both mapped and held — refusing to publish.")
            report["mapped"].append(sku)
        elif bottle in held:
            report["held"].append(sku)
        elif _flavor_key(bottle) in seen:
            # same name already on the menu (curated or another bottle): show once
            report["collisions"].append((sku, bottle, seen[_flavor_key(bottle)]))
            warns.append(f"flavors: unmapped base flavor {sku} ({bottle!r}) matches "
                         f"{seen[_flavor_key(bottle)]!r} already on the menu — shown once; "
                         f"map it in config/flavor-union.json.")
        else:
            seen[_flavor_key(bottle)] = bottle
            appended.append(bottle)
            report["appended"].append((sku, bottle))
    if appended:
        groups.append({"name": cfg["appended_group"], "flavors": sorted(appended)})
    return groups, report


def _terpenes_menu(m, data, site, warns):
    disc = _load_json("disclaimers.json")
    flavors = _load_json("marketing-flavors.json")
    union = flavor_union(data, warns)
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
        "groups": union[0], "star_note": flavors.get("star_note"), "union": union[1],
    })
    return _wrap(m, sections, site, disclaimer=disc["terpene_disclaimer"])


def _infusion_menu(m, data, site, warns):
    disc = _load_json("disclaimers.json")
    flavors = _load_json("marketing-flavors.json")
    union = flavor_union(data, warns)
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
        "groups": union[0], "star_note": flavors.get("star_note"), "union": union[1],
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


def _sticker_chart_menu(m, data, site, warns):
    """Stickers (Oct 20 ruling): the same preset size chart customers always
    saw, priced from the forms app's area-based quote endpoint instead of the
    33 STKR-{TIER}-{SIZE} items — archiving those changes nothing here."""
    from . import sticker_quote
    chart = m["chart"]
    quotes = sticker_quote.load(chart)
    popular = site.get("popular_quantity")
    cols = chart["qtys"]
    specs = _load_json("disclaimers.json")["print_tiers"]["section_specs"]
    sections = [_tierdefs_section(m)]
    snap = {"STKR-GRID": {"ref": quotes["version"], "breaks": {}}}
    for tier in sticker_quote.TIERS:
        rows = []
        for size in chart["sizes"]:
            cards = quotes["prices"][tier][sticker_quote._key(size)]
            rows.append({"label": size["label"],
                         "cells": [money(cards[str(q)], site["quote_text"]) for q in cols]})
            key = ("STKR-GRID-" + TIER_ABBR[tier] + "-"
                   + f'{size["w"]}X{size["h"]}'.replace(".", ""))
            snap[key] = {"ref": None, "breaks": {str(q): cards[str(q)] for q in cols}}
        sections.append({
            "kind": "grid", "header": tier.upper(),
            "variant_note": chart.get("variant_note"),
            "rate_label": m.get("rate_label", "RATE / STICKER"),
            "media": specs[tier]["media"], "finishes": specs[tier]["finishes"],
            "columns": [{"qty": q, "label": _qty_label(q), "popular": q == popular} for q in cols],
            "rows": rows,
        })
    sections[-1]["after"] = [chart["any_size_note"]]
    model = _wrap(m, sections, site, disclaimer=False)
    model["price_note"] = f'Price grid {quotes["version"]}'
    model["quote_snapshot"] = snap
    return model


def _qty_label(q):
    return "EACH" if q == 1 else f"{q:,}"


def _ml(q):
    return f"{q:,} mL"


BUILDERS = {
    "grid": _grid_menu, "devices": _devices_menu, "terpenes": _terpenes_menu,
    "infusion": _infusion_menu, "flat": _flat_menu,
    "sticker_chart": _sticker_chart_menu,
}


def _archived_config_refs(menus_cfg, archived):
    """Archived SKUs that config still points at: any config string equal to
    an archived SKU, or an archived SKU under a dressed-component prefix
    (it would silently blank a dressed price)."""
    refs, prefixes = set(), set()

    def walk(o, key=None):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str):
            if key == "dressed_components":
                prefixes.add(o)
            elif o in archived:
                refs.add(o)
    walk(menus_cfg)
    refs |= {s for s in archived if prefixes and s.startswith(tuple(prefixes))}
    return sorted(refs)


def build_models(cache, site):
    data = Data(cache, site.get("global_exclude_prefixes", []))
    menus_cfg = _load_json("menus.json")["menus"]
    stale = _archived_config_refs(menus_cfg, data.archived)
    if stale:
        raise SystemExit("FATAL: config references ARCHIVED product(s) " + ", ".join(stale)
                         + " — remove them from config/menus.json; refusing to publish.")
    warns = []
    models = []
    for m in sorted(menus_cfg, key=lambda x: x["order"]):
        builder = BUILDERS[m["layout"]]
        models.append(builder(m, data, site, warns))
    return models, warns, data
