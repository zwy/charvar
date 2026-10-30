"""服装家族（costume 双表 studio）：加载（DB 视图）/ 笛卡尔积组合 / caption 融合 / 顺序。

仓库镜像 = presets/costumes/studio/*.yaml（charvar costume import-studio 装库）；
DB 是运行时真源。用 tmp env + 镜像自举或直插行验证，不依赖真库。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from charvar import outfit as outfit_service
from charvar import storage
from charvar.caption import build_caption
from charvar.composer import generate
from charvar.models import GenerateRequest, SubjectProfile, VariantSpec
from charvar.preset_loader import (
    ListPreset,
    MatrixPreset,
    PresetError,
)
from charvar.subject_policy import SubjectPolicy


def _outfit() -> outfit_service.OutfitPreset:
    """组合引擎测试用的家族（原 demo_outfits yaml 等价物）。"""
    return outfit_service.OutfitPreset(
        name="demo_outfits",
        description="测试服装",
        items=[
            outfit_service.OutfitItem(id="sheer_black", label="黑丝", text="wearing sheer black nylon pantyhose"),
            outfit_service.OutfitItem(id="white_thick", label="白丝", text="wearing opaque white pantyhose"),
        ],
        caption_tags={"sheer_black": "sheer black pantyhose"},
    )


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


# ---------- DB 视图加载 ----------

def _seed_outfits(name: str, items: list[dict], caption_tags: dict | None = None) -> None:
    """往（隔离 env 的）studio 库直插一个家族。items = [{id,label,text,policy?}]。"""
    from charvar import costumes as costumes_service

    with storage.connect() as conn:
        for idx, it in enumerate(items):
            now = storage.utcnow()
            conn.execute(
                """INSERT INTO costume_items
                   (source_id, item_id, display_type, category, label, keywords,
                    file_path, image_path, thumb_path, subject_policy, caption_tags,
                    sort_order, created_at, updated_at)
                   VALUES (?, ?, 'flat_lay', ?, ?, ?, '', '', '', ?, ?, ?, ?, ?)""",
                (
                    costumes_service.STUDIO_SOURCE_ID,
                    f"{name}_{it['id']}",
                    name,
                    it["label"],
                    it["text"],
                    json.dumps(it["policy"], ensure_ascii=False) if it.get("policy") else "",
                    json.dumps(caption_tags, ensure_ascii=False) if caption_tags else "",
                    idx,
                    now,
                    now,
                ),
            )
        conn.commit()


@pytest.fixture()
def outfit_db(tmp_path, monkeypatch):
    """隔离数据目录 + 装好仓库镜像服装（DB 是真源，测试不碰真库）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    from charvar import costumes as costumes_service

    with storage.connect() as conn:
        data = costumes_service.import_studio_yaml(conn)
    assert data["added"] >= 5  # 仓库镜像自带的自建服装
    return tmp_path


def test_list_and_load_from_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    _seed_outfits(
        "demo_outfits",
        [
            {"id": "sheer_black", "label": "黑丝", "text": "wearing sheer black nylon pantyhose"},
            {"id": "white_thick", "label": "白丝", "text": "wearing opaque white pantyhose"},
        ],
        caption_tags={"sheer_black": "sheer black pantyhose"},
    )
    listed = outfit_service.list_outfit_presets()
    assert len(listed) == 1
    assert listed[0]["name"] == "demo_outfits" and listed[0]["count"] == 2
    assert listed[0]["items"][0] == {"id": "sheer_black", "label": "黑丝"}
    loaded = outfit_service.load_outfit("demo_outfits")
    assert [i.id for i in loaded.items] == ["sheer_black", "white_thick"]  # sort_order 次序
    assert [i.label for i in loaded.items] == ["黑丝", "白丝"]
    assert loaded.caption_tags["sheer_black"] == "sheer black pantyhose"

    # 文本层优先（DB 编辑实时进批）
    with storage.connect() as conn:
        from charvar import costumes as costumes_service

        costumes_service.set_costume_prompt(
            conn, "studio", "demo_outfits_sheer_black", "edited sheer black text"
        )
    reloaded = outfit_service.load_outfit("demo_outfits")
    assert reloaded.items[0].text == "edited sheer black text"
    assert reloaded.items[1].text == "wearing opaque white pantyhose"


