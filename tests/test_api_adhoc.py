"""Playground 的网页入口：/adhoc/* 接口（MockTransport + 打桩，不发真网）。"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import openrouter_image, storage
from charvar.api import build_app
from charvar.exporters import comfyui as comfyui_exporter

from tests.test_adhoc import _fake_openrouter, _mock_comfy, _png_bytes, _setup_workflows


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    _setup_workflows(tmp_path, monkeypatch)
    db_path = pathlib.Path(tmp_path) / "test.db"
    # 设置写进 client 实际用的那个库（build_app 传了 db_path，两边必须一致）
    with storage.connect(db_path) as conn:
        storage.set_setting(conn, "comfy_t2i_workflow", "TestT2I")
        storage.set_setting(conn, "comfy_edit_workflow", "TestEdit")
    app = build_app(static_dir=None, db_path=db_path)
    return TestClient(app)


def _upload_ref(client: TestClient, size=(40, 30), color=(9, 8, 7)) -> str:
    """上传一张参考图，返回暂存 id。"""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    resp = client.post(
        "/adhoc/references", files={"file": ("ref.png", buf.getvalue(), "image/png")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ---------- 参考图暂存 ----------

def test_upload_reference_returns_id_and_file_is_served(client):
    ref_id = _upload_ref(client)
    assert len(ref_id) == 32
    served = client.get(f"/adhoc/reference/{ref_id}")
    assert served.status_code == 200
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_upload_reference_detects_real_format_not_filename(client):
    """文件名说 .jpg、实际是 PNG → 按实际格式存（后缀不能骗人）。"""
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(buf, format="PNG")
    resp = client.post(
        "/adhoc/references", files={"file": ("lies.jpg", buf.getvalue(), "image/jpeg")}
    )
    ref_id = resp.json()["id"]
    assert client.get(f"/adhoc/reference/{ref_id}").status_code == 200


def test_upload_non_image_rejected(client):
    resp = client.post(
        "/adhoc/references", files={"file": ("a.txt", b"not an image", "text/plain")}
    )
    assert resp.status_code == 400


def test_delete_reference(client):
    ref_id = _upload_ref(client)
    assert client.delete(f"/adhoc/references/{ref_id}").status_code == 200
    assert client.get(f"/adhoc/reference/{ref_id}").status_code == 404


def test_illegal_reference_id_rejected(client):
    """路径穿越：id 不是 32 位十六进制 → 404，不能拿 ../ 读任意文件。"""
    assert client.get("/adhoc/reference/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.delete("/adhoc/references/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_stale_staged_reference_pruned_on_upload(client):
    """传了没出图就走的暂存图会被过期清理（不然一直堆着）。"""
    import os
    import time

    from charvar import adhoc as adhoc_service

    ref_id = _upload_ref(client)
    path = adhoc_service.staged_reference(ref_id)
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))
    _upload_ref(client)  # 再传一张触发清理
    assert not path.exists()
    assert client.get(f"/adhoc/reference/{ref_id}").status_code == 404


# ---------- 出图 ----------

def test_generate_comfyui(client, monkeypatch):
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "a red apple", "backend": "comfyui", "aspect_ratio": "16:9"},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["provider"] == "comfyui"
    assert out["status"] == "ready"
    assert out["run_id"].startswith("adhoc_")
    assert out["image_url"] == f"/adhoc/{out['run_id']}/image"
    # 产物能取到，且是真图
    img = client.get(out["image_url"])
    assert img.status_code == 200
    assert img.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(out["thumb_url"]).status_code == 200
    # 结果里的尺寸 = 实际下载到的图（mock 是 64×96）；请求的画幅注在 payload 里
    assert out["width"] == 64 and out["height"] == 96
    assert queued and queued[0]["prompt"]["55"]["inputs"]["width"] == 1024
    assert queued[0]["prompt"]["55"]["inputs"]["height"] == 576


def test_generate_openrouter(client, monkeypatch):
    calls = _fake_openrouter(monkeypatch)
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "a red apple", "backend": "openrouter", "aspect_ratio": "1:1"},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["provider"] == "openrouter"
    assert out["cost"] == 0.01
    assert calls and calls[0]["width"] == 1024 and calls[0]["height"] == 1024
    assert client.get(out["image_url"]).status_code == 200


def test_comfyui_2k_uses_qwen_official_pixels(client, monkeypatch):
    """ComfyUI 通路 2k → Qwen 官方像素（面积恒定 ~4.2MP），不是长边归一。"""
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "a red apple", "backend": "comfyui", "aspect_ratio": "16:9", "resolution": "2k"},
    )
    assert resp.status_code == 200, resp.text
    # 官方 16:9 = 2752×1536；旧实现给 2048×1152（只有原生的 56%）
    assert queued[0]["prompt"]["55"]["inputs"]["width"] == 2752
    assert queued[0]["prompt"]["55"]["inputs"]["height"] == 1536


def test_openrouter_2k_keeps_long_side_algorithm(client, monkeypatch):
    """OpenRouter 跑的不是 Qwen → 2k 保持长边归一，不套官方表。"""
    calls = _fake_openrouter(monkeypatch)
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "a red apple", "backend": "openrouter", "aspect_ratio": "16:9", "resolution": "2k"},
    )
    assert resp.status_code == 200, resp.text
    assert calls and calls[0]["width"] == 2048 and calls[0]["height"] == 1152


def test_4k_resolution_rejected(client, monkeypatch):
    """4k 档已下线 → 明确报错，不静默降级。"""
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "x", "backend": "comfyui", "resolution": "4k"},
    )
    assert resp.status_code == 400
    assert "4k" in resp.json()["detail"]


def test_generate_with_staged_reference(client, monkeypatch):
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    ref_id = _upload_ref(client)
    resp = client.post(
        "/adhoc/generate",
        json={
            "prompt": "same character <image1>",
            "backend": "comfyui",
            "references": [{"staged_id": ref_id}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(uploads) == 1
    # 出图成功后暂存参考图被清掉（下次再用要重新传）
    assert client.get(f"/adhoc/reference/{ref_id}").status_code == 404


def test_generate_with_subject_reference(client, monkeypatch):
    """从主体档案取参考图（走预处理缓存，不直接读原图）。"""
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    assert client.post(
        "/subjects",
        json={"id": "demo_witch", "name": "演示", "subject_noun": "woman"},
    ).status_code == 201
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), (4, 5, 6)).save(buf, format="PNG")
    up = client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("s.png", buf.getvalue(), "image/png")},
    )
    assert up.status_code == 200, up.text
    resp = client.post(
        "/adhoc/generate",
        json={
            "prompt": "same <image1>",
            "backend": "comfyui",
            "references": [{"subject_id": "demo_witch", "kind": "character_sheet"}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(uploads) == 1


def test_generate_with_recent_run_reference(client, monkeypatch):
    """从「最近生成」取参考图：run_id 指到 Playground run 的落盘原图，成功后不清理。"""
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    first = client.post(
        "/adhoc/generate", json={"prompt": "a red apple", "backend": "comfyui"}
    ).json()
    resp = client.post(
        "/adhoc/generate",
        json={
            "prompt": "same character <image1>",
            "backend": "comfyui",
            "references": [{"run_id": first["run_id"]}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(uploads) == 1  # 第一次出图没有参考图；第二次把 run 的原图传上去了
    # run 产物不随出图被清理（同一张可以反复引用）
    resp2 = client.post(
        "/adhoc/generate",
        json={
            "prompt": "again",
            "backend": "comfyui",
            "references": [{"run_id": first["run_id"]}, {"run_id": first["run_id"]}],
        },
    )
    assert resp2.status_code == 200, resp2.text
    assert len(uploads) == 3


def test_generate_with_bad_recent_run_id_rejected(client):
    """run_id 指不到产物或带路径穿越 → 400，不能拿 ../ 读任意文件。"""
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "x", "references": [{"run_id": "adhoc_0000"}]},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "x", "references": [{"run_id": "adhoc_../etc"}]},
    )
    assert resp.status_code == 400


def test_generate_empty_prompt_rejected(client):
    resp = client.post("/adhoc/generate", json={"prompt": "   "})
    assert resp.status_code == 400
    assert "提示词" in resp.json()["detail"]


def test_generate_bad_aspect_rejected(client):
    resp = client.post("/adhoc/generate", json={"prompt": "x", "aspect_ratio": "7:13"})
    assert resp.status_code == 400


def test_generate_missing_staged_reference_rejected(client):
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "x", "references": [{"staged_id": "0" * 32}]},
    )
    assert resp.status_code == 400


def test_generate_reference_without_source_rejected(client):
    resp = client.post(
        "/adhoc/generate", json={"prompt": "x", "references": [{"kind": "front"}]}
    )
    assert resp.status_code == 400


def test_generate_unknown_subject_reference_rejected(client):
    resp = client.post(
        "/adhoc/generate",
        json={"prompt": "x", "references": [{"subject_id": "nobody", "kind": "front"}]},
    )
    assert resp.status_code == 404


def test_generate_backend_failure_returns_502(client, monkeypatch):
    _fake_openrouter(monkeypatch, error=openrouter_image.OpenRouterImageError("boom"))
    resp = client.post(
        "/adhoc/generate", json={"prompt": "x", "backend": "openrouter"}
    )
    assert resp.status_code == 400
    assert "boom" in resp.json()["detail"]


# ---------- 最近列表 / manifest ----------

def test_recent_list_after_generate(client, monkeypatch):
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    client.post("/adhoc/generate", json={"prompt": "a cat", "backend": "comfyui"})
    recs = client.get("/adhoc?limit=5").json()
    assert len(recs) == 1
    assert recs[0]["prompt"] == "a cat"
    assert recs[0]["backend"] == "comfyui"
    assert recs[0]["status"] == "ready"
    assert recs[0]["has_image"] is True


def test_recent_list_marks_queued_without_image(client, monkeypatch):
    """只入队（wait=false）没有产物 → has_image 必须为 False（前端别去取破图）。"""
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    resp = client.post(
        "/adhoc/generate", json={"prompt": "a bird", "backend": "comfyui", "wait": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"
    recs = client.get("/adhoc?limit=5").json()
    assert recs[0]["status"] == "queued"
    assert recs[0]["has_image"] is False


def test_manifest_endpoint(client, monkeypatch):
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    out = client.post("/adhoc/generate", json={"prompt": "a dog", "backend": "comfyui"}).json()
    manifest = client.get(f"/adhoc/{out['run_id']}/manifest").json()
    assert manifest["request"]["prompt"] == "a dog"
    assert manifest["result"]["provider"] == "comfyui"


def test_illegal_run_id_rejected(client):
    """run_id 直接拼路径 → 非法 id 必须挡住（不能读任意文件）。"""
    assert client.get("/adhoc/..%2F..%2Fetc%2Fpasswd/image").status_code in (400, 404)
    assert client.get("/adhoc/adhoc_..%2F..%2Fetc/image").status_code in (400, 404)


# ---------- 即梦（Dreamina）后端 ----------

def test_adhoc_generate_dreamina_backend(client, monkeypatch):
    from charvar import dreamina_image as dm
    import pathlib as _pathlib

    from PIL import Image as _Image

    def fake_generate(**kwargs):
        directory = _pathlib.Path(kwargs["download_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        image = directory / "sid_image_1.png"
        dm_image = _Image.new("RGB", (32, 48), (10, 10, 10))  # 色块：躲过纯色帧检测
        dm_image.paste((120, 120, 120), (24, 0, 32, 48))
        dm_image.save(image, format="PNG")
        return dm.DreaminaGenerateResult(
            submit_id="dm-1", gen_status="success", images=[image],
            width=32, height=48, model="", ratio="3:4", resolution="2k",
        )

    monkeypatch.setattr(dm, "generate", fake_generate)
    resp = client.post("/adhoc/generate", json={
        "prompt": "a red apple", "backend": "dreamina", "aspect_ratio": "3:4",
    })
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["provider"] == "dreamina"
    assert out["prompt_id"] == "dm-1"
    assert out["image_url"].startswith("/adhoc/")
