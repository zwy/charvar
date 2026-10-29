"""真多页前端的结构校验：页面 html / 路由表 / nav.js 三处同步 + 引用的静态资源真实存在。"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from charvar.api import build_app, _PAGE_ROUTES

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "web" / "static"

# 页面名 → 该页必须包含的主元素 id（抽几个关键 id 防串页/缺页）
PAGE_NEEDLE_IDS = {
    "gen": ["gen-subject", "gen-preset", "push-result", "btn-contact-sheet"],
    "adhoc": ["adhoc-prompt", "adhoc-ref-list"],
    "video": ["video-prompt", "video-frame-list"],
    "tts": ["tts-text", "tts-voice"],
    "recipes": ["recipes-table", "recipe-runs-table"],
    "subjects": ["s-id", "subject-image-slots"],
    "poses": ["pose-page-root"],
    "costumes": ["costume-page-root"],
    "runs": ["runs-table"],
    "settings": ["set-llm-base", "set-or-t2i-model", "settings-sizes"],
}


def test_route_table_covers_all_pages():
    pages = {p.stem for p in STATIC.glob("*.html")}
    routed = {Path(f).stem for f in _PAGE_ROUTES.values()}
    assert pages == routed, f"页面 html 与路由表不一致：{pages ^ routed}"


def test_nav_pages_cover_all_pages():
    nav_js = (STATIC / "nav.js").read_text()
    nav_pages = set(re.findall(r'\["(\w+)",\s*"', nav_js))
    pages = {p.stem for p in STATIC.glob("*.html")}
    assert nav_pages == pages, f"nav.js 与页面 html 不一致：{nav_pages ^ pages}"


def test_routes_serve_pages():
    app = build_app(static_dir=STATIC)
    client = TestClient(app)
    for route in _PAGE_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, route
        assert resp.headers["content-type"].startswith("text/html"), route


def test_page_paths_do_not_shadow_api():
    """页面统一挂在 /ui/ 下 —— 根路径同名 GET 是数据 API（/subjects /runs /tts …），别被页面吞掉。"""
    app = build_app(static_dir=STATIC)
    client = TestClient(app)
    for name in PAGE_NEEDLE_IDS:
        if name == "gen":
            continue
        resp = client.get(f"/{name}")
        assert not resp.headers["content-type"].startswith("text/html"), f"/{name} 被页面路由吞了"


def test_pages_contain_expected_ids():
    app = build_app(static_dir=STATIC)
    client = TestClient(app)
    for route, filename in _PAGE_ROUTES.items():
        needles = PAGE_NEEDLE_IDS[Path(filename).stem]
        html = client.get(route).text
        missing = [nid for nid in needles if f'id="{nid}"' not in html]
        assert not missing, f"{route} 缺元素：{missing}"


def test_page_scripts_exist():
    for page in STATIC.glob("*.html"):
        for src in re.findall(r'src="/static/([^"]+\.js)"', page.read_text()):
            assert (STATIC / src).exists(), f"{page.name} 引用了不存在的 {src}"
        # 每页脚本清单第一位恒为 core.js（$/api/escapeHtml 全站骨架）
        scripts = re.findall(r'<script src="/static/([^"]+)"></script>', page.read_text())
        assert scripts[0] == "core.js", f"{page.name} 的脚本首位不是 core.js"