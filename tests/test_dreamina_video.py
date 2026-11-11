"""即梦视频（画布通路）测试：CanvasRunner 打桩，流程与校验不调真 CLI。

分层：``dreamina_canvas`` 子进程层在 ``test_dreamina_canvas.py`` 单测；
本文件用假 ``CanvasRunner``（同时替换 dreamina_video / dreamina_image 里
的引用）锁「dispatch_mode 分派组合校验」「submit→闸门→manifest.canvas」
「refresh 轮询映射」「finalize 收片与并发兜底」。
"""
from __future__ import annotations

import json
import pathlib
import time
from typing import Any

import pytest

from charvar import adhoc_video, dreamina_image, dreamina_video
from charvar.dreamina_canvas import DreaminaParamError, DreaminaTaskError, DreaminaTimeoutError

MP4_MAGIC = b"\x00\x00\x00\x20ftypisom\x00\x00" + b"\x00" * 24


def _mp4_bytes() -> bytes:
    return MP4_MAGIC


class FakeCanvas:
    """替身 CanvasRunner：记录调用、恒成功、产物是真 mp4 文件。"""

    def __init__(self, name: str, *, project_id: str | None = None) -> None:
        self.name = name
        self.project_id = project_id or "proj-0001"
        self.web_url = "https://jimeng.test/canvas/proj-0001"
        self.calls: list[tuple[str, ...]] = []
        self.rid_seq = 0
        self.run_should_fail = False

    def ensure_canvas(self) -> str:
        self.calls.append(("ensure_canvas", self.name))
        return self.project_id

    def upload(self, path: pathlib.Path) -> str:
        self.rid_seq += 1
        self.calls.append(("upload", str(path)))
        return f"res-{self.rid_seq:04d}"

    def wait_resource(self, rid: str, **kw: Any) -> dict:
        self.calls.append(("wait_resource", rid))
        return dict(resourceId=rid, status="success")

    def create_node(self, **kw: Any) -> str:
        self.calls.append(("create_node", kw["kind"], kw["model"], kw["mode"],
                           kw.get("ratio"), kw.get("resolution"), kw.get("duration"),
                           kw.get("count"), tuple(kw.get("ref_ids") or [])))
        return f"node-{len(self.calls):03d}"

    def quote(self, node_ids: list[str]) -> dict:
        self.calls.append(("quote", tuple(node_ids)))
        return dict(totalMaxCredits=5, confirmationRequired=False)

    def confirm(self, node_ids: list[str], *, credit_ceiling: int) -> str:
        self.calls.append(("confirm", tuple(node_ids), credit_ceiling))
        return "tok-1"

    def run(self, node_ids: list[str], submit_ids: list[str], credit_token: str) -> list[str]:
        self.calls.append(("run", tuple(node_ids), tuple(submit_ids)))
        if self.run_should_fail:
            raise DreaminaTaskError("服务端拒绝（测试桩）")
        return list(submit_ids)

    def create_and_run(self, **kw: Any) -> tuple[str, str]:
        nid = self.create_node(**kw)
        self.quote([nid])
        tok = self.confirm([nid], credit_ceiling=9)
        sids = self.run([nid], [f"sid-{len(self.calls):03d}"], tok)
        return nid, sids[0]

    def status(self, submit_id: str) -> dict:
        self.calls.append(("status", submit_id))
        return dict(operationRef=submit_id, state="succeeded",
                    resources=[dict(resourceId="vid-0001", state="succeeded")])

    def node_outputs(self, node_ids: list[str]) -> dict[str, list[dict]]:
        self.calls.append(("node_outputs", tuple(node_ids)))
        return {node_ids[0]: [dict(
            type="video", status="success", resourceId="vid-0001",
            submitId="sid-1", video=dict(width=1280, height=720),
        )]}

    def download(self, rid: str, dest_dir: pathlib.Path) -> dict:
        self.calls.append(("download", rid, str(dest_dir)))
        dest = pathlib.Path(dest_dir) / f"dreamina-{rid}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_mp4_bytes())
        return dict(resourceId=rid, path=str(dest), size=len(MP4_MAGIC))


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    for key in ("DREAMINA_CLI", "DREAMINA_VIDEO_MODEL", "DREAMINA_IMAGE_MODEL",
                "DREAMINA_VIDEO_RESOLUTION", "DREAMINA_POLL"):
        monkeypatch.delenv(key, raising=False)
    fake = FakeCanvas("charvar-video-test")
    monkeypatch.setattr(dreamina_video, "CanvasRunner", lambda name, **kw: FakeCanvas(name, **kw))
    monkeypatch.setattr(dreamina_image, "CanvasRunner", lambda name, **kw: FakeCanvas(name, **kw))
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def fake_poster(video_path, run_dir):
        target = pathlib.Path(run_dir) / "thumbs" / "000000.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8)
        return target
    monkeypatch.setattr(adhoc_video, "_make_poster", fake_poster)
    return fake


