"""CLI（charvar.cli）：参数校验与输出约定（CliRunner，不发真网）。

项目此前没有 CLI 测试 —— 这层是补上的。
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from charvar import cli, openrouter_image

runner = CliRunner()


def _stderr(result) -> str:
    """取 stderr —— click 8.1 把 stderr 混进 stdout，8.5 起才单独捕获。

    测试环境里两个 click 版本都可能出现（系统 python 与 .venv），所以两种都兜住。
    """
    try:
        return result.stderr
    except ValueError:
        return result.output


def _fake_result(**overrides):
    """造一个 AdhocResult（打桩 adhoc.generate 的返回值）。"""
    from charvar import adhoc as adhoc_service

    data = {
        "image_path": "/tmp/x.png",
        "provider": "comfyui",
        "manifest_path": "/tmp/m.json",
        "width": 1024,
        "height": 1024,
        "seed": 0,
    }
    data.update(overrides)
    return adhoc_service.AdhocResult(**data)


def _fake_models(monkeypatch, models=None):
    calls: list = []

    def fake_choices():
        calls.append(True)
        return models if models is not None else [
            {"id": "meta/muse-image", "name": "Muse Image"},
            {"id": "google/gemini-3-pro-image-preview", "name": "Gemini 3 Pro Image"},
        ]

    monkeypatch.setattr(openrouter_image, "model_choices", fake_choices)
    return calls


# ---------- 参数校验 ----------

def test_prompt_and_prompt_file_mutually_exclusive():
    result = runner.invoke(cli.app, ["image", "comfyui", "--prompt", "a", "--prompt-file", "b"])
    assert result.exit_code == 1
    assert "只能给一个" in _stderr(result)


def test_neither_prompt_nor_prompt_file():
    result = runner.invoke(cli.app, ["image", "comfyui"])
    assert result.exit_code == 1
    assert "需要 --prompt" in _stderr(result)


def test_openrouter_also_validates_prompt():
    result = runner.invoke(cli.app, ["image", "openrouter"])
    assert result.exit_code == 1
    assert "需要 --prompt" in _stderr(result)


def test_missing_prompt_file_dies():
    result = runner.invoke(cli.app, ["image", "comfyui", "--prompt-file", "/nope/missing.md"])
    assert result.exit_code == 1
    assert "提示词文件不存在" in _stderr(result)


# ---------- 提示词读取 ----------

def test_prompt_file_dash_reads_stdin(monkeypatch, tmp_path):
    """--prompt-file - 从 stdin 读提示词。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    seen: list = []

    from charvar import adhoc as adhoc_service

    def fake_generate(req):
        seen.append(req.prompt)
        return _fake_result()

    monkeypatch.setattr(adhoc_service, "generate", fake_generate)
    result = runner.invoke(
        cli.app, ["image", "comfyui", "--prompt-file", "-"], input="prompt from stdin"
    )
    assert result.exit_code == 0, result.output
    assert seen and seen[0] == "prompt from stdin"


def test_prompt_file_reads_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("from a file", encoding="utf-8")
    seen: list = []

    from charvar import adhoc as adhoc_service

    def fake_generate(req):
        seen.append(req.prompt)
        return _fake_result()

    monkeypatch.setattr(adhoc_service, "generate", fake_generate)
    result = runner.invoke(cli.app, ["image", "comfyui", "--prompt-file", str(prompt_file)])
    assert result.exit_code == 0, result.output
    assert seen and seen[0] == "from a file"


# ---------- 输出约定 ----------

def test_json_output_is_pure_json(monkeypatch, tmp_path):
    """--json 时 stdout 只有 JSON（能直接 json.loads）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))

    from charvar import adhoc as adhoc_service

    monkeypatch.setattr(adhoc_service, "generate", lambda req: _fake_result())
    result = runner.invoke(cli.app, ["image", "comfyui", "--prompt", "p", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["image_path"] == "/tmp/x.png"
    assert data["provider"] == "comfyui"


def test_human_output_mentions_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))

    from charvar import adhoc as adhoc_service

    monkeypatch.setattr(adhoc_service, "generate", lambda req: _fake_result())
    result = runner.invoke(cli.app, ["image", "comfyui", "--prompt", "p"])
    assert result.exit_code == 0, result.output
    assert "/tmp/x.png" in result.stdout
    assert "1024×1024" in result.stdout


def test_backend_error_becomes_friendly_message(monkeypatch, tmp_path):
    """后端异常转成「错误：…」+ exit 1（不是 traceback）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))

    from charvar import adhoc as adhoc_service

    def boom(req):
        raise adhoc_service.AdhocError("ComfyUI 连不上")

    monkeypatch.setattr(adhoc_service, "generate", boom)
    result = runner.invoke(cli.app, ["image", "comfyui", "--prompt", "p"])
    assert result.exit_code == 1
    assert "ComfyUI 连不上" in _stderr(result)


# ---------- models ----------

def test_models_command_lists(monkeypatch):
    calls = _fake_models(monkeypatch)
    result = runner.invoke(cli.app, ["image", "models"])
    assert result.exit_code == 0, result.output
    assert "meta/muse-image" in result.stdout
    assert calls


def test_models_command_json(monkeypatch):
    _fake_models(monkeypatch)
    result = runner.invoke(cli.app, ["image", "models", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["id"] == "meta/muse-image"


def test_models_command_handles_fetch_error(monkeypatch):
    def boom():
        raise openrouter_image.OpenRouterFetchError("网络不通")

    monkeypatch.setattr(openrouter_image, "model_choices", boom)
    result = runner.invoke(cli.app, ["image", "models"])
    assert result.exit_code == 1
    assert "模型清单" in _stderr(result)


# ---------- 入口 ----------

def test_main_entry_point_exists():
    """pyproject.toml 声明 charvar.cli:main —— 缺了 console script 就是坏的。"""
    assert callable(cli.main)


def test_main_is_console_script_target():
    from charvar import __version__  # noqa: F401 - 确认包可导入
    import importlib.metadata as md

    try:
        entry_points = md.distribution("charvar").entry_points
    except md.PackageNotFoundError:
        pytest.skip("charvar 未安装为 distribution")
    targets = {ep.value for ep in entry_points if ep.group == "console_scripts"}
    assert "charvar.cli:main" in targets
