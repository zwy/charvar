"""姿势库（pose-db-01）：导入校验 / 浏览清单 / 参考图解析 / 动作库 API。

数据真源在磁盘 catalog.json（scripts/download_pose_db.py 抓取），DB 只是索引。
这里用 tmp 目录造一份小 catalog 验证全链路，不发真网。
"""
from __future__ import annotations

import io
import json
import pathlib
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import poses as poses_service
from charvar import storage
from charvar.api import build_app
from tests.test_adhoc import _mock_comfy, _png_bytes, _setup_workflows


# ---------- 造假姿势库 ----------

def _build_fake_pose_data(
    data_dir: pathlib.Path, *, counts: dict | None = None, source: str | None = None
) -> pathlib.Path:
    """按下载布局造一个小姿势库：catalog.json + img/*.png + thumbs/*.jpg。

    counts: {pose_id: 张数}；缺省 2 个动作（一个 5 视角、一个 4 视角）。
    source 缺省线稿库（同 id 多库测试里传 pose-db-02 造深度库版本）。
    """
    src = data_dir / "poses" / (source or poses_service.SOURCE_ID)
    (src / "img").mkdir(parents=True, exist_ok=True)
    (src / "thumbs").mkdir(parents=True, exist_ok=True)
    plan = counts or {"full_sexy_P001": 5, "full_sexy_P002": 4, "pair60_P010": 5}
    suffixes = ["front", "high", "side", "diag", "low"]
    items: list[dict] = []
    for pose_id, n in plan.items():
        for s_index in range(n):
            suffix = suffixes[s_index % len(suffixes)]
            item_id = f"{pose_id}_{suffix}"
            name = f"{pose_id}_{s_index}"  # 同 suffix 防改名时撞文件
            img_rel = f"img/{item_id}.png"
            thumb_rel = f"thumbs/{item_id}.jpg"
            Image.new("RGB", (90, 120), (120, 120, 120)).save(src / img_rel, format="PNG")
            Image.new("RGB", (30, 40), (120, 120, 120)).save(src / thumb_rel, format="JPEG")
            items.append(
                {
                    "id": item_id,
                    "系統": "全身1人・セクシー系" if "sexy" in pose_id else "2人絡み",
                    "ポーズ番号": pose_id.split("_")[-1],
                    "分類": "立ち" if "sexy" in pose_id else "正常位",
                    "ポーズ名": f"{pose_id} 的动作描述",
                    "アングル": poses_service.ANGLE_ORDER[s_index % len(suffixes)],
                    "サムネ": thumb_rel,
                    "原寸": img_rel,
                }
            )
    (src / "catalog.json").write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8"
    )
    return src


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------- 导入 ----------

def test_import_catalog_loads_and_is_idempotent(data_dir):
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        info = poses_service.import_catalog(conn)
        assert info["poses"] == 3 and info["images"] == 14
        again = poses_service.import_catalog(conn)
        assert again["images"] == 14  # upsert 不翻倍
        row = conn.execute(
            "SELECT line, title, category FROM pose_images WHERE pose_id='pair60_P010' AND angle='正面'"
        ).fetchone()
        assert row["line"] == "2人絡み" and row["category"] == "正常位"


def test_import_catalog_rejects_missing_image(data_dir):
    src = _build_fake_pose_data(data_dir)
    (src / "img" / "full_sexy_P001_front.png").unlink()
    with storage.connect() as conn:
        with pytest.raises(poses_service.PoseError):
            poses_service.import_catalog(conn)
        assert conn.execute("SELECT COUNT(*) FROM pose_images").fetchone()[0] == 0  # 空库