def _req(**kw):
    base = dict(prompt="dance clip", backend="dreamina", seed=7, duration=5,
                resolution="720p", aspect_ratio="9:16")
    base.update(kw)
    return adhoc_video.VideoRequest(**base)


# ---------- dispatch / 校验 ----------


def test_dispatch_mode_matrix():
    ff = pathlib.Path("/tmp/f.png")
    lf = pathlib.Path("/tmp/l.png")
    r = pathlib.Path("/tmp/r.png")
    rv = pathlib.Path("/tmp/v.mp4")
    assert dreamina_video.dispatch_mode() == "t2v"
    assert dreamina_video.dispatch_mode(first_frame=ff) == "first_last_frame"
    assert dreamina_video.dispatch_mode(first_frame=ff, last_frame=lf) == "first_last_frame"
    assert dreamina_video.dispatch_mode(reference_paths=[r]) == "m2v"
    assert dreamina_video.dispatch_mode(reference_video_paths=[rv]) == "m2v"
    with pytest.raises(DreaminaParamError):
        dreamina_video.dispatch_mode(last_frame=lf)  # 尾帧必须配首帧
    with pytest.raises(DreaminaParamError):
        dreamina_video.dispatch_mode(first_frame=ff, reference_paths=[r])  # 首帧与参考互斥


def test_model_resolution_duration_pairing():
    # seedance 系小写枚举、大小写不敏感归一
    assert dreamina_video.validate_video("seedance_2.0_fast_vip", "720P", 5, "t2v") == "720p"
    assert dreamina_video.validate_video("seedance_2.0_vip", "4k", 15, "m2v") == "4k"
    # happyhorse/wan 大写枚举
    assert dreamina_video.validate_video("happyhorse_1.1", "720p", 4, "t2v") == "720P"
    with pytest.raises(DreaminaParamError):
        dreamina_video.validate_video("seedance_2.0_fast_vip", "1080p", 5, "t2v")  # 该档无 1080p
    with pytest.raises(DreaminaParamError):
        dreamina_video.validate_video("seedance_2.0_fast_vip", "720p", 31, "t2v")  # 超时长
    with pytest.raises(DreaminaParamError):
        dreamina_video.validate_video("seedance_pro_fast", "720p", 5, "m2v")  # 1.0fast 无 m2v
    with pytest.raises(DreaminaParamError):
        dreamina_video.validate_video("happyhorse_1.1", "720P", 5, "t2v", ratio="2:3")


def test_m2v_ref_caps():
    dreamina_video._validate_m2v_refs(
        "seedance_2.0_fast_vip",
        reference_paths=[pathlib.Path("/tmp/a.png")] * 9,
        reference_video_paths=[pathlib.Path("/tmp/b.mp4")] * 3,
    )
    with pytest.raises(DreaminaParamError):
        dreamina_video._validate_m2v_refs("happyhorse_1.1", reference_paths=None,
                                          reference_video_paths=[pathlib.Path("/tmp/b.mp4")])
    with pytest.raises(DreaminaParamError):
        dreamina_video._validate_m2v_refs("happyhorse_1.1",
                                          reference_paths=[pathlib.Path("/tmp/a.png")] * 10,
                                          reference_video_paths=None)


def test_resolve_chain(monkeypatch):
    monkeypatch.delenv("DREAMINA_VIDEO_MODEL", raising=False)
    monkeypatch.delenv("DREAMINA_VIDEO_TIMEOUT", raising=False)
    assert dreamina_video.resolve_model(None) == "seedance_2.0_fast_vip"
    assert dreamina_video.resolve_model("seedance2.0fast_vip") == "seedance_2.0_fast_vip"
    assert dreamina_video.resolve_model("seedance1.5pro") == "seedance_2.0_fast_vip"
    assert dreamina_video.resolve_resolution(None) == "720p"
    assert dreamina_video.resolve_duration(None) == 5
    assert dreamina_video.resolve_timeout(None) == 900.0


