"""Render menu models to self-consistent HTML (used for both web and PDF)."""
import html

# The Sprake's brand mark (4-point star), used for star rows and the
# most-popular-quantity marker. Sized by context via CSS (.mark).
STAR = '<img class="mark" src="assets/logo/sprakes-mark-64.png" alt="">'


def e(s):
    return html.escape(str(s)) if s is not None else ""


def _title_html(title):
    if title.upper().startswith("CUSTOM "):
        return "CUSTOM<br>" + e(title[7:])
    return e(title)


def _stars(n=5):
    return '<div class="stars">' + (STAR * n) + "</div>"


def _grid(sec):
    cols = sec["columns"]
    out = ['<section class="sect">']
    out.append(f'<div><span class="sechead">{e(sec["header"])}</span></div>')
    if sec.get("variant_note"):
        out.append(f'<div class="subhead">{e(sec["variant_note"])}</div>')
    if sec.get("media"):
        f = f' &nbsp;·&nbsp; FINISHES : <span class="f">{e(sec["finishes"])}</span>' if sec.get("finishes") else ""
        out.append(f'<div class="media">MEDIA : {e(sec["media"])}{f}</div>')
    if sec.get("rate_label"):
        out.append(f'<div class="ratelabel">( {e(sec["rate_label"])} )</div>')
    cls = "grid compact" if sec.get("compact") else "grid"
    out.append('<div class="tablewrap"><table class="%s">' % cls)
    head = ['<th class="lbl"></th>']
    for c in cols:
        pc = " pop" if c.get("popular") else ""
        star = " " + STAR if c.get("popular") else ""
        head.append(f'<th class="val{pc}">{e(c["label"])}{star}</th>')
    out.append("<thead><tr>" + "".join(head) + "</tr></thead><tbody>")
    for r in sec["rows"]:
        cells = [f'<td class="lbl">{e(r["label"])}</td>']
        for c, val in zip(cols, r["cells"]):
            pc = " pop" if c.get("popular") else ""
            cells.append(f'<td class="val{pc}">{e(val)}</td>')
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table></div>")
    if any(c.get("popular") for c in cols):
        out.append(f'<div class="popnote"><span class="s">{STAR}</span> most popular quantity</div>')
    out.append("</section>")
    return "".join(out)


def _schedule(sec):
    out = ['<section class="sect">']
    out.append(f'<div><span class="sechead">{e(sec["header"])}</span></div>')
    if sec.get("subhead"):
        out.append(f'<div class="subhead">{e(sec["subhead"])}</div>')
    out.append('<table class="kv">')
    for r in sec["rows"]:
        out.append(f'<tr><td class="k">{e(r["label"])}</td><td class="v">{e(r["cells"][0])}</td></tr>')
    out.append("</table></section>")
    return "".join(out)


def _flavors(sec):
    out = ['<section class="sect">']
    out.append(f'<div><span class="sechead">{e(sec["header"])}</span></div>')
    for g in sec["groups"]:
        n = len(g["flavors"])
        ncol = 2 if n <= 24 else (3 if n <= 60 else 4)
        items = "".join(f"<span>{e(x)}</span>" for x in g["flavors"])
        out.append('<div class="flavgroup">')
        out.append(f'<div class="gname">{e(g["name"])}</div>')
        out.append(f'<div class="flavlist cols-{ncol}">{items}</div>')
        out.append("</div>")
    if sec.get("star_note"):
        out.append(f'<div class="popnote">{e(sec["star_note"])}</div>')
    out.append("</section>")
    return "".join(out)


def _kv(sec):
    out = ['<section class="sect">']
    out.append(f'<div><span class="sechead">{e(sec["header"])}</span></div>')
    if sec.get("note"):
        out.append(f'<div class="subhead">{e(sec["note"])}</div>')
    out.append('<table class="kv">')
    for r in sec["rows"]:
        sub = f'<div class="ksub">{e(r["sub"])}</div>' if r.get("sub") else ""
        out.append(f'<tr><td class="k">{e(r["label"])}{sub}</td><td class="v">{e(r["price"])}</td></tr>')
    out.append("</table>")
    for line in sec.get("after") or []:
        out.append(f'<div class="popnote">{e(line)}</div>')
    out.append("</section>")
    return "".join(out)


def _infusion(sec):
    out = ['<section class="sect infuse">']
    out.append('<div><span class="sechead">PRICING</span></div>')
    out.append(f'<div class="subhead">SERVICE PRICE · PER {e(sec["unit"]).upper()}</div>')
    out.append('<table class="tiers">')
    for t in sec["tiers"]:
        out.append(f'<tr><td class="k">{e(t["range"])} {e(sec["unit"])}</td>'
                   f'<td class="d"></td><td class="v">{e(t["price"])}</td></tr>')
    out.append("</table>")
    out.append('<div class="note">')
    out.append(f'<div class="l1">+ TERPENES {e(sec["terpene_add"])} / {e(sec["unit"]).upper()} '
               f'<span class="sub">(botanical flavors, billed separately)</span></div>')
    out.append(f'<div class="l2">{e(sec["own_terpenes"])}</div></div>')
    fn = sec["flavor_note"]
    out.append(f'<div class="flavnote">{e(fn[0])} <span class="o">{e(fn[1])}</span></div>')
    if sec.get("rush"):
        out.append(f'<div class="rush">Same Day / Next Day Fee <b>{e(sec["rush"])}</b></div>')
    out.append("</section>")
    return "".join(out)


