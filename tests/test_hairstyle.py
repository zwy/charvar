"""发型预设（presets/hairstyles/）：解析校验 / 笛卡尔积组合 / caption 融合 / 层级。"""
from __future__ import annotations

from pathlib import Path

import pytest

from charvar import hairstyle as hairstyle_service
from charvar.caption import build_caption
from charvar.composer import generate
from charvar.models import GenerateRequest, SubjectProfile, VariantSpec
from charvar.preset_loader import (
    ListPreset,
    MatrixPreset,
    PresetError,
    load,
)


_HAIRSTYLE_YAML = """
name: demo_hairstyles
type: hairstyle
version: 1
description: 测试发型
items:
- id: french_bob
  label: 法式波波头
  text: chin-length French bob with light wispy bangs
- id: loose_waves
  label: 慵懒大波浪
  text: long continuous S-shaped waves with a side part
caption_tags:
  french_bob: chin-length French bob
"""


def _write_hairstyle(tmp_path: Path) -> Path:
    path = tmp_path / "demo_hairstyles.yaml"
    path.write_text(_HAIRSTYLE_YAML, encoding="utf-8")
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
    path = _write_hairstyle(tmp_path)
    preset = hairstyle_service.parse_hairstyle_file(path)
    assert preset.name == "demo_hairstyles"
    assert [i.id for i in preset.items] == ["french_bob", "loose_waves"]
    assert [i.label for i in preset.items] == ["法式波波头", "慵懒大波浪"]

    def fake_dir():
        return tmp_path

    monkeypatch.setattr(hairstyle_service, "hairstyles_dir", fake_dir)
    listed = hairstyle_service.list_hairstyle_presets()
    assert len(listed) == 1
    assert listed[0]["count"] == 2
    assert listed[0]["items"][0]["label"] == "法式波波头"


def test_parse_rejects_bad_fields(tmp_path):
    bad = _HAIRSTYLE_YAML.replace(
        "text: chin-length French bob with light wispy bangs",
        "text: chin-length French bob with light wispy bangs\n- id: 法式.波波\n  label: x\n  text: y",
    )
    path = tmp_path / "bad.yaml"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(PresetError):
        hairstyle_service.parse_hairstyle_file(path)

    dup = _HAIRSTYLE_YAML.replace("- id: loose_waves", "- id: french_bob")
    path.write_text(dup, encoding="utf-8")
    with pytest.raises(PresetError):
        hairstyle_service.parse_hairstyle_file(path)


def test_combine_ids_text_order_captions(tmp_path):
    """发型层组合：id={发型}__{动作}，text=动作+发型，caption_tags 合并 hairstyle 组。"""
    hair = hairstyle_service.parse_hairstyle_file(_write_hairstyle(tmp_path))
    combined = hairstyle_service.combine(_pose_preset(), hair)
    assert isinstance(combined, ListPreset)
    assert combined.name == "demo_pose+demo_hairstyles"
    ids = [s.id for s in combined.variants]
    # 发型层固定外层循环（每款发型连播全部动作），与服装 outfit_first 同构
    assert ids == [
        "french_bob__wave_hand", "french_bob__stand_still",
        "loose_waves__wave_hand", "loose_waves__stand_still",
    ]
    first = combined.variants[0]
    # 发型段追加在动作段之后；分列留档
    assert first.text == "人物正面朝向镜头站立，单手举起挥手。 chin-length French bob with light wispy bangs"
    assert first.action_text == "人物正面朝向镜头站立，单手举起挥手。"
    assert first.hairstyle_text == "chin-length French bob with light wispy bangs"
    assert first.hairstyle == "french_bob"
    assert first.hairstyle_label == "法式波波头"
    assert first.tags[-1] == "french_bob"
    # caption_tags 合并：保留动作预设映射 + hairstyle 组
    assert combined.caption_tags["pose"]["wave_hand"] == "waving hand"
    assert combined.caption_tags["hairstyle"]["french_bob"] == "chin-length French bob"
    # 画幅声明照抄动作预设
    assert combined.preferred_aspect_ratio == _pose_preset().preferred_aspect_ratio


