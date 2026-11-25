"""Browser smoke test (not collected by pytest). Needs the app running on :8080 with mock backend.

    pip install playwright      # uses the locally installed Edge, no browser download
    python tests/e2e_smoke.py
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
OUT.mkdir(exist_ok=True)
errors = []

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")
    page = b.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto("http://localhost:8080/login")
    page.screenshot(path=str(OUT / "1-login.png"))
    page.fill("input[name=username]", "demo")
    page.fill("input[name=password]", "demo123")
    page.click("button[type=submit]")
    page.wait_for_selector("tr[data-act=pick-account]")
    page.screenshot(path=str(OUT / "2-account.png"))

    page.click("tr[data-act=pick-account] >> nth=0")
    page.click("text=Next →")
    page.wait_for_selector("tr[data-act=pick-ag]")
    page.click("tr[data-act=pick-ag] >> nth=0")
    page.screenshot(path=str(OUT / "3-target.png"))
    page.click("text=Next →")
    page.wait_for_selector("input[data-act=toggle-camp]")
    page.click("input[data-act=toggle-camp] >> nth=0")
    page.click("text=Fetch keywords")
    page.wait_for_selector("input[data-act=toggle-kw]")
    page.screenshot(path=str(OUT / "4-source.png"))
    page.click("#stage >> text=Next →")
    page.screenshot(path=str(OUT / "5-strategy.png"))
    page.click("#stage >> text=Review →")
    page.click("text=Run policy check")
    page.wait_for_selector("text=asset groups to create")
    page.screenshot(path=str(OUT / "6-review.png"), full_page=True)
    page.click("text=Remove them from my selection")
    page.click("text=Run policy check")
    page.wait_for_selector("text=No policy problems found")
    page.click("#stage >> text=Continue →")
    page.wait_for_selector("text=Writes are disabled")
    page.screenshot(path=str(OUT / "7-run.png"))

    page.goto("http://localhost:8080/admin")
    page.wait_for_selector("#emails li")
    page.fill("#add-email input", "someone@client.org")
    page.click("#add-email button")
    page.wait_for_selector("text=someone@client.org")
    page.screenshot(path=str(OUT / "8-admin.png"), full_page=True)
    b.close()

print("console/page errors:", errors or "none")
sys.exit(1 if errors else 0)
