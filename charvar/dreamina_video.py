"""即梦（Dreamina）视频客户端：底层是官方 ``dreamina-canvas`` 画布 CLI。

与生图（``charvar.dreamina_image``）共用画布基座（``charvar.dreamina_canvas``
的子进程 / 异常族 / 静态模型目录）。一次视频 = 一张画布：参考图/首帧/
参考视频先 ``resource upload`` 登记，``node create video`` 按 mode 分派：
纯文生 ``t2v``、首帧（±尾帧）``first_last_frame``（按顺序 [首帧, 尾帧]），
参考图/视频 ``m2v``（全能参考）。建节点后走 报价 → 批准 → 运行，submitId
即作业 id（web 续查键）。

与旧 ``dreamina`` CLI 差异（canvas 目录 1.3.0 实测）：
- 分辨率档每模型独立且**大小写不一**（seedance 系小写 ``720p``、happyhorse/
  wan 大写 ``720P``）—— 提交前按目录大小写不敏感匹配、透传目录字面值；
- ratio 全模型必填（缺省 16:9），2.0 系 6 档、happyhorse/wan 9 档；
- 旧 2.5 的「图生模式拒收显式 ratio」「legacy 模型 ratio 与首帧一致」约束
  在 canvas 目录里已不存在 —— 不再做提交前拦截，交上游裁决；
- m2v 参考上限按模型（2.0 系 图≤9/视频≤3 合计≤12，2.5 ≤50，wan ≤20，
  happyhorse 只收图 ≤9）；
- 无 seed：请求里的 seed 只记 manifest，绝不进命令行；
- 视频 prompt 无目录级长度上限（不预检，上游报错后再校准）。
"""
from __future__ import annotations

import json
import logging
import math
import time
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
    normalize_video_model,
    normalize_resolution as _canonical_resolution,
)
from charvar.dreamina_image import _env, query as _image_query

logger = logging.getLogger(__name__)

# 视频通路别名：与生图同一套画布异常族
DreaminaVideoError = DreaminaImageError

_VIDEO_DEFAULT_RESOLUTION = "720p"  # 缺省档（各模型枚举大小写以目录为准）
_DEFAULT_VIDEO_MODEL = dreamina_canvas._DEFAULT_VIDEO_MODEL  # seedance_2.0_fast_vip
_DEFAULT_DURATION = 5
_DEFAULT_TIMEOUT_S = 900.0
_POLL_INTERVAL_S = 8.0  # web 每 8s 打一次 refresh（见 /videos/{id}/status 约定）

# ratio 兼容集（旧调用面的全局 6 档集合；per-model 枚举在 _VIDEO_MODELS）
_VIDEO_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

__all__ = [
    "DreaminaAuthError", "DreaminaCliError", "DreaminaImageError", "DreaminaParamError",
    "DreaminaTaskError", "DreaminaTimeoutError", "DreaminaVideoError",
    "resolve_model", "resolve_resolution", "resolve_duration", "resolve_timeout",
    "validate_ratio", "validate_video", "explain_fail", "dispatch_mode",
    "submit", "query", "task_status", "poll", "wait_for_video",
    "extract_video_paths", "video_file_ext", "model_list", "submit_id_of",
]


# ---------- 配置 ----------


def video_entry(model: str) -> dict[str, Any]:
    resolved = normalize_video_model(model)
    entry = dreamina_canvas._VIDEO_MODELS.get(resolved)
    if entry is None:
        raise DreaminaParamError(f"即梦画布没有视频模型 {model}")
    return entry


def resolve_model(model: str | None = None) -> str:
    """生效模型 id：显式传入 > env ``DREAMINA_VIDEO_MODEL`` > 默认档。

    旧 CLI 名（seedance2.0fast_vip…）经 LEGACY_ALIAS 映射；与生图不同点不在：
    charvar 约定视频模型必须显式可解析（映射不到回落默认并告警由调用方处理）。
    """
    return normalize_video_model(model if model else _env("DREAMINA_VIDEO_MODEL") or None)


