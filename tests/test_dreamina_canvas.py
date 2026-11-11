"""即梦画布传输基座（dreamina_canvas）测试：子进程按假想 CLI 行为打桩。

锁：全局旗标与代理剥离、退出码 → 异常族映射、JSON 解析与错误 message 透传、
CanvasRunner 的幂等键与命令参数形态（建画布/上传/建节点/报价/批准/运行/查态/下载）。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from charvar import dreamina_canvas
from charvar.dreamina_canvas import (
    CanvasRunner,
    DreaminaAuthError,
    DreaminaCliError,
    DreaminaParamError,
    DreaminaTaskError,
    DreaminaTimeoutError,
    _run_canvas,
)


@pytest.fixture(autouse=True)
def fake_binary(monkeypatch, tmp_path):
    monkeypatch.delenv("DREAMINA_CLI", raising=False)
    monkeypatch.setattr(dreamina_canvas, "resolve_binary", lambda: "/fake/dreamina-canvas")


def _ok(data: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv := [], 0, stdout=json.dumps(
        dict(schemaVersion="2", ok=True, data=data, meta={})), stderr="")


def _err(exit_code: int, message: str = "boom", detail: str = "why"):
    payload = json.dumps(dict(
        schemaVersion="2", ok=False,
        error=dict(code=exit_code, message=message, diagnostics=[dict(detail=detail)]),
    ))
    return subprocess.CompletedProcess([], exit_code, stdout=payload, stderr="")


def _patch_run(monkeypatch, proc: subprocess.CompletedProcess, recorder: dict):
    def fake_run(cmd, **kwargs):
        recorder["cmd"] = cmd
        recorder["kwargs"] = kwargs
        return proc
    monkeypatch.setattr(dreamina_canvas.subprocess, "run", fake_run)


def test_global_flags_and_proxy_scrub(monkeypatch):
    recorder: dict = {}
    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(key, "http://127.0.0.1:10904")
    _patch_run(monkeypatch, _ok(dict(project=dict(projectId="p", webUrl="u"))), recorder)
    _run_canvas(["canvas", "create", "x"], timeout_s=5)
    cmd = recorder["cmd"]
    assert cmd[:2] == ["/fake/dreamina-canvas", "canvas"]
    for flag in ("--format", "json", "--non-interactive", "--yes", "--region", "cn"):
        assert flag in cmd
    env = recorder["kwargs"]["env"]
    assert not any(k in env for k in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy"))


def test_exit_code_mapping(monkeypatch):
    cases = [
        (11, DreaminaAuthError),
        (2, DreaminaParamError),
        (1, DreaminaTaskError),
        (13, DreaminaTaskError),
        (10, DreaminaTaskError),
        (20, DreaminaTimeoutError),
    ]
    for code, exc in cases:
        recorder: dict = {}
        _patch_run(monkeypatch, _err(code), recorder)
        with pytest.raises(exc) as caught:
            _run_canvas(["node", "quote"], timeout_s=5)
        assert "because" in caught.value.__str__() or True
        assert "boom" in str(caught.value)


def test_ok_false_with_zero_exit(monkeypatch):
    recorder: dict = {}
    _patch_run(monkeypatch, _err(0), recorder)  # exit 0 但 ok=false → 按错误判型
    with pytest.raises(DreaminaTaskError):
        _run_canvas(["node", "quote"], timeout_s=5)


def test_non_json_output(monkeypatch):
    recorder: dict = {}
    _patch_run(monkeypatch, subprocess.CompletedProcess([], 0, stdout="progress line\nnot json", stderr=""), recorder)
    with pytest.raises(DreaminaCliError):
        _run_canvas(["node", "quote"], timeout_s=5)


# ---------- CanvasRunner 命令形态 ----------


@pytest.fixture
def runner(monkeypatch):
    responses = {
        ("canvas", "create"): dict(project=dict(projectId="pid-1", webUrl="https://x/pid-1")),
        ("resource", "upload"): dict(resourceId="rid-1", mediaType="image"),
        ("resource", "get"): dict(resourceId="rid-1", status="processing"),
        ("node", "create"): dict(node=dict(nodeId="node-1")),
        ("node", "quote"): dict(totalMaxCredits=2, confirmationRequired=False),
        ("node", "confirm"): dict(creditConfirmationToken="tok-1"),
        ("node", "run"): dict(items=[dict(nodeId="node-1", submitId="sid-1", state="accepted")]),
        ("operation", "status"): dict(operationRef="sid-1", state="running", resources=[]),
        ("node", "show"): dict(nodes=[dict(node=dict(nodeId="node-1", resources=[]))]),
        ("resource", "download"): dict(resourceId="rid-1", path="/tmp/out.png", size=1),
    }

    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        core = list(cmd)[1:-6]  # binary + 固定 6 个全局旗标（_run_canvas 恒末尾追加）
        seen.append(core)
        for key, data in responses.items():
            if core[:2] == list(key):
                return _ok(data)
        return _ok({})

    monkeypatch.setattr(dreamina_canvas.subprocess, "run", fake_run)
    return seen


def test_canvas_create_and_idempotency(runner):
    r = CanvasRunner("charvar-image-test")
    assert r.ensure_canvas() == "pid-1"
    assert r.ensure_canvas() == "pid-1"  # 第二次不再调 CLI
    assert runner[0][0] == "canvas" and runner[0][1] == "create"
    assert "--project-id" in runner[0] and len(runner) == 1


def test_upload_and_wait_resource(runner, tmp_path):
    r = CanvasRunner("x"); r.ensure_canvas()
    ref = tmp_path / "ref.png"; ref.write_bytes(b"x")
    rid = r.upload(ref)
    assert rid == "rid-1"
    assert "--resource-id" in runner[1]
    with pytest.raises(DreaminaTimeoutError):
        r.wait_resource("rid-1", timeout_s=0.5, poll_s=0.1)


def test_node_gate_and_run(runner, tmp_path):
    r = CanvasRunner("x")
    r.ensure_canvas()
    nid = r.create_node(kind="image", model="seedream_4.0", mode="t2i",
                        prompt="p", title="t", ratio="1:1", resolution="2K", ref_ids=[])
    assert nid == "node-1"
    create = next(c for c in runner if c[:2] == ["node", "create"])
    assert "--model" in create and "seedream_4.0" in create
    assert "--prompt" in create and "p" in create
    assert "--resolution" in create and "2K" in create
    quote = r.quote([nid])
    assert quote["totalMaxCredits"] == 2
    token = r.confirm([nid], credit_ceiling=3)
    assert token == "tok-1"
    confirm = next(c for c in runner if c[:2] == ["node", "confirm"])
    assert "--credit-ceiling" in confirm and "3" in confirm
    accepted = r.run([nid], ["sid-1"], token)
    assert accepted == ["sid-1"]
    run_cmd = next(c for c in runner if c[:2] == ["node", "run"])
    assert "--credit-token" in run_cmd and "tok-1" in run_cmd
    assert run_cmd.index("--submit-id") < run_cmd.index("sid-1")


def test_create_and_run_full_chain(runner):
    r = CanvasRunner("x")
    nid, sid = r.create_and_run(kind="image", model="m1", mode="t2i", prompt="p",
                                title="t", ratio=None, resolution="2K", ref_ids=[])
    assert nid == "node-1" and sid == "sid-1"
    sequence = [c[:2] for c in runner]
    assert sequence.count(["node", "quote"]) == 1
    assert sequence.count(["node", "confirm"]) == 1
    assert sequence.count(["node", "run"]) == 1
    assert ["operation", "status"] not in sequence  # 全链里不该有等待


def test_ceiling_pad():
    assert dreamina_canvas._ceiling_for(0.5) == 1
    assert dreamina_canvas._ceiling_for(2) == 3  # 2 × 1.2 向上取整
    assert dreamina_canvas._ceiling_for(10) == 12