def test_load_rejects_bad_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    # 非法 native id（CJK/点号）
    _seed_outfits("bad_ids", [{"id": "黑丝.中", "label": "x", "text": "text here"}])
    with pytest.raises(PresetError):
        outfit_service.load_outfit("bad_ids")
    # （id 重复在 DB 主键层就挡掉，加载时的重复校验只对跨前缀剥离失配等极端情况兜底）
    # 文本全空
    _seed_outfits("blank_text", [{"id": "b", "label": "B", "text": "   "}])
    with pytest.raises(PresetError):
        outfit_service.load_outfit("blank_text")
    # 未知家族 → 404
    with pytest.raises(PresetError) as exc_info:
        outfit_service.load_outfit("no_such_family")
    assert getattr(exc_info.value, "status", None) == 404


# ---------- 笛卡尔积组合（引擎不受存储切换影响） ----------

def test_combine_outfit_first_order_and_text():
    combined = outfit_service.combine(_pose_preset(), _outfit(), "outfit_first")
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


def test_combine_pose_first_order():
    combined = outfit_service.combine(_pose_preset(), _outfit(), "pose_first")
    assert [s.id for s in combined.variants] == [
        "wave_hand__sheer_black", "wave_hand__white_thick",
        "stand_still__sheer_black", "stand_still__white_thick",
    ]


def test_combine_rejects_unknown_order():
    with pytest.raises(PresetError):
        outfit_service.combine(_pose_preset(), _outfit(), "random")


