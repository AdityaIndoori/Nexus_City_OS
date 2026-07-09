"""Mirror the product landing page to docs/ (GitHub Pages).

GitHub Pages serves an always-up copy of platform/ui/landing.html:
  * __CONSOLE_URL__ -> the Access-gated live console hostname
  * /landing-assets/ -> relative landing-assets/ (copied into docs/)
"""
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "platform" / "ui" / "landing.html"
ASSETS = ROOT / "platform" / "ui" / "landing-assets"
DOCS = ROOT / "docs"
CONSOLE = "https://nexus.aindoori.com/"

html = SRC.read_text(encoding="utf-8")
html = html.replace("__CONSOLE_URL__", CONSOLE)
html = html.replace('"/landing-assets/', '"landing-assets/')
(DOCS / "index.html").write_text(html, encoding="utf-8")

dst = DOCS / "landing-assets"
dst.mkdir(exist_ok=True)
n = 0
for png in ASSETS.glob("*.png"):
    shutil.copy2(png, dst / png.name)
    n += 1
print(f"docs/index.html written ({len(html)} bytes); {n} assets copied")