def test_combine_matrix_pose_preset(tmp_path):
    """matrix 预设（维度组合）同样能 × 发型预设。"""
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
    hair = hairstyle_service.parse_hairstyle_file(_write_hairstyle(tmp_path))
    combined = hairstyle_service.combine(parsed, hair)
    ids = [s.id for s in combined.variants]
    assert ids == ["french_bob__full_front", "loose_waves__full_front"]


def test_layered_combine_hair_then_outfit(tmp_path):
    """发型层先并、服装层后并：层级固定 动作→发型→服装（id 与文本顺序都落位）。"""
    from charvar import outfit as outfit_service

    hair = hairstyle_service.parse_hairstyle_file(_write_hairstyle(tmp_path))
    pose = _pose_preset()
    hair_combined = hairstyle_service.combine(pose, hair)

    outfit = outfit_service.OutfitPreset(
        name="demo_skirt",
        description="测试服装",
        items=[outfit_service.OutfitItem(id="mini_skirt", label="短裙", text="wearing a brick-red mini skirt")],
    )

    combined = outfit_service.combine(hair_combined, outfit, "outfit_first")
    ids = [s.id for s in combined.variants]
    # id 层级：服装最外 → 发型 → 动作
    assert ids == [
        "mini_skirt__french_bob__wave_hand", "mini_skirt__french_bob__stand_still",
        "mini_skirt__loose_waves__wave_hand", "mini_skirt__loose_waves__stand_still",
    ]
    first = combined.variants[0]
    # text = 动作 + 发型 + 服装
    assert first.text == (
        "人物正面朝向镜头站立，单手举起挥手。 chin-length French bob with light wispy bangs"
        " wearing a brick-red mini skirt"
    )
    # 三段分列：动作原文 / 发型原文 / 服装原文
    assert first.action_text == "人物正面朝向镜头站立，单手举起挥手。"
    assert first.hairstyle_text == "chin-length French bob with light wispy bangs"
    assert first.outfit_text == "wearing a brick-red mini skirt"
    # caption_tags 保留两层映射
    assert combined.caption_tags["hairstyle"]["french_bob"] == "chin-length French bob"
    assert "outfit" in combined.caption_tags


def test_caption_hairstyle_token_position(tmp_path):
    """caption：发型 token 在 pose 之后、outfit 之前；无发型时不出发型位。"""
    hair = hairstyle_service.parse_hairstyle_file(_write_hairstyle(tmp_path))
    combined = hairstyle_service.combine(_pose_preset(), hair)
    subject = SubjectProfile(id="s", name="n", subject_noun="woman", trigger_word="sw1tch")
    spec = combined.variants[0]
    caption = build_caption(subject, spec, "beach", "soft light", combined.caption_tags)
    parts = caption.split(", ")
    assert parts.index("waving hand") < parts.index("chin-length French bob")

    # 无发型组合的普通预设：caption 无发型段
    plain = build_caption(subject, _pose_preset().variants[0], None, None, {"pose": {"wave_hand": "waving hand"}})
    assert "chin-length French bob" not in plain


def test_generate_run_and_meta(silver_witch, tmp_path):
    """组合走 composer.generate：caption 带发型 token，meta 带发型 id 与中文 label。"""
    hair = hairstyle_service.parse_hairstyle_file(_write_hairstyle(tmp_path))
    combined = hairstyle_service.combine(_pose_preset(), hair)
    req = GenerateRequest(subject=silver_witch, seed=7)
    result = generate(silver_witch, combined, req)
    assert result.preset == "demo_pose+demo_hairstyles"
    from charvar.assets import safe_run_id

    safe_run_id(result.run_id)  # 不抛 = 合法
    assert "+" not in result.run_id
    assert len(result.items) == 4
    item = result.items[0]
    assert "chin-length French bob" in item.caption
    assert item.meta["hairstyle"] == "french_bob"
    assert item.meta["hairstyle_label"] == "法式波波头"
    # 可复现：同种子两次完全一致
    again = generate(silver_witch, combined, GenerateRequest(subject=silver_witch, seed=7))
    assert result.items == again.items


