"""服装预设（presets/outfits/）：解析校验 / 笛卡尔积组合 / caption 融合 / 顺序。"""
from __future__ import annotations

from pathlib import Path

import pytest

from charvar import outfit as outfit_service
from charvar.caption import build_caption
from charvar.composer import generate
from charvar.models import GenerateRequest, SubjectProfile, VariantSpec
from charvar.preset_loader import (
    ListPreset,
    MatrixPreset,
    PresetError,
    load,
)


_OUTFIT_YAML = """
name: demo_outfits
type: outfit
version: 1
description: 测试服装
items:
- id: sheer_black
  label: 黑丝
  text: wearing sheer black nylon pantyhose
- id: white_thick
  label: 白丝
  text: wearing opaque white pantyhose
caption_tags:
  sheer_black: sheer black pantyhose
"""


def _write_outfit(tmp_path: Path) -> Path:
    path = tmp_path / "demo_outfits.yaml"
    path.write_text(_OUTFIT_YAML, encoding="utf-8")
    return path


def _pose_preset() -> ListPreset:
    return ListPreset(
        type="list",
        name="demo_pose",
        master_template="Create an image of the {subject_noun} from {reference_tag}.",
        suffix_template="The background is {background}. The lighting is {lighting}.",
        caption_tags={"pose": {"wave_hand": "waving hand"}},
        variants=[
            VariantSpec(
                id="wave_hand",
                text="人物正面朝向镜头站立，单手举起挥手。",
                framing="full",
                angle="front",
                pose="wave_hand",
                tags=["full", "action", "wave_hand"],
            ),
            VariantSpec(
                id="stand_still",
                text="人物正面朝向镜头站立。",
                framing="full",
                angle="front",
                pose="stand_still",
                tags=["full", "action", "stand_still"],
            ),
        ],
    )


def test_parse_and_list(tmp_path, monkeypatch):
    path = _write_outfit(tmp_path)
    preset = outfit_service.parse_outfit_file(path)
    assert preset.name == "demo_outfits"
    assert [i.id for i in preset.items] == ["sheer_black", "white_thick"]
    assert [i.label for i in preset.items] == ["黑丝", "白丝"]

    def fake_dir():
        return tmp_path

    monkeypatch.setattr(outfit_service, "outfits_dir", fake_dir)
    listed = outfit_service.list_outfit_presets()
    assert len(listed) == 1
    assert listed[0]["count"] == 2
    assert listed[0]["items"][0]["label"] == "黑丝"


def test_parse_rejects_bad_fields(tmp_path):
    bad = _OUTFIT_YAML.replace("label: 黑丝\n  text: wearing sheer black nylon pantyhose",
                               "label: 黑丝\n  text: wearing sheer black nylon pantyhose\n- id: 黑丝.中\n  label: x\n  text: y")
    path = tmp_path / "bad.yaml"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(PresetError):
        outfit_service.parse_outfit_file(path)

    dup = _OUTFIT_YAML.replace("- id: white_thick", "- id: sheer_black")
    path.write_text(dup, encoding="utf-8")
    with pytest.raises(PresetError):
        outfit_service.parse_outfit_file(path)


def test_combine_outfit_first_order_and_text(tmp_path):
    outfit = outfit_service.parse_outfit_file(_write_outfit(tmp_path))
    combined = outfit_service.combine(_pose_preset(), outfit, "outfit_first")
    assert isinstance(combined, ListPreset)
    assert combined.name == "demo_pose+demo_outfits"
    ids = [s.id for s in combined.variants]
    # outfit_first：黑丝（2 动作）→ 白丝（2 动作）
    assert ids == [
        "sheer_black__wave_hand", "sheer_black__stand_still",
        "white_thick__wave_hand", "white_thick__stand_still",
    ]
    first = combined.variants[0]
    # 服装段追加在动作段之后
    assert first.text == "人物正面朝向镜头站立，单手举起挥手。 wearing sheer black nylon pantyhose"
    assert first.outfit == "sheer_black"
    assert first.outfit_label == "黑丝"
    assert first.tags[-1] == "sheer_black"
    # caption_tags 合并：保留动作预设映射 + outfit 组
    assert combined.caption_tags["pose"]["wave_hand"] == "waving hand"
    assert combined.caption_tags["outfit"]["sheer_black"] == "sheer black pantyhose"
    # 画幅声明照抄动作预设
    assert combined.preferred_aspect_ratio == _pose_preset().preferred_aspect_ratio


def test_combine_pose_first_order(tmp_path):
    outfit = outfit_service.parse_outfit_file(_write_outfit(tmp_path))
    combined = outfit_service.combine(_pose_preset(), outfit, "pose_first")
    assert [s.id for s in combined.variants] == [
        "wave_hand__sheer_black", "wave_hand__white_thick",
        "stand_still__sheer_black", "stand_still__white_thick",
    ]


def test_combine_rejects_unknown_order(tmp_path):
    outfit = outfit_service.parse_outfit_file(_write_outfit(tmp_path))
    with pytest.raises(PresetError):
        outfit_service.combine(_pose_preset(), outfit, "random")


