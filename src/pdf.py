"""HTML -> PDF via headless Chrome (Letter portrait, print backgrounds on)."""
import os
import shutil
import subprocess

def _candidates():
    return [
        os.environ.get("CHROME_BIN", ""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome") or "",
        shutil.which("chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    ]


def find_chrome():
    for c in _candidates():
        if c and os.path.exists(c):
            return c
    raise SystemExit("FATAL: headless Chrome/Chromium not found for PDF export "
                     "(set CHROME_BIN to its path).")


def html_to_pdf(chrome, html_path, pdf_path):
    html_path = os.path.abspath(html_path)
    pdf_path = os.path.abspath(pdf_path)
    cmd = [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--print-to-pdf-no-header",
        f"--print-to-pdf={pdf_path}", f"file://{html_path}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not os.path.exists(pdf_path):
        raise SystemExit(f"FATAL: Chrome failed to produce {pdf_path}\n{r.stderr[-800:]}")
    return pdf_path
