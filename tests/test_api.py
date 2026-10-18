"""FastAPI 冒烟：预设/主体 CRUD/生成/历史/导出。"""
from __future__ import annotations

import json
import pathlib

import httpx

import pytest
from fastapi.testclient import TestClient

from charvar.api import build_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_presets_and_detail(client):
    data = client.get("/presets").json()
    names = {p["name"] for p in data}
    assert {"q21_default_30", "q21_legacy_30", "matrix_basic"} <= names
    detail = client.get("/presets/q21_default_30").json()
    assert detail["count"] == 30
    assert len(detail["variants"]) == 30


def test_subject_crud_and_generate_flow(client):
    profile = {
        "id": "demo_witch",
        "name": "演示",
        "subject_noun": "woman",
        "trigger_word": "sw1tch",
        "style_lock": ["the color palette"],
        "identity_lock": ["long silver-white hair"],
    }
    assert client.post("/subjects", json=profile).status_code == 201
    assert client.post("/subjects", json=profile).status_code == 409
    got = client.get("/subjects/demo_witch").json()
    assert got["subject_noun"] == "woman"
    assert client.get("/subjects/missing").status_code == 404

    req = {"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42}
    gen = client.post("/generate", json=req)
    assert gen.status_code == 200
    result = gen.json()
    assert result["run_id"]
    assert len(result["items"]) == 30

    run_id = result["run_id"]
    assert client.get(f"/runs/{run_id}").json()["seed"] == 42
    assert len(client.get("/runs").json()) == 1
    exported = client.get(f"/runs/{run_id}/export", params={"format": "txt"}).text
    assert exported.splitlines()[0] == result["items"][0]["prompt"]
    zip_bytes = client.get(f"/runs/{run_id}/export", params={"format": "captions_zip"}).content
    assert zip_bytes[:2] == b"PK"
    assert client.delete(f"/runs/{run_id}").status_code == 200
    assert client.get(f"/runs/{run_id}").status_code == 404

    put = client.put("/subjects/demo_witch", json={"subject_noun": "girl", "name": "演示 2"})
    assert put.status_code == 200
    assert client.get("/subjects/demo_witch").json()["subject_noun"] == "girl"
    assert client.delete("/subjects/demo_witch").status_code == 200
    assert client.get("/subjects").json() == []


def test_generate_with_inline_subject_and_fixed_parts(client):
    body = {
        "subject": {"id": "temp", "name": "临时", "subject_noun": "person"},
        "preset": "matrix_basic",
        "seed": 7,
        "randomize_background": False,
        "randomize_lighting": False,
        "fixed_background": "red",
    }
    result = client.post("/generate", json=body).json()
    assert len(result["items"]) == 30
    assert result["items"][0]["background"] == "red"
    assert result["items"][0]["lighting"] is None
    assert "against a red background" in result["items"][0]["prompt"]
    assert "The lighting is" not in result["items"][0]["prompt"]


def _mock_comfy(monkeypatch, workflows_dir: pathlib.Path):
    """把 exporter._httpx_client 替换为 mock server（/prompt + /history）。"""
    from charvar.exporters import comfyui as comfyui_exporter

    queued: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            body = json.loads(request.content.decode())
            pid = f"pid-{len(queued)}"
            queued.append(body)
            return httpx.Response(200, json={"prompt_id": pid})
        if request.url.path.startswith("/history/"):
            pid = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    pid: {
                        "status": {"completed": True},
                        "outputs": {
                            "47": {
                                "images": [
                                    {"filename": pid + ".png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        return httpx.Response(404)

    class FakeClient:
        def __init__(self, **kw):
            self._inner = httpx.Client(transport=httpx.MockTransport(handler))

        def __enter__(self):
            return self._inner

        def __exit__(self, *args):
            self._inner.close()

    monkeypatch.setattr(comfyui_exporter, "_httpx_client", lambda **kw: FakeClient(**kw))
    return queued


def test_comfy_queue_with_mocked_server(tmp_path, monkeypatch, client):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "TestWF.json").write_text(
        json.dumps(
            {
                "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
                "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
            }
        ),
        encoding="utf-8",
    )
    (workflows_dir / "TestWF.mapping.json").write_text(
        json.dumps(
            {
                "output_nodes": ["47"],
                "mappings": [
                    {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
                    {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    queued = _mock_comfy(monkeypatch, workflows_dir)

    listing = client.get("/comfy/workflows").json()
    assert listing["workflows_dir"] == str(workflows_dir)
    assert listing["workflows"] == [{"name": "TestWF", "has_workflow": True, "has_mapping": True}]

    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    run_id = client.post(
        "/generate", json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42}
    ).json()["run_id"]

    queue_resp = client.post(
        f"/runs/{run_id}/comfyui", json={"workload": "TestWF", "wait": False}
    ).json()
    assert queue_resp["succeeded"] == 30
    assert queue_resp["failed"] == 0
    assert [s["prompt_id"] for s in queue_resp["submissions"][:2]] == ["pid-0", "pid-1"]
    injected = queued[0]["prompt"]
    assert injected["11"]["inputs"]["text"].startswith("Create an image of the person")
    assert injected["47"]["inputs"]["filename_prefix"].endswith("_000_full_front")

    records = client.get(f"/runs/{run_id}/comfyui").json()
    assert len(records) == 30
    assert records[0]["prompt_id"] == "pid-0"


def test_comfy_wait_records_output_files(tmp_path, monkeypatch, client):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "WaitWF.json").write_text(
        json.dumps(
            {
                "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
                "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
            }
        ),
        encoding="utf-8",
    )
    (workflows_dir / "WaitWF.mapping.json").write_text(
        json.dumps(
            {
                "output_nodes": ["47"],
                "mappings": [
                    {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
                    {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    _mock_comfy(monkeypatch, workflows_dir)

    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    run_id = client.post(
        "/generate",
        json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42, "count": 2},
    ).json()["run_id"]

    resp = client.post(
        f"/runs/{run_id}/comfyui", json={"workload": "WaitWF", "wait": True}
    ).json()
    assert resp["succeeded"] == 2
    files = resp["submissions"][0]["files"]
    assert files == [{"filename": "pid-0.png", "subfolder": "", "type": "output"}]
    record = client.get(f"/runs/{run_id}/comfyui").json()[0]
    assert record["output_files"] == [{"filename": "pid-0.png", "subfolder": "", "type": "output"}]
