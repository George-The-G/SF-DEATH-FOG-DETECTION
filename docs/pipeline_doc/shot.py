"""Fresh screenshots of the beta viewer (read-only: opens the public page)."""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent / "out"
URL = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/?beta=1&lab=1"
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(URL, wait_until="networkidle", timeout=90000)
    time.sleep(25)
    pg.screenshot(path=str(OUT / "shot_beta_now.png"))
    try:
        pg.click("#fogbtn", timeout=5000); time.sleep(1.5)
        pg.evaluate("document.querySelector('#foglab details').open = true"); time.sleep(1)
        pg.locator("#foglab").screenshot(path=str(OUT / "shot_foglab.png"))
    except Exception as e:
        print("lab", e)
    b.close()
print("done")
