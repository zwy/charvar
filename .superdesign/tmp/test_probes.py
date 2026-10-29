from playwright.sync_api import sync_playwright
import json

PROBES = {
  "E_cards_block": ".image-card.pose-card { display: block !important; }",
  "F_no_overflow_hidden": ".image-card.pose-card { overflow: visible !important; }",
  "G_card_flex_only": ".image-card.pose-card { display: flex !important; flex-direction: row !important; }",
  "H_remove_gap": "#pose-grid { gap: 0 !important; }",
  "I_rows_minmax": "#pose-grid { grid-auto-rows: minmax(max-content, max-content) !important; }",
  "J_no_align_start": "#pose-grid { align-items: stretch !important; }",
}

def state(pg):
    return pg.evaluate("""() => {
      const grid = document.querySelector('#pose-grid');
      const card = grid.querySelector('.pose-card');
      const cards = [...grid.querySelectorAll('.pose-card')].slice(0, 4).map(c => Math.round(c.getBoundingClientRect().y));
      const rows = getComputedStyle(grid).gridTemplateRows.split(' ').slice(0, 2);
      return { scrollH: grid.scrollHeight, cardY: cards, rows, h0: Math.round(card.getBoundingClientRect().height) };
    }""")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:8710/", wait_until="networkidle")
    pg.click("#btn-gen-pose-add")
    pg.wait_for_selector(".pose-card")
    pg.wait_for_timeout(400)
    print("BASELINE", json.dumps(state(pg)))
    for name, css in PROBES.items():
        pg.evaluate("""(css) => {
          let s = document.getElementById('fixprobe');
          if (!s) { s = document.createElement('style'); s.id = 'fixprobe'; document.head.appendChild(s); }
          s.textContent = css;
        }""", css)
        pg.wait_for_timeout(150)
        print(name, json.dumps(state(pg)))
    b.close()
