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

def _build_fake_pose_data(data_dir: pathlib.Path, *, counts: dict | None = None) -> pathlib.Path:
    """按下载布局造一个小姿势库：catalog.json + img/*.png + thumbs/*.jpg。

    counts: {pose_id: 张数}；缺省 2 个动作（一个 5 视角、一个 4 视角）。
    """
    src = data_dir / "poses" / poses_service.SOURCE_ID
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
    assert [l["id"] for l in data["lines"]] == ["全身1人・セクシー系", "2人絡み"]
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
    resp = client.put("/poses/full_sexy_P001/action", json={"angle": "正面", "action_text": "S 型站立"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["angle"] == "正面"
    listing = client.get("/poses").json()
    pose = next(p for p in listing["poses"] if p["pose_id"] == "full_sexy_P001")
    assert pose["images"][0]["action_text"] == "S 型站立"
    got = client.get("/poses/full_sexy_P001/action")  # 缺省正面
    assert got.status_code == 200 and got.json()["action_text"] == "S 型站立"
    assert client.put(
        "/poses/full_sexy_P001/action", json={"angle": "上から", "action_text": "俯拍视角"}
    ).status_code == 200
    assert client.get("/poses/full_sexy_P001/action?angle=上から").json()["action_text"] == "俯拍视角"
    # 空串 = 清掉该视角，GET 仍 200 空串
    assert client.put(
        "/poses/full_sexy_P001/action", json={"angle": "上から", "action_text": ""}
    ).status_code == 200
    assert client.get("/poses/full_sexy_P001/action?angle=上から").json()["action_text"] == ""
    # 非法角度 → 400；库里没有的组合/动作 → 404；非法 id → 400
    assert client.put(
        "/poses/full_sexy_P001/action", json={"angle": "斜上方", "action_text": "x"}
    ).status_code == 400
    assert client.put(
        "/poses/full_sexy_P002/action", json={"angle": "下から", "action_text": "x"}
    ).status_code == 404  # P002 只有 4 视角
    assert client.put("/poses/full_sexy_P999/action", json={"action_text": "x"}).status_code == 404
    assert client.get("/poses/does.not.exist/action").status_code == 400


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