"""即梦（Dreamina）生图客户端：底层是官方 ``dreamina-canvas`` 画布 CLI。

底层从旧 ``dreamina`` CLI（text2image/image2image 直发）整体换到画布工作流：
一次生图 = 一张画布（``CanvasRunner``）→ 参考图 ``resource upload`` →
``node create`` → 报价 → 批准 → ``node run`` → `operation status` 轮询 →
``resource download``。子进程与模型目录都在 :mod:`charvar.dreamina_canvas`。

对外 API 与旧通路一致（``charvar image dreamina``、``AdhocResult.prompt_id``、
落盘与 manifest 布局不变）：``generate``/``submit``/``query``/``resolve_*``/
``validate_*``/``DreaminaGenerateResult``，异常族从 ``dreamina_canvas`` 引入
（原名再导出）。即梦不暴露 seed：请求里的 seed 只进 manifest，绝不发送。

prompt 上限按模型（canvas 目录实测 2026-11-11）：seedream 4.x=2000、
5.x/智能图片=5000、美学 V8.2=1024 —— ``validate_prompt`` 按生效模型查表。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from charvar import dreamina_canvas
from charvar.dreamina_canvas import (
    CanvasRunner,
    DreaminaAuthError,
    DreaminaCliError,
    DreaminaImageError,
    DreaminaParamError,
    DreaminaTaskError,
    DreaminaTimeoutError,
    normalize_image_model,
    normalize_resolution as _canonical_resolution,
)

logger = logging.getLogger(__name__)

# canvas 路径下的默认值：模型目录与默认档集中在 dreamina_canvas
_DEFAULT_MODEL = ""  # 空串 = 走 resolve_model 的默认链（缺省 seedream_4.7）
_DEFAULT_POLL_S = 60.0  # 兼容旧参数：首轮提交等待已由 run+status 轮询替代
_DEFAULT_TIMEOUT_S = 300.0
_QUERY_INTERVAL_S = 3.0
_RESOURCE_TIMEOUT_S = 120.0  # 单张参考图登记处理超时

__all__ = [
    "DreaminaAuthError",
    "DreaminaCliError",
    "DreaminaImageError",
    "DreaminaParamError",
    "DreaminaTaskError",
    "DreaminaTimeoutError",
    "DreaminaGenerateResult",
    "CanvasRunner",
    "resolve_binary",
    "resolve_model",
    "resolve_resolution",
    "resolve_poll",
    "resolve_timeout",
    "validate_prompt",
    "validate_ratio",
    "validate_model_resolution",
    "generate",
    "submit",
    "query",
]


@dataclass
class DreaminaGenerateResult:
    """一次即梦生图的结果。

    Fields:
        submit_id:   云端运行提交 id（node run 的 submitId）。
        gen_status:  终态（success / fail；调用方只拿得到成功或异常）。
        fail_reason: 失败原因（成功时为空）。
        images:      下载到本地的产物路径（``download_dir`` 下）。
        width/height: 上游回报的像素尺寸（node 产物元数据，拿不到时 0）。
        model / ratio / resolution: 实际发送的请求参数（manifest 用）。
        project_id / web_url: 本次使用的画布 id 与网页链接（新增字段，便于复看）。
    """

    submit_id: str
    gen_status: str = ""
    fail_reason: str = ""
    images: list[Path] = field(default_factory=list)
    width: int = 0
    height: int = 0
    model: str = ""
    ratio: str = ""
    resolution: str = ""
    project_id: str = ""
    web_url: str = ""


# ---------- 可执行文件与默认链 ----------


def resolve_binary() -> str:
    """dreamina-canvas 路径解析（DREAMINA_CLI env > PATH > ~/.local/bin）。"""
    return dreamina_canvas.resolve_binary()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def image_entry(model: str) -> dict[str, Any]:
    """按模型 id 查静态目录条目；未知模型回落默认并记一次日志。"""
    resolved = normalize_image_model(model)
    entry = dreamina_canvas._IMAGE_MODELS.get(resolved)
    if entry is None:  # normalize 里的映射已兜底，到这里只能说明表自身缺条目
        raise DreaminaParamError(f"即梦画布没有模型 {model}")
    return entry


def resolve_model(model: str | None = None) -> str:
    """生效的模型 id：显式传入 > env DREAMINA_IMAGE_MODEL > 缺省（seedream_4.7）。

    旧 CLI 的模型名（3.0/5.0/5.0Pro…）经 LEGACY_ALIAS 映射到 canvas 目录；
    映射不到回落默认。
    """
    return normalize_image_model(model if model else _env("DREAMINA_IMAGE_MODEL") or None)


def resolve_resolution(resolution: str | None = None, *, model: str | None = None) -> str:
    """生效分辨率：显式 > env DREAMINA_IMAGE_RESOLUTION > 默认 2K（表外档透传）。"""
    raw = resolution if resolution else _env("DREAMINA_IMAGE_RESOLUTION") or None
    if raw and str(raw).strip():
        return str(raw).strip()
    entry = image_entry(resolve_model(model) if model else None)
    candidates = entry["resolutions"]
    return "2K" if "2K" in candidates else candidates[0]


def resolve_poll(poll_s: float | None = None) -> float:
    if poll_s is not None:
        return float(poll_s)
    raw = _env("DREAMINA_POLL")
    try:
        return float(raw) if raw else _DEFAULT_POLL_S
    except ValueError:
        return _DEFAULT_POLL_S


def resolve_timeout(timeout_s: float | None = None) -> float:
    """总等待预算（画布运行轮询）：env DREAMINA_IMAGE_TIMEOUT 或 300s。"""
    if timeout_s is not None:
        return float(timeout_s)
    raw = _env("DREAMINA_IMAGE_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


# ---------- 校验（提交前拦截，别白扣一次费） ----------


def validate_prompt(prompt: str, *, has_reference: bool, model: str | None = None) -> str:
    """长度预检：按生效模型的 canvas 目录上限拦（超限上游硬失败）。"""
    text = str(prompt or "").strip()
    if not text:
        raise DreaminaParamError("prompt 不能为空")
    entry = image_entry(model if model else None)
    limit = int(entry["prompt_limit"])
    if len(text) > limit:
        raise DreaminaParamError(
            f"提示词 {len(text)} 字符超过即梦模型 {entry['label']} 的上限 {limit} 字符；请先压缩提示词"
        )
    return text


def validate_ratio(ratio: str | None, *, model: str | None = None) -> str:
    """比例校验：按生效模型的枚举拦；模型未定则只接受 charvar 老八档之外报错。"""
    value = str(ratio or "").strip()
    if not value:
        return value
    entry = image_entry(model if model else None)
    allowed = entry["ratios"]
    if value not in allowed:
        raise DreaminaParamError(
            f"模型 {entry['label']} 的画幅只支持 {'/'.join(allowed)}，当前：{value}"
        )
    return value


def validate_model_resolution(model: str, resolution: str, *, has_reference: bool) -> str:
    """模型 × 分辨率 × 参考图 组合校验（按 canvas 静态目录表）。

    分辨率未在模型枚举时提交前报错；带参考图的模型能力差异（i2i vs
    t2i 兼收参考）在 generate 里按 ref_max 分派 mode，不在这一步拦。
    """
    if not resolution:
        raise DreaminaParamError("resolution 不能为空（即梦画布每模型必填分辨率档）")
    entry = image_entry(model)
    allowed = entry["resolutions"]
    if resolution not in allowed:
        raise DreaminaParamError(
            f"模型 {entry['label']} 的分辨率档只支持 {'/'.join(allowed)}，当前：{resolution}"
        )
    return resolution


def validate_references(count: int, *, model: str, mode: str) -> str:
    """参考图数量按 (模型, 模式) 的上限拦；返回应该下发到节点的 mode。"""
    entry = image_entry(model)
    ref_max = entry["ref_max"]
    limit = int(ref_max.get(mode, 0))
    if count > limit and mode == "i2i" and int(ref_max.get("t2i", 0)) >= count:
        return "t2i"  # 4.6/4.7/5.0Pro/5.5 文生图也收参考图（上限更高），换道降级
    if count > limit:
        raise DreaminaParamError(
            f"模型 {entry['label']} 的{'参考图上限是 ' + str(limit) + ' 张' if limit else '参考图能力仅在 i2i'}"
            f"，当前给了 {count} 张；请裁剪参考图或换模型"
        )
    return mode


# ---------- 生图主流程 ----------


def _mode_for(model: str, reference_count: int) -> str:
    """有参考图走 i2i；模型 i2i 收不下时 t2i 兼收参考（4.6/4.7/5.0Pro/5.5）。"""
    if reference_count <= 0:
        return "t2i"
    entry = image_entry(model)
    ref_max = entry["ref_max"]
    if reference_count > int(ref_max.get("i2i", 0)) and reference_count <= int(ref_max.get("t2i", 0)):
        return "t2i"
    return "i2i"


def generate(
    *,
    prompt: str,
    download_dir: Path,
    resolution: str | None = None,
    model: str | None = None,
    ratio: str | None = None,
    reference_paths: list[Path] | None = None,
    timeout_s: float | None = None,
    poll_s: float | None = None,
    runner: CanvasRunner | None = None,
    canvas_name: str | None = None,
) -> DreaminaGenerateResult:
    """跑一次即梦生图到拿到本地产物为止（阻塞）。

    流程：建/复用画布 → 参考图上传并等登记成功 → 建节点（有参考 = i2i，
    超 i2i 上限且模型 t2i 兼收则 t2i）→ 报价 → 按报价上限自动批准 →
    运行 → `operation status` 轮询 → 产物下载。

    Args:
        prompt: 提示词（提交前按模型上限预检）。
        download_dir: 产物下载目录（必须显式给；调用方通常传 run_dir/download）。
        resolution: 分辨率档（空 = env DREAMINA_IMAGE_RESOLUTION，再缺省 2K）。
        model: canvas 模型 id（空 = env DREAMINA_IMAGE_MODEL，旧名自动映射；缺省 seedream_4.7）。
        ratio: 比例（空 = 不传，服务端用模型默认）。
        reference_paths: 参考图本地路径（预处理过的成图即传）。
        timeout_s: 总等待预算（秒，env DREAMINA_IMAGE_TIMEOUT，默认 300）。
        poll_s: 兼容旧参数（提交即时受理，此值仅控制首轮查询等待档位）。
        runner: 复用画布（批量生图共用一张画布时由调用方传入）。
        canvas_name: 新建画布的名字（缺省 charvar-image-<uuid8>）。

    Raises:
        DreaminaParamError: 提示词超限 / 枚举不合法 / 参考图数量超限 / 文件缺失。
        DreaminaAuthError: 登录态无效（提示先 dreamina-canvas auth login）。
        DreaminaTaskError: 节点终态失败 / 服务端拒绝。
        DreaminaTimeoutError: 等待超时，任务仍在云端（带 submit_id 与 project_id）。
        DreaminaCliError: CLI 缺失 / 输出解析失败。
    """
    model = resolve_model(model)
    entry = image_entry(model)
    resolution = _canonical_resolution(
        resolve_resolution(resolution, model=model), entry
    )
    resolution = validate_model_resolution(model, resolution, has_reference=bool(reference_paths))
    timeout_s = resolve_timeout(timeout_s)
    resolve_poll(poll_s)  # 兼容旧参数解析，仅吞掉调用方的值
    deadline = time.monotonic() + timeout_s

    mode = _mode_for(model, len(reference_paths or []))
    prompt = validate_prompt(prompt, has_reference=bool(reference_paths), model=model)
    ratio = validate_ratio(ratio, model=model)
    if not ratio and entry["ratio_required"]:
        ratio = "16:9"  # 服务端必填比例的模型（旧 CLI 缺省同为 16:9，保持直觉一致）
    refs = [Path(p).expanduser() for p in (reference_paths or [])]
    refs = validate_references_paths(refs)
    mode = validate_references(len(refs), model=model, mode=mode)

    runner = runner or CanvasRunner(canvas_name or f"charvar-image-{time.strftime('%Y%m%d')}-{time.time_ns() % 10**8}")
    project_id = runner.ensure_canvas()

    ref_ids: list[str] = []
    for ref in refs:
        rid = runner.upload(ref)
        runner.wait_resource(rid, timeout_s=_RESOURCE_TIMEOUT_S)
        ref_ids.append(rid)

    node_id, submit_id = runner.create_and_run(
        kind="image", model=model, mode=mode, prompt=prompt,
        title=f"charvar-image-{time.strftime('%H%M%S')}",
        ratio=ratio or None, resolution=resolution,
        ref_ids=ref_ids, count=1,
    )

    while True:
        data = runner.status(submit_id)
        state = str(data.get("state") or "")
        if state in ("succeeded", "failed", "canceled", "expired"):
            break
        if time.monotonic() >= deadline:
            raise DreaminaTimeoutError(
                f"即梦画布任务等待超时（{timeout_s:.0f}s，state={state or 'running'}）。"
                f"画布 {project_id} 里节点 {node_id} 的产物稍后仍可取",
                submit_id=submit_id,
                project_id=project_id,
            )
        time.sleep(min(_QUERY_INTERVAL_S, max(0.5, deadline - time.monotonic())))

    if state != "succeeded":
        raise DreaminaTaskError(f"即梦画布任务失败（state={state}）", submit_id=submit_id)

    images = _download_outputs(runner, node_id, submit_id, download_dir)
    first_meta = _first_image_meta(runner, node_id)
    logger.info(
        "dreamina.image_generated | submit_id=%s | canvas=%s | model=%s | mode=%s | files=%d",
        submit_id, project_id, model, mode, len(images),
    )
    return DreaminaGenerateResult(
        submit_id=submit_id,
        gen_status="success",
        images=images,
        width=first_meta.get("width", 0),
        height=first_meta.get("height", 0),
        model=model,
        ratio=ratio or "",
        resolution=resolution,
        project_id=project_id,
        web_url=runner.web_url,
    )


def validate_references_paths(refs: list[Path]) -> list[Path]:
    for ref in refs:
        if not ref.is_file():
            raise DreaminaParamError(f"参考图不存在：{ref}")
    return refs


def _first_image_meta(runner: CanvasRunner, node_id: str) -> dict[str, Any]:
    outputs = runner.node_outputs([node_id])
    for res in outputs.get(node_id) or []:
        if res.get("type") == "image" and isinstance(res.get("image"), dict):
            meta = res["image"]
            return {"width": int(meta.get("width") or 0), "height": int(meta.get("height") or 0)}
    return {}


def _download_outputs(
    runner: CanvasRunner, node_id: str, submit_id: str, download_dir: Path
) -> list[Path]:
    """把节点上属于本次提交的图片下载进 download_dir（其余成功产物也属本节点）。"""
    download_dir = Path(download_dir).expanduser()
    outputs = runner.node_outputs([node_id]).get(node_id) or []
    rid_list = [
        str(res.get("resourceId") or "")
        for res in outputs
        if res.get("type") == "image" and res.get("status") == "success"
    ]
    if not rid_list:
        raise DreaminaCliError(f"即梦画布节点成功但没有图片产物：{node_id}")
    images = []
    for rid in rid_list:
        data = runner.download(rid, download_dir)
        images.append(Path(str(data.get("path"))))
    return images


# ---------- 兼容旧调用面（submit/query；generate 是唯一主路径） ----------


def submit(
    *,
    prompt: str,
    resolution: str,
    model: str = "",
    ratio: str | None = None,
    reference_paths: list[Path] | None = None,
    poll_s: float = 0,
) -> dict:
    """提交一次生图（画布全链：建→传→建节点→闸门→运行），返回任务 dict。

    与旧 CLI 通路同形：``submit_id`` / ``gen_status``（querying=已受理）。
    运行即计费 —— 提交前校验在这里全部生效。
    """
    prompt = validate_prompt(prompt, has_reference=bool(reference_paths), model=model or None)
    ratio = validate_ratio(ratio, model=model or None)
    for path in reference_paths or []:
        if not Path(path).expanduser().is_file():
            raise DreaminaParamError(f"参考图不存在：{path}")
    runner = CanvasRunner(f"charvar-image-{time.strftime('%Y%m%d')}-{time.time_ns() % 10**8}")
    model = resolve_model(model)
    resolution = validate_model_resolution(
        model, resolve_resolution(resolution, model=model), has_reference=bool(reference_paths)
    )
    _, submit_id = runner.create_and_run(
        kind="image", model=model, mode=_mode_for(model, len(reference_paths or [])),
        prompt=prompt, title=f"charvar-image-{time.strftime('%H%M%S')}",
        ratio=ratio or None, resolution=resolution,
        ref_ids=_upload_refs(runner, reference_paths or []),
        count=1,
    )
    return {"submit_id": submit_id, "gen_status": "querying", "result_json": {}}


def _upload_refs(runner: CanvasRunner, paths: list[Path]) -> list[str]:
    ref_ids = []
    for path in paths:
        rid = runner.upload(Path(path).expanduser())
        runner.wait_resource(rid, timeout_s=_RESOURCE_TIMEOUT_S)
        ref_ids.append(rid)
    return ref_ids


def query(
    submit_id: str,
    *,
    download_dir: Path | None = None,
    timeout_s: float = 120.0,
    project_id: str = "",
) -> dict:
    """查一次画布运行状态；给 download_dir 时把产物图片下载进那个目录。

    状态映射：running→querying、succeeded→success、failed/canceled→fail。
    project_id 必给（manifest.canvas.project_id）—— 画布 id 是续查的必要键。
    下载按 operation status 回报的 resourceId（服务端 Filename 固定
    dreamina-<rid>.<ext>，后缀与内容一致才可信，调用方按魔数判型）。
    """
    if not project_id:
        raise DreaminaParamError("画布查询需要 project_id（manifest.canvas.project_id）")
    runner = CanvasRunner("(resume)", project_id=project_id)
    data = runner.status(submit_id)
    state = str(data.get("state") or "")
    gen_status = {"running": "querying", "succeeded": "success"}.get(state, "fail" if state else "")
    items: list[dict[str, Any]] = []
    if download_dir is not None and gen_status == "success":
        destination = Path(download_dir).expanduser()
        for res in data.get("resources") or []:
            rid = str(res.get("resourceId") or "")
            if not rid or str(res.get("state") or "") != "succeeded":
                continue
            downloaded = runner.download(rid, destination)
            if downloaded.get("path"):
                items.append({"path": str(downloaded["path"])})
    return {"submit_id": submit_id, "gen_status": gen_status, "result_json": {"images": items}}