def test_repo_hairstyle_yaml_parses():
    """仓库自带的 popular_hairstyles_v1_4 解析合法（4 款、label 齐全、发型段短）。"""
    preset = hairstyle_service.load_hairstyle("popular_hairstyles_v1_4")
    assert len(preset.items) == 4
    assert all(i.label for i in preset.items)
    assert all(len(i.text) <= 200 for i in preset.items)  # 稀释红线：发型段必须短
    ids = {i.id for i in preset.items}
    assert {"french_bob", "collarbone_layers", "butterfly_cut", "loose_waves"} == ids


# ---- API 冒烟（/hairstyle-presets 端点 + /generate 带发型组合） ----

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import pathlib

    from fastapi.testclient import TestClient

    from charvar.api import build_app

    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_api_hairstyle_endpoints(client):
    listed = client.get("/hairstyle-presets").json()
    assert isinstance(listed, list)
    assert any(p["name"] == "popular_hairstyles_v1_4" for p in listed)
    assert client.get("/hairstyle-presets")  # 200 语义


def _make_subject(client):
    profile = {
        "id": "demo_witch",
        "name": "演示",
        "subject_noun": "woman",
        "trigger_word": "sw1tch",
        "style_lock": ["the color palette"],
        "identity_lock": ["long silver-white hair color"],
    }
    assert client.post("/subjects", json=profile).status_code == 201


def test_api_generate_with_hairstyle(client):
    from charvar import costumes as costumes_service
    from charvar import storage

    with storage.connect() as conn:
        costumes_service.import_studio_yaml(conn)
    _make_subject(client)
    # three_pose_pack（3 变体）× popular_hairstyles_v1_4（4 款）= 12 条
    req = {
        "subject_id": "demo_witch",
        "preset": "three_pose_pack",
        "seed": 42,
        "hairstyle_preset": "popular_hairstyles_v1_4",
    }
    gen = client.post("/generate", json=req)
    assert gen.status_code == 200, gen.text
    result = gen.json()
    assert len(result["items"]) == 12
    assert result["preset"] == "three_pose_pack+popular_hairstyles_v1_4"
    first = result["items"][0]
    assert first["variant_id"].startswith("french_bob__")
    assert first["meta"]["hairstyle_label"] == "法式波波头"
    # 发型与服装同给：总条数 = 3 × 4 × 8，id 层级 outfit__hairstyle__pose
    both = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "three_pose_pack",
            "seed": 42,
            "hairstyle_preset": "popular_hairstyles_v1_4",
            "outfit_preset": "stockings_8",
        },
    )
    assert both.status_code == 200
    both_result = both.json()
    assert len(both_result["items"]) == 96
    assert both_result["preset"] == "three_pose_pack+popular_hairstyles_v1_4+stockings_8"
    ids = [it["variant_id"] for it in both_result["items"]]
    # id 层级：服装最外（outfit_first）→ 发型 → 动作
    assert ids[0].startswith("sheer_black_15d__french_bob__")
    assert ids[0].count("__") == 2

    # 未知发型预设 → 404；sticker 预设 + 发型 → 400
    bad = client.post("/generate", json={**req, "hairstyle_preset": "no_such_hairstyle"})
    assert bad.status_code == 404

    sticker_req = {**req, "preset": "chat_replies", "hairstyle_preset": "popular_hairstyles_v1_4"}
    resp = client.post("/generate", json=sticker_req)
    if resp.status_code != 400:  # 仓库若没有 sticker 预设就不验这条
        raise AssertionError(f"sticker+hairstyle 应被拒绝：{resp.status_code}")