def test_matrix_pose_preset_combines():
    """matrix 预设（维度组合）同样能 × 服装家族。"""
    raw = __import__("yaml").safe_load("""
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
    combined = outfit_service.combine(parsed, _outfit(), "outfit_first")
    assert [s.id for s in combined.variants] == ["sheer_black__full_front", "white_thick__full_front"]


def test_generate_run_and_meta(silver_witch):
    """组合走 composer.generate：caption 带 outfit token，meta 带服装与中文 label。

    run_id 里组合名的 + 必须已消毒（assets.safe_run_id 只认字母数字 _ . -）——
    否则推送 ComfyUI 落盘那一刻才炸。
    """
    combined = outfit_service.combine(_pose_preset(), _outfit(), "outfit_first")
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


def test_stockings_family_from_mirror(outfit_db):
    """仓库镜像里的 stockings_8 装库后加载合法（8 档、label 齐全、次序 = 播放次序）。"""
    preset = outfit_service.load_outfit("stockings_8")
    assert [i.id for i in preset.items] == [
        "sheer_black_15d", "sheer_nude_20d", "sheer_white", "sheer_grey",
        "opaque_black_80d", "fishnet_black", "thigh_highs_black", "thigh_highs_white",
    ]
    assert all(i.label for i in preset.items)
    assert all(len(i.text) <= 200 for i in preset.items)  # 稀释红线：服装段必须短


def test_outfit_presets_not_in_main_listing():
    """服装家族不混进主预设下拉（/outfit-presets 是独立端点）。"""
    from charvar.preset_loader import list_presets

    names = {p["name"] for p in list_presets()}
    assert "stockings_8" not in names


# ---- API 冒烟（/outfit-presets 端点 + /generate 带服装组合） ----

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    from charvar.api import build_app

    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def _seed_client_studio(client):
    from charvar import costumes as costumes_service

    with storage.connect() as conn:  # env 已隔离：与 build_app 的库同目录约定
        data = costumes_service.import_studio_yaml(conn)
    assert data["added"] >= 5


def test_api_outfit_endpoints(client):
    _seed_client_studio(client)
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
    _seed_client_studio(client)
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

    # 未知服装家族 → 404；sticker 预设 + 服装 → 400
    bad = client.post("/generate", json={**req, "outfit_preset": "no_such_outfit"})
    assert bad.status_code == 404

    sticker_req = {**req, "preset": "chat_replies", "outfit_preset": "stockings_8"}
    resp = client.post("/generate", json=sticker_req)
    if resp.status_code != 400:  # 仓库若没有 sticker 预设就不验这条
        raise AssertionError(f"sticker+outfit 应被拒绝：{resp.status_code}")


# ---------- 中文服装段 / 位置 / 主体锁策略 ----------

_CHINESE_OUTFIT = (
    "该成年角色仅穿着一条素色、不透明的中腰灰色内裤，上身赤裸，赤足；"
    "除此之外，未穿戴任何其他衣物、袜类、鞋履、头饰、眼镜、首饰或发饰。"
)


def test_chinese_outfit_coverage():
    """中文服装段也要能判定「写进去了没有」——旧实现只认英文词，中文恒判已覆盖。"""
    from charvar.outfit import outfit_covered, outfit_keywords

    assert outfit_covered(_CHINESE_OUTFIT, _CHINESE_OUTFIT)
    # 整段丢弃 → 判定没覆盖（否则程序永远不会补写，服装丢了都不知道）
    assert not outfit_covered("人物正面朝向镜头直立站立。", _CHINESE_OUTFIT)
    # 换个说法写 → 仍算覆盖
    assert outfit_covered(
        "该角色只穿一条灰色内裤，上身赤裸，双脚赤足，没有穿其它衣物、袜子、鞋子、帽子、眼镜或首饰。",
        _CHINESE_OUTFIT,
    )
    # 关键词含中文二元组
    assert any(len(k) == 2 and "一" <= k[0] <= "鿿" for k in outfit_keywords(_CHINESE_OUTFIT))


def test_outfit_text_can_be_chinese():
    """服装段的语言不限：中文服装段能装载、能组合（不译写）。"""
    preset = outfit_service.OutfitPreset(
        name="demo_cn_outfits",
        items=[outfit_service.OutfitItem(id="briefs", label="仅内裤", text=_CHINESE_OUTFIT)],
    )
    assert preset.items[0].text == _CHINESE_OUTFIT
    merged = outfit_service.combine(_pose_preset(), preset)
    assert merged.variants[0].outfit_text == _CHINESE_OUTFIT


def test_combine_carries_subject_policy():
    """服装条目声明的 subject_policy 要透传到变体。"""
    preset = outfit_service.OutfitPreset(
        name="demo_policy_outfits",
        items=[
            outfit_service.OutfitItem(
                id="briefs",
                label="仅内裤",
                text="wearing grey briefs only, barefoot",
                subject_policy=SubjectPolicy(clothing="replace", accessories="remove"),
            )
        ],
    )
    merged = outfit_service.combine(_pose_preset(), preset)
    assert merged.variants[0].outfit_policy == {
        "clothing": "replace",
        "accessories": "remove",
    }


def test_classic_prompt_puts_outfit_first(outfit_db):
    """classic：服装段是提示词第一句，动作段在其后。"""
    from charvar.composer import generate
    from charvar.models import SubjectProfile

    sub = SubjectProfile(id="demo", name="演示", subject_noun="woman")
    preset = outfit_service.combine(_pose_preset(), outfit_service.load_outfit("stockings_8"))
    result = generate(sub, preset, GenerateRequest(subject=sub, seed=7, count=1))
    prompt = result.items[0].prompt
    # stockings_8 已打叠加策略：classic 拼装给服装段带「保留原衣着」前缀
    # （subject_policy.CLOTHING_ADD_LEAD），随后仍是 wearing 服装段
    assert prompt.startswith("保留角色原有衣着不更换，仅在腿部叠加/换穿：wearing sheer black nylon pantyhose")
    assert prompt.index("wearing sheer black nylon pantyhose") < prompt.index("人物正面朝向镜头站立")


def test_barefoot_outfit_replaces_shoe_wording():
    """赤足服装：构图句里的「鞋底 / soles of the shoes」换成脚的措辞。"""
    from charvar.composer import barefootize, is_barefoot_outfit

    assert is_barefoot_outfit(_CHINESE_OUTFIT)
    assert not is_barefoot_outfit("wearing a black bikini, no stockings")
    assert barefootize("from the top of the head to the soles of the shoes") == (
        "from the top of the head to the soles of the feet"
    )
    assert barefootize("双脚并拢、鞋底相贴、脚尖朝前") == "双脚并拢、脚底相贴、脚尖朝前"
    # 负向禁令不动
    assert "socks, shoes, legs" in barefootize("no lower body, socks, shoes, legs")
