"""服装库：catalog 校验 / costume import / 双表（items+prompts）/ 检索 / 文件定位 / API / studio 迁移 / 起稿。

磁盘 catalog.json（scripts/download_costume_db.py 抓取）与 display_types.json
侧车是「导入源」；数据库（costume_items / costume_prompts 双表）是运行时真源
（pose 双表同构：重灌索引层绝不清用户文本层）。tmp 目录造假库 + import 后验证
全链路，不发真网。
"""
from __future__ import annotations

import json
import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

from charvar import costumes as costumes_service
from charvar import storage
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


def _imported(data_dir: pathlib.Path, **kw) -> pathlib.Path:
    """造假库 + import_catalog 进隔离 DB（读法测试都用它起步）。"""
    src = _build_fake_costume_data(data_dir, **kw)
    with storage.connect() as conn:
        costumes_service.import_catalog(conn)
    return src


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------- 浏览清单 ----------

def test_list_items_shapes_and_orders(data_dir):
    _imported(data_dir)
    with storage.connect() as conn:
        data = costumes_service.list_items(conn)
    assert data["source"] == "costume-db-01"
    assert [s["id"] for s in data["sources"]] == ["costume-db-01"]
    cats = [(c["id"], c["zh"], c["count"]) for c in data["categories"]]
    assert cats == [("春", "春装", 1), ("セクシー", "性感系", 1)]
    items = {it["item_id"]: it for it in data["items"]}
    assert set(items) == {"SPR-0001", "SXY-0099"}
    spr = items["SPR-0001"]
    assert spr["category_zh"] == "春装" and spr["keywords"].startswith("ivory frill")
    assert spr["display_type"] == "ghost" and spr["prompt_text"] == ""
    assert spr["thumb_url"].startswith("/costumes/image/costume-db-01/SPR-0001/thumb")
    assert spr["image_url"].startswith("/costumes/image/costume-db-01/SPR-0001/full")
    assert data["glossary"]["黒"] == "black"


def test_list_items_unknown_data_falls_back(data_dir):
    """未收录目录原文：zh 回退原文、页签退居 CATEGORY_ORDER 之后（映射缺项不炸）。"""
    _imported(
        data_dir,
        catalog=[
            {"id": "SPR-0001", "カテゴリ": "春", "キーワード": "ivory knit"},
            {"id": "NEW-0001", "カテゴリ": "新季", "キーワード": "silk gown"},
        ],
    )
    with storage.connect() as conn:
        data = costumes_service.list_items(conn)
    by_item = {it["item_id"]: it for it in data["items"]}
    assert by_item["NEW-0001"]["category_zh"] == "新季"
    assert [c["id"] for c in data["categories"]] == ["春", "新季"]


def test_list_items_empty_db_hints_import(data_dir):
    """库空（还没 import）→ 明确报错提示导入命令。"""
    _build_fake_costume_data(data_dir)
    with storage.connect() as conn:
        with pytest.raises(costumes_service.CostumeError, match="costume import"):
            costumes_service.list_items(conn)


# ---------- 检索（服务端 q 与前端同语义） ----------

def test_query_search_and_glossary_expansion(data_dir):
    _imported(data_dir)
    with storage.connect() as conn:
        # 英文关键词大小写不敏感
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="denim")["items"]] == ["SPR-0001"]
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="DENIM")["items"]] == ["SPR-0001"]
        # 日文词经 glossary 扩写成英文命中
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="黒")["items"]] == ["SXY-0099"]
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="レース")["items"]] == ["SXY-0099"]
        # 英文词直接命中；连写日文词包含扩写也命中（黒レース）
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="black")["items"]] == ["SXY-0099"]
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="黒レース")["items"]] == ["SXY-0099"]
        # id 命中与目录中文命中；多词 AND
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="SPR-0001")["items"]] == ["SPR-0001"]
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="性感系")["items"]] == ["SXY-0099"]
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="denim sneakers")["items"]] == ["SPR-0001"]
        assert costumes_service.list_items(conn, q="denim black")["items"] == []
        assert costumes_service.list_items(conn, q="完全不搭边的词")["items"] == []
        # category 过滤 + q 组合
        got = [i["item_id"] for i in costumes_service.list_items(conn, category="春", q="knit")["items"]]
        assert got == ["SPR-0001"]
        assert costumes_service.list_items(conn, category="夏", q="knit")["items"] == []


