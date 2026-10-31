"""即梦（Dreamina）CLI 客户端：命令拼装 / 输出解析 / 等待与终态（假 CLI，不发真网）。

用 monkeypatch 替换 `charvar.dreamina_image.subprocess.run`，按假想 CLI 行为
回 stdout（含 "download image N URL:" 进度行混排的解析），不发真实请求、
不消耗积分。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from PIL import Image

from charvar import dreamina_image

TASK_SUCCESS_NO_PATH = {
    "submit_id": "sid-1",
    "prompt": "p",
    "gen_status": "success",
    "result_json": {"images": [{"item_id": "1", "width": 1664, "height": 2496}], "videos": []},
}
TASK_QUERYING = {
    "submit_id": "sid-1",
    "gen_status": "querying",
    "result_json": {"images": [], "videos": []},
}


class FakeCli:
    """替身 dreamina CLI：记录每次命令行，按脚本回 stdout / 创建下载文件。"""

    def __init__(self, *, download_root: pathlib.Path, responses: list[dict] | None = None):
        self.calls: list[list[str]] = []
        self.download_root = download_root
        self.responses = responses or []
        self._serial = 0

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        # _run_cli 拼的是 [binary, *args]：子命令在 cmd[1]
        subcommand = cmd[1] if len(cmd) > 1 else ""
        if subcommand == "query_result" and "--download_dir" in cmd:
            directory = pathlib.Path(cmd[cmd.index("--download_dir") + 1])
            directory.mkdir(parents=True, exist_ok=True)
            self._serial += 1
            image = directory / f"{cmd[cmd.index('--submit_id') + 1]}_image_1.png"
            Image.new("RGB", (16, 24), (30, 60, 90)).save(image, format="PNG")
            payload = {
                **TASK_SUCCESS_NO_PATH,
                "result_json": {
                    "images": [{"item_id": "1", "path": str(image), "width": 1664, "height": 2496}],
                    "videos": [],
                },
            }
            # 与真实 CLI 一致：下载动作先打一行进度，JSON 在后面
            stdout = f"download image 1 URL: https://example.com/{self._serial}\n" + json.dumps(
                payload, ensure_ascii=False
            )
            import subprocess

            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if self.responses:
            payload = self.responses.pop(0)
        else:
            payload = TASK_SUCCESS_NO_PATH
        import subprocess

        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    """隔离：数据目录 + 假 CLI 替身 + 固定 sleep（不真等）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    for key in (
        "DREAMINA_CLI",
        "DREAMINA_IMAGE_MODEL",
        "DREAMINA_IMAGE_RESOLUTION",
        "DREAMINA_IMAGE_TIMEOUT",
        "DREAMINA_POLL",
        "DREAMINA_CONCURRENCY",
    ):
        monkeypatch.delenv(key, raising=False)
    # 命令拼装/输出解析与本机是否装了 dreamina 无关：打桩掉解释器解析，
    # 免得没装 CLI 的机器整组用例红（真实解析另有 test_binary_env_override 覆盖）。
    monkeypatch.setattr(dreamina_image, "resolve_binary", lambda: "dreamina-fake")
    fake = FakeCli(download_root=tmp_path)
    monkeypatch.setattr(dreamina_image.subprocess, "run", fake)
    monkeypatch.setattr(dreamina_image.time, "sleep", lambda _s: None)
    return fake


# ---------- 参数拼装 ----------

def test_t2i_submit_command(fake_env):
    dreamina_image.generate(prompt="a cat", download_dir=fake_env.download_root / "dl", resolution="2k")
    submit = fake_env.calls[0][1:]  # [0] 是 binary[1:]  # [0] 是 binary
    assert submit[0] == "text2image"
    assert submit[submit.index("--prompt") + 1] == "a cat"
    assert submit[submit.index("--resolution_type") + 1] == "2k"
    assert submit[submit.index("--poll") + 1].isdigit()
    assert "--ratio" not in submit           # ratio 为空时不传（CLI 默认 16:9）
    assert "--model_version" not in submit    # model 为空时不传（CLI 默认 5.0）
    assert "--images" not in submit


