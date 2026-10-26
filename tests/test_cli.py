"""CLI（charvar.cli）：参数校验与输出约定（CliRunner，不发真网）。

项目此前没有 CLI 测试 —— 这层是补上的。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from charvar import cli, openrouter_image, openrouter_video, storage
from charvar.models import SubjectProfile

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


# ---------- subject export ----------

def _seed_export_subject(subject_id: str = "tvelo_x", *, template: bool = True) -> None:
    profile = SubjectProfile(
        id=subject_id, name="缇珞", subject_noun="woman", trigger_word="tvelo",
        style_lock=["clean lineart"], identity_lock=["short silver bob"],
        outfit_lock=["flight suit"], extra="Keep the same silhouette.",
    )
    with storage.connect() as conn:
        storage.save_subject(conn, profile, replace=True)
        if template:
            storage.save_subject_template(conn, subject_id, "# 模板标题\n\n正文逐字，一字不改。")


@pytest.fixture()
def _export_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    return tmp_path


def test_export_writes_yaml_and_verbatim_md(_export_env, tmp_path):
    _seed_export_subject()
    result = runner.invoke(cli.app, ["subject", "export", "tvelo_x"])
    assert result.exit_code == 0, result.output
    yml = tmp_path / "presets" / "subjects" / "tvelo_x.yaml"
    md = tmp_path / "presets" / "subjects" / "tvelo_x.md"
    assert yml.exists() and md.exists()
    assert md.read_text(encoding="utf-8") == "# 模板标题\n\n正文逐字，一字不改。"
    text = yml.read_text(encoding="utf-8")
    assert "# 由 `charvar subject export` 导出的 DB 快照" in text
    import yaml as _yaml

    data = _yaml.safe_load(text)
    assert data["id"] == "tvelo_x" and data["trigger_word"] == "tvelo"


def test_export_json_fields(_export_env, tmp_path):
    _seed_export_subject()
    result = runner.invoke(cli.app, ["subject", "export", "tvelo_x", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["subject_id"] == "tvelo_x" and data["template_chars"] == len("# 模板标题\n\n正文逐字，一字不改。")
    assert data["template_path"].endswith("tvelo_x.md")


def test_export_conflict_needs_overwrite(_export_env, tmp_path):
    _seed_export_subject()
    assert runner.invoke(cli.app, ["subject", "export", "tvelo_x"]).exit_code == 0
    result = runner.invoke(cli.app, ["subject", "export", "tvelo_x"])
    assert result.exit_code == 1
    assert "--overwrite" in result.output
    assert runner.invoke(cli.app, ["subject", "export", "tvelo_x", "--overwrite"]).exit_code == 0


def test_export_missing_subject(_export_env):
    result = runner.invoke(cli.app, ["subject", "export", "ghost"])
    assert result.exit_code == 1
    assert "主体档案不存在" in _stderr(result)


def test_export_without_template_warns(_export_env, tmp_path):
    _seed_export_subject(template=False)
    result = runner.invoke(cli.app, ["subject", "export", "tvelo_x"])
    assert result.exit_code == 0, result.output
    assert "警告" in result.output
    assert not (tmp_path / "presets" / "subjects" / "tvelo_x.md").exists()


# ---------- subject add（档案 + 模板一起入库） ----------

@pytest.fixture()
def _add_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    return tmp_path


def _write_subject_files(base: Path, subject_id: str, *, template: bool = True,
                         fingerprint: str | None = None) -> Path:
    header = f"# 来源模板：全场景通用提示词模板.md（sha256:{fingerprint}）\n" if fingerprint else ""
    yml = base / f"{subject_id}.yaml"
    yml.write_text(
        header + f"id: {subject_id}\nname: 演示\nsubject_noun: woman\n"
        "style_lock:\n- 卡通插画风\nidentity_lock:\n- 成年女性\n",
        encoding="utf-8",
    )
    if template:
        (base / f"{subject_id}.md").write_text(f"# {subject_id} 模板\n\n正文逐字。\n", encoding="utf-8")
    return yml


def _db_template(subject_id: str) -> str | None:
    with storage.connect() as conn:
        return storage.get_subject_template(conn, subject_id)


def test_add_imports_sibling_template(_add_env, tmp_path):
    """档案与模板是两张表：add 必须把同目录 <id>.md 一起入库（否则是半套导入）。"""
    yml = _write_subject_files(tmp_path, "demo_girl")
    result = runner.invoke(cli.app, ["subject", "add", str(yml)])
    assert result.exit_code == 0, result.output
    with storage.connect() as conn:
        assert storage.get_subject(conn, "demo_girl") is not None
    assert _db_template("demo_girl") == "# demo_girl 模板\n\n正文逐字。\n"
    assert "模板" in result.output


def test_add_without_template_warns(_add_env, tmp_path):
    """找不到模板时要明确警告，不能静默当成功（原来就是静默丢模板）。"""
    yml = _write_subject_files(tmp_path, "solo_girl", template=False)
    result = runner.invoke(cli.app, ["subject", "add", str(yml)])
    assert result.exit_code == 0, result.output
    assert "警告" in result.output and "模板" in result.output
    assert _db_template("solo_girl") is None


def test_add_keeps_existing_template_when_source_missing(_add_env, tmp_path):
    """源文件没有模板时，DB 里原有的模板保留，但提示可能已过期。"""
    yml = _write_subject_files(tmp_path, "keep_girl", template=False)
    with storage.connect() as conn:
        storage.save_subject_template(conn, "keep_girl", "# 旧模板\n")
    result = runner.invoke(cli.app, ["subject", "add", str(yml)])
    assert result.exit_code == 0, result.output
    assert _db_template("keep_girl") == "# 旧模板\n"
    assert "可能已过期" in result.output


def test_add_explicit_template(_add_env, tmp_path):
    """--template 指定的模板优先于同目录同名文件。"""
    yml = _write_subject_files(tmp_path, "demo_girl")
    other = tmp_path / "custom.md"
    other.write_text("# 指定的模板\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["subject", "add", str(yml), "--template", str(other)])
    assert result.exit_code == 0, result.output
    assert _db_template("demo_girl") == "# 指定的模板\n"


def test_add_json_fields(_add_env, tmp_path):
    yml = _write_subject_files(tmp_path, "demo_girl")
    result = runner.invoke(cli.app, ["subject", "add", str(yml), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["subject_id"] == "demo_girl"
    assert data["template_updated"] is True
    assert data["template_chars"] == len("# demo_girl 模板\n\n正文逐字。\n")
    assert data["fingerprint_mismatch"] is False


def test_add_fingerprint_mismatch_warns(_add_env, tmp_path):
    """yaml 头的模板 sha256 与实际模板对不上时要告警（溯源链断了）。"""
    yml = _write_subject_files(tmp_path, "fp_girl", fingerprint="0" * 12)
    result = runner.invoke(cli.app, ["subject", "add", str(yml)])
    assert result.exit_code == 0, result.output
    assert "指纹" in result.output and "警告" in result.output


def test_add_conflict_needs_overwrite(_add_env, tmp_path):
    yml = _write_subject_files(tmp_path, "demo_girl")
    assert runner.invoke(cli.app, ["subject", "add", str(yml)]).exit_code == 0
    result = runner.invoke(cli.app, ["subject", "add", str(yml)])
    assert result.exit_code == 1
    assert "--overwrite" in _stderr(result)


# ---------- image dreamina ----------

def test_dreamina_command_builds_request(monkeypatch, tmp_path):
    """参数原样进 AdhocRequest；没有 --seed（即梦不暴露 seed）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))

    from charvar import adhoc as adhoc_service

    seen: list = []

    def fake_generate(req):
        seen.append(req)
        return _fake_result(provider="dreamina", model="5.0")

    monkeypatch.setattr(adhoc_service, "generate", fake_generate)
    result = runner.invoke(cli.app, [
        "image", "dreamina", "--prompt", "a cat",
        "--aspect", "2:3", "--resolution", "4k", "--model", "5.0Pro", "--json",
    ])
    assert result.exit_code == 0, result.output
    req = seen[0]
    assert req.backend == "dreamina"
    assert req.prompt == "a cat"
    assert req.aspect_ratio == "2:3"
    assert req.resolution == "4k"
    assert req.model == "5.0Pro"
    data = json.loads(result.stdout)
    assert data["provider"] == "dreamina"


