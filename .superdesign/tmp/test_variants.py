from playwright.sync_api import sync_playwright
import json

VARIANTS = {
  "A_img_aspect": """
    .pose-thumb { aspect-ratio: auto; overflow: visible; }
    .pose-thumb img { height: auto !important; aspect-ratio: 3 / 4 !important; object-fit: contain !important; }
  """,
  "B_grid_auto_rows_max": """
    #pose-grid { grid-auto-rows: max-content !important; }
  """,
  "C_keep_box_pixel": """
    .pose-thumb { aspect-ratio: auto !important; }
    .pose-thumb img { height: 191px !important; }
  """,
}

def state(pg):
    return pg.evaluate("""() => {
      const grid = document.querySelector('#pose-grid');
      const cards = [...grid.querySelectorAll('.pose-card')].slice(0, 4).map(c => Math.round(c.getBoundingClientRect().y));
      const rows = getComputedStyle(grid).gridTemplateRows.split(' ').slice(0, 3);
      return { scrollH: grid.scrollHeight, cardY: cards, rows, h0: Math.round(grid.querySelector('.pose-card').getBoundingClientRect().height) };
    }""")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:8710/", wait_until="networkidle")
    pg.click("#btn-gen-pose-add")
    pg.wait_for_selector(".pose-card")
    pg.wait_for_timeout(400)
    print("BASELINE", json.dumps(state(pg)))
    for name, css in VARIANTS.items():
        pg.evaluate("""(css) => {
          let s = document.getElementById('fixprobe');
          if (!s) { s = document.createElement('style'); s.id = 'fixprobe'; document.head.appendChild(s); }
          s.textContent = css;
        }""", css)
        pg.wait_for_timeout(150)
        print(name, json.dumps(state(pg)))
    b.close()