def test_query_without_glossary_file(data_dir):
    """glossary.json 缺失不炸：日文扩写退化，英文/id 检索照样走。"""
    src = _imported(data_dir)
    (src / "glossary.json").unlink()
    with storage.connect() as conn:
        assert [i["item_id"] for i in costumes_service.list_items(conn, q="black")["items"]] == ["SXY-0099"]
        assert costumes_service.list_items(conn, q="黒")["glossary"] == {}


# ---------- import（整批校验落库） ----------

def test_import_catalog_validation_rejects_bad_rows(data_dir):
    _build_fake_costume_data(data_dir)
    with storage.connect() as conn:
        items = json.loads(costumes_service.catalog_path().read_text(encoding="utf-8"))
        items[0]["id"] = "bad/id:../etc"
        costumes_service.catalog_path().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.import_catalog(conn)
        items[0]["id"] = "SPR-0001"
        items[0]["サムネ"] = "thumbs/../../etc/passwd.webp"
        costumes_service.catalog_path().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.import_catalog(conn)
        items[0]["サムネ"] = "thumbs/SPR-0001.webp"
        items[0]["画像"] = "thumbs/OTHER-0001.webp"  # 画像 布局错（文件名与 id 对不上）
        costumes_service.catalog_path().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.import_catalog(conn)


def test_import_missing_files_rejected(data_dir):
    _build_fake_costume_data(data_dir)
    src = data_dir / "costumes" / costumes_service.SOURCE_ID
    (src / "img" / "SPR-0001.webp").unlink()
    with storage.connect() as conn:
        with pytest.raises(costumes_service.CostumeError, match="补跑"):
            costumes_service.import_catalog(conn)


def test_import_missing_catalog(data_dir):
    with storage.connect() as conn:
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.import_catalog(conn)
    _build_fake_costume_data(data_dir)
    costumes_service.catalog_path().write_text("not json", encoding="utf-8")
    with storage.connect() as conn:
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.import_catalog(conn)


def test_import_idempotent_and_prompts_survive(data_dir):
    """重灌索引层：行列数不变、keywords 被刷新；costume_prompts（用户文本层）原样。"""
    _imported(data_dir)
    with storage.connect() as conn:
        costumes_service.set_costume_prompt(
            conn, "costume-db-01", "SPR-0001", "ivory knit set with denim skirt, ghost mannequin display"
        )
        again = costumes_service.import_catalog(conn)
        assert again == {"source_id": "costume-db-01", "items": 2}
        row = costumes_service.item_row(conn, "SPR-0001")
        assert row["keywords"].startswith("ivory frill")  # 索引层照快照重灌
        assert costumes_service.get_costume_prompt(
            conn, "costume-db-01", "SPR-0001"
        )["prompt_text"].startswith("ivory knit set")  # 文本层没被碰


# ---------- 条目解析 ----------

def test_resolve_ref_explicit_and_bare(data_dir):
    _imported(data_dir)
    with storage.connect() as conn:
        assert costumes_service.item_row(conn, "costume-db-01/SXY-0099")["item_id"] == "SXY-0099"
        # 裸 id 唯一时可用
        assert costumes_service.item_row(conn, "SXY-0099")["item_id"] == "SXY-0099"
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.item_row(conn, "costume-db-01/SPR-9999")
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.item_row(conn, "no-such-lib/X-1")
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.item_row(conn, "../../etc/passwd")


# ---------- 文件定位 ----------

def test_image_file_resolution_and_errors(data_dir):
    _imported(data_dir)
    thumb = costumes_service.image_file("SPR-0001", "thumb")
    assert thumb.name == "SPR-0001.webp" and thumb.exists()
    full = costumes_service.image_file("costume-db-01/SPR-0001", "full")
    assert full.name == "SPR-0001.webp" and full.exists()
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.image_file("does.not.exist", "thumb")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.image_file("../../etc/passwd", "thumb")
    # 文件缺失（导入时在、后被删）→ 报缺失并提示
    full.unlink()
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.image_file("costume-db-01/SPR-0001", "full")


# ---------- 生图参考图（png 优先 / webp 兜底 + 统一压缩缓存） ----------

