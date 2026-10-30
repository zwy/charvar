"""Typer CLI（设计文档 §7.1）。

常用命令：
  charvar subject add presets/subjects/silver_witch.yaml
  charvar subject prompt front --material "..." --out front_prompt.txt
  charvar presets list
  charvar gen --subject silver_witch --preset q21_default_30 --seed 42 --out ./out
  charvar gen ... --format comfyui --workflow <workflows目录名称或路径> [--wait]
  charvar image comfyui --prompt "..." --ref ref.png --out out.png
  charvar image openrouter --prompt "..." --out out.png
  charvar image dreamina --prompt "..." --out out.png
  charvar serve --port 8710
"""
from __future__ import annotations

import hashlib
import json
import re
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
from charvar import hairstyle as hairstyle_service
from charvar import overlay as overlay_service

app = typer.Typer(
    help="角色变体提示词生成器：主体档案 + 预设维度 + 种子稳定的背景/光照 → ComfyUI 提示词",
    no_args_is_help=True,
)
subject_app = typer.Typer(help="主体档案（SQLite 存储）", no_args_is_help=True)
subject_image_app = typer.Typer(help="主体档案图片（槽位图导入）", no_args_is_help=True)
presets_app = typer.Typer(help="YAML 预设", no_args_is_help=True)
image_app = typer.Typer(help="Playground：给一段提示词直接出一张图", no_args_is_help=True)
video_app = typer.Typer(help="Playground 视频：给一段提示词直接出一段视频（OpenRouter，异步作业）", no_args_is_help=True)
tts_app = typer.Typer(help="Playground 语音：给一段文本直接出一段语音（OpenRouter，同步）", no_args_is_help=True)
app.add_typer(subject_app, name="subject")
subject_app.add_typer(subject_image_app, name="image")
app.add_typer(presets_app, name="presets")
recipes_app = typer.Typer(help="视频配方：线性步骤执行器（出图批 → 贴字 → 本地合成）", no_args_is_help=True)
app.add_typer(recipes_app, name="recipes")
app.add_typer(image_app, name="image")
app.add_typer(video_app, name="video")
app.add_typer(tts_app, name="tts")
pose_app = typer.Typer(help="姿势库（pose-db-01 人偶线稿参考图）", no_args_is_help=True)
app.add_typer(pose_app, name="pose")
costume_app = typer.Typer(help="服装库（costume-db-01 服装参考图）", no_args_is_help=True)
app.add_typer(costume_app, name="costume")


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


# 模板入库的体积上限，与 API 的 PUT /subjects/<id>/template 同口径（字节）
_TEMPLATE_MAX_BYTES = 200_000

# derive 写在 yaml 头的模板溯源指纹：sha256:<hex>
_FINGERPRINT_RE = re.compile(r"sha256:([0-9a-f]{12,64})")


def _template_fingerprint(yaml_path: Path) -> str | None:
    """读 yaml 头里 derive 留下的模板 sha256 指纹（没有这一行就返回 None）。"""
    try:
        head = yaml_path.read_text(encoding="utf-8")[:2048]
    except OSError:
        return None
    match = _FINGERPRINT_RE.search(head)
    return match.group(1) if match else None


def _resolve_template(
    subject_id: str, yaml_path: Path, explicit: Optional[Path]
) -> tuple[Optional[Path], Optional[str]]:
    """找模板文件：显式 --template 优先，否则档案同目录的同名 <id>.md。

    返回 (路径, 文本)；都没有则 (None, None)——调用方负责警告。
    """
    candidates = []
    if explicit is not None:
        if not explicit.exists():
            _die(f"模板文件不存在：{explicit}")
        candidates.append(explicit)
    else:
        candidates.append(yaml_path.parent / f"{subject_id}.md")
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            _die(f"无法读取模板 {candidate}：{exc}")
        if not text.strip():
            _die(f"模板是空文件：{candidate}")
        return candidate, text
    return None, None


# ---------- 主体档案图片导入（交付包 → 运行时） ----------

def _collect_subject_images(path: Path, kind_hint: Optional[str]) -> tuple[list[tuple[str, Path]], list[str]]:
    """收集待导入的槽位图：返回 ((kind, 文件), 跳过说明)。"""
    from charvar import subject_images as subject_image_service

    hits: list[tuple[str, Path]] = []
    skipped: list[str] = []
    if path.is_dir():
        for candidate in sorted(path.iterdir()):
            if not candidate.is_file():
                continue
            kind = subject_image_service.detect_kind(candidate.name)
            if kind is None:
                skipped.append(candidate.name)
                continue
            hits.append((kind, candidate))
        if not hits:
            detail = "、".join(skipped) if skipped else "（目录为空）"
            _die(f"目录里没有可识别的槽位图（character_sheet / headshot / front / side / behind）：{detail}")
        return hits, skipped
    kind = kind_hint or subject_image_service.detect_kind(path.name)
    if kind is None:
        _die(f"无法从文件名判断槽位类型：{path.name}（用 --kind 指定）")
    return [(kind, path)], skipped


