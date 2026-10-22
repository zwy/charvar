"""Typer CLI（设计文档 §7.1）。

常用命令：
  charvar subject add presets/subjects/silver_witch.yaml
  charvar presets list
  charvar gen --subject silver_witch --preset q21_default_30 --seed 42 --out ./out
  charvar gen ... --format comfyui --workflow <workflows目录名称或路径> [--wait]
  charvar image comfyui --prompt "..." --ref ref.png --out out.png
  charvar image openrouter --prompt "..." --out out.png
  charvar serve --port 8710
"""
from __future__ import annotations

import json
import sys
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
image_app = typer.Typer(help="临时生图：给一段提示词直接出一张图", no_args_is_help=True)
app.add_typer(subject_app, name="subject")
app.add_typer(presets_app, name="presets")
app.add_typer(image_app, name="image")


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


@subject_app.command("derive")
def subject_derive(
    template: Path = typer.Argument(..., exists=True, readable=True, help="「角色全场景通用提示词模板」.md 文件"),
    subject_id: Optional[str] = typer.Option(None, "--id", help="档案 id（缺省由 LLM 从标题推导 slug）"),
    display_name: Optional[str] = typer.Option(None, "--name", help="显示名（缺省沿用模板标题/正文）"),
    lang: str = typer.Option("zh", "--lang", help="zh=模板原文摘录（默认，可防幻觉核对）/ en=翻译成英文"),
    version_hint: Optional[str] = typer.Option(None, "--version", help="多版本模板时指定要抽取的候选关键词"),
    apply: bool = typer.Option(False, "--apply", help="全链通过后直接入库（缺省只写 presets/subjects/<id>.yaml）"),
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="同 id 已存在（DB 或 YAML 文件）时覆盖"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（外部 agent）"),
) -> None:
    """从模板 .md 派生主体档案候选：LLM 抽取 + 引用核对 + 审计（闸门在 charvar/subject_derive.py）。"""
    from charvar import llm as llm_service
    from charvar import settings_service
    from charvar import subject_derive as derive_service

    if lang not in ("zh", "en"):
        _die("lang 只支持 zh/en")
    try:
        template_md = template.read_text(encoding="utf-8")
    except OSError as exc:
        _die(f"无法读取模板 {template}：{exc}")
    if len(template_md) > derive_service._TEMPLATE_MAX_CHARS:
        typer.secho(
            f"警告：模板 {len(template_md)} 字符超过 LLM 输入上限 {derive_service._TEMPLATE_MAX_CHARS}，"
            "超出部分模型看不到（与入库后被静默截断的行为一致）",
            fg=typer.colors.YELLOW,
        )

    with storage.connect() as conn:
        config = settings_service.llm_overrides(conn)
    try:
        outcome = derive_service.derive(
            template_md,
            lang=lang,
            subject_id=subject_id or "",
            name=display_name or "",
            version_hint=version_hint or "",
            config=config,
        )
    except derive_service.DeriveError as exc:
        _die(str(exc))
    except llm_service.LLMError as exc:
        _die(f"LLM 调用失败：{exc}")
    profile = outcome.profile

    presets_subjects = paths.presets_dir() / "subjects"
    yml_path = presets_subjects / f"{profile.id}.yaml"
    with storage.connect() as conn:
        db_conflict = storage.get_subject(conn, profile.id) is not None
    if (db_conflict or yml_path.exists()) and not overwrite:
        where = "DB 与" if db_conflict and yml_path.exists() else "DB" if db_conflict else "presets/subjects"
        _die(f"档案已存在（{where}）：{profile.id}（--overwrite 覆盖）")

    presets_subjects.mkdir(parents=True, exist_ok=True)
    model = llm_service.get_llm_config(config)["model"]
    derive_service.write_profile_yaml(
        yml_path, profile,
        source_name=template.name, source_sha256=_sha256_hex(template_md), model=model, outcome=outcome,
    )
    if apply:
        with storage.connect() as conn:
            storage.save_subject(conn, profile, replace=True)
        typer.echo(f"已入库档案：{profile.id}")

    if json_out:
        typer.echo(json.dumps(_derive_summary(outcome, yml_path), ensure_ascii=False))
        return
    typer.echo(f"起草 {outcome.draft_rounds} 轮 · 审计 {outcome.verify_rounds} 轮（{'通过' if outcome.verify_pass else '未通过'}）")
    typer.echo(f"  {outcome.citation_summary()}")
    if outcome.quality_warnings:
        typer.echo("  警告：")
        for warning in outcome.quality_warnings:
            typer.echo(f"    - {warning}")
    typer.echo(f"档案 → {yml_path}" + ("（已入库）" if apply else "（未入库，确认无误后用 subject add 或 --apply）"))