def test_matrix_pose_preset_combines(tmp_path):
    """matrix 预设（维度组合）同样能 × 服装预设。"""
    import yaml

    raw = yaml.safe_load("""
name: demo_matrix
type: matrix
version: 1
master_template: 'm'
suffix_template: 's'
dimensions:
  framing:
    full: "Full-body shot"
  angle:
    front: "facing camera"
rules:
- product: {framing: [full], angle: "*"}
""")
    parsed = MatrixPreset.model_validate(
        {**raw, "dimensions": raw["dimensions"], "rules": raw["rules"]}
    )
    outfit = outfit_service.parse_outfit_file(_write_outfit(tmp_path))
    combined = outfit_service.combine(parsed, outfit, "outfit_first")
    ids = [s.id for s in combined.variants]
    assert ids == ["sheer_black__full_front", "white_thick__full_front"]


def test_generate_run_and_meta(silver_witch, tmp_path, monkeypatch):
    """组合走 composer.generate：caption 带 outfit token，meta 带服装与中文 label。

    run_id 里组合名的 + 必须已消毒（assets.safe_run_id 只认字母数字 _ . -）——
    否则推送 ComfyUI 落盘那一刻才炸。
    """
    outfit = outfit_service.parse_outfit_file(_write_outfit(tmp_path))
    combined = outfit_service.combine(_pose_preset(), outfit, "outfit_first")
    req = GenerateRequest(subject=silver_witch, seed=7)
    result = generate(silver_witch, combined, req)
    assert result.preset == "demo_pose+demo_outfits"
    from charvar.assets import safe_run_id

    safe_run_id(result.run_id)  # 不抛 = 合法
    assert "+" not in result.run_id
    assert len(result.items) == 4
    item = result.items[0]
    assert "sheer black pantyhose" in item.caption
    assert item.meta["outfit"] == "sheer_black"
    assert item.meta["outfit_label"] == "黑丝"
    # caption 顺序：角度之后是 pose，再是 outfit
    parts = item.caption.split(", ")
    assert parts.index("waving hand") < parts.index("sheer black pantyhose")
    # 可复现：同种子两次完全一致
    again = generate(silver_witch, combined, GenerateRequest(subject=silver_witch, seed=7))
    assert result.items == again.items


def test_repo_stockings_yaml_parses():
    """仓库自带的 stockings_8 解析合法（8 档、label 齐全）。"""
    preset = outfit_service.load_outfit("stockings_8")
    assert len(preset.items) == 8
    assert all(i.label for i in preset.items)
    assert all(len(i.text) <= 200 for i in preset.items)  # 稀释红线：服装段必须短
    ids = {i.id for i in preset.items}
    assert {"sheer_black_15d", "fishnet_black", "thigh_highs_white"} <= ids


def test_outfit_presets_not_in_main_listing(monkeypatch, tmp_path):
    """服装预设住在 presets/outfits/ 子目录，不混进主预设下拉。"""
    from charvar.preset_loader import list_presets

    names = {p["name"] for p in list_presets()}
    assert "stockings_8" not in names


# ---- API 冒烟（/outfit-presets 端点 + /generate 带服装组合） ----

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import pathlib

    from fastapi.testclient import TestClient

    from charvar.api import build_app

    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_api_outfit_endpoints(client):
    listed = client.get("/outfit-presets").json()
    assert isinstance(listed, list)
    assert any(p["name"] == "stockings_8" for p in listed)
    assert client.get("/outfit-presets")  # 200 语义


def _make_subject(client):
    profile = {
        "id": "demo_witch",
        "name": "演示",
        "subject_noun": "woman",
        "trigger_word": "sw1tch",
        "style_lock": ["the color palette"],
        "identity_lock": ["long silver-white hair"],
    }
    assert client.post("/subjects", json=profile).status_code == 201


def test_api_generate_with_outfit(client):
    _make_subject(client)
    # 白裙丝袜动作预设（3 变体）× stockings_8（8 件）= 24 条
    req = {
        "subject_id": "demo_witch",
        "preset": "white_skirt_stockings_poses",
        "seed": 42,
        "outfit_preset": "stockings_8",
        "outfit_order": "outfit_first",
    }
    gen = client.post("/generate", json=req)
    assert gen.status_code == 200
    result = gen.json()
    assert len(result["items"]) == 24
    assert result["preset"] == "white_skirt_stockings_poses+stockings_8"
    first = result["items"][0]
    assert first["variant_id"].startswith("sheer_black_15d__")
    assert first["meta"]["outfit_label"] == "黑丝15D"
    assert "sheer black" in first["caption"] or "sheer_black_15d" in first["caption"]

    # 未知服装预设 → 404；sticker 预设 + 服装 → 400
    bad = client.post("/generate", json={**req, "outfit_preset": "no_such_outfit"})
    assert bad.status_code == 404

    sticker_req = {**req, "preset": "chat_replies_v1_12", "outfit_preset": "stockings_8"}
    resp = client.post("/generate", json=sticker_req)
    if resp.status_code != 400:  # 仓库若没有 sticker 预设就不验这条
        raise AssertionError(f"sticker+outfit 应被拒绝：{resp.status_code}")