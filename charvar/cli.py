"""Typer CLI（设计文档 §7.1）。

常用命令：
  charvar subject add presets/subjects/silver_witch.yaml
  charvar presets list
  charvar gen --subject silver_witch --preset q21_default_30 --seed 42 --out ./out
  charvar gen ... --format comfyui --workflow <workflows目录名称或路径> [--wait]
  charvar serve --port 8710
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
import yaml

from charvar import paths, storage
from charvar.exporters import captions as captions_exporter
from charvar.exporters import comfyui as comfyui_exporter
from charvar.exporters import jsonl as jsonl_exporter
from charvar.exporters import txt as txt_exporter
from charvar.models import GenerateRequest, SubjectProfile
from charvar.preset_loader import PresetError, list_presets, load, load_file

app = typer.Typer(
    help="角色变体提示词生成器：主体档案 + 预设维度 + 种子稳定的背景/光照 → ComfyUI 提示词",
    no_args_is_help=True,
)
subject_app = typer.Typer(help="主体档案（SQLite 存储）", no_args_is_help=True)
presets_app = typer.Typer(help="YAML 预设", no_args_is_help=True)
app.add_typer(subject_app, name="subject")
app.add_typer(presets_app, name="presets")


def _die(message: str) -> None:
    typer.secho(f"错误：{message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _profile_from_file(path: Path) -> SubjectProfile:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _die(f"无法读取主体档案 {path}：{exc}")
    try:
        return SubjectProfile.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - 转成友好提示
        _die(f"主体档案 {path} 校验失败：{exc}")


def _resolve_subject(value: str) -> SubjectProfile:
    path = Path(value)
    if path.exists() and path.suffix in (".yaml", ".yml", ".json"):
        profile = _profile_from_file(path)
        return profile
    with storage.connect() as conn:
        subject = storage.get_subject(conn, value)
    if subject is None:
        _die(f"主体档案不存在：{value}（先 charvar subject add <file.yaml>，或直接给 YAML 路径）")
    return subject


# ---------- subject ----------

@subject_app.command("add")
def subject_add(
    path: Path = typer.Argument(..., exists=True, readable=True, help="主体档案 YAML/JSON 文件"),
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="同 id 覆盖"),
) -> None:
    profile = _profile_from_file(path)
    with storage.connect() as conn:
        existing = storage.get_subject(conn, profile.id)
        if existing is not None and not overwrite:
            _die(f"主体档案已存在：{profile.id}（--overwrite 覆盖）")
        storage.save_subject(conn, profile, replace=True)
    typer.echo(f"已保存主体档案：{profile.id}（{profile.name}）")


@subject_app.command("list")
def subject_list() -> None:
    with storage.connect() as conn:
        subjects = storage.list_subjects(conn)
    if not subjects:
        typer.echo("（还没有主体档案）")
        return
    for s in subjects:
        typer.echo(f"{s.id}\t{s.name}\t[{s.subject_noun}] trigger={s.trigger_word or '-'}")


@subject_app.command("show")
def subject_show(subject_id: str) -> None:
    with storage.connect() as conn:
        subject = storage.get_subject(conn, subject_id)
    if subject is None:
        _die(f"主体档案不存在：{subject_id}")
    typer.echo(json.dumps(subject.model_dump(), ensure_ascii=False, indent=2))


@subject_app.command("remove")
def subject_remove(subject_id: str) -> None:
    with storage.connect() as conn:
        if not storage.delete_subject(conn, subject_id):
            _die(f"主体档案不存在：{subject_id}")
    typer.echo(f"已删除：{subject_id}")


# ---------- presets ----------

@presets_app.command("list")
def presets_list() -> None:
    presets = list_presets()
    if not presets:
        _die("没有找到任何预设（presets/*.yaml）")
    for p in presets:
        typer.echo(f"{p['name']}\t{p['type']}\tv{p['version']}\t{p['count']} 条")


@presets_app.command("show")
def presets_show(
    name: str,
    limit: int = typer.Option(10, "--limit", help="变体最多显示条数"),
) -> None:
    try:
        preset = load(name)
    except PresetError as exc:
        _die(str(exc))
    from charvar.composer import expand_matrix

    typer.echo(f"name: {preset.name}  type: {preset.type}  version: {preset.version}")
    typer.echo("master_template:")
    typer.echo(f"  {preset.master_template}")
    typer.echo("suffix_template:")
    typer.echo(f"  {preset.suffix_template}")
    typer.echo(f"backgrounds({len(preset.backgrounds)}): {', '.join(preset.backgrounds)}")
    typer.echo(f"lighting({len(preset.lighting)}): {', '.join(preset.lighting)}")
    from charvar.preset_loader import ListPreset

    variants = preset.variants if isinstance(preset, ListPreset) else expand_matrix(preset)
    typer.echo(f"variants: {len(variants)} 条")
    for v in variants[:limit]:
        typer.echo(f"  - {v.id}: {v.text[:96]}…")
    if len(variants) > limit:
        typer.echo(f"  …（其余 {len(variants) - limit} 条省略）")


# ---------- gen ----------

@app.command()
def gen(
    subject: str = typer.Option(..., "--subject", "-s", help="主体档案 id，或 YAML/JSON 文件路径"),
    preset: str = typer.Option("q21_default_30", "--preset", "-p"),
    seed: int = typer.Option(0, "--seed"),
    count: Optional[int] = typer.Option(None, "--count", help="None=预设全部"),
    sampling: str = typer.Option("all", "--sampling", help="all/first/random"),
    randomize_background: bool = typer.Option(True, "--random-bg/--no-bg", help="随机背景"),
    randomize_lighting: bool = typer.Option(True, "--random-light/--no-light", help="随机光照"),
    fixed_background: Optional[str] = typer.Option(None, "--fixed-bg"),
    fixed_lighting: Optional[str] = typer.Option(None, "--fixed-light"),
    separator: str = typer.Option(" ", "--separator"),
    out: Path = typer.Option(paths.out_dir(), "--out", "-o"),
    format: str = typer.Option("all", "--format", help="all/txt/jsonl/captions/comfyui"),
    comfy_url: Optional[str] = typer.Option(None, "--comfy-url", help="默认 COMFYUI_HOST/COMFYUI_PORT 组装"),
    workflow: Optional[str] = typer.Option(None, "--workflow", help="workflows 目录里的名称或 workflow JSON 路径（同名 .mapping.json 搭配）"),
    negative_prompt: Optional[str] = typer.Option(None, "--negative-prompt", help="注入负向提示词（mapping 用 field=negative_prompt）"),
    wait: bool = typer.Option(False, "--wait/--no-wait", help="等待每条执行完成并记录输出文件名（默认只排队）"),
    save_run: bool = typer.Option(True, "--save/--no-save", help="保存生成历史到 SQLite"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """一键生成 N 条变体提示词并导出。"""
    profile = _resolve_subject(subject)
    try:
        preset_obj = load(preset)
    except PresetError as exc:
        _die(str(exc))
    if sampling not in ("all", "first", "random"):
        _die("sampling 只支持 all/first/random")

    req = GenerateRequest(
        subject_id=profile.id,
        subject=profile,
        preset=preset_obj.name,
        seed=seed,
        count=count,
        sampling=sampling,  # type: ignore[arg-type]
        randomize_background=randomize_background,
        randomize_lighting=randomize_lighting,
        fixed_background=fixed_background,
        fixed_lighting=fixed_lighting,
        separator=separator,
    )
    try:
        from charvar.composer import generate as _compose

        result = _compose(profile, preset_obj, req)
    except (ValueError, PresetError) as exc:
        _die(str(exc))

    if save_run:
        with storage.connect() as conn:
            storage.save_run(conn, result)

    out_dir = out / f"{profile.id}_{seed}"
    if out_dir.exists():
        # 同目录重复生成：追加时间戳避免覆盖
        from datetime import datetime as _dt

        stamp = _dt.now().strftime("%H%M%S")
        out_dir = out / f"{profile.id}_{seed}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = out_dir / "prompts.txt"
    jsonl_path = out_dir / "prompts.jsonl"
    captions_dir = out_dir / "captions"

    formats = [format]
    if format == "all":
        formats = ["txt", "jsonl", "captions"]

    if "txt" in formats:
        txt_exporter.write(result, text_path)
    if "jsonl" in formats:
        jsonl_exporter.write(result, jsonl_path)
    if "captions" in formats:
        captions_exporter.write_dir(result, captions_dir)
    if format == "comfyui":
        if not workflow:
            _die("--format comfyui 需要 --workflow（workflows 目录里的名称或 workflow JSON 路径）")
        try:
            workflow_data, mapping_data = comfyui_exporter.load_workflow_pair(workflow)
        except FileNotFoundError as exc:
            _die(str(exc))
        except (json.JSONDecodeError, yaml.YAMLError, ValueError) as exc:
            _die(f"workflow/mapping 无效：{exc}")
        overrides = {"negative_prompt": negative_prompt} if negative_prompt is not None else {}
        submissions = comfyui_exporter.submit(
            result,
            workflow_data,
            mapping_data,
            comfy_url=comfy_url,
            overrides=overrides,
            wait=wait,
        )
        failed = [s for s in submissions if not s.get("ok")]
        (out_dir / "comfy_submissions.json").write_text(
            json.dumps(submissions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if save_run:
            with storage.connect() as conn:
                storage.save_comfy_submissions(conn, result.run_id, submissions)
        typer.echo(f"ComfyUI 提交：成功 {len(submissions) - len(failed)} / 失败 {len(failed)}")
        if wait and failed:
            for s in failed:
                typer.secho(f"  #{s.get('index'):03d} {s.get('error')}", fg=typer.colors.RED)

    if json_out:
        typer.echo(result.model_dump_json())
    else:
        first = result.items[0]
        typer.echo(f"生成 {len(result.items)} 条 → {out_dir}")
        typer.echo(f"  run_id: {result.run_id}")
        typer.echo(f"  第 1 条：{first.prompt[:120]}…")
        if "captions" in formats:
            typer.echo(f"  captions → {captions_dir}")


@app.command()
def serve(
    port: int = typer.Option(8710, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """启动 Web 管理端（等价 python -m web.app）。"""
    import uvicorn

    from web.app import app as webapp

    uvicorn.run(webapp, host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