def test_import_catalog_rejects_bad_id_and_unknown_angle(data_dir):
    src = _build_fake_pose_data(data_dir)
    catalog = json.loads((src / "catalog.json").read_text(encoding="utf-8"))
    catalog[0]["id"] = "bad/id:../../etc"
    (src / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    with storage.connect() as conn:
        with pytest.raises(poses_service.PoseError):
            poses_service.import_catalog(conn)

    catalog = json.loads((src / "catalog.json").read_text(encoding="utf-8"))
    catalog[1]["アングル"] = "斜上方"  # 枚举外视角
    (src / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    with storage.connect() as conn:
        with pytest.raises(poses_service.PoseError):
            poses_service.import_catalog(conn)


# ---------- 浏览 ----------

def test_list_poses_groups_and_orders(data_dir):
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        data = poses_service.list_poses(conn)
    # 系統页签按库嵌套：线稿库次序 = LINE_ORDER（未收录系統按字符串退居其后）
    assert [l["id"] for l in data["lines"]["pose-db-01"]] == ["全身1人・セクシー系", "2人絡み"]
    poses = data["poses"]
    assert len(poses) == 3
    by_id = {p["pose_id"]: p for p in poses}
    # 视角按 ANGLE_ORDER 次序：正面永远第一；4 张的动作缺末位视角
    assert by_id["full_sexy_P001"]["images"][0]["angle"] == "正面"
    assert [im["angle"] for im in by_id["full_sexy_P002"]["images"]] == ["正面", "上から", "横", "斜め"]
    assert by_id["full_sexy_P001"]["images"][0]["thumb_url"].startswith("/poses/image/pose-db-01/full_sexy_P001/正面/thumb")
    # zh 映射：未知原文回退原文
    assert by_id["pair60_P010"]["category_zh"] == "正常位"
    assert by_id["pair60_P010"]["line_zh"] == "双人"
    # 过滤
    with storage.connect() as conn:
        sexy = poses_service.list_poses(conn, line="全身1人・セクシー系")
        stand = poses_service.list_poses(conn, category="立ち")
        hit = poses_service.list_poses(conn, q="full_sexy_P001")
        miss = poses_service.list_poses(conn, category="座り")
    assert len(sexy["poses"]) == 2 and len(stand["poses"]) == 2
    assert len(hit["poses"]) == 1 and miss["poses"] == []


def test_unknown_values_fall_back_to_original_text(data_dir):
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        data = poses_service.list_poses(conn)
    # 假造的ポーズ名/分類都按原文返回（映射缺项不炸）
    assert all(p["title"] == f"{p['pose_id']} 的动作描述" for p in data["poses"])


# ---------- 中日对照（title_zh 译文表） ----------

def test_pose_title_zh_glossary_is_wellformed():
    from charvar.pose_translations import POSE_TITLE_ZH

    assert len(POSE_TITLE_ZH) >= 288  # 站点 288 个动作全覆盖
    for pose_id, zh in POSE_TITLE_ZH.items():
        assert re.fullmatch(r"[A-Za-z0-9_]+", pose_id), pose_id
        assert zh.strip() and re.search(r"[一-鿿]", zh), (pose_id, zh)


def test_title_zh_fallback_and_mapping(data_dir, monkeypatch):
    from charvar import pose_translations

    _build_fake_pose_data(data_dir)
    monkeypatch.setitem(pose_translations.POSE_TITLE_ZH, "full_sexy_P001", "S型站立·腰向侧面顶出")
    monkeypatch.setitem(pose_translations.POSE_TITLE_ZH, "pair60_P010", "")  # 空译文 = 回退原文
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        data = poses_service.list_poses(conn)
    by_id = {p["pose_id"]: p for p in data["poses"]}
    assert by_id["full_sexy_P001"]["title_zh"] == "S型站立·腰向侧面顶出"
    # 无译文 / 空译文回退日文原文（映射缺项不炸）
    assert by_id["pair60_P010"]["title_zh"] == by_id["pair60_P010"]["title"]
    assert poses_service.title_zh("日文原题", "no_such_id") == "日文原题"


def test_title_zh_covers_real_catalog_when_present():
    """真实库在盘上时校验译文覆盖度（站点加新动作而译文缺项会先在这里炸）。"""
    from charvar.pose_translations import POSE_TITLE_ZH

    catalog = poses_service.catalog_path()
    if not catalog.exists():
        pytest.skip("真实 catalog 不在盘上（未跑下载脚本）")
    items = json.loads(catalog.read_text(encoding="utf-8"))
    ids = {poses_service.pose_id_from_item_id(item["id"]) for item in items}
    missing = ids - set(POSE_TITLE_ZH)
    assert not missing, f"缺 {len(missing)} 条译文：{sorted(missing)[:5]}"


# ---------- 动作描述（pose_actions：动作×视角 双键 + PUT /poses/{pose_id}/action） ----------

def test_pose_action_double_key_roundtrip(data_dir):
    """双键：同动作各视角独立读写；空串 = 清该视角；重灌索引不碰用户文案。"""
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        poses_service.set_pose_action(conn, "full_sexy_P001", "正面", "身体基本朝向镜头，呈 S 形曲线。")
        poses_service.set_pose_action(conn, "full_sexy_P001", "上から", "俯拍：脚部靠近镜头，头顶在画面深处。")
        got_front = poses_service.pose_action(conn, "full_sexy_P001", "正面")
        assert got_front["action_text"] == "身体基本朝向镜头，呈 S 形曲线。" and got_front["angle"] == "正面"
        assert poses_service.pose_action(conn, "full_sexy_P001", "上から")["action_text"].startswith("俯拍")
        # 没写 = 空串，不回退（描述与参考图必须同视角）
        assert poses_service.pose_action(conn, "full_sexy_P001", "横")["action_text"] == ""
        # 清一个视角不影响另一个
        poses_service.set_pose_action(conn, "full_sexy_P001", "上から", "")
        assert conn.execute(
            "SELECT COUNT(*) FROM pose_actions WHERE pose_id='full_sexy_P001'"
        ).fetchone()[0] == 1
        # 重灌索引（pose import 幂等 upsert）不碰用户文案
        poses_service.import_catalog(conn)
        assert poses_service.pose_action(conn, "full_sexy_P001", "正面")["action_text"] == "身体基本朝向镜头，呈 S 形曲线。"
        # 非法 id / 非法视角 / 库里没有的组合
        with pytest.raises(poses_service.PoseError):
            poses_service.set_pose_action(conn, "bad/id!", "正面", "x")
        with pytest.raises(poses_service.PoseError):
            poses_service.set_pose_action(conn, "full_sexy_P001", "斜上方", "x")
        with pytest.raises(poses_service.PoseError):
            poses_service.set_pose_action(conn, "full_sexy_P002", "下から", "x")  # P002 只有 4 视角
        with pytest.raises(poses_service.PoseError):
            poses_service.set_pose_action(conn, "full_sexy_P999", "正面", "x")


def test_legacy_pose_level_rows_migrate_to_front(data_dir):
    """旧单键库的存量行迁到（pose_id, 正面）—— 用户文案不丢。"""
    import sqlite3

    _build_fake_pose_data(data_dir)  # 先有 catalog，connect 迁移 + import 后才能读
    db = data_dir / "legacy.db"
    legacy_schema = """
    CREATE TABLE IF NOT EXISTS pose_actions (
        pose_id TEXT PRIMARY KEY,
        action_text TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """
    with sqlite3.connect(db) as raw:
        raw.executescript(legacy_schema)
        raw.execute(
            "INSERT INTO pose_actions (pose_id, action_text, source, created_at, updated_at)"
            " VALUES ('full_sexy_P001', '旧动作级描述', 'manual', '2026-10-29 00:00:00', '2026-10-29 00:00:00')"
        )
        raw.commit()
    with storage.connect(db) as conn:
        poses_service.import_catalog(conn)
        data = poses_service.pose_action(conn, "full_sexy_P001", "正面")
        assert data["action_text"] == "旧动作级描述" and data["angle"] == "正面"


def test_list_poses_action_text_on_images_and_search(data_dir):
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        poses_service.set_pose_action(conn, "full_sexy_P001", "正面", "腰胯轻微扭转，呈自然的 S 形曲线")
        poses_service.set_pose_action(conn, "full_sexy_P001", "横", "侧面：右臂近端叉腰")
        data = poses_service.list_poses(conn)
    by_id = {p["pose_id"]: p for p in data["poses"]}
    imgs = {im["angle"]: im for im in by_id["full_sexy_P001"]["images"]}
    assert imgs["正面"]["action_text"] == "腰胯轻微扭转，呈自然的 S 形曲线"
    assert imgs["横"]["action_text"] == "侧面：右臂近端叉腰"
    assert imgs["斜め"]["action_text"] == ""
    # 文本在 images[] 视角上，pose 级不再带（视角级契约）
    assert "action_text" not in by_id["pair60_P010"]
    # 搜索命中动作描述文本（任意视角都命中）
    hit = poses_service.list_poses(conn, q="右臂近端")
    assert [p["pose_id"] for p in hit["poses"]] == ["full_sexy_P001"]
    assert poses_service.list_poses(conn, q="完全不相干的词")["poses"] == []


def test_pose_action_double_key_endpoints(data_dir, client):
    _build_fake_pose_data(data_dir)
    with storage.connect(pathlib.Path(data_dir) / "test.db") as conn:
        poses_service.import_catalog(conn)
    resp = client.put("/poses/pose-db-01/full_sexy_P001/action", json={"angle": "正面", "action_text": "S 型站立"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["angle"] == "正面"
    listing = client.get("/poses").json()
    pose = next(p for p in listing["poses"] if p["pose_id"] == "full_sexy_P001")
    assert pose["images"][0]["action_text"] == "S 型站立"
    got = client.get("/poses/pose-db-01/full_sexy_P001/action")  # 缺省正面
    assert got.status_code == 200 and got.json()["action_text"] == "S 型站立"
    assert client.put(
        "/poses/pose-db-01/full_sexy_P001/action", json={"angle": "上から", "action_text": "俯拍视角"}
    ).status_code == 200
    assert client.get("/poses/pose-db-01/full_sexy_P001/action?angle=上から").json()["action_text"] == "俯拍视角"
    # 空串 = 清掉该视角，GET 仍 200 空串
    assert client.put(
        "/poses/pose-db-01/full_sexy_P001/action", json={"angle": "上から", "action_text": ""}
    ).status_code == 200
    assert client.get("/poses/pose-db-01/full_sexy_P001/action?angle=上から").json()["action_text"] == ""
    # 非法角度 → 400；库里没有的组合/动作 → 404；非法 id → 400
    assert client.put(
        "/poses/pose-db-01/full_sexy_P001/action", json={"angle": "斜上方", "action_text": "x"}
    ).status_code == 400
    assert client.put(
        "/poses/pose-db-01/full_sexy_P002/action", json={"angle": "下から", "action_text": "x"}
    ).status_code == 404  # P002 只有 4 视角
    assert client.put("/poses/pose-db-01/full_sexy_P999/action", json={"action_text": "x"}).status_code == 404
    assert client.get("/poses/pose-db-01/does.not.exist/action").status_code == 400


# ---------- 动作描述 → 出图提示词（pose_prompt 分段模板） ----------

def test_pose_prompt_builds_by_line_kind():
    from charvar import pose_prompt

    full = pose_prompt.build_prompt("全身1人・セクシー系", "呈自然的 S 形曲线")
    assert full.startswith("<image1>为角色设定参考")  # 主体锚定段在最前
    assert "摆出<image2>图中的姿势：呈自然的 S 形曲线" in full
    assert "采用全身构图" in full and full.count("背景简洁，突出角色。") == 1
    upper = pose_prompt.build_prompt("上半身1人", "双手在头顶上方合成心形")
    assert "采用半身构图" in upper and "不裁切手指与手臂" in upper
    pair = pose_prompt.build_prompt("2人絡み", "后方角色环抱前方角色")
    assert "双人姿势（描述中的甲、乙两位都由该角色担任）：后方角色环抱前方角色" in pair
    assert "采用双人全身构图" in pair and "画面固定为两个角色" in pair
    # 双人锚定句点明两位沿用同一角色外观；未收录线回退全身
    assert "两位角色都沿用该外观与服装" in pair
    assert pose_prompt.kind_of_line("乱写的线") == "full"
    assert pose_prompt.build_prompt("乱写的线", "x") != pair
    # pair 双主体：image1/image2 分别锚定甲乙，线稿顺移成 image3；非 pair 不吃这个开关
    pair2 = pose_prompt.build_prompt("2人絡み", "甲从身后环抱乙", pair_two_subjects=True)
    assert pair2.startswith("<image1>为角色设定参考（担当描述中的「甲」）")
    assert "<image2>为角色设定参考（担当描述中的「乙」）" in pair2
    assert "<image3>为姿势参考" in pair2 and "担任描述里的「甲」" in pair2
    full_still = pose_prompt.build_prompt("全身1人・標準系", "x", pair_two_subjects=True)
    assert "<image3>" not in full_still


def test_subject_slot_for_prompt_upper_prefers_headshot(data_dir):
    """半身动作主体参考图用 headshot 槽位；没有半身档位图回退 front；全身/双人恒 front。"""
    with storage.connect() as conn:
        conn.execute("DELETE FROM subject_images WHERE subject_id = 'u_char'")
        storage.upsert_subject_image(conn, {
            "subject_id": "u_char", "kind": "headshot", "status": "ready",
            "source": "comfyui", "file_path": "subjects/u_char/headshot.png",
        })
        storage.upsert_subject_image(conn, {
            "subject_id": "u_char", "kind": "front", "status": "ready",
            "source": "comfyui", "file_path": "subjects/u_char/front.png",
        })
        # 上半身动作 → headshot
        label, row = poses_service.subject_slot_for_prompt(conn, "u_char", "上半身1人")
        assert label == "headshot" and row["file_path"] == "subjects/u_char/headshot.png"
        # 全身 / 未收录线 → front（即便有 headshot 行）
        for line in ("全身1人・セクシー系", "2人絡み", "乱写的线"):
            label, row = poses_service.subject_slot_for_prompt(conn, "u_char", line)
            assert label == "front" and row["file_path"] == "subjects/u_char/front.png"
        # 没有 headshot 行 → 回退 front；完全没有档案时行也是 None
        conn.execute(
            "DELETE FROM subject_images WHERE subject_id = 'u_char' AND kind = 'headshot'"
        )
        conn.commit()
        label, row = poses_service.subject_slot_for_prompt(conn, "u_char", "上半身1人")
        assert label == "front" and row["file_path"] == "subjects/u_char/front.png"
        label, row = poses_service.subject_slot_for_prompt(conn, "nobody", "上半身1人")
        assert label == "front" and row is None  # 连 front 都没有：报错信息指向 front


# ---------- LLM 起稿（pose_describe，mock 通路） ----------

def test_pose_describe_saves_ai_draft_and_validates(data_dir, monkeypatch):
    import charvar.pose_describe as pd

    _build_fake_pose_data(data_dir)
    seen: dict = {}

    def fake_llm(messages, model=None):
        seen["instr"] = messages[0]["content"][0]["text"]
        seen["has_image"] = any(p.get("type") == "image_url" for p in messages[0]["content"])
        return "她直立朝向镜头，双臂轻贴身侧，双脚平踩地面。整体是安静的待机站姿。"

    monkeypatch.setattr(pd, "_call_llm", fake_llm)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        r = pd.describe_one(conn, "full_sexy_P001", "正面")
        assert r["status"] == "done" and r["text"].startswith("她直立")
        # 视觉消息（带图）+ 样板注入
        assert seen["has_image"] and "样板" in seen["instr"] and "甲（后方人物）" in seen["instr"]
        src = conn.execute(
            "SELECT source FROM pose_actions WHERE pose_id='full_sexy_P001' AND angle='正面'"
        ).fetchone()["source"]
        assert src == "ai_draft"
        # 幂等：已写 → skipped；--overwrite 重写
        assert pd.describe_one(conn, "full_sexy_P001", "正面")["status"] == "skipped"
        assert pd.describe_one(conn, "full_sexy_P001", "正面", overwrite=True)["status"] == "done"
        # 双人缺甲乙 → 校验失败
        r_pair = pd.describe_one(conn, "pair60_P010", "正面")
        assert r_pair["status"] == "error" and "甲/乙" in r_pair["error"]
        # dry-run 不落库
        r_dry = pd.describe_one(conn, "full_sexy_P002", "正面", dry_run=True)
        assert r_dry["status"] == "draft"
        assert conn.execute(
            "SELECT COUNT(*) FROM pose_actions WHERE pose_id='full_sexy_P002'"
        ).fetchone()[0] == 0


def test_pose_describe_batch_counts(data_dir, monkeypatch):
    import charvar.pose_describe as pd

    _build_fake_pose_data(data_dir)
    monkeypatch.setattr(pd, "_call_llm", lambda messages, model=None: "她侧转身躯，双臂交叠在胸前，双腿并拢收立，头微微低垂朝向镜头方向。整体是收身站立的安静站姿。")
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        summary = pd.describe_batch(conn, limit=2)
        assert summary["done"] == 2 and summary["errors"] == 0
        again = pd.describe_batch(conn)
        # 剩余的 pair60_P010：假稿没有甲乙 → 双人校验拦下（errors），不误入库
        assert again["done"] == 0 and again["errors"] == 1
        assert conn.execute("SELECT COUNT(*) FROM pose_actions").fetchone()[0] == 2


# ---------- 参考图解析 ----------

def test_full_and_thumb_file_resolution(data_dir):
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
    full = poses_service.full_image_file("full_sexy_P001", "上から")
    assert full.name == "full_sexy_P001_high.png" and full.exists()
    thumb = poses_service.thumb_image_file("full_sexy_P001", "横")
    assert thumb.suffix == ".jpg" and thumb.exists()
    # 缺省正面；未知视角 / 非法 id 报错
    assert poses_service.full_image_file("full_sexy_P001", "").name == "full_sexy_P001_front.png"
    with pytest.raises(poses_service.PoseError):
        poses_service.full_image_file("full_sexy_P001", "斜上方")
    with pytest.raises(poses_service.PoseError):
        poses_service.full_image_file("full_sexy../../P001", "正面")


def test_prepared_reference_file_makes_jpeg_cache(data_dir):
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
    prepared = poses_service.prepared_reference_file("full_sexy_P001", "正面")
    assert prepared.parent.name == "_ref_cache" and prepared.suffix == ".jpg"
    with Image.open(prepared) as im:
        assert im.format == "JPEG"


# ---------- /poses 端点 + /adhoc/generate 参考图通路 ----------

@pytest.fixture()
def client(data_dir, monkeypatch):
    _setup_workflows(data_dir, monkeypatch)
    monkeypatch.setenv("COMFYUI_HOST", "127.0.0.1")
    monkeypatch.setenv("COMFYUI_PORT", str(59999))
    _mock_comfy(monkeypatch, [], [])
    db_path = pathlib.Path(data_dir) / "test.db"
    with storage.connect(db_path) as conn:
        storage.set_setting(conn, "comfy_t2i_workflow", "TestT2I")
        storage.set_setting(conn, "comfy_edit_workflow", "TestEdit")
    app = build_app(static_dir=None, db_path=db_path)
    return TestClient(app)


def test_poses_endpoints_serve_catalog_and_images(data_dir, client):
    _build_fake_pose_data(data_dir)
    # 浏览/取图端点读的是 app 的库（fixture 里建在 test.db），灌同一份
    with storage.connect(pathlib.Path(data_dir) / "test.db") as conn:
        poses_service.import_catalog(conn)
    resp = client.get("/poses")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source"] == "pose-db-01"
    assert len(data["poses"]) == 3
    pose = next(p for p in data["poses"] if p["pose_id"] == "full_sexy_P001")
    assert pose["images"][0]["angle"] == "正面"
    front = pose["images"][0]
    thumb = client.get(front["thumb_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    full = client.get(front["full_url"])
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    assert client.get("/poses/image/pose-db-01/full_sexy_P010/正面/thumb").status_code == 404
    # 视角不在库里 → 404
    bad = client.get("/poses/image/pose-db-01/full_sexy_P002/下から/thumb")
    assert bad.status_code == 404


def test_adhoc_generate_with_pose_reference(data_dir, client, monkeypatch):
    """Playground i2i 带 pose 参考：解析到姿势库文件 + 上传 ComfyUI。"""
    _build_fake_pose_data(data_dir)
    with storage.connect(pathlib.Path(data_dir) / "test.db") as conn:
        poses_service.import_catalog(conn)
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    resp = client.post(
        "/adhoc/generate",
        json={
            "prompt": "mannequin pose study",
            "backend": "comfyui",
            "references": [{"pose_id": "full_sexy_P001", "angle": "横", "role": "pose"}],
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["status"] == "ready"
    # role 缺省 pose 来源 → pose；上传的文件名带唯一前缀
    assert uploads and uploads[0].endswith(".jpg")


def test_generate_llm_rejects_bad_role_but_accepts_pose(data_dir, client):
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
    # role 非法 → 400
    resp = client.post(
        "/generate",
        json={
            "mode": "classic",
            "preset": "q21_default_30",
            "references": [{"pose_id": "full_sexy_P001", "angle": "正面", "role": "headshot"}],
        },
    )
    assert resp.status_code == 400, resp.text
    # role=pose 合法（classic 模式只存规格不解析 —— 推送时才解析）
    resp = client.post(
        "/generate",
        json={
            "mode": "classic",
            "preset": "q21_default_30",
            "references": [{"pose_id": "full_sexy_P001", "angle": "正面", "role": "pose"}],
        },
    )
    assert resp.status_code in (200, 400), resp.text  # 400 = 本环境无主体档案，与 pose 无关


def test_pose_role_clause_in_rewrite_messages(data_dir):
    """角色档案改写：pose 参考图的锚定句/尾句/说明行都按 pose 语义走。"""
    from charvar.llm_prompts import build_rewrite_messages

    subject = _minimal_subject()
    msgs = build_rewrite_messages(
        subject,
        "模板",
        [{"kind": "pose", "tag": "<image1>"}],
        [{"id": "v1", "text": "站着", "framing": "full", "angle": "front", "expression": ""}],
    )
    user = msgs[1]["content"]
    assert "人偶线稿" in user  # KIND_LABELS 的 pose 说明行
    assert "mannequin" in user  # pose 分句
    assert "same number of figures" in user  # pose 尾句
    assert "create a new pose" not in user  # 旧尾句不再出现


def _minimal_subject():
    from charvar.models import SubjectProfile

    return SubjectProfile(id="demo", name="Demo")

# ---------- 服装参考（costume，单人生效） ----------

def test_pose_prompt_costume_layer():
    from charvar import pose_prompt

    plain = pose_prompt.build_prompt("全身1人・セクシー系", "呈自然的 S 形曲线")
    dressed = pose_prompt.build_prompt("全身1人・セクシー系", "呈自然的 S 形曲线", costume=True)
    assert "<image3>为服装参考" in dressed and "替换其原有服装" in dressed
    assert "<image3>" not in plain
    # image1 锚定句换服装版：不再锚原衣着
    assert "保留该角色的面部特征、发型、发色、服装、配饰" not in dressed
    assert "其原有服装不保留" in dressed and "以服装参考为准" in dressed
    # 换装禁令进禁令句，无双标点
    assert "两套服装混搭" in dressed and "。；" not in dressed
    # 身材锚段：身材归 image1；body_lock 原文拼接
    assert "身材、身高与四肢比例一律以<image1>为准" in dressed
    plain_guarded = pose_prompt.build_prompt(
        "全身1人・セクシー系", "呈自然的 S 形曲线", costume=True,
        body_lock=["her build is about six heads tall, narrow shoulders"],
    )
    assert "her build is about six heads tall, narrow shoulders" in plain_guarded
    # 锚尾禁句与悬空配件机制句共存：禁句拦凭空添加（平铺实测没禁句会凭空发明包），
    # 采纳靠悬空点名机制句 —— 两者职责不同
    assert "不额外添加无关装饰" in dressed and "悬空放置的配件" in dressed
    # 悬空展示配件机制点名（WIN-0001 贝雷帽实测：不点名参考图里悬空摆放的帽子就丢）
    assert "悬空放置的配件" in dressed and "装到角色身上" in dressed
    assert "清单上列出的每件都要出现在画面里" in dressed
    with pytest.raises(ValueError):
        pose_prompt.build_prompt("2人絡み", "甲环抱乙", costume=True)


def test_pose_prompt_costume_keywords():
    from charvar import pose_prompt

    with_kw = pose_prompt.build_prompt(
        "全身1人・セクシー系", "呈自然的 S 形曲线", costume=True,
        costume_keywords="beige knit green corduroy skirt fur jacket boots bag beret",
    )
    assert "服装要点（英文关键词，与图一致）：beige knit green corduroy skirt fur jacket boots bag beret。" in with_kw
    assert "不缺件、不改部件类型" in with_kw
    # 次序：服装锚 → 要点注记 → 身材锚
    assert with_kw.index("<image3>为服装参考") < with_kw.index("服装要点") < with_kw.index("身材、身高")
    # 关键词缺省/空白 → 注记整节消失（旧行为不变）
    plain = pose_prompt.build_prompt("全身1人・セクシー系", "呈自然的 S 形曲线", costume=True)
    assert "服装要点" not in plain
    blank = pose_prompt.build_prompt(
        "全身1人・セクシー系", "呈自然的 S 形曲线", costume=True, costume_keywords="   ",
    )
    assert "服装要点" not in blank


def test_build_costume_prompt_no_pose():
    from charvar import pose_prompt

    prompt = pose_prompt.build_costume_prompt()
    assert "<image2>为服装参考" in prompt
    assert "<image3>" not in prompt and "摆出" not in prompt
    assert "穿上<image2>图中的服装" in prompt
    assert "采用全身构图" in prompt and "两套服装混搭" in prompt and "。；" not in prompt
    assert "身材、身高与四肢比例一律以<image1>为准" in prompt  # 身材锚段恒在（no-pose 版）
    with_body = pose_prompt.build_costume_prompt(
        ["narrow shoulders, slender body"]
    )
    assert "narrow shoulders, slender body" in with_body
    # 无动作版同一份服装锚（含悬空配件点名）
    assert "悬空放置的配件" in prompt and "装到角色身上" in prompt


def test_costume_display_form_clause():
    from charvar import pose_prompt

    # 缺省 ghost：形态句恒在，且在服装锚之前（「这张图是什么」先说）
    dressed = pose_prompt.build_prompt("全身1人・セクシー系", "呈自然的 S 形曲线", costume=True)
    assert "隐形模特（ghost mannequin）展示图" in dressed
    assert dressed.index("隐形模特") < dressed.index("<image3>为服装参考")
    # flat_lay：换平铺句，ghost 句消失；无动作版同一分叉
    flat = pose_prompt.build_prompt(
        "全身1人・セクシー系", "呈自然的 S 形曲线", costume=True, costume_display="flat_lay",
    )
    assert "服装平铺展示图（俯视平铺陈列）" in flat and "隐形模特" not in flat
    assert "按穿搭关系在角色身上正确组合穿着" in flat
    flat2 = pose_prompt.build_costume_prompt(costume_display="flat_lay")
    assert "服装平铺展示图" in flat2 and "隐形模特" not in flat2
    assert "隐形模特" in pose_prompt.build_costume_prompt()
    # typo 显式报错，不静默退化成 ghost
    with pytest.raises(ValueError):
        pose_prompt.build_prompt(
            "全身1人・セクシー系", "呈自然的 S 形曲线", costume=True, costume_display="hanging",
        )


def test_build_costume_prompt_keywords():
    from charvar import pose_prompt

    with_kw = pose_prompt.build_costume_prompt(costume_keywords="green corduroy skirt")
    assert "<image2>服装要点（英文关键词，与图一致）：green corduroy skirt" in with_kw
    assert "不缺件、不改部件类型" in with_kw
    # 次序：服装锚 → 要点注记 → 身材锚；缺省时不出现
    assert with_kw.index("<image2>为服装参考") < with_kw.index("服装要点")
    assert "服装要点" not in pose_prompt.build_costume_prompt()
# ---------- 换装语义分叉（replace 整套替换 / add 叠加袜类，本批新增） ----------

def test_costume_mode_add_branches():
    """叠加型：原衣着保留、袜类换上；replace 版仍是整套替换（缺省不变）。"""
    from charvar import pose_prompt

    add = pose_prompt.build_costume_prompt(costume_policy={"clothing": "add"})
    assert "其原有服装保留不变" in add          # 身份锚叠加版
    assert "腿上只穿图中这一双袜类" in add      # 禁令叠加版（正向措辞）
    assert "替换原有服装" not in add
    rep = pose_prompt.build_costume_prompt()
    assert "替换原有服装" in rep
    assert "袜类" not in rep
    with pytest.raises(ValueError):
        pose_prompt.build_costume_prompt(costume_policy={"clothing": "bogus"})
    # 工作流①（带动作、<image3>）同样分叉
    p1 = pose_prompt.build_prompt(
        "全身1人・セクシー系", "呈自然的 S 形曲线",
        costume=True, costume_policy={"clothing": "add"},
    )
    assert "把<image3>图中的袜类穿到角色腿上" in p1
    assert "原有衣着与鞋帽、包袋等其它配件保持原样" in p1
    # 无策略 = replace（现有行为零变化）
    assert "替换其原有服装" in pose_prompt.build_prompt(
        "全身1人・セクシー系", "呈自然的 S 形曲线", costume=True
    )
# ---------- 收藏（pose_favorites：用户层，对齐服饰库） ----------

def test_pose_favorites_toggle_and_survive_reimport(data_dir):
    """收藏在独立用户层表：切换一次生效，pose_images 重灌绝不碰；不存在的动作报错。"""
    _build_fake_pose_data(data_dir)
    with storage.connect() as conn:
        poses_service.import_catalog(conn)
        assert poses_service.toggle_favorite(conn, "full_sexy_P001")["favorite"] is True
        assert poses_service.toggle_favorite(conn, "full_sexy_P001")["favorite"] is False
        poses_service.toggle_favorite(conn, "full_sexy_P001")
    with storage.connect() as conn:
        poses_service.import_catalog(conn)  # 重灌（幂等）
        data = poses_service.list_poses(conn)
        row = next(p for p in data["poses"] if p["pose_id"] == "full_sexy_P001")
        assert row["favorited"] is True
        other = next(p for p in data["poses"] if p["pose_id"] != "full_sexy_P001")
        assert other["favorited"] is False


def test_pose_favorite_endpoint(data_dir, client):
    """POST /poses/{pose_id}/favorite 切换 + 清单透出 favorited；未知动作 404。"""
    _build_fake_pose_data(data_dir)
    with storage.connect(pathlib.Path(data_dir) / "test.db") as conn:
        poses_service.import_catalog(conn)
    assert client.post("/poses/pose-db-01/full_sexy_P002/favorite").json() == {"favorited": True}
    listing = client.get("/poses").json()
    row = next(p for p in listing["poses"] if p["pose_id"] == "full_sexy_P002")
    assert row["favorited"] is True
    assert client.post("/poses/pose-db-01/full_sexy_P002/favorite").json() == {"favorited": False}
    assert client.post("/poses/pose-db-01/full_sexy_NOPE/favorite").status_code == 404


# ---------- 多库（pose-db-02 depth 线稿/深度同 pose 集同 id 共存） ----------

def test_pose_multi_source_same_ids(data_dir):
    """同 pose 集的 depth 库与线稿库共存：同 id 分键、display_type 各按库、
    清单双库透出（来源页签/嵌套系統页签数据）。"""
    _build_fake_pose_data(data_dir)
    _build_fake_pose_data(data_dir, source="pose-db-02")
    with storage.connect() as conn:
        assert poses_service.import_catalog(conn, "pose-db-01")["poses"] == 3
        assert poses_service.import_catalog(conn, "pose-db-02")["poses"] == 3
        data = poses_service.list_poses(conn)
        assert {s["id"]: s["poses"] for s in data["sources"]} == {
            "pose-db-01": 3,
            "pose-db-02": 3,
        }
        by_pair: dict = {}
        for p in data["poses"]:
            by_pair.setdefault(p["pose_id"], {})[p["source"]] = p
        assert len(by_pair) == 3  # 同 id 两库各一条，互不覆盖
        seg = by_pair["full_sexy_P001"]
        assert seg["pose-db-01"]["display_type"] == "line"
        assert seg["pose-db-02"]["display_type"] == "depth"
        assert seg["pose-db-02"]["images"][0]["thumb_url"].startswith(
            "/poses/image/pose-db-02/"
        )
        # 系統页签按库嵌套（两库同名，同序同计数）
        assert {k: [l["id"] for l in v] for k, v in data["lines"].items()}
        assert data["lines"]["pose-db-02"][0]["id"] == data["lines"]["pose-db-01"][0]["id"]
        # 未登记库拒绝
        import pytest as _pytest

        with _pytest.raises(poses_service.PoseError):
            poses_service.import_catalog(conn, "pose-db-09")


def test_pose_actions_and_favorites_are_per_source(data_dir):
    """用户层（描述/收藏）按库分键：同一 pose_id 两库互不串。"""
    _build_fake_pose_data(data_dir)
    _build_fake_pose_data(data_dir, source="pose-db-02")
    with storage.connect() as conn:
        poses_service.import_catalog(conn, "pose-db-01")
        poses_service.import_catalog(conn, "pose-db-02")
        poses_service.set_pose_action(conn, "full_sexy_P001", "正面", "线稿库描述")
        poses_service.set_pose_action(
            conn, "full_sexy_P001", "正面", "深度库描述", source_id="pose-db-02"
        )
        assert poses_service.pose_action(conn, "full_sexy_P001", "正面")["action_text"] == "线稿库描述"
        assert (
            poses_service.pose_action(conn, "full_sexy_P001", "正面", "pose-db-02")["action_text"]
            == "深度库描述"
        )
        poses_service.toggle_favorite(conn, "full_sexy_P002")  # 缺省线稿库
        data = poses_service.list_poses(conn)
        for p in data["poses"]:
            want = p["pose_id"] == "full_sexy_P002" and p["source"] == "pose-db-01"
            assert p["favorited"] is want, (p["pose_id"], p["source"], p["favorited"])


def test_pose_favorite_endpoint_per_source(data_dir, client):
    """POST /poses/{source}/{pose_id}/favorite —— 同 id 两库的收藏互不串；未登记库 404。"""
    _build_fake_pose_data(data_dir)
    _build_fake_pose_data(data_dir, source="pose-db-02")
    with storage.connect(pathlib.Path(data_dir) / "test.db") as conn:
        poses_service.import_catalog(conn, "pose-db-01")
        poses_service.import_catalog(conn, "pose-db-02")
    assert client.post("/poses/pose-db-02/full_sexy_P002/favorite").json() == {"favorited": True}
    listing = client.get("/poses").json()
    rows = [p for p in listing["poses"] if p["pose_id"] == "full_sexy_P002"]
    assert {p["source"]: p["favorited"] for p in rows} == {
        "pose-db-01": False,
        "pose-db-02": True,
    }
    assert client.post("/poses/pose-db-99/full_sexy_P002/favorite").status_code == 404


def test_pose_reference_spec_default_source():
    """ReferenceSpec 无 source（存量 run 重放）→ 回退 pose-db-01。"""
    from charvar.models import ReferenceSpec

    assert ReferenceSpec(pose_id="full_sexy_P001").pose_source() == "pose-db-01"
    assert ReferenceSpec(pose_id="x", source="pose-db-02").pose_source() == "pose-db-02"


# ---------- 人脸身份锚（face/styling 条目当 <image1>；与主体档案互斥在 CLI 把门） ----------

def _face_prompt(mode="plain", **kw):
    from charvar import pose_prompt

    args = dict(
        line="全身1人・標準系",
        action_text="呈自然的 S 形曲线",
    )
    if mode == "plain":
        args["face_section"] = "face"
    else:
        args.update(
            costume=True,
            costume_display="ghost",
            costume_policy={"clothing": mode},
            face_section="face",
        )
    args.update(kw)
    return pose_prompt.build_prompt(**args)


def test_pose_prompt_face_identity_plain():
    """face 段（胸像、无服装/身材信息）无服饰时：身份句不提服装。"""
    p = _face_prompt()
    assert "<image1>为角色脸部参考，保留该角色的面部特征、发型、发色和整体美术风格" in p
    assert "确保生成的是同一个角色" in p
    assert "<image2>为姿势参考" in p and "摆出" in p
    assert "服饰参考" not in p
    assert "服装" not in p.split("\n\n")[0]  # 身份段不提服装（其余段落与主体版相同）


def test_pose_prompt_face_costume_replace():
    """face + replace：服装按 <image3>，融合句无「原有服装」召唤，身材锚 face 版。"""
    p = _face_prompt("replace")
    assert "图中没有服装与身材信息，服装完全按<image3>服饰参考穿着" in p
    assert "把<image3>图中的服装穿在角色身上" in p
    assert "替换其原有服装" not in p and "原服装" not in p.split("<image3>为服装参考")[0]
    assert "不出现两套服装混搭" in p and "原服装残留" not in p
    # face 版身材锚：无 <image1> 身材可锚，用自洽句 + body_lock 原文
    assert "与该角色的美术风格一致" in p and "一律以<image1>为准" not in p
    with_body = _face_prompt("replace", body_lock=["slender hourglass figure"])
    assert "slender hourglass figure" in with_body
    # 服装锚/要点注记/展示形态句原样（机制复用）
    assert "隐形模特（ghost mannequin）展示图" in with_body
    assert "清单上列出的每件都要出现在画面里" in with_body


def test_pose_prompt_face_costume_add():
    """face + add（叠加袜类）：基础衣着按美术风格自拟，袜类换图中这双。"""
    p = _face_prompt("add")
    assert "角色的基础衣着按其美术风格自然搭配" in p
    assert "腿部袜类与<image3>服装参考不一致时，以服装参考为准" in p
    assert "腿上只穿图中这一双袜类" in p
    assert "原有衣着与其它配件保持" in p or "角色原本穿着袜类时" in p


def test_pose_prompt_face_styling_reuses_subject_texts():
    """styling 段（全身带服装）与主体档案身份锚完全同文本 —— 无 face 分叉开销。"""
    from charvar import pose_prompt

    plain_styling = pose_prompt.build_prompt("全身1人・標準系", "呈自然的 S 形曲线", face_section="styling")
    plain_subject = pose_prompt.build_prompt("全身1人・標準系", "呈自然的 S 形曲线")
    assert plain_styling == plain_subject
    dressed_styling = pose_prompt.build_prompt(
        "全身1人・標準系", "呈自然的 S 形曲线", costume=True,
        costume_policy={"clothing": "replace"}, face_section="styling",
    )
    dressed_subject = pose_prompt.build_prompt(
        "全身1人・標準系", "呈自然的 S 形曲线", costume=True,
        costume_policy={"clothing": "replace"},
    )
    assert dressed_styling == dressed_subject


def test_pose_prompt_face_guards():
    """pair 线 + face 拒绝；未知 section 拒绝；双人 + costume 的旧红线不变。"""
    from charvar import pose_prompt

    with pytest.raises(ValueError, match="人脸身份锚"):
        pose_prompt.build_prompt("2人絡み", "甲乙互动", face_section="face")
    with pytest.raises(ValueError, match="人脸身份锚"):
        pose_prompt.build_prompt("2人絡み", "甲乙互动", face_section="styling")
    with pytest.raises(ValueError, match="未知人脸条目形态"):
        pose_prompt.build_prompt("全身1人・標準系", "呈自然的 S 形曲线", face_section="bust")


def test_build_costume_prompt_face():
    """工作流② face 版：穿上引导句/融合句按 mode 分叉（replace 无「替换原有服装」）。"""
    from charvar import pose_prompt

    plain = pose_prompt.build_costume_prompt(costume_policy={"clothing": "replace"}, face_section="face")
    assert "<image1>为角色脸部参考" in plain
    assert "服装完全按<image2>服饰参考穿着" in plain
    assert "穿上<image2>图中的服装，完整还原图中整套搭配" in plain
    assert "把<image2>图中的服装穿在角色身上" in plain
    assert "替换其原有服装" not in plain
    assert "与该角色的美术风格一致" in plain  # face 版身材锚
    add = pose_prompt.build_costume_prompt(costume_policy={"clothing": "add"}, face_section="face")
    assert "基础衣着按其美术风格自然搭配" in add
    assert "穿上<image2>图中的袜类" in add
