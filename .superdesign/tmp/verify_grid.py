from playwright.sync_api import sync_playwright
import json, sys

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    pg.goto("http://127.0.0.1:8710/", wait_until="networkidle")
    pg.click("#btn-gen-pose-add")
    pg.wait_for_selector(".pose-card")
    # wait for first 6 thumbs to fully decode
    pg.wait_for_function("""() => {
      const imgs = [...document.querySelectorAll('#pose-grid .pose-thumb img')].slice(0, 6);
      return imgs.length >= 6 && imgs.every(i => i.complete && i.naturalWidth > 0);
    }""", timeout=15000)
    pg.wait_for_timeout(1200)
    info = pg.evaluate("""() => {
      const grid = document.querySelector('#pose-grid');
      const card = grid.querySelector('.pose-card');
      const thumb = grid.querySelector('.pose-thumb');
      const img = grid.querySelector('.pose-thumb img');
      const meta = card.querySelector('.image-meta');
      const gcs = getComputedStyle(grid);
      const r = (el) => { const b = el.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; };
      return {
        inner: [window.innerWidth, window.innerHeight],
        grid: { display: gcs.display, cols: gcs.gridTemplateColumns, rect: r(grid), children: grid.childElementCount },
        card: r(card), thumb: r(thumb), img: r(img), meta: r(meta),
        natural: [img.naturalWidth, img.naturalHeight],
        scrollHeight: grid.scrollHeight
      };
    }""")
    print(json.dumps(info))
    pg.screenshot(path="/Users/zwy/Sites/charvar/.superdesign/tmp/pw-grid-1440.png")
    b.close()