def _import_subject_images(subject_id: str, path: Path, kind_hint: Optional[str] = None) -> dict:
    """把交付包图片装进运行时：落盘 + 生成缩略图 + 建槽位行。返回导入结果。"""
    from charvar import subject_images as subject_image_service

    hits, skipped = _collect_subject_images(path, kind_hint)
    imported: list[dict] = []
    with storage.connect() as conn:
        if storage.get_subject(conn, subject_id) is None:
            _die(f"主体档案不存在：{subject_id}（先 charvar subject add 建档）")
        for kind, file_path in hits:
            try:
                slot = subject_image_service.import_image_file(conn, subject_id, kind, file_path)
            except subject_image_service.SubjectImageError as exc:
                _die(f"导入 {file_path.name} 失败：{exc}")
            imported.append({
                "kind": kind,
                "source_file": str(file_path),
                "file_path": slot.file_path,
                "thumb": str(subject_image_service.thumb_file(subject_id, kind)),
            })
    return {"imported": imported, "skipped": skipped}


# ---------- subject ----------

@subject_app.command("add")
def subject_add(
    path: Path = typer.Argument(..., exists=True, readable=True, help="主体档案 YAML/JSON 文件"),
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="同 id 覆盖"),
    template: Optional[Path] = typer.Option(None, "--template", help="通用提示词模板 .md（缺省自动找同目录同名 <id>.md）"),
    images: Optional[Path] = typer.Option(None, "--images", help="一并导入交付包图片（目录或单个文件），自动生成缩略图"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（外部 agent）"),
) -> None:
    """建档，并把通用提示词模板一起入库（模板逐字，不改写）。

    模板来源＝--template 指定的路径，或档案同目录的同名 <id>.md；都没有时只导档案并警告。
    档案与模板是两张表（subjects / subject_templates），两边都入库才算导入完整。
    --images 可同时把交付包里的槽位图装进运行时（落盘 + 缩略图 + 建槽位行）。
    """
    profile = _profile_from_file(path)
    tpl_path, tpl_text = _resolve_template(profile.id, path, template)
    if tpl_text is not None:
        size = len(tpl_text.encode("utf-8"))
        if size > _TEMPLATE_MAX_BYTES:
            _die(f"模板过大（{size} 字节，上限 {_TEMPLATE_MAX_BYTES} 字节）")

    fingerprint = _template_fingerprint(path)
    fp_mismatch = False
    if fingerprint and tpl_text is not None:
        actual = hashlib.sha256(tpl_text.encode("utf-8")).hexdigest()
        fp_mismatch = not actual.startswith(fingerprint)
        if fp_mismatch:
            typer.secho(
                f"警告：档案头的模板指纹 {fingerprint} 与 {tpl_path.name} 实际内容不符"
                f"（实际 {actual[:len(fingerprint)]}）——溯源链已断，请同步更新 yaml 头的 sha256",
                fg=typer.colors.YELLOW,
            )

    with storage.connect() as conn:
        existing = storage.get_subject(conn, profile.id)
        if existing is not None and not overwrite:
            _die(f"主体档案已存在：{profile.id}（--overwrite 覆盖）")
        had_template = storage.get_subject_template(conn, profile.id) is not None
        storage.save_subject(conn, profile, replace=True)
        if tpl_text is not None:
            storage.save_subject_template(conn, profile.id, tpl_text)

    image_result = None
    if images is not None:
        if not images.exists():
            _die(f"图片路径不存在：{images}")
        image_result = _import_subject_images(profile.id, images)

    if json_out:
        payload = {
            "subject_id": profile.id,
            "name": profile.name,
            "template_path": str(tpl_path) if tpl_path else None,
            "template_chars": len(tpl_text) if tpl_text else 0,
            "template_updated": tpl_text is not None,
            "fingerprint_mismatch": fp_mismatch,
        }
        if image_result is not None:
            payload["images"] = image_result
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(f"已保存主体档案：{profile.id}（{profile.name}）")
    if tpl_text is not None:
        typer.echo(f"  模板（{len(tpl_text)} 字符，逐字）→ {tpl_path}")
    else:
        message = f"  警告：没有找到模板（{profile.id}.md 不存在），只导入了档案"
        if had_template:
            message += "；DB 里原有的模板保持不变，可能已过期"
        message += "（模板可用 --template 指定，或 PUT /subjects/<id>/template 上传）"
        typer.secho(message, fg=typer.colors.YELLOW)
    if image_result is not None:
        for item in image_result["imported"]:
            typer.echo(f"  图片 {item['kind']} → {item['file_path']}（缩略图 {item['thumb']}）")
        if image_result["skipped"]:
            typer.echo(f"  跳过（非槽位图）：{'、'.join(image_result['skipped'])}")


