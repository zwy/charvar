"""生成页新流程：参考图走「Playground 式来源 + role 分工」。

覆盖：
- /generate 两模式都把 references 存进结果（页面刷新不丢）；
- 三种来源（上传暂存 / 主体档案槽位 / 最近生成原图）解析成真实文件；
- role 非法 / 来源缺失 / 未知槽位 → 400（浏览器不能塞任意路径）；
- /runs/{id}/comfyui 带 references → 上传 + 注入 image1..N + 自动选图片编辑 workflow；
- 旧 reference_kinds 参数仍可用（向后兼容）；
- regenerate 重放快照里的 references。
"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar.api import build_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def _png_bytes(size=(64, 96), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _mock_comfy(monkeypatch, uploads: list[str]):
    """mock ComfyUI：/upload/image 收下文件名，/prompt 记下注入后的 prompt。"""
    queued: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            import re

            m = re.search(rb'filename="([^"]+)"', request.content)
            name = m.group(1).decode() if m else "uploaded.png"
            uploads.append(name)
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
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
                            "47": {"images": [{"filename": pid + ".png", "subfolder": "", "type": "output"}]}
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

    from charvar.exporters import comfyui as comfyui_exporter

    monkeypatch.setattr(comfyui_exporter, "_httpx_client", lambda **kw: FakeClient(**kw))
    return queued


def _workflow_pair(workflows_dir: pathlib.Path, name: str, with_image: bool = False) -> None:
    nodes = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
    }
    mappings = [
        {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
        {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
    ]
    if with_image:
        nodes["470"] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}}
        mappings.insert(0, {"field": "image", "node_id": "470", "input_key": "image", "required": False})
    (workflows_dir / f"{name}.json").write_text(json.dumps(nodes), encoding="utf-8")
    (workflows_dir / f"{name}.mapping.json").write_text(
        json.dumps({"output_nodes": ["47"], "mappings": mappings}), encoding="utf-8"
    )


def _subject_with_front(client, subject_id: str = "demo_witch") -> None:
    client.post("/subjects", json={"id": subject_id, "name": "演示"})
    resp = client.post(
        f"/subjects/{subject_id}/images/front/upload",
        files={"file": ("front.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200, resp.text


def _upload_staged(client) -> str:
    resp = client.post(
        "/adhoc/references", files={"file": ("ref.png", _png_bytes(), "image/png")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ---------- /generate 存 references ----------

def test_generate_classic_stores_references(client):
    """经典模式：references 原样存进结果（推送生图时按它取图）。"""
    _subject_with_front(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 42,
            "count": 2,
            "references": [{"subject_id": "demo_witch", "kind": "front", "role": "front"}],
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["references"] == [
        {"staged_id": None, "subject_id": "demo_witch", "kind": "front", "run_id": None, "index": None, "role": "front"}
    ]
    # 落库后重新取也还在（页面刷新不丢）
    again = client.get(f"/runs/{result['run_id']}").json()
    assert again["references"][0]["subject_id"] == "demo_witch"


def test_generate_without_references_stores_empty(client):
    _subject_with_front(client)
    result = client.post(
        "/generate", json={"subject_id": "demo_witch", "preset": "q21_default_30", "count": 1}
    ).json()
    assert result["references"] == []


# ---------- 来源解析与校验 ----------

def test_reference_from_subject_slot(client):
    _subject_with_front(client)
    from charvar.api import _reference_paths_from_specs
    from charvar.models import ReferenceSpec

    paths_out, roles, staged = _reference_paths_from_specs(
        [ReferenceSpec(subject_id="demo_witch", kind="front")]
    )
    assert len(paths_out) == 1
    assert paths_out[0].exists()
    assert roles == ["front"]  # role 缺省按 front
    assert staged == []


def test_reference_role_is_kept(client):
    _subject_with_front(client)
    from charvar.api import _reference_paths_from_specs
    from charvar.models import ReferenceSpec

    _paths, roles, _staged = _reference_paths_from_specs(
        [ReferenceSpec(subject_id="demo_witch", kind="front", role="headshot")]
    )
    assert roles == ["headshot"]


def test_reference_from_staged_upload(client):
    from charvar.api import _reference_paths_from_specs
    from charvar.models import ReferenceSpec

    ref_id = _upload_staged(client)
    paths_out, roles, staged = _reference_paths_from_specs([ReferenceSpec(staged_id=ref_id)])
    assert len(paths_out) == 1 and paths_out[0].exists()
    assert roles == ["front"]
    assert staged == [ref_id]


def test_reference_from_run_original(client, tmp_path):
    """来源 = 最近生成的原图：charvar run 按 index 取第几张。"""
    from charvar.api import _reference_paths_from_specs
    from charvar.models import ReferenceSpec

    run_id = "demo_run_20260101"
    from charvar import assets

    run_dir = assets.original_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "000000.png").write_bytes(_png_bytes(color=(1, 2, 3)))
    (run_dir / "000001.png").write_bytes(_png_bytes(color=(4, 5, 6)))

    first, _roles, _staged = _reference_paths_from_specs([ReferenceSpec(run_id=run_id)])
    second, _roles, _staged = _reference_paths_from_specs(
        [ReferenceSpec(run_id=run_id, index=1)]
    )
    assert first[0].name == "000000.png"
    assert second[0].name == "000001.png"


def test_reference_rejects_illegal_role(client):
    _subject_with_front(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "references": [{"subject_id": "demo_witch", "kind": "front", "role": "../../etc/passwd"}],
        },
    )
    assert resp.status_code == 400
    assert "分工不合法" in resp.text


def test_reference_rejects_unknown_kind(client):
    _subject_with_front(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "references": [{"subject_id": "demo_witch", "kind": "nope"}],
        },
    )
    assert resp.status_code == 400
    assert "未知图片类型" in resp.text


def test_reference_rejects_missing_source(client):
    _subject_with_front(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "references": [{"role": "front"}],
        },
    )
    assert resp.status_code == 400
    assert "缺少来源" in resp.text


def test_reference_rejects_missing_file(client):
    """主体档案里没这张图 → 400，不接受浏览器给的路径。"""
    client.post("/subjects", json={"id": "empty_witch", "name": "空"})
    resp = client.post(
        "/generate",
        json={
            "subject_id": "empty_witch",
            "preset": "q21_default_30",
            "references": [{"subject_id": "empty_witch", "kind": "front"}],
        },
    )
    assert resp.status_code == 400
    assert "参考图不存在" in resp.text


def test_reference_rejects_run_without_originals(client):
    from charvar.api import _reference_paths_from_specs
    from charvar.models import ReferenceSpec

    with pytest.raises(Exception) as excinfo:
        _reference_paths_from_specs([ReferenceSpec(run_id="demo_run_missing")])
    assert "没有原图" in str(excinfo.value)


# ---------- 推送生图（ComfyUI） ----------

def test_comfy_push_uploads_reference_and_injects_image(tmp_path, monkeypatch, client):
    """带 references 推送：上传参考图 → 注入 image 节点 → 自动选图片编辑 workflow。"""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    _workflow_pair(workflows_dir, "T2I")
    _workflow_pair(workflows_dir, "Edit", with_image=True)
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads)
    assert client.put(
        "/settings", json={"comfy_t2i_workflow": "T2I", "comfy_edit_workflow": "Edit"}
    ).status_code == 200

    _subject_with_front(client)
    run_id = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 42,
            "count": 2,
            "references": [{"subject_id": "demo_witch", "kind": "front", "role": "front"}],
        },
    ).json()["run_id"]

    resp = client.post(
        f"/runs/{run_id}/comfyui",
        json={
            "wait": False,
            "references": [{"subject_id": "demo_witch", "kind": "front", "role": "front"}],
        },
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["workload"] == "Edit"  # 有参考图 → 图片编辑 workflow
    assert out["succeeded"] == 2 and out["failed"] == 0
    # remote_name 唯一（含 run_id，避免同名互相覆盖）
    assert uploads == [f"charvar_{run_id}_0.jpg"]
    # 参考图注入了 LoadImage 的 image 字段
    assert queued[0]["prompt"]["470"]["inputs"]["image"] == uploads[0]


def test_comfy_push_without_reference_uses_t2i(tmp_path, monkeypatch, client):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    _workflow_pair(workflows_dir, "T2I")
    _workflow_pair(workflows_dir, "Edit", with_image=True)
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    _mock_comfy(monkeypatch, [])
    assert client.put(
        "/settings", json={"comfy_t2i_workflow": "T2I", "comfy_edit_workflow": "Edit"}
    ).status_code == 200

    _subject_with_front(client)
    run_id = client.post(
        "/generate",
        json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42, "count": 1},
    ).json()["run_id"]

    out = client.post(f"/runs/{run_id}/comfyui", json={"wait": False}).json()
    assert out["workload"] == "T2I"


def test_legacy_reference_kinds_still_work(tmp_path, monkeypatch, client):
    """老参数 reference_kinds（主体档案槽位）继续可用。"""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    _workflow_pair(workflows_dir, "T2I")
    _workflow_pair(workflows_dir, "Edit", with_image=True)
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads)
    assert client.put(
        "/settings", json={"comfy_t2i_workflow": "T2I", "comfy_edit_workflow": "Edit"}
    ).status_code == 200

    _subject_with_front(client)
    run_id = client.post(
        "/generate",
        json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42, "count": 1},
    ).json()["run_id"]

    out = client.post(
        f"/runs/{run_id}/comfyui", json={"wait": False, "reference_kinds": ["front"]}
    ).json()
    assert out["workload"] == "Edit"
    assert uploads == [f"charvar_{run_id}_0.jpg"]


def test_push_snapshots_references_for_regenerate(tmp_path, monkeypatch, client):
    """推送时把 references 存进队列快照 → 单条重新生成原样重放。"""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    _workflow_pair(workflows_dir, "Edit", with_image=True)
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads)
    assert client.put("/settings", json={"comfy_edit_workflow": "Edit"}).status_code == 200

    _subject_with_front(client)
    run_id = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 42,
            "count": 1,
            "references": [{"subject_id": "demo_witch", "kind": "front", "role": "front"}],
        },
    ).json()["run_id"]

    client.post(
        f"/runs/{run_id}/comfyui",
        json={
            "wait": False,
            "references": [{"subject_id": "demo_witch", "kind": "front", "role": "front"}],
        },
    )
    assert len(uploads) == 1

    # 重新生成：参考图按快照重放（再上传一次，名字仍唯一）
    resp = client.post(f"/runs/{run_id}/regenerate", json={"item_index": 0, "seed": 99})
    assert resp.status_code == 200, resp.text
    assert len(uploads) == 2