def resolve_resolution(resolution: str | None = None) -> str:
    """生效分辨率档：显式传入 > env > 720p（大小写校验在 validate_video 按模型）。"""
    candidate = str(resolution or "").strip()
    if candidate:
        return candidate
    return _env("DREAMINA_VIDEO_RESOLUTION") or _VIDEO_DEFAULT_RESOLUTION


def resolve_duration(duration: int | None = None) -> int:
    if duration is not None:
        return int(duration)
    raw = _env("DREAMINA_VIDEO_DURATION")
    try:
        return int(float(raw)) if raw else _DEFAULT_DURATION
    except ValueError:
        return _DEFAULT_DURATION


def resolve_timeout(timeout_s: float | None = None) -> float:
    """总等待预算：env ``DREAMINA_VIDEO_TIMEOUT`` 或 900s（视频分钟级）。"""
    if timeout_s is not None:
        return float(timeout_s)
    raw = _env("DREAMINA_VIDEO_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


# ---------- 提交前校验 ----------


def validate_ratio(ratio: str | None, *, model: str | None = None) -> str:
    """比例校验：per-model 枚举（2.0 系 6 档 / happyhorse/wan 9 档）。"""
    value = str(ratio or "").strip()
    if not value:
        return value
    allowed = _VIDEO_RATIOS
    if model:
        allowed = video_entry(model)["ratios"]
    if value not in allowed:
        raise DreaminaParamError(
            f"即梦视频画幅只支持 {'/'.join(allowed)}，当前：{value}"
        )
    return value


def validate_video(
    model: str, resolution: str, duration: int | None, mode: str, *, ratio: str = ""
) -> str:
    """模型 × 分辨率 × 时长 × 模式 组合校验（canvas 静态目录）。

    分辨率大小写不敏感匹配目录字面值（seedance 小写 720p、happyhorse/wan
    大写 720P）；显式给了目录外的档直接报错。
    """
    if not resolution:
        raise DreaminaParamError("resolution 不能为空（即梦视频每档模型必填分辨率）")
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            raise DreaminaParamError(f"时长必须是正整数秒，当前：{duration!r}")
    entry = video_entry(model)
    resolution = _canonical_resolution(resolution, entry)
    if resolution not in entry["resolutions"]:
        raise DreaminaParamError(
            f"模型 {entry['label']} 的分辨率档只支持 {'/'.join(entry['resolutions'])}，当前：{resolution}"
        )
    if duration is not None:
        lo, hi = entry["durations"]
        if not (lo <= duration <= hi):
            raise DreaminaParamError(f"模型 {entry['label']} 支持时长 {lo}-{hi}s，当前：{duration}s")
    allowed_modes = tuple(entry["modes"])
    if mode not in allowed_modes:
        raise DreaminaParamError(
            f"模型 {entry['label']} 不支持 {mode} 模式（支持：{'/'.join(allowed_modes)}）"
        )
    if ratio and ratio not in entry["ratios"]:
        raise DreaminaParamError(
            f"模型 {entry['label']} 的画幅只支持 {'/'.join(entry['ratios'])}，当前：{ratio}"
        )
    return resolution


def explain_fail(reason: str) -> str:
    """失败原因翻译：首用合规确认提示网页端先行；其余原样返回。"""
    text = str(reason or "").strip()
    if "AigcComplianceConfirmationRequired" in text:
        return (
            f"{text} —— 该模型首次使用需先在即梦 Web 端完成一次生成（合规确认）："
            "请用同一账号在 jimeng.jianying.com 网页端手动用一次该模型，再回来重试"
        )
    return text


def dispatch_mode(
    *,
    first_frame: Path | None = None,
    last_frame: Path | None = None,
    reference_paths: list[Path] | None = None,
    reference_video_paths: list[Path] | None = None,
) -> str:
    """按输入选 mode：首帧（±尾帧）→ first_last_frame；参考图/视频 → m2v；都没有 → t2v。

    首帧与参考图/参考视频互斥（即梦 i2v 语义只收一帧，宁可拦下不静默丢弃）；
    尾帧单独给（无首帧）也报错 —— first_last_frame 按顺序 [首, 尾]。
    """
    if first_frame is not None or last_frame is not None:
        if last_frame is not None and first_frame is None:
            raise DreaminaParamError(
                "即梦首尾帧模式必须同时给首帧（按顺序 首帧→尾帧）；只有尾帧请去掉或带上首帧"
            )
        if reference_paths or reference_video_paths:
            raise DreaminaParamError(
                "即梦首帧与参考图/参考视频互斥：参考素材请留空，或去掉首帧改走全能参考"
            )
        return "first_last_frame"
    if reference_paths or reference_video_paths:
        return "m2v"
    return "t2v"


# ---------- 提交与查询 ----------


def _resource_timeout() -> float:
    raw = _env("DREAMINA_RESOURCE_TIMEOUT")
    try:
        return float(raw) if raw else 120.0
    except ValueError:
        return 120.0


def _ref_ids_for(runner: CanvasRunner, paths: list[Path], human: str) -> list[str]:
    ref_ids = []
    for path in paths:
        resolved = Path(path).expanduser()
        if not resolved.is_file():
            raise DreaminaParamError(f"{human}不存在：{resolved}")
        rid = runner.upload(resolved)
        runner.wait_resource(rid, timeout_s=_resource_timeout())
        ref_ids.append(rid)
    return ref_ids


def _validate_m2v_refs(
    model: str, *,
    reference_paths: list[Path] | None,
    reference_video_paths: list[Path] | None,
) -> None:
    entry = video_entry(model)
    caps = entry["m2v_ref"]
    n_image = len(reference_paths or [])
    n_video = len(reference_video_paths or [])
    total = n_image + n_video
    if total == 0:
        raise DreaminaParamError(f"模型 {entry['label']} 的 m2v 模式至少要 1 张参考图或 1 段参考视频")
    if n_image > caps["image"] or n_video > caps["video"] or total > caps["total"]:
        raise DreaminaParamError(
            f"模型 {entry['label']} 的全能参考上限：图 ≤{caps['image']}、视频 ≤{caps['video']}、"
            f"合计 ≤{caps['total']}；当前图 {n_image} 张、视频 {n_video} 段"
        )


def _infer_ratio_from_frame(first_frame: Path, ratios: tuple[str, ...]) -> str:
    """首帧模式缺省画幅 → 从首帧图挑最接近的档（旧 CLI 的「按首帧推断」语义）。"""
    try:
        from PIL import Image

        width, height = Image.open(Path(first_frame).expanduser()).size
    except Exception:  # noqa: BLE001 - 打不开就交缺省，别拦合法请求
        return "16:9"
    if width <= 0 or height <= 0:
        return "16:9"
    target = width / height

    def gap(candidate: str) -> float:
        w, h = str(candidate).split(":")
        value = float(w) / float(h)
        return abs(math.log(target / value))

    return min(ratios, key=gap)


def submit(
    *,
    prompt: str,
    model: str,
    resolution: str,
    duration: int | None = None,
    ratio: str | None = None,
    first_frame: Path | None = None,
    last_frame: Path | None = None,
    reference_paths: list[Path] | None = None,
    reference_video_paths: list[Path] | None = None,
    poll_s: float = 0,
    runner: CanvasRunner | None = None,
    canvas_name: str | None = None,
) -> dict:
    """提交视频作业（画布全链到 node run），返回任务 dict。

    返回形态与旧通路同形：``submit_id`` / ``gen_status``（querying=已受理），
    另加 ``canvas``={project_id, web_url, node_id}（caller 落 manifest，
    续查/下载的必要键）。``--poll=0`` 语义照旧：只提交不等待，节奏由
    adhoc_video 的 wait/refresh 控制。
    """
    text = str(prompt or "").strip()
    if not text:
        raise DreaminaParamError("prompt 不能为空（即梦视频无目录级长度上限，上游报错后再校准）")
    mode = dispatch_mode(
        first_frame=first_frame, last_frame=last_frame,
        reference_paths=reference_paths, reference_video_paths=reference_video_paths,
    )
    model = resolve_model(model)
    resolution = validate_video(
        model, resolve_resolution(resolution), resolve_duration(duration) if duration is not None else None, mode
    )
    duration = resolve_duration(duration) if duration is not None else None
    ratios = video_entry(model)["ratios"]
    ratio = validate_ratio(ratio, model=model)
    if not ratio and mode == "first_last_frame" and first_frame is not None:
        ratio = _infer_ratio_from_frame(first_frame, ratios)  # 图生语义：画幅跟首帧走
    ratio = ratio or "16:9"
    if mode == "m2v":
        _validate_m2v_refs(model, reference_paths=reference_paths, reference_video_paths=reference_video_paths)

    runner = runner or CanvasRunner(canvas_name or f"charvar-video-{time.strftime('%Y%m%d')}-{time.time_ns() % 10**8}")
    project_id = runner.ensure_canvas()

    ref_ids: list[str] = []
    if mode == "first_last_frame":
        ref_ids = _ref_ids_for(runner, [first_frame], "首帧图")
        if last_frame is not None:
            last_ids = _ref_ids_for(runner, [last_frame], "尾帧图")
            ref_ids.extend(last_ids)
    elif mode == "m2v":
        ref_ids.extend(_ref_ids_for(runner, list(reference_paths or []), "参考图"))
        ref_ids.extend(_ref_ids_for(runner, list(reference_video_paths or []), "参考视频"))

    node_id, submit_id = runner.create_and_run(
        kind="video", model=model, mode=mode, prompt=text,
        title=f"charvar-video-{time.strftime('%H%M%S')}",
        ratio=ratio, resolution=resolution,
        ref_ids=ref_ids, duration=duration, count=1,
    )
    return {
        "submit_id": submit_id,
        "gen_status": "querying",
        "result_json": {},
        "canvas": {
            "project_id": project_id,
            "web_url": runner.web_url,
            "node_id": node_id,
            "mode": mode,
        },
    }


def task_status(task: dict) -> tuple[str, str]:
    """(gen_status, fail_reason) 元组；querying 只是受理不算终态。"""
    status = str(task.get("gen_status") or "").strip()
    reason = str(task.get("fail_reason") or "").strip()
    return status, reason


def query(
    submit_id: str,
    *,
    download_dir: Path | None = None,
    timeout_s: float = 120.0,
    project_id: str = "",
) -> dict:
    """查一次视频作业状态；成功且给 download_dir 时把产物下载进那个目录。

    状态映射 running→querying、succeeded→success、其余（failed/canceled/
    expired）→fail。project_id 必给（manifest.canvas.project_id）。
    """
    if not project_id:
        raise DreaminaParamError("画布查询需要 project_id（manifest.canvas.project_id）")
    data = _image_query(
        submit_id, download_dir=download_dir,
        timeout_s=timeout_s, project_id=project_id,
    )
    result_json = data.get("result_json") or {}
    # operation status 的 resources 是视频产物；把它挪进 videos[]（旧契约）
    videos = result_json.get("images") or []
    return {
        "submit_id": submit_id,
        "gen_status": data.get("gen_status") or "",
        "result_json": {"videos": videos},
    }


def poll(submit_id: str, *, timeout_s: float = 120.0, project_id: str = "") -> dict:
    """轮询一次作业状态（不给 download_dir：成片等到终态再单独下载）。"""
    return query(submit_id, timeout_s=timeout_s, project_id=project_id)


def wait_for_video(
    submit_id: str,
    *,
    first_result: dict | None = None,
    poll_interval: float = 5.0,
    timeout_s: float = 900.0,
    sleep: Any = time.sleep,
    timeout_error: type[Exception] | None = None,
    project_id: str = "",
) -> dict:
    """阻塞轮询到终态（success/fail）返回最终任务 JSON。

    ``sleep`` 给测试替换；``timeout_error`` 给调用方（adhoc_video）换成自己的
    类型（AdhocVideoTimeout，作业还在跑、refresh 可续）。project_id 为续查键。
    """
    if not str(submit_id or "").strip():
        raise DreaminaCliError("submit_id 不能为空")
    if not project_id:
        raise DreaminaParamError("画布轮询需要 project_id（manifest.canvas.project_id）")
    deadline = time.monotonic() + max(1.0, timeout_s)
    task = first_result if isinstance(first_result, dict) else poll(submit_id, project_id=project_id)
    while True:
        status, _reason = task_status(task)
        if status in ("success", "fail"):
            return task
        if time.monotonic() >= deadline:
            message = (
                f"即梦视频作业超时未完成（>{int(timeout_s)}s，gen_status={status or 'querying'}）。"
                f"画布作业稍后仍可续查（submit_id={submit_id}）"
            )
            if timeout_error is not None:
                raise timeout_error(message)
            raise DreaminaTimeoutError(message, submit_id=submit_id, project_id=project_id)
        sleep(max(0.5, poll_interval))
        task = poll(submit_id, project_id=project_id,
                    timeout_s=min(60.0, max(15.0, deadline - time.monotonic())))


def extract_video_paths(payload: dict) -> list[Path]:
    """从查询任务 dict 里取本地产物路径（``result_json.videos[]`` 优先兜底 images）。

    调用方拿到的 path 由 ``query/download`` 落盘返回；逐个 is_file() 校验。
    """
    payload_json = payload.get("result_json") if isinstance(payload.get("result_json"), dict) else {}
    items: list[dict] = []
    for key in ("videos", "images"):
        entries = payload_json.get(key) or []
        items = [item for item in entries if isinstance(item, dict) and str(item.get("path") or "").strip()]
        if items:
            break
    if not items:
        raise DreaminaCliError(
            f"即梦作业成功但未落盘视频文件：{json.dumps(payload, ensure_ascii=False)[:300]}"
        )
    paths = [Path(str(item["path"])).expanduser() for item in items]
    for path in paths:
        if not path.is_file():
            raise DreaminaCliError(f"即梦产物文件不存在：{path}")
    return paths


def video_file_ext(path: Path) -> str:
    """即梦产物文件 → 扩展名（按魔数嗅探，不信任落盘名）。"""
    from charvar import openrouter_video

    head = Path(path).expanduser().read_bytes()[:64]
    return openrouter_video.sniff_video_ext(head)


# ---------- CLI `charvar video models --provider dreamina` 用 ----------


def model_list() -> list[dict[str, Any]]:
    """静态模型档位表 → CLI 列表输出形态（保留 id/resolutions/durations/ratios/vip_only 键）。"""
    out: list[dict[str, Any]] = []
    for model, entry in dreamina_canvas._VIDEO_MODELS.items():
        out.append(
            {
                "id": model,
                "label": entry["label"],
                "resolutions": list(entry["resolutions"]),
                "durations": list(entry["durations"]),
                "ratios": list(entry["ratios"]),
                "modes": list(entry["modes"]),
                "m2v_ref": entry["m2v_ref"],
                "count_max": entry["count_max"],
                "vip_only": False,  # canvas 目录不回 VIP 标记（账号可用性以服务端准入为准）
            }
        )
    return out


def submit_id_of(task: dict) -> str:
    """任务 JSON → submit_id（缺了报错：它是 web 续查与 CLI 追查的唯一凭据）。"""
    value = str((task.get("items") or [{}])[0].get("submitId") or task.get("submit_id") or "").strip()
    if not value:
        raise DreaminaCliError(
            f"即梦任务没有 submit_id：{json.dumps(task, ensure_ascii=False)[:300]}"
        )
    return value