# ---------- submit（画布闸门全链 + manifest.canvas） ----------


def test_submit_t2v_flow(monkeypatch):
    fake = FakeCanvas("charvar-video-test")
    monkeypatch.setattr(dreamina_video, "CanvasRunner", lambda name, **kw: fake)
    task = dreamina_video.submit(prompt="dance", model="seedance_2.0_fast_vip",
                                 resolution="720p", duration=5, ratio=None)
    assert task["gen_status"] == "querying"
    assert task["submit_id"].startswith("sid-")
    canvas = task["canvas"]
    assert canvas["project_id"] == "proj-0001" and canvas["web_url"]
    node_call = next(c for c in fake.calls if c[0] == "create_node")
    assert node_call[1] == "video" and node_call[2] == "seedance_2.0_fast_vip"
    assert node_call[3] == "t2v"
    assert node_call[4] == "16:9"  # 缺省画幅 16:9
    assert node_call[6] == 5 and node_call[7] == 1
    sequence = [c[0] for c in fake.calls]
    assert sequence.count("quote") == 1 and sequence.count("confirm") == 1 and sequence.count("run") == 1


def test_submit_first_frame_ratio_inferred(monkeypatch, tmp_path):
    from PIL import Image
    fake = FakeCanvas("x")
    monkeypatch.setattr(dreamina_video, "CanvasRunner", lambda name, **kw: fake)
    portrait = tmp_path / "f.png"
    Image.new("RGB", (360, 640)).save(portrait)  # 竖屏首帧 → 9:16
    task = dreamina_video.submit(prompt="p", model="seedance_2.0_fast_vip",
                                 resolution="720p", duration=5,
                                 first_frame=portrait)
    node_call = next(c for c in fake.calls if c[0] == "create_node")
    assert node_call[3] == "first_last_frame"
    assert node_call[4] == "9:16"
    assert len(node_call[8]) == 1  # 只上传了首帧
    uploads = [c for c in fake.calls if c[0] == "upload"]
    assert len(uploads) == 1


def test_submit_first_and_last_two_refs(monkeypatch, tmp_path):
    from PIL import Image
    fake = FakeCanvas("x")
    monkeypatch.setattr(dreamina_video, "CanvasRunner", lambda name, **kw: fake)
    first = tmp_path / "f.png"; Image.new("RGB", (640, 640)).save(first)
    last = tmp_path / "l.png"; Image.new("RGB", (640, 640)).save(last)
    dreamina_video.submit(prompt="p", model="seedance_2.0_fast_vip",
                          resolution="720p", duration=5,
                          first_frame=first, last_frame=last)
    node_call = next(c for c in fake.calls if c[0] == "create_node")
    assert len(node_call[8]) == 2  # 首帧 + 尾帧


def test_submit_run_rejected(monkeypatch):
    fake = FakeCanvas("x"); fake.run_should_fail = True
    monkeypatch.setattr(dreamina_video, "CanvasRunner", lambda name, **kw: fake)
    with pytest.raises(dreamina_video.DreaminaTaskError):
        dreamina_video.submit(prompt="p", model="seedance_2.0_fast_vip",
                              resolution="720p", duration=5)


# ---------- 轮询 / 收片 ----------


def test_query_mapping_and_download(monkeypatch, tmp_path):
    fake = FakeCanvas("x")
    monkeypatch.setattr(dreamina_image, "CanvasRunner", lambda name, **kw: fake)
    task = dreamina_video.query("sid-1", project_id="proj-0001", download_dir=tmp_path / "dl")
    assert task["gen_status"] == "success"
    assert task["result_json"]["videos"][0]["path"].endswith(".mp4")
    assert pathlib.Path(task["result_json"]["videos"][0]["path"]).is_file()
    paths = dreamina_video.extract_video_paths(task)
    assert len(paths) == 1
    assert dreamina_video.video_file_ext(paths[0]) == "mp4"


def test_query_requires_project_id():
    with pytest.raises(DreaminaParamError):
        dreamina_video.query("sid-1")


