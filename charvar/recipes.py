"""配方（presets/recipes/*.yaml）与线性步骤执行器。

配方 = 一步列表的编排声明（2026-10-24 方案定稿）：
- ``images``      主体档案 + 预设（可带服装预设笛卡尔积）建 run（classic 模式）；
                  ``push: comfyui`` 时自动提交并等待 + 同步成品（复用 exporter/assets 原语）；
- ``overlay``     贴字预设对指定 images 步的成品图贴字（PIL 后处理，落 labeled/，原图不动）；
- ``video_synth`` 本地 ffmpeg 合成（presets/video/*.yaml），可吃贴字产物（labeled/）。

大类由末步推导：末步 video_model → 生成配方（⑤期接入 kind）；其余 → 合成配方。
人工检查点：step.manual（缺省 images=False、其余 True）——执行到 pending 的
manual 步就停在 waiting；人工过目后 advance 继续。

状态全部落盘 ``data/generated/recipe_runs/<id>/manifest.json``，不建 DB 表；
出图步产生的 run 仍是普通 runs（复用现有推送/图墙/同步全套）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from charvar import paths
from charvar.preset_loader import VALID_ASPECT_RATIOS, PresetError

TEXT_FROM_VALUES = ("outfit_label", "pose_tag", "variant_id", "fixed")

# 配方 run_id 直接拼进磁盘路径：与 adhoc.run_dir 同一严格白名单（另拦 ..）
_RECIPE_RUN_ID_RE = re.compile(r"^recipe_[A-Za-z0-9_.-]+$")
_RECIPE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_RECIPE_ROOT_KEYS = frozenset(
    {"name", "type", "version", "title", "doc", "subject", "seed", "refs", "video_refs", "steps"}
)
_STEP_ROOT_KEYS = frozenset(
    {
        "kind", "label", "manual",
        "subject", "presets", "outfit_order", "seed", "count", "sampling",
        "aspect", "size", "reference_kinds", "push", "workflow", "run_id",
        "overlay", "text_from", "fixed", "input", "video_preset", "music", "use_labeled",
    }
)


class RecipeRef(BaseModel):
    """配方自带的参考图/参考视频声明（⑤期 video_model 步消费；先占位校验）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: Literal["subject", "run", "asset"] = Field(alias="from")
    slot: str | None = None       # source=subject：主体档案槽位（front/...）
    run_id: str | None = None     # source=run：历史生成原图
    path: str | None = None       # source=asset：配方素材（presets/recipes/assets/<name>/）
    label: str | None = None