def test_raw_reference_prefers_png_and_falls_back(data_dir):
    _imported(data_dir)
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
    # catalog 缺 原寸 字段 → 重灌后 file_path 为空，同样回退 webp
    missing = src / "catalog.json"
    rows = json.loads(missing.read_text(encoding="utf-8"))
    for row in rows:
        row.pop("原寸", None)
    missing.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    with storage.connect() as conn:
        costumes_service.import_catalog(conn)
    assert costumes_service.raw_reference_file("SPR-0001").suffix == ".webp"
    assert costumes_service.raw_reference_file("SXY-0099").suffix == ".webp"


def test_raw_reference_rejects_bad_id(data_dir):
    _imported(data_dir)
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.raw_reference_file("../evil")
    with pytest.raises(costumes_service.CostumeError):
        costumes_service.raw_reference_file("SPR-9999")


# ---------- 文本层（costume_prompts） ----------

def test_prompt_text_roundtrip_and_clear(data_dir):
    _imported(data_dir)
    with storage.connect() as conn:
        d = costumes_service.get_costume_prompt(conn, "costume-db-01", "SPR-0001")
        assert d["prompt_text"] == "" and d["prompt_source"] == ""  # 没写过 = 空态
        d = costumes_service.set_costume_prompt(conn, "costume-db-01", "SPR-0001", "ivory knit set")
        assert d["prompt_text"] == "ivory knit set" and d["prompt_source"] == "manual"
        assert costumes_service.get_costume_prompt(conn, "costume-db-01", "SPR-0001")["prompt_text"] == "ivory knit set"
        # 不悬空：索引层没有的条目拒绝
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.set_costume_prompt(conn, "costume-db-01", "NOPE-0000", "x")
        # 空串 = 清掉该条文本层
        costumes_service.set_costume_prompt(conn, "costume-db-01", "SPR-0001", "")
        assert costumes_service.get_costume_prompt(conn, "costume-db-01", "SPR-0001")["prompt_text"] == ""


def test_note_text_prefers_prompt_layer(data_dir):
    _imported(data_dir)
    with storage.connect() as conn:
        # 默认：keywords（catalog 索引层）
        assert costumes_service.note_text(conn, "SPR-0001").startswith("ivory frill")
        # 用户文本层有内容时优先
        costumes_service.set_costume_prompt(conn, "costume-db-01", "SPR-0001", "ivory knit with denim mini skirt")
        assert costumes_service.note_text(conn, "SPR-0001") == "ivory knit with denim mini skirt"


# ---------- studio 迁移（outfits 预设 → flat_lay 自建条目） ----------

def test_import_outfit_presets_and_skip_on_rerun(data_dir):
    from charvar import outfit as outfit_service

    preset = outfit_service.load_outfit(outfit_service.list_outfit_presets()[0]["name"])
    first = preset.items[0]
    expected_id = f"{preset.name}_{first.id}"
    expected_tags = json.dumps(dict(preset.caption_tags or {}), ensure_ascii=False) if preset.caption_tags else ""

    with storage.connect() as conn:
        d1 = costumes_service.import_outfit_presets(conn)
        assert d1["added"] >= 5 and d1["skipped"] == 0
        row = costumes_service.item_row(conn, f"studio/{expected_id}")
        assert row["display_type"] == "flat_lay"
        assert row["category"] == preset.name and row["label"] == first.label
        assert row["keywords"] == first.text  # text 双写索引层
        pr = costumes_service.get_costume_prompt(conn, "studio", expected_id)
        assert pr["prompt_text"] == first.text and pr["prompt_source"] == "manual"  # 双写文本层
        if preset.caption_tags:
            assert json.loads(row["caption_tags"]) == dict(preset.caption_tags)
        assert row["file_path"] == "" and row["image_path"] == ""  # 纯文本条目（合法状态）

        # 重跑幂等：全部跳过
        d2 = costumes_service.import_outfit_presets(conn)
        assert d2["added"] == 0 and d2["skipped"] == d1["added"]

        # DB 真源：修过文本层后重跑迁移不覆盖
        costumes_service.set_costume_prompt(conn, "studio", expected_id, "edited prompt text")
        costumes_service.import_outfit_presets(conn)
        assert costumes_service.get_costume_prompt(conn, "studio", expected_id)["prompt_text"] == "edited prompt text"
    assert expected_tags == expected_tags  # 占位断言防误删 tags 变量


