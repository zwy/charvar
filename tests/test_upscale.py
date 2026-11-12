"""放大（charvar.adhoc_upscale）：校验/倍数/run_id/暂存池/mapping 一致性 + ffmpeg 冒烟。"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import time

import pytest

from charvar import adhoc_upscale, storage
from charvar.exporters import comfyui as comfyui_exporter

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO / "data" / "workflows" / "utility_seedvr2_3b_int8_upscale_video.json"
WORKFLOW_MAPPING = REPO / "data" / "workflows" / "utility_seedvr2_3b_int8_upscale_video.mapping.json"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    return tmp_path


def _mp4_bytes():
    return b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2\x00" + b"\x00" * 32


# ---------- 倍数与校验 ----------

def test_compute_multiplier():
    assert adhoc_upscale.compute_multiplier(540, 1080) == 2.0
    assert adhoc_upscale.compute_multiplier(720, 1080) == 1.5
    with pytest.raises(adhoc_upscale.UpscaleError):
        adhoc_upscale.compute_multiplier(0, 1080)


def test_validate_target_and_backend(env):
    video = env / "in.mp4"
    video.write_bytes(_mp4_bytes())
    with pytest.raises(adhoc_upscale.UpscaleError, match="target"):
        adhoc_upscale.validate(adhoc_upscale.UpscaleRequest(input_path=video, target="2160p"))
    # backend 不是 Literal 校验能收的字面值 → 构造期就是 pydantic 错；饶过构造再查 validate 的兜底
    with pytest.raises(adhoc_upscale.UpscaleError, match="backend"):
        adhoc_upscale.validate(
            adhoc_upscale.UpscaleRequest.model_construct(backend="seedvr2", input_path=video, target="1080p")
        )
    adhoc_upscale.validate(adhoc_upscale.UpscaleRequest(input_path=video))  # 不抛 = 通过
    adhoc_upscale.validate(adhoc_upscale.UpscaleRequest(input_path=video, backend="ffmpeg", target="4k"))


def test_validate_missing_and_bad_magic(env):
    with pytest.raises(adhoc_upscale.UpscaleError, match="不存在"):
        adhoc_upscale.validate(adhoc_upscale.UpscaleRequest(input_path=env / "nope.mp4"))
    junk = env / "junk.mp4"
    junk.write_bytes(b"not a video at all")
    with pytest.raises(adhoc_upscale.UpscaleError, match="容器"):
        adhoc_upscale.validate(adhoc_upscale.UpscaleRequest(input_path=junk))


# ---------- run_id 严格正则（run_id 直接拼路径，防 ..） ----------

def test_run_dir_guard():
    assert adhoc_upscale.run_dir("ups_20260101-000000_abcd1234").name.startswith("ups_")
    for bad in ("../etc/passwd", "adhoc_x", "ups_..x", ""):
        with pytest.raises(adhoc_upscale.UpscaleError):
            adhoc_upscale.run_dir(bad)


# ---------- 暂存输入池 ----------

def test_staged_input_roundtrip(env):
    path = adhoc_upscale.save_staged_input(_mp4_bytes(), "clip.mp4")
    assert path.suffix == ".mp4"
    assert adhoc_upscale.staged_input(path.stem).exists()
    adhoc_upscale.clear_staged_inputs([path.stem])
    with pytest.raises(adhoc_upscale.UpscaleError):
        adhoc_upscale.staged_input(path.stem)


def test_staged_input_rejects_junk_and_oversize(env, monkeypatch):
    with pytest.raises(adhoc_upscale.UpscaleError, match="容器"):
        adhoc_upscale.save_staged_input(b"junk-junk", "x.mp4")
    monkeypatch.setattr(adhoc_upscale, "_INPUT_MAX_BYTES", 4)
    with pytest.raises(adhoc_upscale.UpscaleError, match="上限"):
        adhoc_upscale.save_staged_input(_mp4_bytes(), "big.mp4")
    with pytest.raises(adhoc_upscale.UpscaleError, match="id"):
        adhoc_upscale.staged_input("../evil")


# ---------- mapping 与 workflow 的一致性（换 workflow 碎了测试能报警） ----------

def test_mapping_targets_real_nodes_and_inputs():
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    mapping = json.loads(WORKFLOW_MAPPING.read_text(encoding="utf-8"))
    required = {"file", "multiplier"}
    fields = set()
    for entry in mapping["mappings"]:
        node_id = str(entry["node_id"])
        assert node_id in workflow, f"mapping 指向的节点不存在：{node_id}"
        node = workflow[node_id]
        assert entry["input_key"] in node.get("inputs", {}), (
            f"workflow {node_id} 没有 input_key {entry['input_key']}（field={entry['field']}）"
        )
        fields.add(entry["field"])
    assert required <= fields
    # output_nodes 存在且是 SaveVideo 类输出节点
    for node_id in mapping["output_nodes"]:
        assert node_id in workflow
        assert workflow[node_id]["class_type"] == "SaveVideo"


def test_load_workflow_pair_from_repo_workflows_dir():
    workflow, mapping = comfyui_exporter.load_workflow_pair(WORKFLOW.stem)
    assert workflow and mapping.get("mappings")
    assert mapping["output_nodes"] == ["76"]


# ---------- history 输出三键兼容（SaveVideo 的键与图片节点不同） ----------

def test_output_filenames_key_variants():
    def entry(key):
        return {
            "outputs": {"76": {key: [{"filename": "upscale/u_00001.mp4", "subfolder": "upscale", "type": "output"}]}},
            "status": {"completed": True},
        }

    for key in ("images", "gifs", "videos"):
        files = comfyui_exporter._output_filenames(entry(key), ["76"])
        assert len(files) == 1, key
        assert files[0]["subfolder"] == "upscale"
        assert files[0]["type"] == "output"
    # 没有任何键 → 空（调用方报「没有输出文件」）
    assert comfyui_exporter._output_filenames(entry("other"), ["76"]) == []


# ---------- ffmpeg 后端端到端（本地真实 ffmpeg；慢但都在秒级） ----------

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")


def _make_video(path: pathlib.Path, width: int, height: int, seconds: float = 0.5) -> pathlib.Path:
    cmd = [
        shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=10:duration={seconds}",
        "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def test_ffmpeg_upscale_end_to_end(env):
    source = _make_video(env / "src.mp4", 480, 320)
    result = adhoc_upscale.generate(
        adhoc_upscale.UpscaleRequest(backend="ffmpeg", input_path=source, target="1080p")
    )
    assert result.status == "ready"
    assert result.passthrough is False
    assert result.source_short == 320
    assert result.target_short == 1080
    assert result.width == 1620 and result.height == 1080  # 取偶后的精确放大
    out = pathlib.Path(result.video_path)
    assert out.exists() and out.stat().st_size > 0
    assert result.poster_path and pathlib.Path(result.poster_path).exists()
    manifest = adhoc_upscale.read_manifest(result.run_id)
    assert manifest["status"] == "ready"
    assert manifest["compute"]["multiplier"] == pytest.approx(3.375)


def test_ffmpeg_passthrough_when_source_already_meets_target(env):
    source = _make_video(env / "src_hd.mp4", 1920, 1080)
    result = adhoc_upscale.generate(
        adhoc_upscale.UpscaleRequest(backend="ffmpeg", input_path=source, target="720p")
    )
    assert result.status == "ready"
    assert result.passthrough is True
    assert result.multiplier == 0.0
    assert pathlib.Path(result.video_path).exists()


def test_list_recent_shows_ready_record(env):
    source = _make_video(env / "src.mp4", 480, 320)
    result = adhoc_upscale.generate(
        adhoc_upscale.UpscaleRequest(backend="ffmpeg", input_path=source, target="720p")
    )
    records = adhoc_upscale.list_recent()
    assert [r["run_id"] for r in records] == [result.run_id]
    rec = records[0]
    assert rec["has_video"] is True
    assert rec["status"] == "ready"
    assert rec["backend"] == "ffmpeg"
    assert rec["target"] == "720p"


# ---------- 网页来源：图片页最近生成视频（video_run_id → 服务端解析路径） ----------


def test_api_upscale_video_run_id_source(tmp_path, monkeypatch):
    """video_run_id 来源：run_id 过严格正则、产物从落盘布局解析（浏览器不给路径）。"""
    from fastapi.testclient import TestClient
    from charvar.api import build_app

    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=tmp_path / "test.db")
    client = TestClient(app)

    # 手工造一个 视频 run（original/ 里放一段真视频，manifest 供查询）
    from charvar import adhoc_video

    run_id = "video_20260101-000000_feedface"
    run = adhoc_video.video_root() / run_id
    (run / "original").mkdir(parents=True)
    _make_video(run / "original" / "000000.mp4", 480, 320, seconds=0.4)
    (run / "manifest.json").write_text("{}", encoding="utf-8")

    # 两个来源同给 / 同缺 → 400
    r = client.post("/upscales/generate", json={"staged_id": "x", "video_run_id": "y",
                                                "backend": "ffmpeg", "target": "720p"})
    assert r.status_code == 400 and "二选一" in r.json()["detail"]
    r = client.post("/upscales/generate", json={"backend": "ffmpeg", "target": "720p"})
    assert r.status_code == 400 and "二选一" in r.json()["detail"]
    # run_id 不合法 → 400（严格正则，防路径穿越）
    r = client.post("/upscales/generate", json={"video_run_id": "../evil", "backend": "ffmpeg",
                                                "target": "720p"})
    assert r.status_code == 400
    # 存在但没有成片 → 404
    empty = adhoc_video.video_root() / "video_20260101-000000_deadbeef"
    empty.mkdir(parents=True)
    r = client.post("/upscales/generate", json={"video_run_id": "video_20260101-000000_deadbeef",
                                                "backend": "ffmpeg", "target": "720p"})
    assert r.status_code == 404
    # 正常路径：pending 返回 + input_label 记 run_id + refresh 到 ready
    r = client.post("/upscales/generate", json={"video_run_id": run_id, "backend": "ffmpeg",
                                                "target": "720p"})
    assert r.status_code == 200, r.text
    payload = r.json()
    run_dir = adhoc_upscale.run_dir(payload["run_id"])
    manifest = adhoc_upscale.read_manifest(payload["run_id"])
    assert manifest["request"]["input_label"] == run_id
    for _ in range(100):
        status = client.get(f"/upscales/{payload['run_id']}/status").json()
        if status["status"] in ("ready", "failed"):
            break
        time.sleep(0.1)
    assert status["status"] == "ready", status
    assert status["passthrough"] is False
    # 「最近生成」列表显示 label（视频 run_id）
    rec = adhoc_upscale.list_recent()[0]
    assert rec["input_name"] == run_id


# ---------- 设置（放大页默认组回读） ----------

def test_settings_upscale_group_roundtrip(tmp_path, monkeypatch):
    from charvar import settings_service

    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    with storage.connect(None) as conn:
        saved = settings_service.save_settings(
            conn, {"upscale": {"backend": "ffmpeg", "target": "720p", "workflow": "w1"}}
        )
        assert saved.upscale.backend == "ffmpeg"
        again = settings_service.get_settings(conn)
    assert again.upscale.target == "720p"
    assert again.upscale.workflow == "w1"
