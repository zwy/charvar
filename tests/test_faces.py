"""人脸库（face-db-01）：导入校验 / 浏览清单 / 参考图解析 / API。

数据真源在磁盘 catalog.json（scripts/download_face_db.py 抓取），DB 只是索引。
这里用 tmp 目录造一份小 catalog 验证全链路，不发真网。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import faces as faces_service
from charvar import storage
from charvar.api import build_app
from tests.test_adhoc import _png_bytes


# ---------- 造假人脸库 ----------

def _build_fake_face_data(
    data_dir: pathlib.Path, *, counts: dict | None = None, source: str | None = None
) -> pathlib.Path:
    """按下载布局造一个小人脸库：catalog.json + img/full/*.png + img/thumb/*.webp。

    counts: {face_id: section}；缺省 2 个 face + 1 个 styling。
    """
    src = data_dir / "faces" / (source or faces_service.SOURCE_ID)
    (src / "img" / "full").mkdir(parents=True, exist_ok=True)
    (src / "img" / "thumb").mkdir(parents=True, exist_ok=True)
    plan = counts or {
        "faceaaa11111": "face",
        "facebbb22222": "face",
        "styleccc33333": "styling",
    }
    items: list[dict] = []
    for seq, (face_id, section) in enumerate(plan.items()):
        full_rel = f"img/full/{face_id}.png"
        thumb_rel = f"img/thumb/{face_id}.webp"
        Image.new("RGB", (90, 112), (200, 190, 180)).save(src / full_rel, format="PNG")
        Image.new("RGB", (40, 50), (200, 190, 180)).save(
            src / thumb_rel, "WEBP"
        )
        items.append(
            {
                "id": face_id,
                "section": section,
                "hair": "ショート" if face_id.endswith("1") or face_id.endswith("3") else "ロング",
                "style": "" if face_id == "facebbb22222" else f"测试发型 {seq}",
                "w": 90,
                "h": 112,
                "full": full_rel,
                "thumb": thumb_rel,
                "thumb_w": 40,
                "thumb_h": 50,
                "bytes": 1024,
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
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        info = faces_service.import_catalog(conn)
        assert info["faces"] == 3
        again = faces_service.import_catalog(conn)
        assert again["faces"] == 3  # upsert 不翻倍
        row = conn.execute(
            "SELECT section, hair, sort_seq FROM face_items WHERE face_id='faceaaa11111'"
        ).fetchone()
        assert row["section"] == "face" and row["hair"] == "ショート" and row["sort_seq"] == 0


def test_import_catalog_rejects_missing_image(data_dir):
    src = _build_fake_face_data(data_dir)
    (src / "img" / "full" / "faceaaa11111.png").unlink()
    with storage.connect() as conn:
        with pytest.raises(faces_service.FaceError):
            faces_service.import_catalog(conn)
        assert conn.execute("SELECT COUNT(*) FROM face_items").fetchone()[0] == 0  # 空库


def test_import_catalog_rejects_bad_id_and_unknown_section(data_dir):
    src = _build_fake_face_data(data_dir)
    catalog = json.loads((src / "catalog.json").read_text(encoding="utf-8"))
    catalog[0]["id"] = "bad/id:../../etc"  # 非法字符
    (src / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
    )
    with storage.connect() as conn:
        with pytest.raises(faces_service.FaceError):
            faces_service.import_catalog(conn)
    src2 = _build_fake_face_data(data_dir)
    catalog = json.loads((src2 / "catalog.json").read_text(encoding="utf-8"))
    catalog[0]["section"] = "mystery"  # 未登记的展示形态（分句没配）
    (src2 / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
    )
    with storage.connect() as conn:
        with pytest.raises(faces_service.FaceError):
            faces_service.import_catalog(conn)


def test_import_catalog_rejects_unregistered_source(data_dir):
    src = _build_fake_face_data(data_dir, source="face-db-99")
    assert (src / "catalog.json").exists()
    with storage.connect() as conn:
        with pytest.raises(faces_service.FaceError):
            faces_service.import_catalog(conn, "face-db-99")


# ---------- 浏览 ----------

def test_list_faces_groups_and_filters(data_dir):
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
        data = faces_service.list_faces(conn)
    assert data["source"] == "face-db-01"
    assert [s["id"] for s in data["sources"]] == ["face-db-01"]
    assert [s["id"] for s in data["sections"]["face-db-01"]] == ["face", "styling"]
    assert data["items"][0]["face_id"] == "faceaaa11111"  # catalog 数组次序（sort_seq）
    assert data["items"][0]["hair_zh"] == "短发"
    assert data["items"][-1]["face_id"] == "styleccc33333"
    with storage.connect() as conn:
        only = faces_service.list_faces(conn, section="styling")
        assert [i["face_id"] for i in only["items"]] == ["styleccc33333"]
        hit = faces_service.list_faces(conn, q="测试发型 0")
        assert [i["face_id"] for i in hit["items"]] == ["faceaaa11111"]


def test_list_faces_requires_import(data_dir):
    with storage.connect() as conn:
        with pytest.raises(faces_service.FaceError):
            faces_service.list_faces(conn)


# ---------- 参考图解析（压缩版走 reference_images 统一规格） ----------

def test_prepared_reference_and_image_files(data_dir):
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
        # 文件服务（原图 / 缩略图路径）
        full = faces_service.full_image_file("faceaaa11111")
        thumb = faces_service.thumb_image_file("faceaaa11111")
        assert full.exists() and thumb.exists()
        assert faces_service.item_section(conn, "faceaaa11111") == "face"
        assert faces_service.item_section(conn, "styleccc33333") == "styling"
    prepared = faces_service.prepared_reference_file("styleccc33333")
    assert prepared.exists() and prepared.suffix == ".jpg"


def test_face_ref_404_on_unknown_id(data_dir):
    _build_fake_face_data(data_dir)
    with pytest.raises(faces_service.FaceError):
        faces_service.full_image_file("nosuchface")


# ---------- API ----------

def test_api_faces_list_and_images(data_dir):
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
    app = build_app(static_dir=pathlib.Path(__file__).resolve().parent.parent / "web" / "static")
    client = TestClient(app)
    resp = client.get("/faces")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload["items"]) == 3
    assert payload["sources"][0]["id"] == "face-db-01"
    face_id = payload["items"][0]["face_id"]
    thumb_url = payload["items"][0]["thumb_url"]
    path_only = thumb_url.split("?")[0]
    img = client.get(path_only)
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp", path_only
    full_path = f"/faces/image/face-db-01/{face_id}/full".replace("face-db-01", payload["items"][0]["source"])
    full = client.get(full_path)
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    assert client.get("/faces/image/face-db-01/nosuchface/full").status_code == 404
    # 未登记库 → 404（不是空清单）
    assert client.get("/faces?source=face-db-99").status_code == 404


def test_api_generate_accepts_face_reference(data_dir):
    """face 参考走 /generate 的参考图解析（role 缺省 face，文件服务端定位）。"""
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
    app = build_app(static_dir=pathlib.Path(__file__).resolve().parent.parent / "web" / "static")
    client = TestClient(app)
    assert client.post("/subjects", json={"id": "demo_w", "name": "演示"}).status_code == 201
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_w",
            "preset": "q21_default_30",
            "seed": 1,
            "count": 1,
            "mode": "classic",
            "references": [{"face_id": "faceaaa11111", "role": "face"}],
        },
    )
    assert resp.status_code == 200, resp.text
    refs = resp.json()["references"]
    assert refs[0]["face_id"] == "faceaaa11111" and refs[0]["role"] == "face"


def test_api_adhoc_accepts_face_reference(data_dir, monkeypatch):
    """Playground 通路：face 参考解析成路径进 _prepare_references（不发真网）。"""
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
    from charvar import adhoc as adhoc_service
    from charvar.reference_images import prepared_reference

    app = build_app(static_dir=pathlib.Path(__file__).resolve().parent.parent / "web" / "static")
    client = TestClient(app)
    # mock 生成核心：只验参考图解析与请求组装
    from charvar.adhoc import AdhocResult

    def _fake_generate(req, db_path=None):
        assert str(req.reference_paths[0]).endswith(".jpg")
        return AdhocResult(
            image_path="/tmp/none.png", provider="comfyui", manifest_path="",
            width=90, height=112, seed=0, status="queued",
        )
    monkeypatch.setattr(adhoc_service, "generate", _fake_generate)
    resp = client.post(
        "/adhoc/generate",
        json={
            "prompt": "test",
            "backend": "comfyui",
            "references": [{"face_id": "faceaaa11111"}],
            "wait": False,
        },
    )
    assert resp.status_code == 200, resp.text


# ---------- 收藏（face_favorites：用户层，对齐动作/服饰库） ----------

# ---------- 引用解析（face id 乱码 → 唯一前缀 / source 前缀消歧） ----------

def test_resolve_face_prefix_and_scoping(data_dir):
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
        assert faces_service.resolve_face(conn, "faceaaa11111") == ("face-db-01", "faceaaa11111")
        assert faces_service.resolve_face(conn, "faceaaa") == ("face-db-01", "faceaaa11111")
        assert faces_service.resolve_face(conn, "face-db-01/styleccc") == ("face-db-01", "styleccc33333")
        with pytest.raises(faces_service.FaceError, match="没有这个条目"):
            faces_service.resolve_face(conn, "zzzz")
        # 非法字符集（face id 只认 [A-Za-z0-9_-]）不进 LIKE
        with pytest.raises(faces_service.FaceError, match="非法"):
            faces_service.resolve_face(conn, "face.aaa")
        # 空引用
        with pytest.raises(faces_service.FaceError, match="缺少"):
            faces_service.resolve_face(conn, "   ")
        # 未登记库前缀
        with pytest.raises(faces_service.FaceError, match="没有登记"):
            faces_service.resolve_face(conn, "face-db-99/faceaaa11111")


def test_face_favorites_toggle_and_survive_reimport(data_dir):
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
        assert faces_service.toggle_favorite(conn, "faceaaa11111")["favorite"] is True
        assert faces_service.toggle_favorite(conn, "faceaaa11111")["favorite"] is False
        faces_service.toggle_favorite(conn, "faceaaa11111")
        rows = faces_service.list_faces(conn)["items"]
        assert [i["favorited"] for i in rows] == [True, False, False]
        # 显式 bool 也能设（缺省 None = 切换）
        faces_service.toggle_favorite(conn, "styleccc33333", favorite=True)
        faces_service.toggle_favorite(conn, "faceaaa11111", favorite=False)
        rows = faces_service.list_faces(conn)["items"]
        assert [i["favorited"] for i in rows] == [False, False, True]
        # 重灌索引层不碰收藏（face_favorites 用户层，同 pose/costume 哲学）
        faces_service.import_catalog(conn)
        rows = faces_service.list_faces(conn)["items"]
        assert [i["favorited"] for i in rows] == [False, False, True]
        with pytest.raises(faces_service.FaceError):
            faces_service.toggle_favorite(conn, "nosuchface")
        with pytest.raises(faces_service.FaceError):
            faces_service.toggle_favorite(conn, "faceaaa11111", source="face-db-99")


def test_face_favorite_endpoint(data_dir):
    """POST /faces/{source}/{face_id}/favorite 切换 + 清单透出 favorited；未知 404。"""
    _build_fake_face_data(data_dir)
    with storage.connect() as conn:
        faces_service.import_catalog(conn)
    app = build_app(static_dir=pathlib.Path(__file__).resolve().parent.parent / "web" / "static")
    client = TestClient(app)
    assert client.post("/faces/face-db-01/faceaaa11111/favorite").json() == {"favorited": True}
    items = client.get("/faces").json()["items"]
    assert [i["favorited"] for i in items] == [True, False, False]
    assert client.post("/faces/face-db-01/faceaaa11111/favorite").json() == {"favorited": False}
    # 未知条目 / 未登记库 → 404；非法 id 字符 → 400（正则拦在进库前）
    assert client.post("/faces/face-db-01/nosuchface/favorite").status_code == 404
    assert client.post("/faces/face-db-99/faceaaa11111/favorite").status_code == 404
    assert client.post("/faces/face-db-01/bad.id/favorite").status_code == 400