def test_wait_for_video_success_and_timeout(monkeypatch):
    fake = FakeCanvas("x")
    monkeypatch.setattr(dreamina_image, "CanvasRunner", lambda name, **kw: fake)
    task = dreamina_video.wait_for_video("sid-1", project_id="proj-0001")
    assert task["gen_status"] == "success"

    class AlwaysPending(FakeCanvas):
        def status(self, submit_id):
            return dict(operationRef=submit_id, state="running", resources=[])

    pending = AlwaysPending("x")
    monkeypatch.setattr(dreamina_image, "CanvasRunner", lambda name, **kw: pending)
    with pytest.raises(TimeoutError) as exc:
        dreamina_video.wait_for_video("sid-1", project_id="proj-0001", timeout_s=0.5,
                                      poll_interval=0.1, timeout_error=TimeoutError)


# ---------- adhoc pipeline 端到端（stub 画布，走真 generate/refresh/finalize） ----------


def test_pipeline_wait_flow_end_to_end():
    result = adhoc_video.generate(_req(), poll_interval=0.01, timeout_s=30.0)
    assert result.status == "ready"
    assert pathlib.Path(result.video_path).read_bytes()[:8] == MP4_MAGIC[:8]
    run_dir = adhoc_video._run_dir(result.run_id)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "ready"
    assert manifest["job"]["id"].startswith("sid-")
    assert manifest["canvas"]["project_id"] == "proj-0001"


def _make_run(run_id: str, **kw):
    adhoc_video._run_dir(run_id).mkdir(parents=True, exist_ok=True)
    return adhoc_video._submit(_req(**kw), run_id, None)


def test_pipeline_web_refresh_flow(monkeypatch):
    _make_run("video_canvas_web", wait=False)
    summary = adhoc_video.refresh("video_canvas_web")
    assert summary["status"] == "ready"


def test_pipeline_legacy_manifest_poll_error(monkeypatch, tmp_path):
    # 旧通路（直发 CLI）留下的 manifest 没有 canvas.project_id：不静默，poll_error 暴露
    manifest = _make_run("video_legacy_canvas", wait=False)
    del manifest["canvas"]
    adhoc_video._write_manifest(adhoc_video._run_dir("video_legacy_canvas"), manifest)
    summary = adhoc_video.refresh("video_legacy_canvas")
    assert "画布" in summary["poll_error"]


def test_pipeline_fail_keeps_reason(monkeypatch):
    fake = FakeCanvas("x")
    fake.run_should_fail = True

    def fake_factory(name, **kw):
        return fake
    monkeypatch.setattr(dreamina_video, "CanvasRunner", fake_factory)
    monkeypatch.setattr(dreamina_image, "CanvasRunner", fake_factory)
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.generate(_req(), poll_interval=0.01, timeout_s=30.0)
    run_dir = None
    from charvar import paths as charvar_paths
    runs = pathlib.Path(charvar_paths.data_dir() / "generated" / "adhoc_video").glob("*/manifest.json")
    manifests = [json.loads(p.read_text()) for p in runs]
    assert any(m["status"] == "failed" for m in manifests)


# ---------- finalize 并发兜底 ----------


def test_finalize_race_guard_reuses_existing_video():
    manifest = _make_run("video_race", wait=False)
    run_dir = adhoc_video._run_dir("video_race")
    original = run_dir / "original"
    original.mkdir(parents=True, exist_ok=True)
    ready = original / "000000.mp4"
    ready.write_bytes(_mp4_bytes())
    result = adhoc_video._finalize_dreamina("video_race", manifest, time.time())
    assert result["status"] == "ready"
    assert result["result"]["video_path"] == str(ready)


def test_finalize_fails_without_canvas():
    manifest = _make_run("video_nocanvas", wait=False)
    del manifest["canvas"]
    result = adhoc_video._finalize_dreamina("video_nocanvas", manifest, time.time())
    assert result["status"] == "failed"


# ---------- CLI 枚举 ----------


def test_model_list_shape():
    models = dreamina_video.model_list()
    ids = {m["id"] for m in models}
    assert "seedance_2.0_fast_vip" in ids and "wan_3.0" in ids
    for m in models:
        assert m["resolutions"] and m["durations"][0] <= m["durations"][1]
        assert m["ratios"] and m["modes"]
