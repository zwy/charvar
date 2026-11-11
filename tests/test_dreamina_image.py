"""即梦生图（画布通路）测试：流程与校验打桩，不调真 CLI。

分层：``charvar.dreamina_canvas`` 的子进程层在 ``test_dreamina_canvas.py``
单测；本文件用假 ``CanvasRunner`` 打桩画布编排，锁「generate 的流程语义」
——mode 选择、参考图上限、默认链、轮询到终态、产物落盘、异常判型。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from charvar import dreamina_canvas, dreamina_image


class FakeRunner:
    """替身 CanvasRunner：记录调用序列，可配置运行终态。"""

    def __init__(self, name: str, *, project_id: str | None = None) -> None:
        self.name = name
        self.created_with = name
        self.project_id = project_id or "proj-0000"
        self.web_url = "https://jimeng.test/canvas/proj-0000"
        self.calls: list[tuple[str, ...]] = []
        self.terminal_states: list[str] = ["running", "succeeded"]
        self.node_state = "succeeded"
        self.rid_seq = 0

    def ensure_canvas(self) -> str:
        self.calls.append(("ensure_canvas",))
        return self.project_id

    def upload(self, path: Path) -> str:
        self.rid_seq += 1
        self.calls.append(("upload", str(path)))
        return f"res-{self.rid_seq:04d}"

    def resource_get(self, resource_id: str) -> dict[str, Any]:
        self.calls.append(("resource_get", resource_id))
        return dict(resourceId=resource_id, status="success")

    def wait_resource(self, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("wait_resource", resource_id))
        return dict(resourceId=resource_id, status="success")

    def create_node(self, *, kind, model, mode, prompt, title, ratio, resolution, ref_ids, duration=None, count=None):
        self.calls.append(("create_node", kind, model, mode, resolution, ratio, tuple(ref_ids)))
        self.node_id = f"node_{len(self.calls)}"
        return self.node_id

    def quote(self, node_ids: list[str]) -> dict[str, Any]:
        self.calls.append(("quote", tuple(node_ids)))
        return dict(totalMaxCredits=2, confirmationRequired=False)

    def confirm(self, node_ids: list[str], *, credit_ceiling: int) -> str:
        self.calls.append(("confirm", tuple(node_ids), credit_ceiling))
        assert credit_ceiling >= 2
        return "tok-1"

    def run(self, node_ids: list[str], submit_ids: list[str], credit_token: str) -> list[str]:
        self.calls.append(("run", tuple(node_ids), tuple(submit_ids), credit_token))
        return list(submit_ids)

    def create_and_run(self, **kwargs: Any):
        assert kwargs.get("submit_id") is None
        node_id = self.create_node(
            kind=kwargs["kind"], model=kwargs["model"], mode=kwargs["mode"],
            prompt=kwargs["prompt"], title=kwargs["title"], ratio=kwargs["ratio"],
            resolution=kwargs["resolution"], ref_ids=list(kwargs["ref_ids"]),
            duration=kwargs.get("duration"), count=kwargs.get("count"),
        )
        self.quote([node_id])
        token = self.confirm([node_id], credit_ceiling=3)
        submit_id = "sid-1"
        self.run([node_id], [submit_id], token)
        return node_id, submit_id

    def status(self, submit_id: str) -> dict[str, Any]:
        self.calls.append(("status", submit_id))
        state = self.terminal_states.pop(0) if len(self.terminal_states) > 1 or self.terminal_states[0] != "running" else self.terminal_states[0]
        if len(self.terminal_states) == 1 and self.terminal_states[0] == "running" and state == "running":
            self.terminal_states[0] = "running"  # 恒 running（超时用例改写）
        return dict(operationRef=submit_id, state=state,
                    resources=[dict(resourceId="out-0001", state=state)])

    def node_outputs(self, node_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        self.calls.append(("node_outputs", tuple(node_ids)))
        return {self.node_id: [dict(
            type="image", status="success", resourceId="out-0001", submitId="sid-1",
            image=dict(width=2048, height=2048, format="jpeg"),
        )]}

    def download(self, resource_id: str, dest_dir: Path) -> dict[str, Any]:
        self.calls.append(("download", resource_id, str(dest_dir)))
        dest = Path(dest_dir) / f"dreamina-{resource_id}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG fake")
        return dict(resourceId=resource_id, path=str(dest), size=10)


@pytest.fixture(autouse=True)
def clean_dreamina_env(monkeypatch):
    for key in ("DREAMINA_IMAGE_MODEL", "DREAMINA_IMAGE_RESOLUTION", "DREAMINA_IMAGE_TIMEOUT", "DREAMINA_POLL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_runner(monkeypatch):
    runner = FakeRunner("charvar-image-test")
    monkeypatch.setattr(dreamina_image, "CanvasRunner", lambda name, **kw: runner)
    monkeypatch.setattr(dreamina_image.time, "sleep", lambda _s: None)
    return runner


def test_generate_happy_path(tmp_path, fake_runner):
    ref1 = tmp_path / "ref1.png"; ref1.write_bytes(b"x")
    ref2 = tmp_path / "ref2.png"; ref2.write_bytes(b"x")
    result = dreamina_image.generate(
        prompt="a red car", download_dir=tmp_path / "dl", ratio="1:1",
        reference_paths=[ref1, ref2],
    )
    assert result.gen_status == "success"
    assert result.submit_id == "sid-1"
    assert result.model == "seedream_4.7"
    assert result.resolution == "2K"
    assert result.width == 2048 and result.height == 2048
    assert result.project_id == "proj-0000"
    assert result.web_url.endswith("/proj-0000")
    assert len(result.images) == 1 and result.images[0].is_file()
    # 参考图上传成资源、节点 mode=i2i、闸门走 quote→confirm→run
    kinds = [c[0] if isinstance(c, tuple) else c for c in fake_runner.calls]
    assert "upload" in kinds and "wait" in str(kinds)
    node_call = next(c for c in fake_runner.calls if c[0] == "create_node")
    assert node_call[1] == "image" and node_call[3] == "i2i" and node_call[4] == "2K"


def test_generate_t2i_without_refs(tmp_path, fake_runner):
    dreamina_image.generate(prompt="a cat", download_dir=tmp_path / "dl")
    node_call = next(c for c in fake_runner.calls if c[0] == "create_node")
    assert node_call[1] == "image" and node_call[3] == "t2i"
    assert not any(c[0] == "upload" for c in fake_runner.calls)


def test_generate_t2i_ref_fallback(tmp_path, fake_runner):
    # seedream_4.7 i2i 上限 6，8 张走 t2i 兼收参考（t2i 上限 14）
    refs = []
    for i in range(8):
        ref = tmp_path / f"r{i}.png"; ref.write_bytes(b"x"); refs.append(ref)
    dreamina_image.generate(prompt="p", download_dir=tmp_path / "dl", model="seedream_4.7", reference_paths=refs)
    node_call = next(c for c in fake_runner.calls if c[0] == "create_node")
    assert node_call[3] == "t2i"
    upload_calls = [c for c in fake_runner.calls if c[0] == "upload"]
    assert len(upload_calls) == 8


def test_generate_ref_over_limit(tmp_path, fake_runner):
    refs = []
    for i in range(8):
        ref = tmp_path / f"r{i}.png"; ref.write_bytes(b"x"); refs.append(ref)
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.generate(prompt="p", download_dir=tmp_path / "dl", model="seedream_4.5", reference_paths=refs)


def test_generate_prompt_over_limit(tmp_path, fake_runner):
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.generate(prompt="x" * 2001, download_dir=tmp_path / "dl")
    # 5.0Pro 上限 5000
    dreamina_image.validate_prompt("x" * 2000, has_reference=False, model="seedream_5.0_pro")


def test_generate_ratio_and_model_validation(tmp_path, fake_runner):
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.generate(prompt="p", download_dir=tmp_path / "dl", ratio="2:5")
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.generate(prompt="p", download_dir=tmp_path / "dl", resolution="3K")


def test_generate_timeout(tmp_path, monkeypatch):
    runner = FakeRunner("charvar-image-test")
    runner.terminal_states = ["running", "running"]
    monkeypatch.setattr(dreamina_image, "CanvasRunner", lambda name, **kw: runner)
    monkeypatch.setattr(dreamina_image.time, "sleep", lambda _s: None)

    real_monotonic = time.monotonic
    clock = [0.0]
    monkeypatch.setattr(dreamina_image.time, "monotonic", lambda: clock[0])
    orig_sleep = dreamina_image.time.sleep
    monkeypatch.setattr(dreamina_image.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    with pytest.raises(dreamina_image.DreaminaTimeoutError) as exc:
        dreamina_image.generate(prompt="p", download_dir=tmp_path / "dl", timeout_s=10)
    assert exc.value.submit_id == "sid-1"
    assert exc.value.project_id == "proj-0000"


def test_generate_failed_state(tmp_path, monkeypatch):
    runner = FakeRunner("charvar-image-test")
    monkeypatch.setattr(dreamina_image, "CanvasRunner", lambda name, **kw: runner)
    monkeypatch.setattr(dreamina_image.time, "sleep", lambda _s: None)

    def failed_status(submit_id):
        return dict(operationRef=submit_id, state="failed", resources=[])
    monkeypatch.setattr(runner, "status", failed_status)

    with pytest.raises(dreamina_image.DreaminaTaskError) as exc:
        dreamina_image.generate(prompt="p", download_dir=tmp_path / "dl")
    assert exc.value.submit_id == "sid-1"


def test_default_chain_via_env(monkeypatch, tmp_path, fake_runner):
    monkeypatch.setenv("DREAMINA_IMAGE_MODEL", "5.0Pro")
    monkeypatch.setenv("DREAMINA_IMAGE_RESOLUTION", "1.5k")
    result = dreamina_image.generate(prompt="p", download_dir=tmp_path / "dl")
    assert result.model == "seedream_5.0_pro"
    assert result.resolution == "1.5K"
    node_call = next(c for c in fake_runner.calls if c[0] == "create_node")
    assert node_call[4] == "1.5K"


def test_resolve_and_validate_surface(monkeypatch):
    monkeypatch.delenv("DREAMINA_IMAGE_MODEL", raising=False)
    assert dreamina_image.resolve_model(None) == "seedream_4.7"
    assert dreamina_image.resolve_model("5.0Pro") == "seedream_5.0_pro"
    assert dreamina_image.resolve_model("3.0") == "seedream_4.7"
    assert dreamina_image.validate_model_resolution("seedream_4.7", "1K", has_reference=True) == "1K"
    with pytest.raises(dreamina_image.DreaminaParamError):
        dreamina_image.validate_model_resolution("seedream_4.7", "7K", has_reference=True)
    # 参考图数量按模式校验：i2i 6 张上限内直通
    assert dreamina_image.validate_references(6, model="seedream_4.5", mode="i2i") == "i2i"
    # prompt 校验：默认模型 2000
    assert dreamina_image.validate_prompt("x" * 2000, has_reference=False) == "x" * 2000
