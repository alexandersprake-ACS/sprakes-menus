"""One-shot: generate responsive page-header banners into assets/banners/.

Sources are the owner-approved studio banners (Oct 13 order). Outputs are
committed; the build just copies assets/ — no CI image tooling needed.
Re-run only when a banner is replaced: python scripts/gen_banners.py <srcdir> <miron_png>
"""
import json
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "banners")

# slug -> source filename inside the Set 1 drop (miron comes from its own
# approved file: the Set 1 miron render was rejected — jars must be violet).
SET1 = {
    "glass-jars": "menu_glass_jars.png",
    "concentrate-jars": "menu_concentrate_jars.png",
    "chubby-jars": "menu_chubby_jars.png",
    "mylar-bags": "menu_mylar_bags.png",
    "direct-to-bag": "menu_direct_print.png",
    "preroll-tubes": "menu_preroll_tubes.png",
    "stickers": "menu_stickers.png",
    "terpenes": "menu_terpenes.png",
    "terpene-infusion": "menu_infusion.png",
    "brand-development": "menu_brand_dev.png",
    "o2-devices": "menu_o2_devices.png",
}

WIDTHS = (640, 1280)  # plus the source's own width


def emit(slug, src_path, manifest):
    im = Image.open(src_path).convert("RGB")
    w, h = im.size
    widths = sorted(set([x for x in WIDTHS if x < w] + [w]))
    for tw in widths:
        scaled = im if tw == w else im.resize((tw, round(h * tw / w)), Image.LANCZOS)
        scaled.save(os.path.join(OUT, f"{slug}-{tw}.avif"), quality=60)
        scaled.save(os.path.join(OUT, f"{slug}-{tw}.webp"), quality=80, method=6)
    manifest[slug] = {"w": w, "h": h, "widths": widths}


def main(set1_dir, miron_png):
    os.makedirs(OUT, exist_ok=True)
    manifest = {}
    for slug, name in sorted(SET1.items()):
        emit(slug, os.path.join(set1_dir, name), manifest)
    emit("miron-jars", miron_png, manifest)
    with open(os.path.join(OUT, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
    print(f"{len(manifest)} banners, {total/1e6:.1f} MB in {OUT}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
