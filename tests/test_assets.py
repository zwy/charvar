"""图片资产 API：history 同步 / 本地展示 / contact sheet。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar.api import build_app
from charvar.assets import ensure_thumb, resolve_run_asset, sheet_path, thumb_path
from charvar import storage
from charvar.models import GenerateResult, PromptItem


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    return TestClient(build_app(static_dir=None, db_path=tmp_path / "test.db"))


def make_result(run_id: str = "demo_run") -> GenerateResult:
    return GenerateResult(
        run_id=run_id,
        subject_id="demo_subject",
        preset="test",
        seed=42,
        items=[
            PromptItem(
                index=0,
                variant_id="full_front",
                prompt="demo prompt",
                master="master",
                variant_text="variant",
                background="gray",
                lighting="soft light",
                caption="demo caption",
                meta={"framing": "full"},
            )
        ],
    )


def test_image_entry_reads_local_assets(tmp_path, monkeypatch, client):
    result = make_result()
    with storage.connect(tmp_path / "test.db") as conn:
        storage.save_run(conn, result)
        storage.save_comfy_submission(
            conn,
            result.run_id,
            0,
            "full_front",
            "prompt-1",
            "demo_subject_000_full_front",
            [{"filename": "a.png", "subfolder": "", "type": "output"}],
        )

    assets_dir = tmp_path / "generated" / result.run_id
    (assets_dir / "original").mkdir(parents=True)
    (assets_dir / "thumbs").mkdir(parents=True)
    Image.new("RGB", (32, 24), "red").save(assets_dir / "original" / "000000.png")

    response = client.get(f"/runs/{result.run_id}/images")
    assert response.status_code == 200
    data = response.json()
    assert data["completed"] == 1
    assert data["items"][0]["primary_url"].endswith("/comfy-image/0")
    assert "/assets/thumbs/000000.webp" in data["items"][0]["thumb_url"]
    assert (assets_dir / "thumbs" / "000000.webp").exists()


def test_comfy_image_serves_local_file(tmp_path, monkeypatch, client):
    result = make_result()
    with storage.connect(tmp_path / "test.db") as conn:
        storage.save_run(conn, result)
        storage.save_comfy_submission(
            conn,
            result.run_id,
            0,
            "full_front",
            "prompt-1",
            "demo_subject_000_full_front",
            [{"filename": "a.png", "subfolder": "", "type": "output"}],
        )
    assets_dir = tmp_path / "generated" / result.run_id
    (assets_dir / "original").mkdir(parents=True)
    Image.new("RGB", (40, 30), "green").save(assets_dir / "original" / "000000.png")

    response = client.get(f"/runs/{result.run_id}/comfy-image/0")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")


def test_asset_endpoints_resolve_relative_paths(tmp_path, monkeypatch, client):
    result = make_result()
    with storage.connect(tmp_path / "test.db") as conn:
        storage.save_run(conn, result)
        storage.save_comfy_submission(
            conn,
            result.run_id,
            0,
            "full_front",
            "prompt-1",
            "demo_subject_000_full_front",
            [{"filename": "a.png", "subfolder": "", "type": "output"}],
        )
    assets_dir = tmp_path / "generated" / result.run_id
    (assets_dir / "original").mkdir(parents=True)
    (assets_dir / "thumbs").mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(assets_dir / "thumbs" / "000000.webp")

    assert client.get(f"/runs/{result.run_id}/assets/thumbs/000000.webp").status_code == 200
    assert client.get(f"/runs/{result.run_id}/sheets/contact").status_code == 404
    traversal = client.get(f"/runs/{result.run_id}/assets/thumbs/..%2F..%2Ftest.db")
    assert traversal.status_code in (400, 404)


def test_contact_sheet_endpoint_creates_png(tmp_path, monkeypatch, client):
    result = make_result()
    with storage.connect(tmp_path / "test.db") as conn:
        storage.save_run(conn, result)
        storage.save_comfy_submission(
            conn,
            result.run_id,
            0,
            "full_front",
            "prompt-1",
            "demo_subject_000_full_front",
            [{"filename": "a.png", "subfolder": "", "type": "output"}],
        )
    assets_dir = tmp_path / "generated" / result.run_id
    (assets_dir / "original").mkdir(parents=True)
    Image.new("RGB", (40, 30), "blue").save(assets_dir / "original" / "000000.png")

    response = client.post(f"/runs/{result.run_id}/contact-sheet", json={"mode": "contact"})
    assert response.status_code == 200
    sheet_path = assets_dir / "sheets" / "contact_sheet.png"
    assert sheet_path.exists()
