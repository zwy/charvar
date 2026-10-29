from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:8710/", wait_until="networkidle")
    pg.click("#btn-gen-pose-add")
    pg.wait_for_selector(".pose-card")
    pg.wait_for_function("""() => {
      const imgs = [...document.querySelectorAll('#pose-grid .pose-thumb img')].slice(0, 9);
      return imgs.length >= 9 && imgs.every(i => i.complete && i.naturalWidth > 0);
    }""", timeout=20000)
    pg.wait_for_timeout(800)
    print(json.dumps(pg.evaluate("""() => {
      const grid = document.querySelector('#pose-grid');
      const ys = [...grid.querySelectorAll('.pose-card')].slice(0, 9).map(c => Math.round(c.getBoundingClientRect().y - grid.getBoundingClientRect().y));
      const hs = [...grid.querySelectorAll('.pose-card')].slice(0, 3).map(c => Math.round(c.getBoundingClientRect().height));
      const rows = getComputedStyle(grid).gridTemplateRows.split(' ').slice(0, 2);
      const img = grid.querySelector('.pose-thumb img');
      const r = img.getBoundingClientRect();
      return { cols: getComputedStyle(grid).gridTemplateColumns.split(' ').length, cardY: ys, cardH: hs, rows, imgRect: [Math.round(r.width), Math.round(r.height)], scrollH: grid.scrollHeight };
    }""")))
    pg.screenshot(path="/Users/zwy/Sites/charvar/.superdesign/tmp/pw-final-top.png")
    # narrow viewport: media query path
    pg.set_viewport_size({"width": 620, "height": 900})
    pg.wait_for_timeout(300)
    print("NARROW", json.dumps(pg.evaluate("""() => {
      const grid = document.querySelector('#pose-grid');
      return { cols: getComputedStyle(grid).gridTemplateColumns.split(' ').length, cardW: Math.round(grid.querySelector('.pose-card').getBoundingClientRect().width) };
    }""")))
    b.close()