def test_dreamina_prompt_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))

    from charvar import adhoc as adhoc_service

    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("from a file", encoding="utf-8")
    seen: list = []

    def fake_generate(req):
        seen.append(req)
        return _fake_result(provider="dreamina")

    monkeypatch.setattr(adhoc_service, "generate", fake_generate)
    result = runner.invoke(cli.app, ["image", "dreamina", "--prompt-file", str(prompt_file)])
    assert result.exit_code == 0, result.output
    assert seen[0].prompt == "from a file"


def test_dreamina_error_is_friendly(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))

    from charvar import adhoc as adhoc_service

    def boom(req):
        raise adhoc_service.AdhocError("即梦生图失败：即梦任务失败：内容违规")

    monkeypatch.setattr(adhoc_service, "generate", boom)
    result = runner.invoke(cli.app, ["image", "dreamina", "--prompt", "p"])
    assert result.exit_code == 1
    assert "内容违规" in _stderr(result)


# ---------- video models ----------

def _fake_video_models(monkeypatch, models=None):
    """打桩 openrouter_video.list_models，返回视频模型目录。"""
    calls: list = []

    def fake_list_models(*, force_refresh=False):
        calls.append(force_refresh)
        return models if models is not None else {
            "data": [
                {
                    "id": "heygen/heygen-video-1",
                    "name": "HeyGen: HeyGen Video",
                    "supported_durations": [5, 10, 15],
                    "supported_resolutions": ["480p", "768p", "2K"],
                    "supported_aspect_ratios": ["16:9", "9:16"],
                    "pricing_skus": {
                        "duration_seconds_480p": "0.02",
                        "duration_seconds_768p": "0.036",
                        "duration_seconds_2k": "0.1",
                    },
                },
                {
                    "id": "x-ai/grok-imagine-video-1.5-lite",
                    "name": "Grok Imagine Video",
                    "supported_durations": [5, 10],
                    "supported_resolutions": ["480p"],
                    "supported_aspect_ratios": ["16:9"],
                    "pricing_skus": {
                        "cents_per_video_output_second_480p": "2",
                        "cents_per_image_input": "1",
                    },
                },
            ]
        }

    monkeypatch.setattr(openrouter_video, "list_models", fake_list_models)
    return calls