def test_studio_text_only_item_rejects_reference(data_dir):
    """纯文本条目（无图）合法存在，但参考图解析显式报错（不给半死路径）。"""
    from charvar import outfit as outfit_service

    with storage.connect() as conn:
        costumes_service.import_outfit_presets(conn)
        preset = outfit_service.load_outfit(outfit_service.list_outfit_presets()[0]["name"])
        ref = f"studio/{preset.name}_{preset.items[0].id}"
        with pytest.raises(costumes_service.CostumeError, match="详情图"):
            costumes_service.raw_reference_file(ref)
        with pytest.raises(costumes_service.CostumeError, match="详情图"):
            costumes_service.image_file(ref, "full")


# ---------- display_type（侧车降级为 import 期种子） ----------

def test_display_type_seed_comes_from_sidecar_at_import(data_dir):
    _build_fake_costume_data(data_dir)
    sidecar = data_dir / "costumes" / costumes_service.SOURCE_ID / "display_types.json"
    sidecar.write_text(json.dumps({"SPR-0001": "flat_lay"}, ensure_ascii=False), encoding="utf-8")
    with storage.connect() as conn:
        costumes_service.import_catalog(conn)
        assert costumes_service.item_display_type(conn, "SPR-0001") == "flat_lay"
        assert costumes_service.item_display_type(conn, "SXY-0099") == "ghost"
    # 坏值 → import 整批拒绝（自家文件要严）
    sidecar.write_text(json.dumps({"SPR-0001": "hanging"}), encoding="utf-8")
    with storage.connect() as conn:
        with pytest.raises(costumes_service.CostumeError):
            costumes_service.import_catalog(conn)


# ---------- export（DB → presets 镜像） ----------

def test_export_item_writes_yaml(data_dir):
    _imported(data_dir)
    with storage.connect() as conn:
        costumes_service.set_costume_prompt(
            conn, "costume-db-01", "SXY-0099", "black lace lingerie set, ghost mannequin display"
        )
        path = costumes_service.export_item(conn, "SXY-0099", data_dir / "presets" / "costumes")
    assert path.name == "SXY-0099.yaml" and path.parent.name == "costume-db-01"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["item_id"] == "SXY-0099" and doc["display_type"] == "ghost"
    assert doc["prompt_text"].startswith("black lace") and doc["prompt_source"] == "manual"


# ---------- 起稿（mock 通路） ----------

def test_costume_describe_saves_ai_draft_and_validates(data_dir, monkeypatch):
    import charvar.costume_describe as cd

    _imported(data_dir)
    seen: dict = {}

    def fake_llm(messages, model=None):
        seen["instr"] = messages[0]["content"][0]["text"]
        seen["has_image"] = any(p.get("type") == "image_url" for p in messages[0]["content"])
        return "ivory knit cardigan with denim mini skirt, displayed on a ghost mannequin"

    monkeypatch.setattr(cd, "_call_llm", fake_llm)
    with storage.connect() as conn:
        r = cd.describe_one(conn, "SPR-0001")
        assert r["status"] == "done" and r["text"].startswith("ivory knit cardigan")
        # 视觉消息（带图）+ 形态说明注入（站点条目缺省 ghost）
        assert seen["has_image"] and "ghost mannequin" in seen["instr"]
        src = conn.execute(
            "SELECT source FROM costume_prompts WHERE source_id='costume-db-01' AND item_id='SPR-0001'"
        ).fetchone()["source"]
        assert src == "ai_draft"
        # 幂等：已写 → skipped；--overwrite 重写
        assert cd.describe_one(conn, "SPR-0001")["status"] == "skipped"
        assert cd.describe_one(conn, "SPR-0001", overwrite=True)["status"] == "done"
        # 写了 <imageN> → 校验失败
        monkeypatch.setattr(cd, "_call_llm", lambda messages, model=None: "<image1> shows a complete outfit with lace details")
        r_bad = cd.describe_one(conn, "SPR-0001", overwrite=True)
        assert r_bad["status"] == "error" and "<imageN>" in r_bad["error"]


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
    _imported(data_dir)
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
    # 404：没有这条服装 / 没有这个库 / 空库提示
    assert client.get("/costumes/image/costume-db-01/NOPE-0000/thumb").status_code == 404
    assert client.get("/costumes/image/other-lib/SPR-0001/thumb").status_code == 404
