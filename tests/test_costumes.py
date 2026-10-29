"""服装库（costume-db-01）：catalog 校验 / 浏览清单 / 检索词扩写 / 文件定位 / API。

数据真源在磁盘 catalog.json（scripts/download_costume_db.py 抓取），v1 磁盘直读
（无 DB 表）。这里用 tmp 目录造一份小 catalog 验证全链路，不发真网。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import costumes as costumes_service
from charvar.api import build_app
from tests.test_adhoc import _mock_comfy, _setup_workflows


# ---------- 造假服装库 ----------

def _build_fake_costume_data(
    data_dir: pathlib.Path,
    *,
    catalog: list[dict] | None = None,
    glossary: dict[str, str] | None = None,
) -> pathlib.Path:
    """按下载布局造一个小服装库：catalog.json + thumbs/*.webp + img/*.webp。"""
    src = data_dir / "costumes" / costumes_service.SOURCE_ID
    (src / "thumbs").mkdir(parents=True, exist_ok=True)
    (src / "img").mkdir(parents=True, exist_ok=True)
    plan = catalog
    if plan is None:
        plan = [
            {
                "id": "SPR-0001",
                "カテゴリ": "春",
                "キーワード": "ivory frill knit denim mini skirt coat socks white sneakers",
            },
            {
                "id": "SXY-0099",
                "カテゴリ": "セクシー",
                "キーワード": "black lace lingerie",
            },
        ]
    items = []
    for entry in plan:
        item_id = str(entry["id"])
        Image.new("RGB", (60, 90), (240, 240, 240)).save(
            src / f"thumbs/{item_id}.webp", format="WEBP"
        )
        Image.new("RGB", (120, 180), (240, 240, 240)).save(
            src / f"img/{item_id}.webp", format="WEBP"
        )
        item = dict(entry)
        item.setdefault("サムネ", f"thumbs/{item_id}.webp")
        item.setdefault("画像", f"img/{item_id}.webp")
        item.setdefault("原寸", f"png/{item_id}.png")
        items.append(item)
    (src / "catalog.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    if glossary is None:
        glossary = {"黒": "black", "レース": "lace", "デニム": "denim"}
    (src / "glossary.json").write_text(json.dumps(glossary, ensure_ascii=False), encoding="utf-8")
    return src


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------- 浏览清单 ----------

def test_list_items_shapes_and_orders(data_dir):
    _build_fake_costume_data(data_dir)
    data = costumes_service.list_items()
    assert data["source"] == "costume-db-01"
    # 页签按站点目录次序，未收录原文退居其后；zh 映射 + 条数
    cats = [(c["id"], c["zh"], c["count"]) for c in data["categories"]]
    assert cats == [("春", "春装", 1), ("セクシー", "性感系", 1)]
    items = {it["item_id"]: it for it in data["items"]}
    assert set(items) == {"SPR-0001", "SXY-0099"}
    spr = items["SPR-0001"]
    assert spr["category_zh"] == "春装" and spr["keywords"].startswith("ivory frill")
    assert spr["thumb_url"].startswith("/costumes/image/costume-db-01/SPR-0001/thumb")
    assert spr["image_url"].startswith("/costumes/image/costume-db-01/SPR-0001/full")
    # 站点 JA 词表透传给前端（日文搜索扩写用）
    assert data["glossary"]["黒"] == "black"


def test_list_items_unknown_data_falls_back(data_dir):
    """未收录目录原文：zh 回退原文、页签退居 CATEGORY_ORDER 之后（映射缺项不炸）。"""
    _build_fake_costume_data(
        data_dir,
        catalog=[
            {"id": "SPR-0001", "カテゴリ": "春", "キーワード": "ivory knit"},
            {"id": "NEW-0001", "カテゴリ": "新季", "キーワード": "silk gown"},
        ],
    )
    data = costumes_service.list_items()
    by_item = {it["item_id"]: it for it in data["items"]}
    assert by_item["NEW-0001"]["category_zh"] == "新季"
    assert [c["id"] for c in data["categories"]] == ["春", "新季"]


# ---------- 检索（服务端 q 与前端同语义） ----------

def test_query_search_and_glossary_expansion(data_dir):
    _build_fake_costume_data(data_dir)
    # 英文关键词大小写不敏感
    assert [i["item_id"] for i in costumes_service.list_items(q="denim")["items"]] == [
        "SPR-0001"
    ]
    assert [i["item_id"] for i in costumes_service.list_items(q="DENIM")["items"]] == [
        "SPR-0001"
    ]
    # 日文词经 glossary 扩写成英文命中
    assert [i["item_id"] for i in costumes_service.list_items(q="黒")["items"]] == [
        "SXY-0099"
    ]
    assert [i["item_id"] for i in costumes_service.list_items(q="レース")["items"]] == [
        "SXY-0099"
    ]
    # 英文词直接命中；连写日文词包含扩写也命中（黒連れたレース）
    assert [i["item_id"] for i in costumes_service.list_items(q="black")["items"]] == [
        "SXY-0099"
    ]
    assert [i["item_id"] for i in costumes_service.list_items(q="黒レース")["items"]] == [
        "SXY-0099"
    ]
    # id 命中与目录中文命中；多词 AND
    assert [i["item_id"] for i in costumes_service.list_items(q="SPR-0001")["items"]] == [
        "SPR-0001"
    ]
    assert [i["item_id"] for i in costumes_service.list_items(q="性感系")["items"]] == [
        "SXY-0099"
    ]
    assert [i["item_id"] for i in costumes_service.list_items(q="denim sneakers")["items"]] == [
        "SPR-0001"
    ]
    assert costumes_service.list_items(q="denim black")["items"] == []
    assert costumes_service.list_items(q="完全不搭边的词")["items"] == []
    # category 过滤 + q 组合
    got = [i["item_id"] for i in costumes_service.list_items(category="春", q="knit")["items"]]
    assert got == ["SPR-0001"]
    assert costumes_service.list_items(category="夏", q="knit")["items"] == []


def test_query_without_glossary_file(data_dir):
    """glossary.json 缺失不炸：日文扩写退化，英文/id 检索照样走。"""
    src = _build_fake_costume_data(data_dir)
    (src / "glossary.json").unlink()
    assert [i["item_id"] for i in costumes_service.list_items(q="black")["items"]] == [
        "SXY-0099"
    ]
    assert costumes_service.list_items(q="黒")["glossary"] == {}


# ---------- catalog 整批校验 ----------

def test_catalog_validation_rejects_bad_rows(data_dir):
    _build_fake_costume_data(data_dir)
    # id 不合法 → 整批拒绝
    items = json.loads(costumes_service.catalog_path().read_text(encoding="utf-8"))
    items[0]["id"] = "bad/id:../etc"
    costumes_service.catalog_path().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.list_items()
    # サムネ 路径逃逸 / 与 id 不匹配 → 整批拒绝
    items[0]["id"] = "SPR-0001"
    items[0]["サムネ"] = "thumbs/../../etc/passwd.webp"
    costumes_service.catalog_path().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.list_items()
    items[0]["画像"] = "thumbs/OTHER-0001.webp"  # 画像 布局错（文件名与 id 对不上）
    costumes_service.catalog_path().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.list_items()


def test_catalog_missing_or_corrupt(data_dir):
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.list_items()
    _build_fake_costume_data(data_dir)
    costumes_service.catalog_path().write_text("not json", encoding="utf-8")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.list_items()


# ---------- 文件定位 ----------

def test_image_file_resolution_and_errors(data_dir):
    _build_fake_costume_data(data_dir)
    thumb = costumes_service.image_file("SPR-0001", "thumb")
    assert thumb.name == "SPR-0001.webp" and thumb.exists()
    full = costumes_service.image_file("SPR-0001", "full")
    assert full.name == "SPR-0001.webp" and full.exists()
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.image_file("does.not.exist", "thumb")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.image_file("../../etc/passwd", "thumb")
    # 文件缺失（catalog 在、图没下）→ 报缺失并提示重跑下载脚本
    full.unlink()
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.image_file("SPR-0001", "full")


# ---------- /costumes 端点 ----------

@pytest.fixture()
def client(data_dir, monkeypatch):
    _setup_workflows(data_dir, monkeypatch)
    monkeypatch.setenv("COMFYUI_HOST", "127.0.0.1")
    monkeypatch.setenv("COMFYUI_PORT", str(59999))
    _mock_comfy(monkeypatch, [], [])
    db_path = pathlib.Path(data_dir) / "test.db"
    app = build_app(static_dir=None, db_path=db_path)
    return TestClient(app)


def test_costumes_endpoints_serve_catalog_and_images(data_dir, client):
    _build_fake_costume_data(data_dir)
    resp = client.get("/costumes")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source"] == "costume-db-01"
    assert [c["id"] for c in data["categories"]] == ["春", "セクシー"]
    assert len(data["items"]) == 2
    item = next(it for it in data["items"] if it["item_id"] == "SPR-0001")
    thumb = client.get(item["thumb_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    full = client.get(item["image_url"])
    assert full.status_code == 200 and full.headers["content-type"] == "image/webp"
    # 过滤与检索
    assert len(client.get("/costumes", params={"category": "春"}).json()["items"]) == 1
    assert len(client.get("/costumes", params={"q": "denim"}).json()["items"]) == 1
    assert client.get("/costumes", params={"q": "完全不搭边"}).json()["items"] == []
    # 404：没有这条服装 / 没有这个库
    assert client.get("/costumes/image/costume-db-01/NOPE-0000/thumb").status_code == 404
    assert client.get("/costumes/image/other-lib/SPR-0001/thumb").status_code == 404

# ---------- 生图参考图（png 优先 / webp 兜底 + 统一压缩缓存） ----------
# ---------- 生图参考图（png 优先 / webp 兜底 + 统一压缩缓存） ----------

def test_raw_reference_prefers_png_and_falls_back(data_dir):
    _build_fake_costume_data(data_dir)
    src = data_dir / "costumes" / costumes_service.SOURCE_ID
    (src / "png").mkdir(parents=True, exist_ok=True)  # fixture 只建 thumbs/img
    png = src / "png" / "SPR-0001.png"
    Image.new("RGB", (1366, 2048), (240, 240, 240)).save(png, format="PNG")

    # 原寸 png 在 → 用 png；压成统一规格（长边 1536 / JPEG），缓存按 item_id
    assert costumes_service.raw_reference_file("SPR-0001") == png
    prepared = costumes_service.prepared_reference_file("SPR-0001")
    assert prepared.name == "costume_SPR-0001.jpg" and prepared.is_file()
    with Image.open(prepared) as im:
        assert im.format == "JPEG" and max(im.size) == 1536

    # 无原寸 png（字段有值但文件缺失）→ 回退详情 webp，不报错
    png.unlink()
    assert costumes_service.raw_reference_file("SPR-0001").suffix == ".webp"
    # catalog 缺 原寸 字段也一路可用
    missing = data_dir / "costumes" / costumes_service.SOURCE_ID / "catalog.json"
    rows = json.loads(missing.read_text(encoding="utf-8"))
    for row in rows:
        row.pop("原寸", None)
    missing.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    assert costumes_service.raw_reference_file("SPR-0001").suffix == ".webp"
    assert costumes_service.raw_reference_file("SXY-0099").suffix == ".webp"


def test_raw_reference_rejects_bad_id(data_dir):
    _build_fake_costume_data(data_dir)
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.raw_reference_file("../evil")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.raw_reference_file("SPR-9999")


def test_item_keywords_roundtrip(data_dir):
    _build_fake_costume_data(data_dir)
    assert costumes_service.item_keywords("SPR-0001").startswith("ivory frill knit")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.item_keywords("SPR-9999")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.item_keywords("../evil")