def test_i2i_submit_carries_refs_and_ratio(fake_env, tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(ref, format="PNG")
    dreamina_image.generate(
        prompt="keep <image1>", download_dir=fake_env.download_root / "dl",
        resolution="4k", model="5.0Pro", ratio="2:3", reference_paths=[ref],
    )
    submit = fake_env.calls[0][1:]  # [0] 是 binary[1:]  # [0] 是 binary
    assert submit[0] == "image2image"
    assert submit[submit.index("--model_version") + 1] == "5.0Pro"
    assert submit[submit.index("--ratio") + 1] == "2:3"
    assert submit[submit.index("--resolution_type") + 1] == "4k"
    assert submit.count("--images") >= 1


def test_env_defaults_fill_in(fake_env, monkeypatch):
    monkeypatch.setenv("DREAMINA_IMAGE_MODEL", "4.7")
    monkeypatch.setenv("DREAMINA_IMAGE_RESOLUTION", "4K")
    dreamina_image.generate(prompt="p", download_dir=fake_env.download_root / "dl")
    submit = fake_env.calls[0][1:]  # [0] 是 binary[1:]  # [0] 是 binary
    assert submit[submit.index("--model_version") + 1] == "4.7"
    assert submit[submit.index("--resolution_type") + 1] == "4k"  # 归一小写


def test_default_resolution_is_2k(fake_env):
    dreamina_image.generate(prompt="p", download_dir=fake_env.download_root / "dl")
    submit = fake_env.calls[0][1:]  # [0] 是 binary
    assert submit[submit.index("--resolution_type") + 1] == "2k"


# ---------- 等待与终态 ----------

def test_success_within_poll_downloads_once(fake_env):
    result = dreamina_image.generate(
        prompt="p", download_dir=fake_env.download_root / "dl", resolution="2k",
    )
    # submit（poll 内 success）→ 直接下载一次
    assert fake_env.calls[0][1] == "text2image"
    assert fake_env.calls[1][1] == "query_result"
    assert len(fake_env.calls) == 2
    assert result.submit_id == "sid-1"
    assert result.gen_status == "success"
    assert len(result.images) == 1 and result.images[0].is_file()
    assert result.width == 1664 and result.height == 2496


def test_querying_then_success(fake_env):
    fake_env.responses = [TASK_QUERYING, TASK_SUCCESS_NO_PATH]
    result = dreamina_image.generate(
        prompt="p", download_dir=fake_env.download_root / "dl", timeout_s=60, poll_s=0,
    )
    kinds = [c[1] for c in fake_env.calls]
    # submit(querying) → query 拿到 success → 下载查询，共 2 次 query_result
    assert kinds == ["text2image", "query_result", "query_result"]
    assert result.images and result.images[0].is_file()


def test_fail_status_raises_with_reason(fake_env):
    fake_env.responses = [{
        **TASK_QUERYING,
        "gen_status": "fail",
        "fail_reason": "prompt len is larger than limited len",
    }]
    with pytest.raises(dreamina_image.DreaminaTaskError) as exc:
        dreamina_image.generate(
            prompt="p", download_dir=fake_env.download_root / "dl", timeout_s=30, poll_s=0,
        )
    assert "prompt len" in str(exc.value)
    assert exc.value.submit_id == "sid-1"


def test_timeout_carries_submit_id(fake_env):
    fake_env.responses = [TASK_QUERYING]
    with pytest.raises(dreamina_image.DreaminaTimeoutError) as exc:
        dreamina_image.generate(
            prompt="p", download_dir=fake_env.download_root / "dl", timeout_s=0, poll_s=0,
        )
    assert exc.value.submit_id == "sid-1"


# ---------- 提交前校验（防白扣积分） ----------

def test_prompt_limit(fake_env):
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.validate_prompt("x" * 1600, has_reference=False)
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.validate_prompt("x" * 2001, has_reference=True)
    # 上限内不报错
    dreamina_image.validate_prompt("x" * 1200, has_reference=False)


def test_prompt_too_long_never_submits(fake_env):
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.generate(prompt="x" * 2500, download_dir=fake_env.download_root / "dl")
    assert fake_env.calls == []  # 没白提交一次


def test_bad_ratio_rejected(fake_env):
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.validate_ratio("2:5")


def test_model_resolution_mismatch_rejected(fake_env):
    # 5.0 只收 2k/4k
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.validate_model_resolution("5.0", "1k", has_reference=False)
    # 3.0/3.1 不支持 image2image
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.validate_model_resolution("3.0", "2k", has_reference=True)
    # 5.0Pro 的 1.5k 放行；表外模型不拦
    assert dreamina_image.validate_model_resolution("5.0Pro", "1.5k", has_reference=False) == "1.5k"
    assert dreamina_image.validate_model_resolution("9.9", "7k", has_reference=False) == "7k"
    # 请求带大写档位会被归一：generate 里 resolve_resolution 先小写化
    dreamina_image.generate(
        prompt="p", download_dir=fake_env.download_root / "dl", resolution="4K", model="5.0",
    )
    submit = fake_env.calls[0][1:]  # [0] 是 binary[1:]  # [0] 是 binary
    assert submit[submit.index("--resolution_type") + 1] == "4k"


# ---------- 输出解析 ----------

def test_parse_skips_progress_lines():
    text = (
        "download image 1 URL: https://p.example.com/x?signature=1\n"
        "download image 2 URL: https://p.example.com/y?signature=2\n"
        + json.dumps({"submit_id": "sid-2", "gen_status": "success"})
    )
    assert dreamina_image.parse_task_output(text)["submit_id"] == "sid-2"


def test_parse_accepts_list_task_format():
    text = json.dumps([{"submit_id": "sid-3", "gen_status": "success"}])
    assert dreamina_image.parse_task_output(text)["submit_id"] == "sid-3"


def test_parse_without_json_raises():
    with pytest.raises(dreamina_image.DreaminaCliError):
        dreamina_image.parse_task_output("download image 1 URL: ok\nno json at all")


# ---------- CLI 缺失 / 鉴权 ----------

def test_binary_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "dreamina-fake"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("DREAMINA_CLI", str(fake))
    assert dreamina_image.resolve_binary() == str(fake)


def test_nonzero_exit_maps_auth_error(fake_env, monkeypatch):
    import subprocess

    def fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="错误：您尚未登录，请先登录")

    monkeypatch.setattr(dreamina_image.subprocess, "run", fail)
    with pytest.raises(dreamina_image.DreaminaAuthError) as exc:
        dreamina_image.query("sid-1")
    assert "dreamina login" in str(exc.value)


def test_nonzero_exit_generic_error(fake_env, monkeypatch):
    import subprocess

    def fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom: something else")

    monkeypatch.setattr(dreamina_image.subprocess, "run", fail)
    with pytest.raises(dreamina_image.DreaminaCliError):
        dreamina_image.query("sid-1")


def test_missing_reference_rejected(fake_env, tmp_path):
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.generate(
            prompt="p", download_dir=fake_env.download_root / "dl",
            reference_paths=[tmp_path / "nope.png"],
        )
    assert fake_env.calls == []