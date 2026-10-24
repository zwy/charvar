"""贴字预设（presets/overlays/）：解析 / 文本来源 / PIL 渲染 / run 级应用。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from PIL import Image

from charvar import overlay as overlay_service
from charvar import storage
from charvar.models import GenerateResult, PromptItem
from charvar.preset_loader import PresetError


_YAML = """
name: demo_bl
type: overlay
version: 1
description: 测试款
position: left_bottom
margin_x: 0.05
margin_y: 0.05
font: ""
size: 0.05
color: "#FFFFFF"
stroke: 0.0
stroke_color: "#000000"
plate: pill
plate_color: "#11223366"
plate_padding: 0.01
opacity: 0.9
rotate: 0.0
max_width: 0.5
align: center
line_gap: 0.1
"""


def _write_overlay(tmp_path: Path) -> Path:
    path = tmp_path / "demo_bl.yaml"
    path.write_text(_YAML, encoding="utf-8")
    return path


def _run_fixture(tmp_path, monkeypatch, n_items=2, db_name="db.sqlite"):
    """建一个带 2 张成品图的临时 run（CHARVAR_DATA_DIR/预预设目录都指向 tmp）。

    CHARVAR_PRESETS_DIR 指向 tmp 后，贴字预设 demo_bl 从 tmp 的 overlays/ 读——
    不动仓库的 presets/；paths 动态读 env，无需重载模块。
    db_name：api 测试里 build_app 用 test.db，这里对齐它。
    """
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    fixtures_presets = tmp_path / "presets"
    fixtures_presets.mkdir(exist_ok=True)
    (fixtures_presets / "overlays").mkdir(exist_ok=True)
    (fixtures_presets / "overlays" / "demo_bl.yaml").write_text(_YAML, encoding="utf-8")
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(fixtures_presets))
    run_id = "demo_overlay_run"
    original_dir = overlay_service.original_dir(run_id)
    original_dir.mkdir(parents=True, exist_ok=True)
    for index in range(n_items):
        Image.new("RGBA", (320, 480), (120, 30, 30, 255)).save(
            original_dir / f"{index:06d}.png"
        )
    items = [
        PromptItem(
            index=index,
            variant_id=vid,
            prompt="p",
            master="m",
            variant_text="t",
            background=None,
            lighting=None,
            caption="c",
            meta={"pose": f"pose_{vid}", "outfit_label": f"服装{vid}"},
        )
        for index, vid in enumerate(("sheer_black__wave", "sheer_nude__wave"))
    ]
    result = GenerateResult(run_id=run_id, subject_id="demo", preset="demo", seed=1, items=items)
    with storage.connect(tmp_path / db_name) as conn:
        storage.save_run(conn, result)
    return run_id


def test_parse_overlay_and_list(tmp_path, monkeypatch):
    path = _write_overlay(tmp_path)
    preset = overlay_service.parse_overlay_file(path)
    assert preset.position == "left_bottom"
    assert preset.plate == "pill"

    bad = _YAML + "whatever: 1\n"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(PresetError):
        overlay_service.parse_overlay_file(path)


def test_resolve_text_sources():
    assert overlay_service.resolve_text("fixed", " 固定字 ", {}, "v1") == "固定字"
    assert overlay_service.resolve_text("fixed", "", {}, "v1") is None
    assert overlay_service.resolve_text("outfit_label", None, {"outfit_label": "黑丝"}, "x__y") == "黑丝"
    # 回退：meta 没有 label → 拆组合变体 id 取服装 id
    assert overlay_service.resolve_text("outfit_label", None, {}, "sheer_black__wave") == "sheer_black"
    assert overlay_service.resolve_text("outfit_label", None, {}, "plain") == "plain"
    assert overlay_service.resolve_text("pose_tag", None, {"pose": "wave_hand"}, "v") == "wave_hand"
    assert overlay_service.resolve_text("pose_tag", None, {}, "v") is None
    assert overlay_service.resolve_text("variant_id", None, {}, "a__b") == "a__b"
    with pytest.raises(PresetError):
        overlay_service.resolve_text("nonsense", None, {}, "v")


def test_wrap_text_cjk_and_latin():
    # 每个"字"宽 1：max 5 → 每 5 个字一行
    measure = lambda s: len(s)  # noqa: E731
    assert overlay_service.wrap_text("一二三四五六七", measure, 5) == ["一二三四五", "六七"]
    # 拉丁按词换行，不拆词
    assert overlay_service.wrap_text("hello xworlds", measure, 7) == ["hello", "xworlds"]
    # 显式换行优先生效
    assert overlay_service.wrap_text("ab\ncd", measure, 100) == ["ab", "cd"]
    assert overlay_service.wrap_text("", measure, 10) == [""]


def test_resolve_font_bundled_priority(tmp_path, monkeypatch):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    monkeypatch.setattr(overlay_service, "fonts_dir", lambda: font_dir)
    # 仓库没有该字体 → None
    assert overlay_service.resolve_font("missing_font") is None
    # 带后缀名也命中
    (font_dir / "my.ttf").write_bytes(b"fake")
    assert overlay_service.resolve_font("my.ttf") == font_dir / "my.ttf"
    assert overlay_service.resolve_font("my") == font_dir / "my.ttf"
    # 空 = 自动：返回存在的系统字体（测试机没有就 None，不猜）
    auto = overlay_service.resolve_font("")
    assert auto is None or auto.exists()


def test_render_overlay_changes_pixels(tmp_path):
    auto_font = overlay_service.resolve_font("")
    if auto_font is None:  # 没有任何可用字体的环境跳过（正常 mac/linux/win 都有）
        pytest.skip("本机没有可用的 CJK 字体候选")
    src = tmp_path / "000000.png"
    Image.new("RGBA", (320, 480), (120, 30, 30, 255)).save(src)
    preset = overlay_service.parse_overlay_file(_write_overlay(tmp_path))
    dst = tmp_path / "labeled" / "000000.png"
    overlay_service.render_overlay(src, dst, "黑丝15D", preset, auto_font)
    out = Image.open(dst).convert("RGBA")
    assert out.size == (320, 480)
    # 左下角区域不再是原图纯色（文字/底板盖上了）
    region = out.crop((10, 400, 200, 470))
    assert len(region.getcolors(maxcolors=100000) or []) > 1


def test_run_overlay_flow(tmp_path, monkeypatch):
    """run 级应用：全部条目贴字 → labeled/ 落盘；指定 items 只处理子集。"""
    run_id = _run_fixture(tmp_path, monkeypatch)
    with storage.connect(tmp_path / "db.sqlite") as conn:
        info = overlay_service.apply_overlay(conn, run_id, overlay="demo_bl")
    assert len(info["applied"]) == 2
    assert not info["skipped"]
    # 文本来源 = outfit_label（meta 里有）→ text 是中文 label
    texts = {e["item_index"]: e["text"] for e in info["applied"]}
    assert set(texts.values()) == {"服装sheer_black__wave", "服装sheer_nude__wave"}
    for entry in info["applied"]:
        assert overlay_service.labeled_path(run_id, entry["item_index"]).exists()

    # 指定 items 只处理子集；不存在的 run → 404 语义
    with storage.connect(tmp_path / "db.sqlite") as conn:
        info2 = overlay_service.apply_overlay(conn, run_id, overlay="demo_bl", items=[0])
        assert [e["item_index"] for e in info2["applied"]] == [0]
        with pytest.raises(PresetError):
            overlay_service.apply_overlay(conn, "no_such_run", overlay="demo_bl")

    # 字体解析不到 → 明确报错（禁用系统候选）
    monkeypatch.setattr(overlay_service, "_FONT_CANDIDATES", ())
    with storage.connect(tmp_path / "db.sqlite") as conn:
        with pytest.raises(PresetError, match="字体不可用"):
            overlay_service.apply_overlay(conn, run_id, overlay="demo_bl")


def test_repo_overlay_yaml_parses():
    for name in ("stockings_label_bl", "label_topleft_shadow"):
        preset = overlay_service.load_overlay(name)
        assert preset.type == "overlay"
        assert overlay_service.resolve_font(preset.font) is not None


def test_overlay_presets_not_in_main_listing():
    from charvar.preset_loader import list_presets

    names = {p["name"] for p in list_presets()}
    assert "stockings_label_bl" not in names


# ---- API 冒烟（/overlay-presets 端点 + POST /runs/{id}/overlay） ----

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import pathlib

    from fastapi.testclient import TestClient

    from charvar.api import build_app

    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_api_overlay_endpoint(client, tmp_path, monkeypatch):
    run_id = _run_fixture(tmp_path, monkeypatch, db_name="test.db")  # 对齐 api fixture 的库文件
    listed = client.get("/overlay-presets").json()
    # 预设目录被指到 tmp：这里应看到的正是 tmp fixtures 里那一枚
    assert any(p["name"] == "demo_bl" for p in listed)

    resp = client.post(f"/runs/{run_id}/overlay", json={"overlay": "demo_bl"})
    assert resp.status_code == 200
    info = resp.json()
    assert len(info["applied"]) == 2 and not info["skipped"]
    assert info["applied"][0]["labeled_url"].startswith("/runs/")

    # 产物经资产路由可取（scene 白名单含 labeled）
    url = info["applied"][0]["labeled_url"]
    img = client.get(url)
    assert img.status_code == 200
    assert img.content[:8] == b"\x89PNG\r\n\x1a\n"

    # 未知预设 → 404；未知 run → 404
    assert client.post(f"/runs/{run_id}/overlay", json={"overlay": "nope"}).status_code == 404
    assert (
        client.post("/runs/no_such_run/overlay", json={"overlay": "demo_bl"}).status_code == 404
    )

    # 图集条目带 labeled_url
    images = client.get(f"/runs/{run_id}/images").json()
    labeled = [it.get("labeled_url") for it in images["items"]]
    assert all(labeled)