@subject_image_app.command("import")
def subject_image_import(
    path: Path = typer.Argument(..., exists=True, readable=True, help="图片文件或交付包目录"),
    subject: Optional[str] = typer.Option(None, "--subject", "-s", help="主体 id（缺省取目录名）"),
    kind: Optional[str] = typer.Option(None, "--kind", help="槽位类型（单文件时缺省按文件名推断）"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（外部 agent）"),
) -> None:
    """把交付包里的槽位图装进运行时：落盘 → 生成缩略图 → 建槽位行。

    目录导入按文件名识别槽位（character_sheet / headshot / front / side / behind），
    其余文件跳过并在结果里列出。**缩略图随导入一并生成**，网页立刻有预览。
    """
    subject_id = subject or (path.name if path.is_dir() else None)
    if not subject_id:
        _die("缺主体 id：用 --subject <id> 指定")
    result = _import_subject_images(subject_id, path, kind)
    if json_out:
        typer.echo(json.dumps({"subject_id": subject_id, **result}, ensure_ascii=False))
        return
    typer.echo(f"已导入 {subject_id} 的图片：{len(result['imported'])} 张")
    for item in result["imported"]:
        typer.echo(f"  {item['kind']:16s} {item['source_file']} → {item['file_path']}（缩略图 {item['thumb']}）")
    if result["skipped"]:
        typer.echo(f"  跳过（非槽位图）：{'、'.join(result['skipped'])}")


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
            # 模板全文一并入库：档案与模板是两张表，只入档案等于导入半套
            storage.save_subject_template(conn, profile.id, template_md)
        typer.echo(f"已入库档案：{profile.id}")
        typer.echo(f"  模板（{len(template_md)} 字符，逐字）→ subject_templates")

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


@subject_app.command("prompt")
def subject_prompt(
    kind: Optional[str] = typer.Argument(
        None, help="槽位类型 character_sheet/headshot/front/side/behind；与 --all 二选一"
    ),
    all_kinds: bool = typer.Option(False, "--all", help="起草全部 5 个槽位（5 次独立 LLM 调用）"),
    material: Optional[str] = typer.Option(
        None, "--material", help="自定义参考资料原文：一段角色描述、character_sheet 文生图提示词、任何资料"
    ),
    material_file: Optional[str] = typer.Option(
        None, "--material-file", help="从文件读参考资料（- 读 stdin）；与 --material 二选一"
    ),
    subject_ref: Optional[str] = typer.Option(
        None, "--subject", help="主体档案：YAML 路径或已入库 id（可选，给了拼【角色档案】块加权）"
    ),
    template: Optional[Path] = typer.Option(
        None, "--template", help="「角色全场景通用提示词模板」.md（缺省链：显式 > 档案同目录同名 <id>.md > DB 模板）"
    ),
    lang: str = typer.Option("any", "--lang", help="zh=强制中文 / en=强制英文 / any=不写语言规定（缺省，输出跟随材料语言）"),
    apply: bool = typer.Option(False, "--apply", help="起草完写回 DB 槽位草稿（只写 prompt 不动状态机；需主体已入库）"),
    out: Optional[Path] = typer.Option(None, "--out", help="写文件：单槽=文件路径；--all=目录（落 <kind>.txt）"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（外部 agent）"),
) -> None:
    """起草主体档案 5 槽位的图片提示词（网页「AI 起草」同一条链路，纯文本 LLM 调用）。

    没有主体档案和模板也能用：--material 给一段自定义参考资料即可起草。
    headshot/front/side/behind 的草稿天然含 <image1>——出图时把 character_sheet 成品图
    `--ref` 进去（ComfyUI 自动切编辑 workflow / OpenRouter 走 i2i 组）。
    """
    from charvar import llm as llm_service
    from charvar import settings_service
    from charvar import subject_images as subject_image_service
    from charvar.llm_prompts import (
        DRAFT_SCHEMA,
        _TEMPLATE_MAX_CHARS,
        draft_subject_image_prompt_messages,
    )

    if lang not in ("zh", "en", "any"):
        _die(f"lang 只支持 zh/en/any，收到：{lang}")
    if kind is None and not all_kinds:
        _die("缺槽位类型：给 <kind>（character_sheet/headshot/front/side/behind）或 --all")
    if kind is not None and all_kinds:
        _die("<kind> 与 --all 二选一")
    if material and material_file is not None:
        _die("--material 与 --material-file 二选一")

    material_text: Optional[str] = material
    if material_file is not None:
        if material_file == "-":
            material_text = sys.stdin.read()
        else:
            mf = Path(material_file)
            if not mf.exists():
                _die(f"参考资料文件不存在：{mf}")
            try:
                material_text = mf.read_text(encoding="utf-8")
            except OSError as exc:
                _die(f"无法读取参考资料 {mf}：{exc}")

    kinds: list[str]
    if all_kinds:
        kinds = list(subject_image_service.IMAGE_KINDS)
    else:
        try:
            subject_image_service._validate_kind(kind or "")
        except subject_image_service.SubjectImageError as exc:
            _die(str(exc))
        kinds = [kind or ""]

    profile: Optional[SubjectProfile] = None
    yaml_path: Optional[Path] = None
    if subject_ref is not None:
        profile = _resolve_subject(subject_ref)
        probe = Path(subject_ref)
        if probe.exists() and probe.suffix in (".yaml", ".yml", ".json"):
            yaml_path = probe

    # 模板解析链：显式 --template > 档案同目录同名 <id>.md > DB 模板；都没有 → 空
    template_text: Optional[str] = None
    template_source = ""
    if template is not None:
        if not template.exists():
            _die(f"模板文件不存在：{template}")
        template_text = template.read_text(encoding="utf-8")
        if not template_text.strip():
            _die(f"模板是空文件：{template}")
        template_source = str(template)
    else:
        if yaml_path is not None and profile is not None:
            sibling = yaml_path.parent / f"{profile.id}.md"
            if sibling.exists():
                candidate = sibling.read_text(encoding="utf-8")
                if candidate.strip():
                    template_text, template_source = candidate, str(sibling)
        if template_text is None and profile is not None:
            with storage.connect() as conn:
                db_template = storage.get_subject_template(conn, profile.id)
            if db_template and db_template.strip():
                template_text, template_source = db_template, "DB subject_templates"

    if material_text is None and subject_ref is None and template_text is None:
        _die("没有可用的起草材料：--material/--material-file、--subject、--template 至少给一个")
    if template_text and len(template_text) > _TEMPLATE_MAX_CHARS:
        typer.secho(
            f"警告：模板 {len(template_text)} 字符超过起草输入上限 {_TEMPLATE_MAX_CHARS}，"
            "超出部分 LLM 看不到（与网页/入库行为一致）",
            fg=typer.colors.YELLOW,
            err=True,
        )
    # --json 是机器模式：stdout 只有 JSON，状态行全不发（模板过长的警告除外）
    if template_source and not json_out:
        typer.secho(f"模板来源：{template_source}（{len(template_text or '')} 字符）", fg=typer.colors.CYAN, err=True)

    if apply:
        if profile is None:
            _die("--apply 需要主体：用 --subject 给已入库 id 或 YAML 路径")
        with storage.connect() as conn:
            if storage.get_subject(conn, profile.id) is None:
                _die(f"主体 {profile.id} 未入库，--apply 前先 charvar subject add <yaml>")

    with storage.connect() as conn:
        config = settings_service.llm_overrides(conn)

    results: list[dict] = []
    for k in kinds:
        messages = draft_subject_image_prompt_messages(
            profile, k, template_text, material=material_text, language=lang,
        )
        try:
            data = llm_service.chat_json(
                messages, config=config, schema=DRAFT_SCHEMA, schema_name="subject_image_prompt", max_tokens=None,
            )
        except llm_service.LLMError as exc:
            _die(f"LLM 起草失败（{k}）：{exc}")
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            _die(f"LLM 未返回提示词（{k}）")
        results.append({"kind": k, "prompt": prompt, "db_saved": False})
        if not json_out:
            typer.secho(
                f"已起草 {subject_image_service.KIND_LABELS.get(k, k)}（{k}）：{len(prompt)} 字符",
                fg=typer.colors.GREEN, err=True,
            )

    if apply and profile is not None:
        with storage.connect() as conn:
            for r in results:
                subject_image_service.save_subject_prompt(conn, profile.id, r["kind"], r["prompt"])
                r["db_saved"] = True
        if not json_out:
            typer.secho(f"已写回 DB 槽位草稿：{profile.id} 的 {len(results)} 个槽位", err=True)

    if json_out:
        if all_kinds:
            payload: dict[str, object] = {"language": lang, "items": results}
        else:
            payload = {**results[0], "language": lang}
        if profile is not None:
            payload["subject_id"] = profile.id
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return

    if out is not None:
        if all_kinds:
            if out.exists() and not out.is_dir():
                _die(f"--out 是文件但 --all 需要目录：{out}")
            out.mkdir(parents=True, exist_ok=True)
            for r in results:
                (out / f"{r['kind']}.txt").write_text(r["prompt"] + "\n", encoding="utf-8")
            if not json_out:
                typer.secho(f"已写入 {len(results)} 个文件 → {out}/<kind>.txt", err=True)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(results[0]["prompt"] + "\n", encoding="utf-8")
            if not json_out:
                typer.secho(f"已写入 → {out}", err=True)
        return

    # 默认输出：单槽时 stdout 只放提示词本身（可 | 管道接 charvar image --prompt-file -）
    if all_kinds:
        for i, r in enumerate(results):
            if i:
                typer.echo("")
            typer.echo(f"===== {r['kind']} =====")
            typer.echo(r["prompt"])
    else:
        typer.echo(results[0]["prompt"])


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
    outfit: Optional[str] = typer.Option(None, "--outfit", help="服装家族名（服装库 studio 条目，如 stockings_8），与预设笛卡尔积"),
    outfit_order: str = typer.Option("outfit_first", "--outfit-order", help="组合顺序：outfit_first（默认，每件服装连播全部动作）/pose_first"),
    hairstyle: Optional[str] = typer.Option(None, "--hairstyle", help="发型预设名（presets/hairstyles/），与预设笛卡尔积；层级 动作→发型→服装"),
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
    if hairstyle or outfit:
        from charvar.sticker_policy import is_sticker_preset

        if is_sticker_preset(preset_obj):
            _die("sticker 预设（带字表情包）不支持服装/发型组合：图片文案与服装段语义冲突")
        try:
            # 层级固定 动作→发型→服装：发型层先并、服装层后并
            if hairstyle:
                hairstyle_obj = hairstyle_service.load_hairstyle(hairstyle)
                preset_obj = hairstyle_service.combine(preset_obj, hairstyle_obj)
            if outfit:
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


# ---------- tts（Playground 语音） ----------

def _run_tts(req, json_out: bool) -> None:
    """跑一次 Playground 语音合成并输出结果（--json 时 stdout 只有 JSON）。"""
    from charvar import adhoc_tts as adhoc_tts_service

    try:
        result = adhoc_tts_service.generate(req)
    except adhoc_tts_service.AdhocTtsError as exc:
        _die(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - 网络/后端异常转友好提示
        _die(f"{type(exc).__name__}: {exc}")
        return
    if json_out:
        typer.echo(result.model_dump_json())
        return
    typer.echo(f"完成：{result.audio_path}")
    typer.echo(f"  模型：{result.model or ''}  voice：{result.voice or '（provider 默认）'}  {result.response_format or 'pcm'}")
    if result.duration_s is not None:
        typer.echo(f"  时长：{result.duration_s:.1f}s · {result.size_bytes} 字节")
    if result.cost is not None:
        typer.echo(f"  预计：${result.cost:.4f}")
    typer.echo(f"  用时：{result.duration_ms / 1000:.1f}s  manifest → {result.manifest_path}")


@tts_app.command("openrouter")
def tts_openrouter(
    prompt: Optional[str] = typer.Option(None, "--prompt", help="要朗读的文本"),
    prompt_file: Optional[str] = typer.Option(None, "--prompt-file", help="文本文件（- 表示读 stdin）"),
    voice: str = typer.Option("", "--voice", help="voice 标识（空 = 目录枚举第一项兜底；用 charvar tts models 查清单）"),
    model: Optional[str] = typer.Option(
        None, "--model", help="模型 slug（默认设置页 / OP_TTS_MODEL，兜底 heygen/heygen-voice-1-turbo:free）"
    ),
    output_format: str = typer.Option(
        "", "--format", help="输出格式 mp3/pcm（空 = 自动协商；heygen 只出 pcm、minimax 只出 mp3，给错会自动换档重试）"
    ),
    timeout: float = typer.Option(300.0, "--timeout", help="HTTP 超时秒数（默认 300）"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="另外落一份到这个路径"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """用 OpenRouter 出一段语音（文本 → 音频；同步，一次请求直接返回）。

    默认模型 heygen/heygen-voice-1-turbo:free（免费，10 个默认 voice）。
    HeyGen 系 5000 字符上限在提交前拦截；语音落盘为 wav（裸 PCM 自动封装）或 mp3。
    """
    from charvar import adhoc_tts as adhoc_tts_service

    req = adhoc_tts_service.TtsRequest(
        text=_read_prompt(prompt, prompt_file),
        backend="openrouter",
        voice=voice,
        model=model,
        response_format=output_format,
        out=out,
    )
    _run_tts(req, json_out)


@tts_app.command("models")
def tts_models(
    refresh: bool = typer.Option(False, "--refresh", help="忽略本地缓存，强制拉取最新目录"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出到终端"),
) -> None:
    """列 OpenRouter 可用的语音模型及其 voice 枚举。

    提交前先跑这个：voice 超枚举会被拒（404），枚举非空的模型 voice 必填
    （空值会自动用第一项兜底）。
    """
    from charvar import openrouter_tts

    try:
        data = openrouter_tts.list_models(force_refresh=refresh)
    except openrouter_tts.OpenRouterTtsError as exc:
        _die(f"读 OpenRouter 语音模型目录失败：{exc}")
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
        voices = [str(v) for v in (model.get("supported_voices") or []) if isinstance(v, (str, int))]
        if voices:
            shown = voices[:12]
            suffix = f"…（共 {len(voices)} 个）" if len(voices) > len(shown) else ""
            typer.echo(f"    voice：{', '.join(shown)}{suffix}")
        rate = (model.get("pricing") or {}).get("prompt")
        if rate is not None:
            try:
                per_million = float(rate) * 1_000_000
                price = "免费" if per_million == 0 else f"${per_million:.2f}/百万字符"
                typer.echo(f"    价格：{price}")
            except (TypeError, ValueError):
                pass
        typer.echo("")


# ---------- pose（姿势库） ----------

@pose_app.command("import")
def pose_import_cmd(
    source: str = typer.Argument("", help="下载目录（缺省 data/poses/pose-db-01/）"),
) -> None:
    """把磁盘下载目录灌进 pose_images 表（幂等；先跑 scripts/download_pose_db.py）。"""
    from charvar import poses as poses_service

    try:
        info = poses_service.import_catalog(storage.connect(), Path(source) if source else None)
    except poses_service.PoseError as exc:
        _die(str(exc))
    typer.echo(
        f"姿势库已导入：{info['images']} 张图 · {info['poses']} 个动作（source={info['source']}）"
    )


@pose_app.command("list")
def pose_list_cmd(
    line: str = typer.Option("", "--line", help="按系統原文过滤"),
    category: str = typer.Option("", "--category", help="按分類原文过滤"),
    query: str = typer.Option("", "--q", "--query", help="按动作名/编号/id 过滤"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """浏览姿势库（按动作分组）。"""
    from charvar import poses as poses_service

    with storage.connect() as conn:
        data = poses_service.list_poses(conn, line=line, category=category, q=query)
    if json_out:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=1))
        return
    poses = data["poses"]
    if not poses:
        _die("姿势库为空 —— 先跑 scripts/download_pose_db.py，再 charvar pose import")
    typer.echo(f"共 {len(poses)} 个动作（source={data['source']}）")
    for p in poses:
        angles = " / ".join(im["angle"] for im in p["images"])
        typer.echo(
            f"{p['number']}\t{p['pose_id']}\t[{p['line']} · {p['category'] or '-'}]"
            f" {p['title']}　视角：{angles}"
        )


@pose_app.command("action")
def pose_action_cmd(
    pose_id: str = typer.Argument(..., help="动作 id，如 full_sexy_P001"),
    angle: str = typer.Option("", "--angle", help="视角（原文，缺省正面）"),
    set_text: Optional[str] = typer.Option(
        None, "--set", help="写入/更新该动作视角的动作描述（空串 = 清掉）"
    ),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """读/写单个动作某视角的动作描述（pose_actions 表，动作×视角 双键）。"""
    from charvar import poses as poses_service

    with storage.connect() as conn:
        try:
            data = (
                poses_service.set_pose_action(conn, pose_id, angle, set_text)
                if set_text is not None
                else poses_service.pose_action(conn, pose_id, angle)
            )
        except poses_service.PoseError as exc:
            _die(str(exc))
    if json_out:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=1))
        return
    text = data["action_text"] if data["action_text"].strip() else "（空 —— 该动作这个视角还没写描述）"
    typer.echo(f"{data['pose_id']} · {data['angle']}")
    typer.echo(text)


@pose_app.command("prompt")
def pose_prompt_cmd(
    pose_id: str = typer.Argument(..., help="动作 id，如 full_sexy_P001"),
    angle: str = typer.Option("", "--angle", help="视角（原文，缺省正面）"),
    subject: Optional[str] = typer.Option(None, "--subject", help="主体档案 id（--run 必填；上半身动作用 headshot 槽位，缺则回退 front）"),
    subject2: Optional[str] = typer.Option(None, "--subject2", help="双人动作的第二主体 id（2人絡み 才有效）"),
    costume: Optional[str] = typer.Option(None, "--costume", help="服装库条目 id（如 SPR-0001；加 <image3> 服装参考，双人动作暂不支持）"),
    run: bool = typer.Option(False, "--run", help="拼好的提示词直接出图（Playground 通路）"),
    backend: str = typer.Option("comfyui", "--backend", help="comfyui / openrouter / dreamina"),
    aspect: str = typer.Option("3:4", "--aspect", help="画幅：3:4/1:1/16:9/…"),
    resolution: str = typer.Option("1k", "--resolution", help="分辨率档：1k/2k（或 OpenRouter 模型枚举）"),
    seed: int = typer.Option(0, "--seed", help="0 = 固定复现；换种子出不同图"),
    negative: Optional[str] = typer.Option(None, "--negative", help="负向提示词（空串 = 明确不用）"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="另外落一份到这个路径"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="等待出图完成（默认）"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（提示词不回显）"),
) -> None:
    """按动作描述拼 demo 结构的姿势编辑提示词并可选出图。

    <image1> = 主体档案槽位图（角色锚定：上半身动作取 headshot，缺则回退 front；
    全身/双人取 front），<image2> = 人偶线稿（姿势锚定，role=pose 语义）；
    构图句/背景句/禁令句按库线型（全身/半身/双人）取版本。
    """
    from charvar import adhoc as adhoc_service
    from charvar import costumes as costumes_service
    from charvar import paths
    from charvar import pose_prompt as pose_prompt_module
    from charvar import poses as poses_service

    with storage.connect() as conn:
        try:
            action = poses_service.pose_action(conn, pose_id, angle)
            row = conn.execute(
                "SELECT line, number, title FROM pose_images WHERE pose_id = ? AND angle = ?",
                (action["pose_id"], action["angle"]),
            ).fetchone()
            slot_label = "front"
            slot = None
            if subject:
                line = row["line"] if row is not None else ""
                slot_label, slot = poses_service.subject_slot_for_prompt(conn, subject, line)
            slot2 = storage.get_subject_image(conn, subject2, "front") if subject2 else None
            body_lock: list[str] | None = None
            if subject and costume:
                profile = storage.get_subject(conn, subject)
                body_lock = list(profile.body_lock) if profile else None
            costume_note = ""
            costume_display = "ghost"
            if costume:
                try:
                    c_row = costumes_service.resolve_ref(conn, costume)
                    costume_note = costumes_service.note_text(
                        conn, f"{c_row['source_id']}/{c_row['item_id']}"
                    )
                    costume_display = c_row["display_type"]
                except costumes_service.CostumeError as exc:
                    _die(str(exc))
        except poses_service.PoseError as exc:
            _die(str(exc))
    if row is None:
        _die("姿势库为空 —— 先跑 scripts/download_pose_db.py，再 charvar pose import")
    if not action["action_text"].strip():
        _die(
            f"该动作视角还没有动作描述：{action['pose_id']} / {action['angle']}\n"
            f"先写：charvar pose action {action['pose_id']} --angle {action['angle']} --set '动作描述'"
        )
    kind = pose_prompt_module.kind_of_line(row["line"])
    if subject2 and kind != "pair":
        _die("--subject2 只对双人（2人絡み）动作有效")
    costume_file = None
    if costume:
        if kind == "pair":
            _die("--costume 暂不支持双人（2人絡み）动作")
        try:
            costume_file = costumes_service.prepared_reference_file(costume)
        except costumes_service.CostumeError as exc:
            _die(str(exc))
    prompt = pose_prompt_module.build_prompt(
        row["line"],
        action["action_text"],
        pair_two_subjects=bool(subject2),
        costume=costume_file is not None,
        body_lock=body_lock,
        costume_keywords=costume_note or None,
        costume_display=costume_display,
    )
    if not json_out:
        typer.echo(prompt)
    if not run:
        return
    if not subject:
        _die("--run 需要 --subject（主体档案 id）")
    if slot is None or not slot.get("file_path"):
        _die(f"主体档案没有 {slot_label} 槽位图：{subject}")
    subject_file = paths.data_dir() / str(slot["file_path"])
    if not subject_file.exists():
        _die(f"主体 {slot_label} 槽位图文件缺失：{slot['file_path']}")
    pose_file = poses_service.prepared_reference_file(action["pose_id"], action["angle"])
    refs = [subject_file]
    if subject2:
        if slot2 is None or not slot2.get("file_path"):
            _die(f"主体档案没有 front 槽位图：{subject2}")
        subject_file2 = paths.data_dir() / str(slot2["file_path"])
        if not subject_file2.exists():
            _die(f"主体 front 槽位图文件缺失：{slot2['file_path']}")
        refs.append(subject_file2)
    refs.append(pose_file)
    if costume_file is not None:
        refs.append(costume_file)
    req = adhoc_service.AdhocRequest(
        prompt=prompt,
        backend=backend,
        negative_prompt=negative,
        reference_paths=refs,
        aspect_ratio=aspect,
        resolution=resolution,
        seed=seed,
        wait=wait,
        out=out,
    )
    _run_adhoc(req, json_out)


@costume_app.command("prompt")
def costume_prompt_cmd(
    item_id: str = typer.Argument(..., help="服装条目 id，如 SPR-0001"),
    subject: Optional[str] = typer.Option(None, "--subject", help="主体档案 id（--run 必填，取 front 槽位图）"),
    run: bool = typer.Option(False, "--run", help="拼好的提示词直接出图（Playground 通路）"),
    backend: str = typer.Option("comfyui", "--backend", help="comfyui / openrouter / dreamina"),
    aspect: str = typer.Option("3:4", "--aspect", help="画幅：3:4/1:1/16:9/…"),
    resolution: str = typer.Option("1k", "--resolution", help="分辨率档：1k/2k（或 OpenRouter 模型枚举）"),
    seed: int = typer.Option(0, "--seed", help="0 = 固定复现；换种子出不同图"),
    negative: Optional[str] = typer.Option(None, "--negative", help="负向提示词（空串 = 明确不用）"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="另外落一份到这个路径"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="等待出图完成（默认）"),
    json_out: bool = typer.Option(False, "--json", help="结果以 JSON 输出（提示词不回显）"),
) -> None:
    """单主体 + 服装 → 换装全身图提示词（无动作参考）。

    <image1> = 主体档案 front 槽位图（角色锚定），<image2> = 服装参考图（换装
    锚定，role=costume 语义；catalog 英文关键词拼「服装要点」注记进提示词）。
    要「主体 + 动作 + 服装」用 `charvar pose prompt --costume`。
    """
    from charvar import adhoc as adhoc_service
    from charvar import costumes as costumes_service
    from charvar import paths
    from charvar import pose_prompt as pose_prompt_module
    from charvar import poses as poses_service

    c_has_image = True
    with storage.connect() as conn:
        try:
            c_row = costumes_service.resolve_ref(conn, item_id)
            keywords = costumes_service.note_text(
                conn, f"{c_row['source_id']}/{c_row['item_id']}"
            )
            display = c_row["display_type"]
            c_has_image = bool(
                str(c_row["file_path"] or "").strip() or str(c_row["image_path"] or "").strip()
            )
        except costumes_service.CostumeError as exc:
            _die(str(exc))
    body_lock: list[str] | None = None
    slot_label, slot = "front", None
    if subject:
        with storage.connect() as conn:
            profile = storage.get_subject(conn, subject)
            slot_label, slot = poses_service.subject_slot_for_prompt(conn, subject, "")
        body_lock = list(profile.body_lock) if profile else None
    prompt = pose_prompt_module.build_costume_prompt(
        body_lock=body_lock, costume_keywords=keywords, costume_display=display
    )
    if not json_out:
        typer.echo(prompt)
    if not run:
        return
    if not subject:
        _die("--run 需要 --subject（主体档案 id）")
    if not c_has_image:
        _die(
            "这条服装是纯文本条目（无参考图）—— 图锚定换装用不了；"
            "纯文本换装走预设通路（charvar gen --outfit）"
        )
    try:
        costume_file = costumes_service.prepared_reference_file(item_id)
    except costumes_service.CostumeError as exc:
        _die(str(exc))
    if slot is None or not slot.get("file_path"):
        _die(f"主体档案没有 {slot_label} 槽位图：{subject}")
    subject_file = paths.data_dir() / str(slot["file_path"])
    if not subject_file.exists():
        _die(f"主体 {slot_label} 槽位图文件缺失：{slot['file_path']}")
    req = adhoc_service.AdhocRequest(
        prompt=prompt,
        backend=backend,
        negative_prompt=negative,
        reference_paths=[subject_file, costume_file],
        aspect_ratio=aspect,
        resolution=resolution,
        seed=seed,
        wait=wait,
        out=out,
    )
    _run_adhoc(req, json_out)


@costume_app.command("import")
def costume_import_cmd() -> None:
    """磁盘 catalog.json + display_types.json 侧车 → costume_items 索引（幂等重灌）。

    costume_prompts（用户文本层）绝不碰 —— pose_actions 同红线。缺图/魔数不符整批拒绝。
    """
    from charvar import costumes as costumes_service
    from charvar import storage

    with storage.connect() as conn:
        try:
            data = costumes_service.import_catalog(conn, costumes_service.SOURCE_ID)
        except costumes_service.CostumeError as exc:
            _die(str(exc))
    typer.echo(f"导入完成：{data['items']} 条 → costume_items（{data['source_id']}）")


@costume_app.command("import-studio")
def costume_import_studio_cmd() -> None:
    """presets/costumes/studio/ 镜像逐条 → 自建服装行（source_id=studio，flat_lay）。

    DB 是运行时真源，镜像只是重建快照：已存在整条跳过（不覆盖 DB 里改过的文本），
    缺行按镜像补齐（sort_order 家族次序在内）。镜像由 charvar costume export 维护。
    """
    from charvar import costumes as costumes_service
    from charvar import storage

    with storage.connect() as conn:
        try:
            data = costumes_service.import_studio_yaml(conn)
        except costumes_service.CostumeError as exc:
            _die(str(exc))
    typer.echo(f"自建条目：新增 {data['added']}，跳过（已存在）{data['skipped']}")


@costume_app.command("prompt-text")
def costume_prompt_text_cmd(
    ref: str = typer.Argument(..., help="条目引用（SPR-0001 或 costume-db-01/SPR-0001）"),
    set_text: Optional[str] = typer.Option(None, "--set", help="写入文本层（空串 = 清掉该条文本）"),
    json_out: bool = typer.Option(False, "--json", help="当前文本层以 JSON 输出"),
) -> None:
    """读/写一条服装的文生图提示词（costume_prompts 表；source=manual）。"""
    from charvar import costumes as costumes_service
    from charvar import storage

    with storage.connect() as conn:
        try:
            row = costumes_service.resolve_ref(conn, ref)
        except costumes_service.CostumeError as exc:
            _die(str(exc))
        source_id, item_id = row["source_id"], row["item_id"]
        if set_text is None:
            data = costumes_service.get_costume_prompt(conn, source_id, item_id)
            typer.echo(json.dumps(data, ensure_ascii=False, indent=2) if json_out else data["prompt_text"])
            return
        data = costumes_service.set_costume_prompt(conn, source_id, item_id, set_text)
    typer.echo(
        f"已更新 costume_prompts：{source_id}/{item_id}"
        f"（{len(data['prompt_text'])} 字符，source={data['prompt_source']}）"
    )


@costume_app.command("describe")
def costume_describe_cmd(
    refs: list[str] = typer.Argument(None, help="条目引用（可多个；source/item 或裸 item）"),
    limit: int = typer.Option(0, "--limit", help="本次最多处理几个（0 = 全部给出的）"),
    model: Optional[str] = typer.Option(None, "--model", help="视觉模型（缺省 = 设置页/env 的 LLM 配置）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只起稿打印，不落库"),
    overwrite: bool = typer.Option(False, "--overwrite", help="已有文本也重写"),
) -> None:
    """LLM 看图起稿服装条目的 t2i 提示词（落 costume_prompts，source=ai_draft）。

    幂等：已有文本的条目跳过；--overwrite 重写。LLM 需支持视觉。
    """
    from charvar import costume_describe
    from charvar import storage

    if not refs:
        _die("给出至少一个条目引用（如 SPR-0001）")
    picks = list(refs)
    if limit and limit < len(picks):
        picks = picks[:limit]
    with storage.connect() as conn:
        for ref in picks:
            try:
                data = costume_describe.describe_one(
                    conn, ref, model=model, dry_run=dry_run, overwrite=overwrite
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不拦批次
                typer.echo(f"[{ref}] 起稿失败：{type(exc).__name__}: {exc}")
                continue
            line = f"[{data['ref']}] {data['status']}"
            if data.get("text"):
                line += f"：{data['text'][:120]}"
            typer.echo(line)


@costume_app.command("export")
def costume_export_cmd(
    ref: str = typer.Argument(..., help="条目引用（source/item 或裸 item）"),
    out: Optional[Path] = typer.Option(None, "--out", help="输出目录（缺省 presets/costumes/<source_id>/）"),
) -> None:
    """单条服装（含文本层）快照回 presets/costumes/<source_id>/<item_id>.yaml。"""
    from charvar import costumes as costumes_service
    from charvar import storage

    with storage.connect() as conn:
        try:
            path = costumes_service.export_item(conn, ref, out)
        except costumes_service.CostumeError as exc:
            _die(str(exc))
    typer.echo(f"已导出：{path}")


@pose_app.command("describe")
def pose_describe_cmd(
    angle: str = typer.Option("正面", "--angle", help="视角（原文）"),
    pose_id: list[str] = typer.Option([], "--pose-id", help="只处理指定动作（可重复）"),
    limit: int = typer.Option(0, "--limit", help="本次最多处理几个（0 = 全部缺描述的）"),
    model: Optional[str] = typer.Option(None, "--model", help="视觉模型（缺省 = 设置页/env 的 LLM 配置）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只起稿打印，不落库"),
    overwrite: bool = typer.Option(False, "--overwrite", help="已有描述也重写（source 记 ai_draft）"),
    json_out: bool = typer.Option(False, "--json", help="汇总以 JSON 输出"),
) -> None:
    """调用项目 LLM 看缩略图批量起稿动作描述（写入 source=ai_draft）。

    方法与样板见 docs/姿势库.md「动作描述怎么写」；已写的自动跳过，
    失败的（校验不过/视觉不支持）重跑一遍续传。模型无视觉时用 --model 换。
    """
    from charvar import pose_describe

    with storage.connect() as conn:
        if pose_id:
            results = []
            for pid in pose_id:
                r = pose_describe.describe_one(
                    conn, pid, angle, model=model, dry_run=dry_run, overwrite=overwrite
                )
                head = (r.get("text") or r.get("error") or "")[:40]
                typer.echo(f"[{r['status']:>7}] {pid} · {angle} | {head}")
                results.append(r)
            summary = {
                "angle": angle,
                "done": sum(1 for r in results if r["status"] == "done"),
                "skipped": sum(1 for r in results if r["status"] == "skipped"),
                "draft": sum(1 for r in results if r["status"] == "draft"),
                "errors": sum(1 for r in results if r["status"] == "error"),
                "results": results,
            }
        else:
            try:
                summary = pose_describe.describe_batch(
                    conn, angle=angle, limit=limit, model=model, dry_run=dry_run, overwrite=overwrite
                )
            except Exception as exc:  # noqa: BLE001 - key/网络统一转友好提示
                _die(f"{type(exc).__name__}: {exc}")
    typer.echo(
        f"汇总：{summary['angle']}  完成 {summary['done']} · 跳过 {summary['skipped']}"
        f" · 试稿 {summary['draft']} · 失败 {summary['errors']}"
    )
    if json_out:
        typer.echo(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))


def main() -> None:
    """console script 入口（pyproject.toml 声明 charvar.cli:main）。"""
    app()


if __name__ == "__main__":
    main()
