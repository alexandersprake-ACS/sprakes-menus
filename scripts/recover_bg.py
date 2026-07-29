"""OPTIONAL: recover a clean brand background from a reference PDF page by
inpainting the text/stars out (the README technique). The generator ships with
a shared approved background (assets/bg/brand_bg.png) that already covers all 12
menus, so this is only needed if you want a per-menu background lifted from a
specific reference. Not run by the build.

Usage:
  python scripts/recover_bg.py reference/9-glass-jars.pdf 0 assets/bg/glass.png
"""
import sys

import cv2
import fitz
import numpy as np


def recover(pdf_path, page_index, out_png, scale=2.0):
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    pm = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    img = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pm.n == 3 else cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # mask bright text + purple stars (light pixels over the dark brand bg)
    _, mask = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)
    out = cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA)
    out = cv2.GaussianBlur(out, (0, 0), 3)
    cv2.imwrite(out_png, out)
    print(f"wrote {out_png} ({out.shape[1]}x{out.shape[0]})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    recover(sys.argv[1], int(sys.argv[2]), sys.argv[3])