class RecipeStep(BaseModel):
    """配方一步。kind 决定字段集；extra 禁止（写错字段名要显式报错）。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["images", "overlay", "video_synth"]
    label: str | None = None
    # None = 按 kind 缺省：images False（自动推进），其余 True（人工过目后继续）
    manual: bool | None = None

    # ---- images 步 ----
    subject: str | None = None
    presets: list[str] = Field(default_factory=list)  # [动作/表情预设 [, 服装预设]]
    outfit_order: Literal["outfit_first", "pose_first"] = "outfit_first"
    seed: int | None = None
    count: int | None = None
    sampling: Literal["all", "first", "random"] = "all"
    aspect: str | None = None          # 出图画幅（进 preferred_aspect_ratio + push 参数）
    size: str | None = None            # charvar 档位 1k/2k（push 时传 ComfyUI 分辨率档）
    reference_kinds: list[str] = Field(default_factory=list)  # 主体档案槽位参考图
    push: Literal["none", "comfyui"] = "none"
    workflow: str | None = None        # push 时指定 workflow；缺省按参考图自动选（设置页）
    run_id: str | None = None          # 绑定既有 run（跳过建批，复用它做后续步骤）

    # ---- overlay 步 ----
    overlay: str | None = None         # 贴字预设名（presets/overlays/）
    text_from: Literal["outfit_label", "pose_tag", "variant_id", "fixed"] = "outfit_label"
    fixed: str | None = None

    # ---- 跨步引用与合成步 ----
    input: str | None = None           # 前面步骤的 label；缺省 = 最近的 images 步
    video_preset: str | None = None
    music: str | None = None           # 合成音乐文件名覆盖（缺省 = 视频预设内置）
    use_labeled: bool = True           # 输入 run 有贴字产物时优先用 labeled/


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["recipe"] = "recipe"
    version: int = 1
    title: str = ""
    doc: str | None = None             # 配方文档（presets/recipes/<doc>，markdown）
    subject: str | None = None         # 缺省主体（步骤可覆盖）
    seed: int = 0                      # 配方级种子（步骤可覆盖）
    refs: list[RecipeRef] = Field(default_factory=list)
    video_refs: list[RecipeRef] = Field(default_factory=list)
    steps: list[RecipeStep] = Field(min_length=1)


class StepState(BaseModel):
    """manifest 里一步的状态。"""

    index: int
    kind: str
    label: str | None = None
    manual: bool = False
    status: Literal["pending", "done", "failed"] = "pending"
    note: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)


class RecipeRunState(BaseModel):
    """配方运行的落盘状态（data/generated/recipe_runs/<id>/manifest.json）。"""

    recipe: str
    title: str = ""
    recipe_run_id: str
    created_at: str
    seed: int = 0
    status: Literal["waiting", "running", "done", "failed"] = "running"
    steps: list[StepState] = Field(default_factory=list)


class _GateStop(Exception):
    """消耗步的前置不满足（成品没齐/后端不可达）→ 当前轮停在 waiting 而不是 failed。"""


# ---------- 配方定义：装载与校验 ----------

def recipes_dir() -> Path:
    return paths.presets_dir() / "recipes"


def resolve_recipe_path(name: str) -> Path:
    base = recipes_dir()
    if "/" in name or "\\" in name or name in (".", ".."):
        raise PresetError(f"配方名不合法：{name!r}")
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"
    return base / name


def category_of(recipe: Recipe) -> str:
    """大类由末步推导：video_model 步在列 → 生成配方；其余 → 合成配方。"""
    kinds = [step.kind for step in recipe.steps]
    if not kinds:
        raise PresetError(f"配方 {recipe.name} 没有任何步骤")
    return "model" if kinds[-1] == "video_model" else "synthesis"


def _validate_recipe(recipe: Recipe, path: Path) -> None:
    if not _RECIPE_NAME_RE.match(recipe.name):
        raise PresetError(
            f"配方 {path.name} 的 name 非法：{recipe.name!r}"
            "（只允许字母/数字/下划线/连字符，且以字母或数字开头）"
        )
    labels: list[str] = []
    kinds_seen: set[str] = set()
    for i, step in enumerate(recipe.steps):
        label = step.label or f"step{i}"
        step.label = label
        if label in labels:
            raise PresetError(f"配方 {path.name} 的步骤 label 重复：{label}（input 按 label 引用，必须唯一）")
        labels.append(label)
        if step.kind in ("overlay", "video_synth"):
            if "images" not in kinds_seen:
                raise PresetError(f"配方 {path.name} 的 {label} 需要 input 前置 labels 步骤（本步之前没有 images 步骤）")
        if step.kind == "images":
            if not step.run_id and not step.presets:
                raise PresetError(f"配方 {path.name} 的 {label}：presets 必填（或直接绑定 run_id）")
        if step.aspect is not None and step.aspect not in VALID_ASPECT_RATIOS:
            raise PresetError(
                f"配方 {path.name} 的 {label} aspect 非法：{step.aspect!r}"
                f"（只支持 {'/'.join(VALID_ASPECT_RATIOS)}，YAML 里记得加引号）"
            )
        if step.size is not None and step.size not in ("1k", "2k"):
            raise PresetError(f"配方 {path.name} 的 {label} size 只支持 1k/2k，当前 {step.size!r}")
        if step.kind == "overlay" and not step.overlay:
            raise PresetError(f"配方 {path.name} 的 {label}：overlay 必填（贴字预设名）")
        if step.kind == "video_synth" and not step.video_preset:
            raise PresetError(f"配方 {path.name} 的 {label}：video_preset 必填（presets/video/ 预设名）")
        kinds_seen.add(step.kind)
    # ref 占位：声明但不校验深语义（消费方在⑤期；这里挡非法引用形态）
    for ref in [*recipe.refs, *recipe.video_refs]:
        if ref.source == "subject" and not ref.slot:
            raise PresetError(f"配方 {path.name} 的 refs 里有 source=subject 但缺 slot")
        if ref.source == "run" and not ref.run_id:
            raise PresetError(f"配方 {path.name} 的 refs 里有 source=run 但缺 run_id")
        if ref.source == "asset" and not ref.path:
            raise PresetError(f"配方 {path.name} 的 refs 里有 source=asset 但缺 path")


def parse_recipe_file(path: Path) -> Recipe:
    if not path.exists():
        raise PresetError(f"配方文件不存在：{path}", status=404)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise PresetError(f"配方 {path} 不是合法 YAML 映射")
    unknown = sorted(set(raw) - _RECIPE_ROOT_KEYS)
    if unknown:
        raise PresetError(f"配方 {path.name} 有未知根字段：{unknown}")
    if raw.get("type") != "recipe":
        raise PresetError(f"配方 {path.name} 的 type 必须是 recipe，当前 {raw.get('type')!r}")
    for i, step in enumerate(raw.get("steps") or []):
        if isinstance(step, dict):
            unknown = sorted(set(step) - _STEP_ROOT_KEYS)
            if unknown:
                raise PresetError(f"配方 {path.name} steps[{i}] 有未知字段：{unknown}")
    try:
        recipe = Recipe.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise PresetError(f"配方 {path} 校验失败：\n{exc}") from exc
    _validate_recipe(recipe, path)
    return recipe


def load_recipe(name: str) -> Recipe:
    return parse_recipe_file(resolve_recipe_path(name))


def read_doc(recipe_name: str) -> str | None:
    """读取配方文档 markdown（缺文件返回 None，不阻塞配方使用）。"""
    recipe = load_recipe(recipe_name)
    if not recipe.doc:
        return None
    path = recipes_dir() / recipe.doc
    if "/" in recipe.doc or "\\" in recipe.doc or ".." in recipe.doc:
        raise PresetError(f"配方文档路径不合法：{recipe.doc!r}")
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def list_recipes() -> list[dict[str, Any]]:
    base = recipes_dir()
    result: list[dict[str, Any]] = []
    if not base.exists():
        return result
    for path in sorted(base.glob("*.yaml")):
        try:
            recipe = parse_recipe_file(path)
        except PresetError as exc:
            result.append({"name": path.stem, "title": "", "category": "", "error": str(exc), "steps": []})
            continue
        result.append(
            {
                "name": recipe.name,
                "title": recipe.title,
                "category": category_of(recipe),
                "version": recipe.version,
                "subject": recipe.subject,
                "steps": [
                    {"index": i, "label": s.label, "kind": s.kind, "manual": manual_default(s)}
                    for i, s in enumerate(recipe.steps)
                ],
            }
        )
    return result


# ---------- 配方运行：状态落盘与执行器 ----------

def recipe_runs_root() -> Path:
    return paths.data_dir() / "generated" / "recipe_runs"


def recipe_run_dir(run_id: str) -> Path:
    rid = str(run_id)
    if not _RECIPE_RUN_ID_RE.match(rid) or ".." in rid:
        raise PresetError(f"配方 run_id 不合法：{rid!r}")
    return recipe_runs_root() / rid


def manifest_path(run_id: str) -> Path:
    return recipe_run_dir(run_id) / "manifest.json"


def manual_default(step: RecipeStep) -> bool:
    return step.manual if step.manual is not None else step.kind != "images"


def read_state(run_id: str) -> RecipeRunState:
    path = manifest_path(run_id)
    if not path.exists():
        raise PresetError(f"配方运行不存在：{run_id}", status=404)
    try:
        return RecipeRunState.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise PresetError(f"配方运行 {run_id} 的 manifest 损坏：{exc}") from exc


def write_state(state: RecipeRunState) -> Path:
    path = manifest_path(state.recipe_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def list_recipe_runs() -> list[dict[str, Any]]:
    root = recipe_runs_root()
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            state = RecipeRunState.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        out.append({"recipe_run_id": state.recipe_run_id, "recipe": state.recipe, "title": state.title,
                    "seed": state.seed, "status": state.status, "created_at": state.created_at})
    return out


def _new_recipe_run_id(recipe: Recipe, seed: int) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", recipe.name)
    return f"recipe_{safe_name}_s{seed}_{stamp}"


def start_run(conn, recipe_name: str, *, seed: int | None = None) -> RecipeRunState:
    """开一次配方运行：建 manifest 并自动推进到第一个 manual 步停住。"""
    recipe = load_recipe(recipe_name)
    seed = recipe.seed if seed is None else int(seed)
    run_id = _new_recipe_run_id(recipe, seed)
    state = RecipeRunState(
        recipe=recipe.name,
        title=recipe.title,
        recipe_run_id=run_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        seed=seed,
        steps=[
            StepState(index=i, kind=s.kind, label=s.label, manual=manual_default(s))
            for i, s in enumerate(recipe.steps)
        ],
    )
    advance(conn, state, recipe)
    return state


def advance_state(conn, recipe_run_id: str) -> RecipeRunState:
    """continue：批准当前 manual 检查点并推进（人工过目后调用）。"""
    state = read_state(recipe_run_id)
    recipe = load_recipe(state.recipe)
    # 配方可能已改版：manifest 与定义对不上（步数/kind）时拒绝，要求重建（不静默）
    if len(recipe.steps) != len(state.steps) or any(
        s_kind != s_state.kind for s_kind, s_state in zip((s.kind for s in recipe.steps), state.steps)
    ):
        raise PresetError(
            f"配方 {recipe.name} 与运行 {recipe_run_id} 的步骤不一致（定义已改）——重建一次配方运行"
        )
    advance(conn, state, recipe, approve_manual=True)
    return state


def _resolve_input_step(recipe: Recipe, state: RecipeRunState, step: RecipeStep, index: int) -> StepState:
    """input → 目标 images 步的 StepState；缺省 = 当前步之前最近的 images 步。"""
    wanted = step.input
    candidates = [
        s for s in state.steps
        if s.index < index and s.kind == "images" and (wanted is None or s.label == wanted)
    ]
    if wanted is None:
        if not candidates:
            raise PresetError(f"{step.label}：找不到 input（前面没有 images 步骤）")
        return candidates[-1]
    hit = next((s for s in candidates if s.label == wanted), None)
    if hit is None:
        raise PresetError(f"{step.label}：input 引用的步骤不存在或不是 images 步：{wanted!r}")
    return hit


def _images_ready(conn, run_id: str) -> tuple[int, int]:
    """(成品数, 总数)：按 original/ 落盘文件数数，不看队列记录。"""
    from charvar.assets import original_dir
    from charvar import storage

    result = storage.get_run(conn, run_id)
    if result is None:
        raise PresetError(f"生成记录不存在：{run_id}", status=404)
    done = 0
    suffixes = (".png", ".jpg", ".jpeg", ".webp")
    for item in result.items:
        if any((original_dir(run_id) / f"{item.index:06d}{suffix}").exists() for suffix in suffixes):
            done += 1
    return done, len(result.items)


def _push_comfyui(conn, result, step: RecipeStep) -> None:
    """自动提交 ComfyUI（复用 exporter 原语；配方步不支持 sticker 预设，已在校验层拦）。"""
    import httpx

    from charvar import reference_images, settings_service, storage, subject_images
    from charvar.assets import sync_run_outputs
    from charvar.exporters import comfyui as comfyui_exporter

    settings = settings_service.get_settings(conn)
    workload = step.workflow or (
        settings.comfy_edit_workflow if step.reference_kinds else settings.comfy_t2i_workflow
    )
    if not workload:
        raise _GateStop("未指定 workflow：先在设置页配置（文生图/图片编辑）workflow，或在本步写 workflow 名")
    try:
        workflow, mapping = comfyui_exporter.load_workflow_pair(workload)
    except FileNotFoundError as exc:
        raise _GateStop(f"workflow 不存在：{exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise PresetError(f"workflow/mapping 无效：{exc}") from exc

    base = paths.comfy_base_url()
    overrides: dict[str, Any] = {}
    seen: list[str] = []
    subject_dir = subject_images.subject_dir(result.subject_id)
    for kind in step.reference_kinds:
        if kind in seen:
            continue
        seen.append(kind)
        local = subject_images.find_reference_file(result.subject_id, kind)
        if local is None:
            raise _GateStop(f"参考图不存在：{kind}（先到主体档案生成/上传，或去掉本步 reference_kinds）")
        prepared = reference_images.prepared_reference(local, subject_dir, kind)
        try:
            remote = comfyui_exporter.upload_image(
                base, prepared, remote_name=f"{result.subject_id}_{kind}{prepared.suffix}"
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise _GateStop(f"上传参考图到 ComfyUI 失败：{exc}") from exc
        overrides["image" if len(seen) == 1 else f"image{len(seen)}"] = remote

    def _on_submitted(item, prompt_id: str, prefix: str) -> None:
        storage.save_comfy_submission(
            conn, result.run_id, item.index, item.variant_id, prompt_id, prefix, []
        )
        conn.commit()

    try:
        submissions = comfyui_exporter.submit(
            result,
            workflow,
            mapping,
            comfy_url=None,
            wait=True,
            aspect_ratio=None if step.aspect in (None, "auto") else step.aspect,
            resolution=step.size,
            on_submitted=_on_submitted,
        )
    except (httpx.HTTPError, OSError) as exc:
        raise _GateStop(f"ComfyUI 不可达（可手动推送后继续）：{exc}") from exc
    except ValueError as exc:
        raise PresetError(f"mapping 注入失败：{exc}") from exc
    storage.save_comfy_submissions(conn, result.run_id, submissions)
    sync_run_outputs(
        conn,
        result,
        base,
        records=storage.list_comfy_submissions(conn, result.run_id),
        build_sheets=True,
    )


def _run_images_step(conn, recipe: Recipe, state: RecipeRunState, step: RecipeStep, st: StepState) -> dict[str, Any]:
    from charvar import storage
    from charvar.composer import generate as compose_generate
    from charvar import preset_loader, sticker_policy

    # 绑定既有 run（YAML 声明 run_id，或之前已建批）：不再建批/重推；
    # push 失败且一张成品都没有时允许整体重推
    existing = step.run_id or st.outputs.get("run_id")
    if existing:
        done, total = _images_ready(conn, existing)
        if step.push == "comfyui" and done == 0:
            result = storage.get_run(conn, existing)
            _push_comfyui(conn, result, step)
            done, total = _images_ready(conn, existing)
            if done < total:
                raise _GateStop(f"成品 {done}/{total}：ComfyUI 部分未出图（可手动重推缺失项后 continue）")
            return {"run_id": existing, "images_done": done, "images_total": total}
        return {"run_id": existing, "note": f"复用既有 run（成品 {done}/{total}）"}

    subject_id = step.subject or recipe.subject
    if not subject_id:
        raise PresetError(f"{step.label}：subject 必填（或配方级 subject）")
    subject = storage.get_subject(conn, subject_id)
    if subject is None:
        raise PresetError(f"{step.label}：主体档案不在 DB：{subject_id}（先 charvar subject add / 网页建档）")
    if not step.presets:
        raise PresetError(f"{step.label}：presets 必填（或直接绑 run_id）")
    try:
        preset = preset_loader.load(step.presets[0])
    except PresetError as exc:
        raise PresetError(f"{step.label}：{exc}") from exc
    if sticker_policy.is_sticker_preset(preset):
        raise PresetError(f"{step.label}：配方出图步不做 sticker 预设（模型画字请走生成页）")
    if len(step.presets) > 1:
        from charvar import outfit as outfit_service

        outfit_obj = outfit_service.load_outfit(step.presets[1])
        preset = outfit_service.combine(preset, outfit_obj, step.outfit_order)
    if len(step.presets) > 2:
        raise PresetError(f"{step.label}：presets 最多 [动作预设, 服装预设]，收到 {len(step.presets)} 个")
    if step.aspect not in (None, "auto"):
        # 画幅覆写只动 preferred_aspect_ratio 声明（auto 推导时用它；手动选的画幅仍优先）
        preset = preset.model_copy(update={"preferred_aspect_ratio": step.aspect})
    seed = int(step.seed) if step.seed is not None else int(recipe.seed)
    from charvar.models import GenerateRequest

    req = GenerateRequest(
        subject_id=subject.id,
        subject=subject,
        preset=preset.name,
        seed=seed,
        count=step.count,
        sampling=step.sampling,
    )
    result = compose_generate(subject, preset, req)
    storage.save_run(conn, result)
    # run_id 即刻写进 step 状态（advance 会在 gate 路径 save_state）：
    # push 失败/中断后重试走「复用既有 run」分支，不重复建批。
    outputs: dict[str, Any] = {"run_id": result.run_id, "item_count": len(result.items)}
    st.outputs = dict(outputs)
    if step.push == "comfyui":
        _push_comfyui(conn, result, step)
        done, total = _images_ready(conn, result.run_id)
        outputs["images_done"] = done
        outputs["images_total"] = total
        if done < total:
            raise _GateStop(f"成品 {done}/{total}：可手动重推缺失项后 continue")
    return outputs


def _consume_gate(conn, input_step: StepState) -> dict[str, Any]:
    """消耗步统一前置：来源 images 步必须已产出 run 且成品齐。"""
    if input_step.status != "done" or not input_step.outputs.get("run_id"):
        raise _GateStop(f"来源步骤 {input_step.label} 未完成")
    done, total = _images_ready(conn, input_step.outputs["run_id"])
    if done < total:
        raise _GateStop(f"来源 run 成品 {done}/{total} 未齐：先在生成页/CLI 推送并同步，再 continue")
    return dict(input_step.outputs)


def _run_overlay_step(conn, recipe: Recipe, state: RecipeRunState, step: RecipeStep, st: StepState) -> dict[str, Any]:
    from charvar import overlay as overlay_service
    from charvar.assets import run_assets_dir

    if st.outputs.get("overlay_dir"):
        return {"note": "已贴过（重跑会覆盖 labeled/）", "overlay_dir": st.outputs["overlay_dir"]}
    source = _consume_gate(conn, _resolve_input_step(recipe, state, step, st.index))
    run_id = source["run_id"]
    info = overlay_service.apply_overlay(
        conn,
        run_id,
        overlay=str(step.overlay),
        text_from=step.text_from,
        fixed=step.fixed,
    )
    if not info["applied"]:
        raise PresetError(f"{step.label}：没有可贴字条目（{info.get('skipped')}）")
    return {
        "run_id": run_id,
        "overlay_dir": str(run_assets_dir(run_id) / "labeled"),
        "applied": len(info["applied"]),
        "skipped": [f"#{e['item_index']:03d} {e['reason']}" for e in info["skipped"]],
    }


def _run_video_synth_step(conn, recipe: Recipe, state: RecipeRunState, step: RecipeStep, st: StepState) -> dict[str, Any]:
    from charvar import storage
    from charvar import video as video_service

    if st.outputs.get("video"):
        return {"note": "已合成（重跑覆盖同名 MP4）", "video": st.outputs["video"]}
    source = _consume_gate(conn, _resolve_input_step(recipe, state, step, st.index))
    run_id = source["run_id"]
    result = storage.get_run(conn, run_id)
    records = storage.list_comfy_submissions(conn, run_id)
    preset = video_service.load_video_preset(str(step.video_preset))
    images_dir: Path | None = None
    if step.use_labeled:
        # 同一来源 run 的贴字步已就绪 → 优先吃 labeled/（不存在就回落原图）
        overlay_state = next(
            (
                s for s in state.steps
                if s.kind == "overlay" and s.status == "done"
                and s.outputs.get("run_id") == run_id
                and (Path(s.outputs["overlay_dir"]) / "000000.png").exists()
            ),
            None,
        )
        if overlay_state is not None:
            images_dir = Path(overlay_state.outputs["overlay_dir"])
    try:
        info = video_service.render_video(
            run_id,
            result,
            records,
            preset,
            music_override=step.music,
            images_dir=images_dir,
        )
    except video_service.VideoPresetError as exc:
        raise PresetError(f"{step.label}：{exc}") from exc
    return {
        "run_id": run_id,
        "video": {k: info.get(k) for k in ("name", "duration", "seconds_per_image")},
        "images_dir": str(images_dir) if images_dir else None,
    }


_HANDLERS = {
    "images": _run_images_step,
    "overlay": _run_overlay_step,
    "video_synth": _run_video_synth_step,
}


def advance(conn, state: RecipeRunState, recipe: Recipe | None = None, *, approve_manual: bool = False) -> RecipeRunState:
    """按线性顺序推进：从第一个非 done 步起顺序执行。

    - approve_manual=False（start_run）：pending 的 manual 步直接停（waiting）；
    - approve_manual=True（continue）：**批准并执行当前 manual 步**，之后的 manual
      步再停；failed 步也直接重试（修好之后 continue 重跑）；
    - 消耗步前置不满足（成品没齐/后端不可达）停在 waiting，note 写明原因。
    """
    if recipe is None:
        recipe = load_recipe(state.recipe)
    state.status = "running"
    write_state(state)  # 进入 running 即落盘：中途被杀也能看到进度与已建批的 run_id
    approved_first = False
    for st in state.steps:
        step = recipe.steps[st.index]
        if st.status == "done":
            continue
        if st.status == "pending" and st.manual and not (approve_manual and not approved_first):
            st.note = "等待人工确认（过目/筛选成品后 continue）"
            state.status = "waiting"
            write_state(state)
            return state
        approved_first = True
        if st.status == "pending":
            st.note = ""
            try:
                handler = _HANDLERS.get(step.kind)
                if handler is None:
                    raise PresetError(f"本程序还不支持 {step.kind} 步骤（后续期接入）")
                st.outputs.update(handler(conn, recipe, state, step, st))
                extra_note = str(st.outputs.get("note") or "")
                st.status = "done"
                st.note = extra_note
                write_state(state)  # 每步完成即落盘（被杀后 continue 不重复已完成步）
            except _GateStop as exc:
                st.note = str(exc)
                state.status = "waiting"
                write_state(state)
                return state
            except Exception as exc:  # noqa: BLE001
                st.status = "failed"
                st.note = str(exc)
                state.status = "failed"
                write_state(state)
                return state
    state.status = "done"
    write_state(state)
    return state