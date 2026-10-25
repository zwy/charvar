"""Typer CLI（设计文档 §7.1）。

常用命令：
  charvar subject add presets/subjects/silver_witch.yaml
  charvar presets list
  charvar gen --subject silver_witch --preset q21_default_30 --seed 42 --out ./out
  charvar gen ... --format comfyui --workflow <workflows目录名称或路径> [--wait]
  charvar image comfyui --prompt "..." --ref ref.png --out out.png
  charvar image openrouter --prompt "..." --out out.png
  charvar image dreamina --prompt "..." --out out.png
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
from charvar import outfit as outfit_service
from charvar import overlay as overlay_service

app = typer.Typer(
    help="角色变体提示词生成器：主体档案 + 预设维度 + 种子稳定的背景/光照 → ComfyUI 提示词",
    no_args_is_help=True,
)
subject_app = typer.Typer(help="主体档案（SQLite 存储）", no_args_is_help=True)
presets_app = typer.Typer(help="YAML 预设", no_args_is_help=True)
image_app = typer.Typer(help="Playground：给一段提示词直接出一张图", no_args_is_help=True)
video_app = typer.Typer(help="Playground 视频：给一段提示词直接出一段视频（OpenRouter，异步作业）", no_args_is_help=True)
app.add_typer(subject_app, name="subject")
app.add_typer(presets_app, name="presets")
recipes_app = typer.Typer(help="视频配方：线性步骤执行器（出图批 → 贴字 → 本地合成）", no_args_is_help=True)
app.add_typer(recipes_app, name="recipes")
app.add_typer(image_app, name="image")
app.add_typer(video_app, name="video")


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


@subject_app.command("export")
def subject_export(
    subject_id: str = typer.Argument(..., help="主体档案 id"),
    dir_out: Optional[Path] = typer.Option(None, "--dir", "-o", help="导出目录（默认 presets/subjects）"),
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="目标文件已存在时覆盖"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（外部 agent）"),
) -> None:
    """把 DB 里的主体档案快照回仓库：<id>.yaml + <id>.md（模板逐字，无改写）。"""
    import yaml as _yaml

    if dir_out is None:  # 运行时解析：模块导入期的默认值会绕过 CHARVAR_PRESETS_DIR
        dir_out = paths.presets_dir() / "subjects"
    with storage.connect() as conn:
        profile = storage.get_subject(conn, subject_id)
        if profile is None:
            _die(f"主体档案不存在：{subject_id}")
        template_md = storage.get_subject_template(conn, subject_id)

    yml_path = dir_out / f"{profile.id}.yaml"
    md_path = dir_out / f"{profile.id}.md"
    existing = [p for p in (yml_path, *( [md_path] if template_md else [] )) if p.exists()]
    if existing and not overwrite:
        _die(f"目标文件已存在：{existing[0]}（--overwrite 覆盖）")
    dir_out.mkdir(parents=True, exist_ok=True)
    body = _yaml.safe_dump(profile.model_dump(exclude_none=True), allow_unicode=True, sort_keys=False)
    yml_path.write_text(
        f"# 由 `charvar subject export` 导出的 DB 快照（{storage.utcnow()}），模板逐字在同名 .md。\n" + body,
        encoding="utf-8",
    )
    if template_md:
        md_path.write_text(template_md, encoding="utf-8")
    if json_out:
        typer.echo(json.dumps({
            "subject_id": profile.id,
            "yaml_path": str(yml_path),
            "template_path": str(md_path) if template_md else None,
            "template_chars": len(template_md) if template_md else 0,
        }, ensure_ascii=False))
        return
    typer.echo(f"已导出：{yml_path}")
    if template_md:
        typer.echo(f"  模板（{len(template_md)} 字符，逐字）→ {md_path}")
    else:
        typer.secho("  警告：DB 里没有该主体的模板，只导出档案（.md 未写）", fg=typer.colors.YELLOW)


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
    outfit: Optional[str] = typer.Option(None, "--outfit", help="服装预设名（presets/outfits/），与预设笛卡尔积"),
    outfit_order: str = typer.Option("outfit_first", "--outfit-order", help="组合顺序：outfit_first（默认，每件服装连播全部动作）/pose_first"),
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
    if outfit:
        from charvar.sticker_policy import is_sticker_preset

        if is_sticker_preset(preset_obj):
            _die("sticker 预设（带字表情包）不支持服装组合：图片文案与服装段语义冲突")
        try:
            outfit_obj = outfit_service.load_outfit(outfit)
            preset_obj = outfit_service.combine(preset_obj, outfit_obj, outfit_order)
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


# ---------- overlay（贴字后处理） ----------

@app.command("overlay")
def overlay_cmd(
    run_id: str = typer.Argument(..., help="run_id（历史记录里的生成轮次）"),
    overlay: str = typer.Option(..., "--overlay", "-l", help="贴字预设名（presets/overlays/）"),
    text_from: str = typer.Option(
        "outfit_label", "--text-from",
        help="outfit_label（服装组合的中文 label）/ pose_tag / variant_id / fixed",
    ),
    text: Optional[str] = typer.Option(None, "--text", help="固定文本（--text-from fixed 时）"),
    items: Optional[str] = typer.Option(None, "--items", help="只处理这些条目序号（逗号分隔；缺省全部）"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出"),
) -> None:
    """对已生成 run 贴字（PIL 后处理；产物落 labeled/，原图不动）。"""
    if text_from not in overlay_service.TEXT_SOURCES:
        _die(f"text_from 只支持 {'/'.join(overlay_service.TEXT_SOURCES)}")
    indexes = None
    if items:
        try:
            indexes = [int(v) for v in str(items).split(",") if v.strip()]
        except ValueError:
            _die("--items 需要逗号分隔的条目序号（如 --items 0,1,2）")
    try:
        with storage.connect() as conn:
            info = overlay_service.apply_overlay(
                conn,
                run_id,
                overlay=overlay,
                text_from=text_from,
                fixed=text,
                items=indexes,
            )
    except PresetError as exc:
        _die(str(exc))
    if json_out:
        typer.echo(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"贴字 {len(info['applied'])} 张（overlay={info['overlay']} · text_from={text_from}）")
        for s in info["skipped"]:
            typer.echo(f"  跳过 #{s['item_index']:03d}：{s['reason']}")
        if info["applied"]:
            typer.echo(f"产物目录：{info['applied'][0]['labeled_url'].rsplit('/', 1)[0]}".replace("/assets/labeled/", "/labeled/"))


# ---------- recipes（视频配方执行器） ----------

@recipes_app.command("list")
def recipes_list() -> None:
    from charvar import recipes as recipes_service

    items = recipes_service.list_recipes()
    if not items:
        _die("没有找到任何配方（presets/recipes/*.yaml）")
    for r in items:
        if r.get("error"):
            typer.secho(f"{r['name']}　配置错误：{r['error']}", fg=typer.colors.RED)
            continue
        steps = " → ".join(f"{s['label']}({s['kind']})" + ("［manual］" if s["manual"] else "") for s in r["steps"])
        typer.echo(f"{r['name']}　{r['title'] or '-'}　类别 {r['category']}　步骤：{steps}")


@recipes_app.command("status")
def recipes_status(
    run_id: Optional[str] = typer.Argument(None, help="配方 run_id；缺省列最近运行"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """查看配方运行状态（缺省 = 列最近 20 次）。"""
    from charvar import recipes as recipes_service

    if run_id:
        try:
            state = recipes_service.read_state(run_id)
        except PresetError as exc:
            _die(str(exc))
        states = [state]
    else:
        states = []
        for row in recipes_service.list_recipe_runs()[:20]:
            states.append(recipes_service.read_state(row["recipe_run_id"]))
        if not states:
            typer.echo("（还没有配方运行：先 charvar recipes run <配方名>）")
    if json_out:
        typer.echo(json.dumps([s.model_dump() for s in states], ensure_ascii=False, indent=2))
        return
    for state in states:
        typer.echo(f"{state.recipe_run_id}　{state.status}　种子 {state.seed}")
        for st in state.steps:
            mark = {"pending": "·", "done": "✓", "failed": "✗"}.get(st.status, "?")
            line = f"  {mark} {st.label}（{st.kind}）{st.status}"
            if st.note:
                line += f"　{st.note}"
            typer.echo(line)


@recipes_app.command("run")
def recipes_run(
    name: str = typer.Argument(..., help="配方名（presets/recipes/<name>.yaml）"),
    seed: Optional[int] = typer.Option(None, "--seed", help="种子（缺省 = 配方级 seed）"),
    continue_run: bool = typer.Option(False, "--continue", help="继续该配方最近一次未完成的运行（人工过目后）"),
    json_out: bool = typer.Option(False, "--json", help="状态以 JSON 输出"),
) -> None:
    """启动 / 继续一次配方运行。状态落 data/generated/recipe_runs/<id>/manifest.json。"""
    from charvar import recipes as recipes_service

    with storage.connect() as conn:
        try:
            if continue_run:
                runs = [r for r in recipes_service.list_recipe_runs() if r["recipe"] == name]
                if not runs:
                    _die(f"配方 {name} 没有可继续的运行（先不带 --continue 跑一次）")
                state = recipes_service.advance_state(conn, runs[0]["recipe_run_id"])
            else:
                state = recipes_service.start_run(conn, name, seed=seed)
        except PresetError as exc:
            _die(str(exc))
    if json_out:
        typer.echo(json.dumps(state.model_dump(), ensure_ascii=False, indent=2))
        return
    typer.echo(f"配方运行 {state.recipe_run_id}　状态：{state.status}")
    for st in state.steps:
        mark = {"pending": "·", "done": "✓", "failed": "✗"}.get(st.status, "?")
        line = f"  {mark} {st.label}（{st.kind}）{st.status}"
        if st.note:
            line += f"　{st.note}"
        typer.echo(line)
    if state.status == "waiting":
        typer.echo("→ 停在人工检查点：过目/推送成品后执行 charvar recipes run %s --continue" % name)


# ---------- image（Playground） ----------

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
    """跑一次 Playground 生图并输出结果（--json 时 stdout 只有 JSON）。"""
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
    resolution: str = typer.Option(
        "1k", "--resolution", help="分辨率：1k/2k 或模型声明枚举值（512/1K/1.5K/2K/4K…）；空 = 设置页/env 模式默认"
    ),
    seed: int = typer.Option(0, "--seed", help="采样种子（0 = 固定复现；换种子出不同图）"),
    model: Optional[str] = typer.Option(
        None, "--model", help="模型 slug（默认按模式取设置页 / OP_IMAGE_T2I_MODEL、OP_IMAGE_I2I_MODEL）"
    ),
    quality: Optional[str] = typer.Option(
        None, "--quality", help="质量档（模型声明支持才生效；空 = 设置页/env 模式默认）"
    ),
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


@image_app.command("dreamina")
def image_dreamina(
    prompt: Optional[str] = typer.Option(None, "--prompt", help="图片提示词"),
    prompt_file: Optional[str] = typer.Option(
        None, "--prompt-file", help="提示词文件（- 表示读 stdin）"
    ),
    ref: Optional[list[str]] = typer.Option(
        None, "--ref", help="参考图路径，可重复（最多 4 张，走 image2image）"
    ),
    aspect: str = typer.Option("3:4", "--aspect", help="画幅：1:1/3:4/16:9/4:3/9:16/2:3/3:2/21:9（即梦原生 8 档，同列表）"),
    resolution: str = typer.Option(
        "", "--resolution", help="分辨率档 2k/4k（5.0Pro 另有 1.5k）；空 = 设置页/env（DREAMINA_IMAGE_RESOLUTION）默认 2k"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", help="即梦 model_version（3.0/3.1/4.0/4.1/4.5/4.6/4.7/5.0/5.0Pro）；空 = 设置页/env（DREAMINA_IMAGE_MODEL）默认 5.0"
    ),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="另外落一份到这个路径"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """用即梦（Dreamina）官方 CLI 出图（需已 dreamina login；不暴露 seed，消耗即梦积分）。"""
    from charvar import adhoc as adhoc_service

    req = adhoc_service.AdhocRequest(
        prompt=_read_prompt(prompt, prompt_file),
        backend="dreamina",
        reference_paths=[Path(p).expanduser() for p in (ref or [])],
        aspect_ratio=aspect,
        resolution=resolution,
        model=model,
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


# ---------- video（Playground 视频） ----------

def _run_video(req, json_out: bool) -> None:
    """跑一次 Playground 视频生成并输出结果（--json 时 stdout 只有 JSON）。"""
    from charvar import adhoc_video as adhoc_video_service

    try:
        result = adhoc_video_service.generate(req)
    except adhoc_video_service.AdhocVideoTimeout as exc:
        _die(f"超时未完成（作业还在跑）：{exc}\n  可稍后用网页「视频」页续查，或重跑等待")
        return
    except adhoc_video_service.AdhocVideoError as exc:
        _die(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - 网络/后端异常转友好提示
        _die(f"{type(exc).__name__}: {exc}")
        return
    if json_out:
        typer.echo(result.model_dump_json())
        return
    if result.status == "pending":
        typer.echo(f"已提交（job_id={result.job_id}）")
        typer.echo(f"  manifest → {result.manifest_path}")
        return
    typer.echo(f"完成：{result.video_path}")
    typer.echo(f"  模型：{result.model or ''}  {result.resolution or ''} {result.duration or ''}s {result.aspect_ratio}")
    if result.cost is not None:
        typer.echo(f"  费用：${result.cost:.4f}")
    typer.echo(f"  用时：{result.duration_ms / 1000:.1f}s  manifest → {result.manifest_path}")


@video_app.command("openrouter")
def video_openrouter(
    prompt: Optional[str] = typer.Option(None, "--prompt", help="视频提示词"),
    prompt_file: Optional[str] = typer.Option(
        None, "--prompt-file", help="提示词文件（- 表示读 stdin）"
    ),
    ref: Optional[list[str]] = typer.Option(
        None, "--ref", help="参考图路径，可重复（最多 4 张；input_references 参考生成）"
    ),
    video_ref: Optional[list[str]] = typer.Option(
        None, "--video-ref", help="参考视频路径，可重复（≤2 条 mp4/mov/webm；Seedance gen2+ 才接）"
    ),
    first_frame: Optional[str] = typer.Option(
        None, "--first-frame", help="首帧图路径（1 张；图生视频 frame_images，主体一致性靠它）"
    ),
    aspect: str = typer.Option("9:16", "--aspect", help="画幅：随所选模型枚举（heygen：21:9/16:9/4:3/1:1/3:4/9:16）"),
    duration: Optional[int] = typer.Option(
        None, "--duration", help="时长秒数（整数；默认不发送 = 跟随 provider；heygen 支持 5–15）"
    ),
    resolution: Optional[str] = typer.Option(
        None, "--resolution", help="分辨率档（480p/768p/2K…；默认不发送 = 跟随 provider）"
    ),
    seed: int = typer.Option(0, "--seed", help="采样种子（0 = 固定复现；换种子出不同片）"),
    model: Optional[str] = typer.Option(
        None, "--model", help="模型 slug（默认设置页 / OP_VIDEO_MODEL，兜底 heygen/heygen-video-1）"
    ),
    timeout: float = typer.Option(900.0, "--timeout", help="轮询超时秒数（默认 900）"),
    poll_interval: float = typer.Option(5.0, "--poll-interval", help="轮询间隔秒数（默认 5）"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="另外落一份到这个路径"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """用 OpenRouter 出一段视频（提示词 + 可选首帧/参考图 → 一段 MP4；异步作业）。

    测试默认模型 heygen/heygen-video-1（按秒计费，便宜档 480p $0.02/s）。
    """
    from charvar import adhoc_video as adhoc_video_service

    req = adhoc_video_service.VideoRequest(
        prompt=_read_prompt(prompt, prompt_file),
        backend="openrouter",
        reference_paths=[Path(p).expanduser() for p in (ref or [])],
        reference_video_paths=[Path(p).expanduser() for p in (video_ref or [])],
        first_frame=Path(first_frame).expanduser() if first_frame else None,
        aspect_ratio=aspect,
        duration=duration,
        resolution=resolution,
        seed=seed,
        model=model,
        wait=True,
        out=out,
    )
    _run_video(req, json_out)


@video_app.command("models")
def video_models(
    refresh: bool = typer.Option(False, "--refresh", help="忽略本地缓存，强制拉取最新目录"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """列 OpenRouter 可用的视频模型及其合法枚举。

    提交前先跑这个：时长／分辨率／画幅超枚举会在提交阶段被拒，
    而视频按秒计费，提交成功就开始计费。
    """
    from charvar import openrouter_video

    try:
        data = openrouter_video.list_models(force_refresh=refresh)
    except openrouter_video.OpenRouterVideoError as exc:
        _die(f"读 OpenRouter 视频模型目录失败：{exc}")
        return

    if json_out:
        typer.echo(json.dumps(data, ensure_ascii=False))
        return

    items = [m for m in (data.get("data") or []) if isinstance(m, dict)]
    if not items:
        typer.echo("（目录为空）")
        return
    for model in items:
        model_id = str(model.get("id") or "").strip()
        if not model_id:
            continue
        typer.echo(f"{model_id}\t{str(model.get('name') or model_id)}")
        for label, key in (
            ("时长", "supported_durations"),
            ("分辨率", "supported_resolutions"),
            ("画幅", "supported_aspect_ratios"),
        ):
            values = model.get(key)
            if isinstance(values, list) and values:
                typer.echo(f"    {label}：{', '.join(str(v) for v in values)}")
        pricing = openrouter_video.pricing_hints(model)
        if pricing:
            typer.echo(f"    价格：{'、'.join(pricing)}")
        typer.echo("")


def main() -> None:
    """console script 入口（pyproject.toml 声明 charvar.cli:main）。"""
    app()


if __name__ == "__main__":
    main()