def test_video_models_lists_enums_and_prices(monkeypatch):
    calls = _fake_video_models(monkeypatch)
    result = runner.invoke(cli.app, ["video", "models"])
    assert result.exit_code == 0, result.output
    assert "heygen/heygen-video-1" in result.stdout
    # 枚举：提交前要照着填，超枚举会被拒
    assert "480p, 768p, 2K" in result.stdout
    assert "5, 10, 15" in result.stdout
    # 价格：heygen 美元/秒、grok 美分/秒，两套键名都要读对
    assert "480p $0.0200/s" in result.stdout
    assert "2K $0.1000/s" in result.stdout
    assert calls


def test_video_models_json_is_pure_json(monkeypatch):
    _fake_video_models(monkeypatch)
    result = runner.invoke(cli.app, ["video", "models", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["data"][0]["id"] == "heygen/heygen-video-1"


def test_video_models_refresh_passes_force_refresh(monkeypatch):
    calls = _fake_video_models(monkeypatch)
    result = runner.invoke(cli.app, ["video", "models", "--refresh"])
    assert result.exit_code == 0, result.output
    assert calls == [True]


def test_video_models_handles_fetch_error(monkeypatch):
    def boom(*, force_refresh=False):
        raise openrouter_video.OpenRouterVideoFetchError("网络不通")

    monkeypatch.setattr(openrouter_video, "list_models", boom)
    result = runner.invoke(cli.app, ["video", "models"])
    assert result.exit_code == 1
    assert "视频模型目录" in _stderr(result)


# ---------- subject image import（交付包图片 → 运行时） ----------

def _png_file(path: Path, size=(32, 48)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (10, 20, 30)).save(path, format="PNG")
    return path


def _seed_subject(subject_id: str = "demo_witch") -> None:
    profile = SubjectProfile(
        id=subject_id, name="演示", subject_noun="woman",
        style_lock=["clean lineart"], identity_lock=["short silver bob"],
        outfit_lock=["flight suit"], extra="Keep the same silhouette.",
    )
    with storage.connect() as conn:
        storage.save_subject(conn, profile, replace=True)


def test_subject_image_import_directory_makes_thumbs(_export_env, tmp_path):
    """目录导入：按文件名认槽位，落盘 + 缩略图 + 槽位行一次到位。"""
    _seed_subject()
    pkg = tmp_path / "final" / "demo_witch"
    _png_file(pkg / "character_sheet.png")
    _png_file(pkg / "front.png")
    (pkg / "全场景通用提示词模板.md").write_text("模板正文", encoding="utf-8")

    result = runner.invoke(cli.app, ["subject", "image", "import", str(pkg), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["subject_id"] == "demo_witch"  # 缺省取目录名
    assert [i["kind"] for i in data["imported"]] == ["character_sheet", "front"]
    assert data["skipped"] == ["全场景通用提示词模板.md"]

    root = tmp_path / "data" / "subjects" / "demo_witch"
    assert (root / "character_sheet.png").exists()
    assert (root / "front.png").exists()
    assert (root / "thumbs" / "character_sheet.webp").exists()
    assert (root / "thumbs" / "front.webp").exists()

    with storage.connect() as conn:
        row = storage.get_subject_image(conn, "demo_witch", "front")
    assert row["status"] == "ready" and row["source"] == "uploaded"


def test_subject_image_import_single_file_needs_kind(_export_env, tmp_path):
    _seed_subject()
    src = _png_file(tmp_path / "pkg" / "mystery.png")
    result = runner.invoke(cli.app, ["subject", "image", "import", str(src), "--subject", "demo_witch"])
    assert result.exit_code == 1
    assert "--kind" in _stderr(result)

    result = runner.invoke(cli.app, [
        "subject", "image", "import", str(src), "--subject", "demo_witch", "--kind", "side", "--json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["imported"][0]["kind"] == "side"
    assert (tmp_path / "data" / "subjects" / "demo_witch" / "thumbs" / "side.webp").exists()


def test_subject_image_import_requires_existing_subject(_export_env, tmp_path):
    pkg = tmp_path / "final" / "ghost"
    _png_file(pkg / "front.png")
    result = runner.invoke(cli.app, ["subject", "image", "import", str(pkg)])
    assert result.exit_code == 1
    assert "主体档案不存在" in _stderr(result)


def test_subject_add_can_import_images_at_once(_export_env, tmp_path):
    """导入档案 + 导入图片一条命令做完（档案、模板、图片三样都入库）。"""
    yml = tmp_path / "presets" / "subjects" / "demo_witch.yaml"
    yml.parent.mkdir(parents=True, exist_ok=True)
    yml.write_text(
        "id: demo_witch\nname: 演示\nsubject_noun: woman\n"
        "style_lock: [clean lineart]\nidentity_lock: [short silver bob]\n"
        "outfit_lock: [flight suit]\nextra: Keep the same silhouette.\n",
        encoding="utf-8",
    )
    (tmp_path / "presets" / "subjects" / "demo_witch.md").write_text("# 模板\n", encoding="utf-8")
    pkg = tmp_path / "final" / "demo_witch"
    _png_file(pkg / "front.png")

    result = runner.invoke(cli.app, [
        "subject", "add", str(yml), "--images", str(pkg), "--json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["subject_id"] == "demo_witch"
    assert data["template_updated"] is True
    assert [i["kind"] for i in data["images"]["imported"]] == ["front"]
    assert (tmp_path / "data" / "subjects" / "demo_witch" / "thumbs" / "front.webp").exists()