def _sha256_hex(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _derive_summary(outcome, yml_path: Path) -> dict:
    return {
        "profile": outcome.profile.model_dump(),
        "yaml_path": str(yml_path),
        "lang": outcome.lang,
        "draft_rounds": outcome.draft_rounds,
        "verify_rounds": outcome.verify_rounds,
        "verify_pass": outcome.verify_pass,
        "verify_issues": [issue.get("detail") for issue in outcome.verify_issues],
        "citation_misses": [[group, entry] for group, entry in outcome.citation_misses],
        "citation_repaired": [[group, entry] for group, entry in outcome.citation_repaired],
        "quality_warnings": outcome.quality_warnings,
        "notes": outcome.notes,
    }


@subject_app.command("verify")
def subject_verify(
    subject_id: str = typer.Argument(..., help="主体档案 id"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（外部 agent）"),
) -> None:
    """审计已入库档案与它的模板是否一致（omission/矛盾/违例；通过 exit 0，有问题 exit 1）。"""
    from charvar import llm as llm_service
    from charvar import settings_service
    from charvar import subject_derive as derive_service

    with storage.connect() as conn:
        subject = storage.get_subject(conn, subject_id)
        if subject is None:
            _die(f"主体档案不存在：{subject_id}")
        template = storage.get_subject_template(conn, subject_id)
        if not template:
            _die(f"主体 {subject_id} 没有模板，无法审计（先 PUT /subjects/<id>/template 或网页「主体档案」上传）")
        config = settings_service.llm_overrides(conn)
    try:
        verdict = llm_service.chat_json(
            derive_service.build_verify_messages(subject, template),
            schema=derive_service.VERIFY_SCHEMA,
            schema_name="subject_profile_verify",
            config=config,
        )
    except llm_service.LLMError as exc:
        _die(f"LLM 审计失败：{exc}")
    issues = [i for i in (verdict.get("issues") or []) if (i.get("detail") or "").strip()]
    passed = bool(verdict.get("pass")) and not issues
    if json_out:
        typer.echo(json.dumps({"subject_id": subject_id, "pass": passed, "issues": issues}, ensure_ascii=False))
    else:
        if passed:
            typer.echo(f"审计通过：{subject_id} 与模板一致")
        else:
            typer.echo(f"审计发现问题（{len(issues)} 条）：")
            for issue in issues:
                quote = f"（模板原文：{issue['quote']}）" if (issue.get("quote") or "").strip() else ""
                typer.echo(f"  - [{issue.get('severity') or 'issue'}] {issue.get('detail')}{quote}")
    if not passed:
        raise typer.Exit(1)


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


# ---------- image（临时生图） ----------

def _read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    """``--prompt`` 与 ``--prompt-file`` 二选一；``--prompt-file -`` 读 stdin。"""
    if prompt is not None and prompt_file is not None:
        _die("--prompt 与 --prompt-file 只能给一个")
    if prompt is None and prompt_file is None:
        _die("需要 --prompt 或 --prompt-file")
    if prompt is not None:
        return prompt
    if prompt_file == "-":
        return sys.stdin.read()
    path = Path(prompt_file).expanduser()
    if not path.exists():
        _die(f"提示词文件不存在：{path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        _die(f"无法读取提示词文件 {path}：{exc}")
    return ""  # pragma: no cover - _die 已抛异常


def _run_adhoc(req, json_out: bool) -> None:
    """跑一次临时生图并输出结果（--json 时 stdout 只有 JSON）。"""
    from charvar import adhoc as adhoc_service

    try:
        result = adhoc_service.generate(req)
    except adhoc_service.AdhocError as exc:
        _die(str(exc))
    except Exception as exc:  # noqa: BLE001 - 网络/后端异常转友好提示
        _die(f"{type(exc).__name__}: {exc}")
        return
    if json_out:
        typer.echo(result.model_dump_json())
        return
    if result.status == "queued":
        typer.echo(f"已入队（ComfyUI prompt_id={result.prompt_id}）")
        typer.echo(f"  manifest → {result.manifest_path}")
        return
    typer.echo(f"完成：{result.image_path}")
    typer.echo(f"  后端：{result.provider}  尺寸：{result.width}×{result.height}  seed：{result.seed}")
    if result.model:
        typer.echo(f"  模型：{result.model}")
    if result.cost is not None:
        typer.echo(f"  费用：{result.cost}")
    typer.echo(f"  manifest → {result.manifest_path}")


@image_app.command("comfyui")
def image_comfyui(
    prompt: Optional[str] = typer.Option(None, "--prompt", help="图片提示词"),
    prompt_file: Optional[str] = typer.Option(
        None, "--prompt-file", help="提示词文件（- 表示读 stdin）"
    ),
    ref: Optional[list[str]] = typer.Option(
        None, "--ref", help="参考图路径，可重复（最多 4 张）"
    ),
    aspect: str = typer.Option("3:4", "--aspect", help="画幅：1:1/3:4/16:9/4:3/9:16/2:3/3:2/21:9"),
    resolution: str = typer.Option("1k", "--resolution", help="分辨率档：1k/2k"),
    seed: int = typer.Option(0, "--seed", help="采样种子（0 = 固定复现；换种子出不同图）"),
    negative: Optional[str] = typer.Option(None, "--negative", help="负向提示词（空串 = 明确不用）"),
    workflow: Optional[str] = typer.Option(
        None, "--workflow", help="workflows 目录里的名称或 workflow JSON 路径"
    ),
    comfy_url: Optional[str] = typer.Option(None, "--comfy-url", help="默认 COMFYUI_HOST/COMFYUI_PORT 组装"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="另外落一份到这个路径"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="等待出图完成（默认）；--no-wait 只入队回 prompt_id"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """用本机 ComfyUI 出图（提示词 + 可选参考图 → 一张图）。"""
    from charvar import adhoc as adhoc_service

    req = adhoc_service.AdhocRequest(
        prompt=_read_prompt(prompt, prompt_file),
        backend="comfyui",
        negative_prompt=negative,
        reference_paths=[Path(p).expanduser() for p in (ref or [])],
        aspect_ratio=aspect,
        resolution=resolution,
        seed=seed,
        comfy_url=comfy_url,
        workflow=workflow,
        wait=wait,
        out=out,
    )
    _run_adhoc(req, json_out)


@image_app.command("openrouter")
def image_openrouter(
    prompt: Optional[str] = typer.Option(None, "--prompt", help="图片提示词"),
    prompt_file: Optional[str] = typer.Option(
        None, "--prompt-file", help="提示词文件（- 表示读 stdin）"
    ),
    ref: Optional[list[str]] = typer.Option(
        None, "--ref", help="参考图路径，可重复（最多 4 张）"
    ),
    aspect: str = typer.Option("3:4", "--aspect", help="画幅：1:1/3:4/16:9/4:3/9:16/2:3/3:2/21:9"),
    resolution: str = typer.Option("1k", "--resolution", help="分辨率档：1k/2k"),
    seed: int = typer.Option(0, "--seed", help="采样种子（0 = 固定复现；换种子出不同图）"),
    model: Optional[str] = typer.Option(None, "--model", help="模型 slug（默认 OP_IMAGE_MODEL / 设置页）"),
    quality: Optional[str] = typer.Option(None, "--quality", help="质量档（模型声明支持才生效）"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="另外落一份到这个路径"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """用 OpenRouter 在线生图（提示词 + 可选参考图 → 一张图）。"""
    from charvar import adhoc as adhoc_service

    req = adhoc_service.AdhocRequest(
        prompt=_read_prompt(prompt, prompt_file),
        backend="openrouter",
        reference_paths=[Path(p).expanduser() for p in (ref or [])],
        aspect_ratio=aspect,
        resolution=resolution,
        seed=seed,
        model=model,
        quality=quality,
        out=out,
    )
    _run_adhoc(req, json_out)


@image_app.command("models")
def image_models(
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """列 OpenRouter 可用的生图模型。"""
    from charvar import openrouter_image

    try:
        models = openrouter_image.model_choices()
    except openrouter_image.OpenRouterImageError as exc:
        _die(f"读 OpenRouter 模型清单失败：{exc}")
        return
    if json_out:
        typer.echo(json.dumps(models, ensure_ascii=False))
        return
    for model in models:
        typer.echo(f"{model['id']}\t{model['name']}")


def main() -> None:
    """console script 入口（pyproject.toml 声明 charvar.cli:main）。"""
    app()


if __name__ == "__main__":
    main()
