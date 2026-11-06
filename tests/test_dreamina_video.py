"""即梦（Dreamina）视频通路：客户端命令拼装/枚举校验/收片解析 + adhoc_video 管线分派。

全部打桩（monkeypatch ``dreamina_image.subprocess.run`` —— dreamina_video 与生图
共用同一个子进程出口），不发真网、不扣积分。真实视频任务历史上一条都没有
（首个真任务时若上游解析形状与预期不符，先看这份测试的假设再改代码）。
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest
from PIL import Image

from charvar import adhoc_video, dreamina_image, dreamina_video, openrouter_video, storage

TASK_QUERYING = {
    "submit_id": "v-submit-1",
    "gen_status": "querying",
    "result_json": {"images": [], "videos": []},
}
TASK_SUCCESS = {
    "submit_id": "v-submit-1",
    "gen_status": "success",
    "result_json": {"images": [], "videos": [{"item_id": "v1", "width": 1280, "height": 720}]},
}
# 上游 fail_reason 的首用合规确认话式
FAIL_COMPLIANCE = {
    "submit_id": "v-submit-2",
    "gen_status": "fail",
    "fail_reason": "AigcComplianceConfirmationRequired",
    "result_json": {"images": [], "videos": []},
}


def _mp4_bytes():
    return b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2\x00" + b"\x00" * 32


def _png_file(path, size=(64, 48)):
    """真 PNG（_prepare_images 会过 PIL）：不是只写字节头的假货。"""
    Image.new("RGB", size, (10, 20, 30)).save(path, format="PNG")
    return path


class FakeVideoCli:
    """替身 dreamina CLI：记录命令行；下载调用按 ``<submit_id>_video_1.mp4`` 落文件。"""

    def __init__(self, *, download_root: pathlib.Path, responses: list[dict] | None = None,
                 download_responses: list[dict] | None = None):
        self.calls: list[list[str]] = []
        self.download_root = download_root
        self.responses = responses or []
        self.download_responses = download_responses or []
        self.simulate_race = False  # 开着时：下载后立刻删文件，模拟并发收片清目录

    def __call__(self, cmd, **kwargs):
        import os
        import subprocess

        # patch 的是全局 subprocess.run：ffprobe 之类借道的调用别当 dreamina 命令记
        if "dreamina" not in os.path.basename(str(cmd[0])):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not-fake-cli")
        self.calls.append(cmd)
        subcommand = cmd[1] if len(cmd) > 1 else ""
        if subcommand == "query_result" and "--download_dir" in cmd:
            if self.download_responses:
                payload = self.download_responses.pop(0)
                if payload["gen_status"] != "success":
                    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")
            directory = pathlib.Path(cmd[cmd.index("--download_dir") + 1])
            directory.mkdir(parents=True, exist_ok=True)
            sid = cmd[cmd.index("--submit_id") + 1]
            video = directory / f"{sid}_video_1.mp4"
            video.write_bytes(_mp4_bytes())
            if self.simulate_race:
                video.unlink()  # 另一路收片后清掉了 download 目录
            payload = {
                **TASK_SUCCESS,
                "result_json": {
                    "images": [],
                    "videos": [{"item_id": "v1", "path": str(video), "width": 1280, "height": 720}],
                },
            }
            stdout = f"download video 1 URL: https://example.com/v/{self._n()}\n" + json.dumps(
                payload, ensure_ascii=False
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        payload = self.responses.pop(0) if self.responses else dict(TASK_QUERYING)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")

    def _n(self):
        return str(len(self.calls))


class _NoSleep:
    def __call__(self, _seconds):
        return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离数据目录 + 假 CLI + 不真等（dreamina_video.wait 的 sleep 也掐掉）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    for key in ("DREAMINA_CLI", "DREAMINA_VIDEO_MODEL", "DREAMINA_IMAGE_MODEL", "DREAMINA_POLL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(dreamina_image, "resolve_binary", lambda: "dreamina-fake")
    fake = FakeVideoCli(download_root=tmp_path)
    monkeypatch.setattr(dreamina_image.subprocess, "run", fake)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    # 海报用替身（ffmpeg 在假 mp4 上会失败；海报不是本组用例的关注点）
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


# ---------- 命令拼装 ----------


def test_t2v_submit_command(env):
    dreamina_video.submit(prompt="a cat running", model="seedance2.0fast_vip",
                          resolution="720p", duration=5, poll_s=0)
    args = env.calls[0][1:]
    assert args[0] == "text2video"
    assert args[args.index("--model_version") + 1] == "seedance2.0fast_vip"
    assert args[args.index("--video_resolution") + 1] == "720p"
    assert args[args.index("--duration") + 1] == "5"
    assert args[args.index("--poll") + 1] == "0"
    assert "--ratio" not in args and "--seed" not in args and "--image" not in args


def test_i2v_submit_omits_ratio_when_empty(env, tmp_path):
    # 管线里首帧要先过 reference_images（PIL 真 PNG 才行）；此处直接过 is_file 校验
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"0000")
    dreamina_video.submit(prompt="push in", model="seedance2.0fast_vip", resolution="720p",
                          duration=5, ratio="", first_frame=frame, poll_s=0)
    args = env.calls[0][1:]
    assert args[0] == "image2video"
    assert args[args.index("--image") + 1] == str(frame)
    assert "--ratio" not in args


def test_i2v_submit_carries_explicit_ratio(env, tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"0000")
    dreamina_video.submit(prompt="push", model="seedance2.0fast", resolution="720p",
                          duration=5, ratio="21:9", first_frame=frame, poll_s=0)
    args = env.calls[0][1:]
    assert args[0] == "image2video"
    assert args[args.index("--ratio") + 1] == "21:9"


def test_refs_dispatch_multimodal(env, tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"0000")
    dreamina_video.submit(prompt="keep <image1> pose", model="seedance2.0fast_vip",
                          resolution="720p", duration=5, ratio="9:16",
                          reference_paths=[ref], poll_s=0)
    args = env.calls[0][1:]
    assert args[0] == "multimodal2video"
    assert args.count("--image") == 1
    assert "--first" not in args and "--last" not in args


def test_vref_only_dispatch_multimodal_video_flag(env, tmp_path):
    vref = tmp_path / "ref.mp4"
    vref.write_bytes(_mp4_bytes())
    dreamina_video.submit(prompt="mimic this move", model="seedance2.0fast_vip",
                          resolution="720p", duration=5, ratio="9:16",
                          reference_video_paths=[vref], poll_s=0)
    args = env.calls[0][1:]
    assert args[0] == "multimodal2video"
    assert args.count("--video") == 1
    assert "--image" not in args


# ---------- 提交前拦截（上游严格校验，白提交就是白扣积分） ----------


def test_first_frame_with_refs_rejected(env, tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"0000")
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"0000")
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.submit(prompt="p", model="seedance2.0fast_vip", resolution="720p",
                              duration=5, ratio="9:16", first_frame=frame,
                              reference_paths=[ref], poll_s=0)
    assert env.calls == []


def test_last_frame_rejected(env, tmp_path):
    first = tmp_path / "first.png"
    first.write_bytes(b"0000")
    last = tmp_path / "last.png"
    last.write_bytes(b"0000")
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.submit(prompt="p", model="seedance2.0fast_vip", resolution="720p",
                              duration=5, ratio="9:16",
                              first_frame=first, last_frame=last, poll_s=0)
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.submit(prompt="p", model="seedance2.0fast_vip", resolution="720p",
                              duration=5, ratio="9:16", last_frame=last, poll_s=0)
    assert env.calls == []


def test_ratio_six_tiers_only(env):
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.validate_ratio("2:3")
    assert dreamina_video.validate_ratio("3:4") == "3:4"
    assert dreamina_video.validate_ratio("") == ""


def test_model_resolution_duration_pairing(env):
    # fast_vip 恒 720p
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.validate_video("seedance2.0fast_vip", "1080p", 5, "text2video")
    # 2.0_vip 是唯一 1080p/4k 档
    assert dreamina_video.validate_video("seedance2.0_vip", "1080p", 5, "text2video") == "1080p"
    # 时长越界：fast_vip 4-15
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.validate_video("seedance2.0fast_vip", "720p", 3, "text2video")
    # 老模型只支持 image2video
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.validate_video("seedance1.0fast", "720p", 5, "text2video")
    assert dreamina_video.validate_video("seedance1.0fast", "720p", 5, "image2video") == "720p"
    # 表外模型放行（CLI 升级新增不拦）
    assert dreamina_video.validate_video("seedance9.9", "7k", 5, "text2video") == "7k"
    # resolution 必填
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.validate_video("seedance2.0fast_vip", "", 5, "text2video")


# ---------- 默认链 ----------


def test_resolve_model_chain(env, monkeypatch):
    monkeypatch.setenv("DREAMINA_VIDEO_MODEL", "seedance2.0_vip")
    assert dreamina_video.resolve_model(None) == "seedance2.0_vip"
    assert dreamina_video.resolve_model("seedance2.0mini") == "seedance2.0mini"
    monkeypatch.delenv("DREAMINA_VIDEO_MODEL")
    assert dreamina_video.resolve_model(None) == "seedance2.0fast_vip"


def test_resolve_duration_and_resolution_defaults():
    assert dreamina_video.resolve_duration(None) == 5
    assert dreamina_video.resolve_resolution("1080P") == "1080p"  # 大写归一
    assert dreamina_video.resolve_resolution("") == "720p"


def test_resolve_timeout_default(env, monkeypatch):
    assert dreamina_video.resolve_timeout(None) == 900.0
    monkeypatch.setenv("DREAMINA_VIDEO_TIMEOUT", "120")
    assert dreamina_video.resolve_timeout(None) == 120.0


# ---------- 等待与终态 ----------


def test_wait_for_video_polls_to_success(env):
    env.responses = [dict(TASK_QUERYING), dict(TASK_QUERYING), dict(TASK_SUCCESS)]
    dreamina_video.submit(prompt="p", model="seedance2.0fast_vip", resolution="720p",
                          duration=5, poll_s=0)
    submit_id = dreamina_video.submit_id_of(dreamina_video.parse_task_output(
        json.dumps(TASK_QUERYING, ensure_ascii=False)))
    # submit 已走了一次（querying），wait 侧再补两次轮询
    env.responses = [dict(TASK_QUERYING), dict(TASK_SUCCESS)]
    task = dreamina_video.wait_for_video(submit_id, poll_interval=1, timeout_s=60,
                                         sleep=lambda _s: None)
    assert task["gen_status"] == "success"
    kinds = [c[1] for c in env.calls]
    assert kinds == ["text2video", "query_result", "query_result"]  # 提交 + 每间隔轮询一次


def test_wait_for_video_timeout_uses_custom_error(env):
    env.responses = [dict(TASK_QUERYING)]
    with pytest.raises(adhoc_video.AdhocVideoTimeout) as exc:
        dreamina_video.wait_for_video("sid-x", poll_interval=1, timeout_s=0,
                                      sleep=lambda _s: None,
                                      timeout_error=adhoc_video.AdhocVideoTimeout)
    assert "sid-x" in str(exc.value)


def test_submit_reports_compliance_fail(env):
    env.responses = [dict(FAIL_COMPLIANCE)]
    with pytest.raises(dreamina_video.DreaminaTaskError) as exc:
        dreamina_video.submit(prompt="p", model="seedance2.0fast_vip", resolution="720p",
                              duration=5, poll_s=0)
    assert "AigcComplianceConfirmationRequired" in str(exc.value)
    assert "jimeng.jianying.com" in str(exc.value)


# ---------- 收片解析 ----------


def test_extract_video_paths_prefers_videos(env):
    sid = "abc"
    download_dir = pathlib.Path(env.download_root) / "probe"
    download_dir.mkdir(parents=True)
    video = download_dir / f"{sid}_video_1.mp4"
    video.write_bytes(_mp4_bytes())
    payload = {"result_json": {"images": [{"item_id": "i1", "path": str(download_dir / "x.png")}],
                               "videos": [{"item_id": "v1", "path": str(video)}]}}
    paths = dreamina_video.extract_video_paths(payload)
    assert paths == [video]


def test_extract_video_paths_empty_raises(env):
    with pytest.raises(dreamina_video.DreaminaCliError):
        dreamina_video.extract_video_paths({"result_json": {"images": [], "videos": []}})


def test_video_file_ext_sniffs_magic(env):
    probe = pathlib.Path(env.download_root) / "probe2.mp4"
    probe.write_bytes(_mp4_bytes())
    assert dreamina_video.video_file_ext(probe) == "mp4"
    probe.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 20)
    assert dreamina_video.video_file_ext(probe) == "webm"


# ---------- adhoc_video 管线（dreamina 分派） ----------


def test_pipeline_wait_flow_end_to_end(env):
    # submit=1st（querying）、wait 轮询第 1 发就 success → 不进 sleep 分支
    env.responses = [dict(TASK_QUERYING), dict(TASK_SUCCESS)]
    result = adhoc_video.generate(_req())
    assert result.status == "ready"
    assert result.provider == "dreamina"
    assert result.job_id == "v-submit-1"
    assert result.cost is None
    assert result.model == "seedance2.0fast_vip"  # 请求没给 → 设置页默认链（env 都为空）
    assert result.duration == 5 and result.resolution == "720p"
    vd = pathlib.Path(result.video_path)
    assert vd.exists() and vd.parent.name == "original" and vd.suffix == ".mp4"
    manifest = adhoc_video.read_manifest(result.run_id)
    assert manifest["job"]["id"] == "v-submit-1"
    assert manifest["job"]["model"] == "seedance2.0fast_vip"
    assert manifest["job"]["resolution"] == "720p"
    # 子进程调用顺序：text2video 提交 → query 轮询 → query+下载
    kinds = [c[1] for c in env.calls]
    assert kinds[0] == "text2video"
    assert kinds.count("query_result") >= 2


def test_pipeline_web_refresh_flow(env):
    env.responses = [dict(TASK_QUERYING)]  # submit
    result = adhoc_video.generate(_req(wait=False))
    assert result.status == "pending" and result.provider == "dreamina"
    manifest = adhoc_video.read_manifest(result.run_id)
    assert manifest["status"] == "in_progress"
    # 第 1 次 refresh：仍是 querying → in_progress
    env.responses = [dict(TASK_QUERYING)]
    summary = adhoc_video.refresh(result.run_id)
    assert summary["status"] == "in_progress"
    assert summary["job_status"] == "querying"
    # 第 2 次 refresh：success → 下载收片
    env.responses = [dict(TASK_SUCCESS)]
    summary = adhoc_video.refresh(result.run_id)
    assert summary["status"] == "ready"
    assert summary["cost"] is None
    # 终态幂等：第 3 次 refresh 不再打 CLI（calls 数不变）
    calls_after_ready = len(env.calls)
    adhoc_video.refresh(result.run_id)
    assert len(env.calls) == calls_after_ready


def test_pipeline_fail_persists_reason(env):
    # submit=querying → wait 轮询到 fail → 收片查询也 fail（URL 还没过期也是 fail 终态）
    env.responses = [dict(TASK_QUERYING), dict(FAIL_COMPLIANCE)]
    env.download_responses = [dict(FAIL_COMPLIANCE)]
    with pytest.raises(adhoc_video.AdhocVideoError) as exc:
        adhoc_video.generate(_req())
    run_ids = [d for d in (adhoc_video.video_root()).iterdir() if d.is_dir()]
    assert run_ids  # 失败也留痕
    m = adhoc_video.read_manifest(run_ids[0].name)
    assert m["status"] == "failed"
    assert "AigcComplianceConfirmationRequired" in m["error"]
    assert "jimeng.jianying.com" in str(m["error"])


def test_pipeline_poll_error_keeps_state(env, monkeypatch):
    env.responses = [dict(TASK_QUERYING)]  # submit
    run_id = adhoc_video.generate(_req(wait=False)).run_id
    # poll 阶段 CLI 报错（如网络抖动）→ poll_error 不改状态
    def broken(cmd, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="boom: net broken")
    monkeypatch.setattr(dreamina_image.subprocess, "run", broken)
    summary = adhoc_video.refresh(run_id)
    assert summary["status"] == "in_progress"
    assert "net broken" in summary["poll_error"]
    manifest = adhoc_video.read_manifest(run_id)
    assert manifest["status"] == "in_progress"


def test_pipeline_ref_and_vref_full_combo(env):
    ref = _png_file(pathlib.Path(env.download_root) / "ref.png")
    vref = pathlib.Path(env.download_root) / "refv.mp4"
    vref.write_bytes(_mp4_bytes())
    env.responses = [dict(TASK_QUERYING)]
    result = adhoc_video.generate(_req(reference_paths=[ref],
                                       reference_video_paths=[vref], wait=False))
    args = env.calls[0][1:]
    assert args[0] == "multimodal2video"
    assert args.count("--image") == 1 and args.count("--video") == 1
    # web 路径：pending 返回，job_id 是即梦 submit_id
    assert result.job_id == "v-submit-1"
    assert result.provider == "dreamina"


def test_pipeline_first_frame_image2video(env):
    frame = _png_file(pathlib.Path(env.download_root) / "frame.png")
    env.responses = [dict(TASK_QUERYING)]
    adhoc_video.generate(_req(first_frame=frame, aspect_ratio="", wait=False))
    args = env.calls[0][1:]
    assert args[0] == "image2video"
    assert "--ratio" not in args  # 画幅空 = 从首帧推断


def test_pipeline_last_frame_rejected_no_run(env):
    first = _png_file(pathlib.Path(env.download_root) / "f0.png")
    last = _png_file(pathlib.Path(env.download_root) / "fl.png")
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.generate(_req(first_frame=first, last_frame=last))
    # 提交前拦截（没打 CLI），但 generate 的失败留痕仍在：失败 manifest + 提示尾帧
    runs = list(adhoc_video.video_root().iterdir())
    assert len(runs) == 1
    m = adhoc_video.read_manifest(runs[0].name)
    assert m["status"] == "failed" and "尾帧" in str(m["error"])
    assert all(c[1] != "image2video" for c in env.calls)


def test_openrouter_backend_untouched(env, monkeypatch):
    """OpenRouter 分支保持原行为（provider/落盘/作业 id 语义不回归）。"""
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")  # 缺 key 检查在提交前（conftest 已清真 .env）
    catalog = {
        "data": [{
            "id": "heygen/heygen-video-1", "name": "HeyGen Video",
            "supported_durations": [5, 6, 10], "supported_resolutions": ["480p", "768p", "2K"],
            "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
            "supported_frame_images": ["first_frame"], "seed": True,
            "pricing_skus": {"duration_seconds_480p": "0.02"},
        }]
    }
    cache = pathlib.Path(env.download_root) / "openrouter" / "video-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(catalog), encoding="utf-8")
    calls = {"submit": [], "download": []}

    def fake_submit(body, *, api_key=None, base_url=None, timeout_s=None):
        calls["submit"].append(dict(body))
        return openrouter_video.VideoJob("job-or-1", "pending")

    def fake_wait(job_id, **kw):
        return {"status": "completed", "unsigned_urls": ["https://x/0"], "usage": {"cost": 0.02}}

    def fake_poll(job_id, **kw):
        return {"status": "in_progress", "id": job_id}

    def fake_download(job_id, *, url=None, api_key=None, base_url=None, timeout_s=None):
        calls["download"].append(job_id)
        return _mp4_bytes()

    monkeypatch.setattr(openrouter_video, "submit", fake_submit)
    monkeypatch.setattr(openrouter_video, "wait_for_job", fake_wait)
    monkeypatch.setattr(openrouter_video, "poll", fake_poll)
    monkeypatch.setattr(openrouter_video, "download", fake_download)
    def fake_poster_or(video_path, run_dir):
        target = pathlib.Path(run_dir) / "thumbs" / "000000.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8)
        return target
    monkeypatch.setattr(adhoc_video, "_make_poster", fake_poster_or)
    result = adhoc_video.generate(_req(backend="openrouter",
                                       model="heygen/heygen-video-1",
                                       resolution="480p"))
    assert result.provider == "openrouter" and result.cost == pytest.approx(0.02)
    assert calls["submit"] and calls["download"] == ["job-or-1"]
    # dreamina 侧完全没被碰
    assert all(c[1] != "text2video" for c in env.calls)

def test_finalize_race_guard_reuses_existing_video(env):
    """并发收片竞态：另一路（CLI/web 同盯一个作业）把成片收进 original/ 并清掉
    download 目录；本路的 query 结果里产物已不在 —— 兜底复用 original/ 照样 ready。"""
    env.responses = [dict(TASK_QUERYING)]  # submit
    result = adhoc_video.generate(_req(wait=False))
    run_id = result.run_id
    run_dir = adhoc_video.run_dir(run_id)
    # 另一路已把成片收进 original/
    destination = run_dir / "original" / "000000.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_mp4_bytes())
    # download 分支：写文件 → 又被并发者清掉 → 回的 payload 里 path 已不存在
    env.download_responses = []
    env.simulate_race = True
    manifest = adhoc_video.read_manifest(run_id)
    manifest["status"] = "in_progress"
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    env.responses = [dict(TASK_SUCCESS)]  # refresh 轮询到终态
    summary = adhoc_video.refresh(run_id)
    assert summary["status"] == "ready"
    assert destination.is_file()


# ---------- 提交前配对规则（CLI -h 明文的 2.5/legacy/媒体时长） ----------


def test_25_i2v_rejects_explicit_ratio(env, tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"0000")
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.submit(prompt="p", model="seedance2.5", resolution="720p", duration=5,
                              ratio="9:16", first_frame=frame, poll_s=0)
    assert env.calls == []
    # 空画幅（= 跟首帧走）放行
    dreamina_video.submit(prompt="p", model="seedance2.5", resolution="720p", duration=5,
                          ratio="", first_frame=frame, poll_s=0)
    args = env.calls[0][1:]
    assert args[0] == "image2video" and "--ratio" not in args


def test_legacy_ratio_must_match_first_frame(env, tmp_path):
    wide = _png_file(tmp_path / "wide.png", (160, 90))  # 16:9
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.submit(prompt="p", model="seedance1.0fast", resolution="720p", duration=5,
                              ratio="9:16", first_frame=wide, poll_s=0)
    assert env.calls == []
    # 一致的 16:9 → 过（2.0 系不拦，legacy 才拦）
    dreamina_video.submit(prompt="p", model="seedance1.0fast", resolution="720p", duration=10,
                          ratio="16:9", first_frame=wide, poll_s=0)
    args = env.calls[0][1:]
    assert args[0] == "image2video" and "--ratio" in args


def test_multimodal_vref_duration_precheck(env, tmp_path, monkeypatch):
    vref = tmp_path / "v.mp4"
    vref.write_bytes(_mp4_bytes())
    base = dict(prompt="p", model="seedance2.0fast_vip", resolution="720p", duration=5,
                ratio="9:16", poll_s=0)
    monkeypatch.setattr(dreamina_video, "probe_media_seconds", lambda _p: 20.0)
    with pytest.raises(dreamina_video.DreaminaParamError):
        dreamina_video.submit(reference_video_paths=[vref], **base)
    assert env.calls == []
    monkeypatch.setattr(dreamina_video, "probe_media_seconds", lambda _p: 10.0)
    dreamina_video.submit(reference_video_paths=[vref], **base)
    assert env.calls[0][1:] and env.calls[-1][1] == "multimodal2video"
    # 探测不到（None）就不拦，交上游
    env.calls.clear()
    monkeypatch.setattr(dreamina_video, "probe_media_seconds", lambda _p: None)
    dreamina_video.submit(reference_video_paths=[vref], **base)
    assert len(env.calls) == 1


def test_mark_if_open_never_clobbers_ready(env, tmp_path, monkeypatch):
    """终态写入保护：盘上已是 ready/failed 时不要用旧 dict 覆写。"""
    run_dir = tmp_path / "m"
    run_dir.mkdir()
    disk = {"status": "ready", "result": {"run_id": "r"}}
    (run_dir / "manifest.json").write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
    got = adhoc_video._mark_if_open(run_dir, {"status": "in_progress"},
                                    status="failed", error="本路失败")
    assert got["status"] == "ready" and got["result"]["run_id"] == "r"
    # 盘上是中间态 → 可写
    (run_dir / "manifest.json").write_text(json.dumps({"status": "in_progress"}, ensure_ascii=False), encoding="utf-8")
    got = adhoc_video._mark_if_open(run_dir, {"status": "in_progress"},
                                    status="failed", error="本路失败")
    assert got["status"] == "failed"


def test_finalize_uses_unique_download_dir(env):
    """并发收片的两次下载不能共享同一目录（CLI 按确定性文件名写，会互相截断）。"""
    env.responses = [dict(TASK_QUERYING), dict(TASK_SUCCESS)]
    result = adhoc_video.generate(_req())
    assert result.status == "ready"
    dl_dirs = [d for d in adhoc_video.run_dir(result.run_id).iterdir()
               if d.name.startswith("download")]
    assert dl_dirs == []  # 用完即清