def _tierdefs(sec):
    out = ['<section class="sect tierdefs">']
    out.append(f'<div class="tdh">{e(sec["heading"])}</div>')
    out.append(f'<div class="tdlead">{e(sec["lead"])}</div>')
    for name, desc in sec["tiers"]:
        out.append(f'<div class="tdline"><b>{e(name)}</b> — {e(desc)}</div>')
    out.append("</section>")
    return "".join(out)


RENDERERS = {"grid": _grid, "schedule": _schedule, "flavors": _flavors, "kv": _kv,
             "infusion": _infusion, "tierdefs": _tierdefs}


def render_menu_body(model, site, build_date):
    """The menu's inner content (title -> price date), shared by the standalone
    page and the index hub panels."""
    parts = [f'<h1 class="title">{_title_html(model["title"])}</h1>']
    for line in model.get("subtitle", []):
        parts.append(f'<div class="subtitle">{e(line)}</div>')
    parts.append(_stars())
    if model.get("includes"):
        inc = model["includes"]
        body = f'<span class="h">{e(inc[0])}</span><br>' + "<br>".join(e(x) for x in inc[1:])
        parts.append(f'<div class="includes">{body}</div>')
    parts.append('<div class="rule"></div>')
    for sec in model["sections"]:
        parts.append(RENDERERS[sec["kind"]](sec))
    if model.get("disclaimer"):
        d = model["disclaimer"]
        parts.append(f'<div class="disclaimer"><div class="dh">{e(d["heading"])}</div>'
                     f'<div class="dp">{e(d["body"])}</div></div>')
    parts.append(f'<div class="foot">{e(site["footer_discount"])}</div>')
    parts.append(f'<div class="pricedate">Prices current as of {e(build_date)}.</div>')
    return "".join(parts)


def render_menu(model, site, build_date, rel_assets="assets"):
    noindex = ('<meta name="robots" content="noindex,nofollow">' if site.get("noindex") else "")
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        noindex,
        f"<title>{e(model['title'])} — {e(site['brand'])}</title>",
        f'<link rel="stylesheet" href="{rel_assets}/css/menu.css?v={site.get("asset_v", "")}">',
        "</head><body>",
        f'<img class="bgfix" src="{rel_assets}/bg/brand_bg.png" alt="">',
        '<main class="wrap">',
        render_menu_body(model, site, build_date),
        "</main></body></html>",
    ]
    return "".join(parts)


HUB_JS = """
(function(){
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var slugs = tabs.map(function(t){ return t.dataset.slug; });
  function select(slug, push){
    if (slugs.indexOf(slug) < 0) slug = slugs[0];
    tabs.forEach(function(t){
      var on = t.dataset.slug === slug;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      document.getElementById('panel-' + t.dataset.slug).hidden = !on;
    });
    if (push) history.pushState(null, '', '#' + slug);
    var sel = document.querySelector('.tab[aria-selected="true"]');
    if (sel && sel.scrollIntoView) sel.scrollIntoView({block:'nearest', inline:'center'});
    window.scrollTo(0, 0);
  }
  tabs.forEach(function(t){
    t.addEventListener('click', function(){ select(t.dataset.slug, true); });
  });
  window.addEventListener('popstate', function(){ select(location.hash.slice(1), false); });
  select(location.hash.slice(1), false);
  var bar = document.querySelector('.tabbar');
  function onScroll(){ bar.classList.toggle('scrolled', window.scrollY > 8); }
  window.addEventListener('scroll', onScroll, {passive:true});
  onScroll();
})();
"""


def render_hub(models, site, build_date, rel_assets="assets"):
    """One-page hub: tab bar + all 12 menus embedded as show/hide panels.
    Hash routing (#slug) keeps tabs linkable; no full page loads."""
    noindex = ('<meta name="robots" content="noindex,nofollow">' if site.get("noindex") else "")
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        noindex,
        f"<title>{e(site['site_title'])}</title>",
        f'<link rel="stylesheet" href="{rel_assets}/css/menu.css?v={site.get("asset_v", "")}">',
        "</head><body>",
        f'<img class="bgfix" src="{rel_assets}/bg/brand_bg.png" alt="">',
        '<header class="hubhead">',
        f'<img class="hublogo" src="{rel_assets}/logo/sprakes-wordmark.svg" alt="{e(site["brand"])}">',
        "</header>",
        '<nav class="tabbar"><div class="tabs" role="tablist" aria-label="Menus">',
    ]
    for m in models:
        parts.append(
            f'<button class="tab" role="tab" data-slug="{m["slug"]}" '
            f'aria-selected="false" aria-controls="panel-{m["slug"]}">{e(m["tab_label"])}</button>'
        )
    parts.append("</div></nav>")
    for m in models:
        parts.append(
            f'<section class="wrap panel" id="panel-{m["slug"]}" role="tabpanel" hidden>'
            f'<div class="panellinks"><a href="pdf/{m["slug"]}.pdf" target="_blank" '
            f'rel="noopener">DOWNLOAD PDF</a></div>'
            + render_menu_body(m, site, build_date)
            + "</section>"
        )
    parts.append(f"<script>{HUB_JS}</script>")
    parts.append("</body></html>")
    return "".join(parts)
