"""即梦（Dreamina）视频客户端：封装官方 ``dreamina`` CLI 的视频子命令（子进程）。

与生图（``charvar.dreamina_image``）的共用与差异：

- 直接复用生图通路的基础件：``resolve_binary`` / ``_run_cli``（含鉴权标记判型）/
  ``parse_task_output`` / ``query``（``query_result`` 与子命令无关，视频共用）/
  ``_task_status`` / 整套异常族（基类 ``DreaminaImageError``，此处按
  ``DreaminaVideoError`` 别名引用）—— 不写第二套子进程逻辑；
- 子命令按输入映射：纯文生 ``text2video``；首帧单图 ``image2video``（缺省
  ``--ratio`` 由首帧图推断，Seedance 2.0 系也可显式设）；参考图/参考视频走
  ``multimodal2video``（全能参考，原 ref2video；2.0 系图≤9/视频≤3，至少一条图
  或视频）。首尾帧（``frames2video``）与多图故事（``multiframe2video``）不接
  —— 现有网页/CLI 没有尾帧入口，请求带了尾帧直接提交前报错；
- 与生图的枚举差异（CLI -h 1.4.21 实测，CLI 升级后要同步 _VIDEO_MODELS 表）：
  ``--video_resolution`` **必填**且按模型配对（``seedance2.0fast_vip`` 恒 720p、
  ``seedance2.0_vip`` 720p/1080p/4k、``seedance2.5`` 480p/720p/1080p 且
  VIP-only）；时长按模型（2.0 系 4-15s、``seedance1.0fast`` 5-10、
  ``seedance1.5pro`` 5-12、``seedance2.5`` 4-30s）；ratio 只有 6 档
  （**没有**生图侧的 2:3/3:2）；上游对时长/分辨率/显式 ratio 严格校验、
  拒收不静默调整 —— 所以提交前必须用静态表拦；
- CLI 各子命令自己的默认模型不同（text2video 默认 seedance2.0fast、其余默认
  seedance2.0_vip）且都不符合 charvar 约定 —— charvar 必须**每次显式传**
  ``--model_version``：默认链最后一档是 ``seedance2.0fast_vip``（用户指定的
  最低价档）；
- 无 seed：请求里的 seed 只记 manifest，绝不进命令行；
- 无价格 API / 无 usage：成片的 ``cost`` 显示不了逐条预估（按即梦积分与
  会员权益计），``query_result`` 的任务 JSON 里只有 ``commerce_info`` 权益标签；
- 提示词长度上限视频侧无 CLI 文档（别抄生图的 1592/1992）—— 不做提交前长度
  拦截（只拦空串），上游 fail_reason 报超长后再按实测校准；
- 首用合规确认：部分模型首次使用需先在即梦 Web 端用该模型完成一次生成，提交会
  返回 ``AigcComplianceConfirmationRequired`` —— 按话式识别并给用户指引，
  重试救不回来。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from charvar import dreamina_image
from charvar.dreamina_image import (
    DreaminaCliError,
    DreaminaImageError,
    DreaminaParamError,
    DreaminaTaskError,
    DreaminaTimeoutError,
    parse_task_output,
)

logger = logging.getLogger(__name__)

# 视频通路的别名：基类与各子类直接沿用 dreamina_image 的异常族（同一套 CLI 基础件）
DreaminaVideoError = DreaminaImageError

# ratio：视频只有 6 档（CLI 1.4.21；**没有**生图通路的 2:3 / 3:2）
_VIDEO_RATIOS = ("1:1", "3:4", "16:9", "4:3", "9:16", "21:9")

_DEFAULT_MODEL = "seedance2.0fast_vip"
_DEFAULT_RESOLUTION = "720p"
_DEFAULT_DURATION = 5
_DEFAULT_TIMEOUT_S = 900.0

# 2.0 全系共用的子命令面（text2video/image2video/frames2video/multimodal2video）
_SEEDANCE2 = ("text2video", "image2video", "frames2video", "multimodal2video")

# model_version → 档位表（CLI -h 1.4.21；CLI 升级后同步，别只改一处）
_VIDEO_MODELS: dict[str, dict[str, Any]] = {
    "seedance2.0": {"resolutions": ("720p",), "durations": (4, 15), "subcommands": _SEEDANCE2},
    "seedance2.0fast": {"resolutions": ("720p",), "durations": (4, 15), "subcommands": _SEEDANCE2},
    "seedance2.0_vip": {
        "resolutions": ("720p", "1080p", "4k"), "durations": (4, 15), "subcommands": _SEEDANCE2,
    },
    "seedance2.0fast_vip": {"resolutions": ("720p",), "durations": (4, 15), "subcommands": _SEEDANCE2},
    "seedance2.0mini": {"resolutions": ("720p",), "durations": (4, 15), "subcommands": _SEEDANCE2},
    "seedance2.5": {
        "resolutions": ("480p", "720p", "1080p"),
        "durations": (4, 30),
        "subcommands": _SEEDANCE2,
        "vip_only": True,
        # 2.5 的参考帧模式拒收显式 --ratio（输出跟首帧走；CLI -h 明文）
        "i2v_ratio_locked": True,
        # 全能参考的输入媒体时长 2-30s（2.0 系是 2-15s，见下方缺省）
        "input_media_durations": (2, 30),
    },
    "seedance1.0fast": {
        "resolutions": ("720p",), "durations": (5, 10), "subcommands": ("image2video",),
        # legacy 模型：显式 ratio 必须与首帧一致（CLI -h 明文），按首帧推断最稳
        "ratio_match_frame": True,
    },
    "seedance1.5pro": {
        "resolutions": ("720p",), "durations": (5, 12), "subcommands": ("image2video", "frames2video"),
        "ratio_match_frame": True,
    },
}

# 首次使用需在即梦 Web 端完成一次生成的上游报错（各 -h Notes 原文词根）
_COMPLIANCE_MARKER = "AigcComplianceConfirmationRequired"


# ---------- 配置 ----------


def resolve_model(model: str | None = None) -> str:
    """生效的 model_version：显式传入 > env ``DREAMINA_VIDEO_MODEL`` > seedance2.0fast_vip。

    设置页的 DB 值由上层（settings_service.dreamina_video_config）先合并进
    ``model`` 再调这里。与生图不同：视频不回落 CLI 默认 —— CLI 各子命令的
    默认模型互不相同，charvar 必须显式传。
    """
    for candidate in (model, dreamina_image._env("DREAMINA_VIDEO_MODEL")):
        candidate = str(candidate or "").strip()
        if candidate:
            return candidate
    return _DEFAULT_MODEL


def resolve_resolution(resolution: str | None = None) -> str:
    """生效的 ``--video_resolution``：显式传入 > 720p（必填参数，没有「不发」路径）。

    统一小写归一（CLI 枚举写法 480p/720p/1080p/4k）。
    """
    candidate = str(resolution or "").strip()
    if candidate:
        return candidate.lower()
    return _DEFAULT_RESOLUTION


def resolve_duration(duration: int | None = None) -> int:
    """生效的 ``--duration``：显式传入 > 5（CLI 默认同值，但显式发避免依赖 CLI 默认）。"""
    if duration is not None:
        return int(duration)
    return _DEFAULT_DURATION


def resolve_timeout(timeout_s: float | None = None) -> float:
    """总等待预算：env ``DREAMINA_VIDEO_TIMEOUT`` 或 900s（视频分钟级，比生图长）。"""
    if timeout_s is not None:
        return float(timeout_s)
    raw = dreamina_image._env("DREAMINA_VIDEO_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


# ---------- 提交前校验 ----------


def validate_ratio(ratio: str | None) -> str:
    """比例校验：视频只有 6 档（没有生图侧的 2:3/3:2），超出在提交前报错。"""
    value = str(ratio or "").strip()
    if value and value not in _VIDEO_RATIOS:
        raise DreaminaParamError(
            f"即梦视频画幅只支持 {'/'.join(_VIDEO_RATIOS)}，当前：{value}"
        )
    return value


def validate_video(
    model: str, resolution: str, duration: int | None, subcommand: str, *, ratio: str = ""
) -> str:
    """model_version × video_resolution × duration × 子命令 组合校验（CLI -h 1.4.21）。

    --video_resolution 是即梦必填参数（缺失/空串直接拦）；表外模型（CLI 升级
    新增）不拦，交给 CLI 裁决。
    """
    if not resolution:
        raise DreaminaParamError(
            "resolution 不能为空（--video_resolution 是即梦视频必填参数）"
        )
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            raise DreaminaParamError(f"时长必须是正整数秒，当前：{duration!r}")
    entry = _VIDEO_MODELS.get(model.lower())
    if entry is None:
        return resolution
    allowed_res = tuple(entry["resolutions"])
    if resolution not in allowed_res:
        raise DreaminaParamError(
            f"模型 {model} 的分辨率档只支持 {'/'.join(allowed_res)}，当前：{resolution}"
        )
    if duration is not None:
        lo, hi = entry["durations"]
        if not (lo <= duration <= hi):
            raise DreaminaParamError(
                f"模型 {model} 支持时长 {lo}-{hi}s，当前：{duration}s"
            )
    accepted = tuple(entry["subcommands"])
    if subcommand not in accepted:
        raise DreaminaParamError(
            f"模型 {model} 不支持 {subcommand}（支持：{'/'.join(accepted)}）"
        )
    if subcommand == "image2video" and entry.get("i2v_ratio_locked") and str(ratio or "").strip():
        raise DreaminaParamError(
            f"模型 {model} 的图生视频拒收显式画幅（输出跟首帧走）："
            "把画幅留「自动（按首帧推断）」或换非 2.5 模型"
        )
    return resolution


def explain_fail(reason: str) -> str:
    """fail_reason 翻译：首用合规确认给网页端指引；其余原样返回。"""
    text = str(reason or "").strip()
    if _COMPLIANCE_MARKER in text:
        return (
            f"{text} —— 该模型首次使用需先在即梦 Web 端完成一次生成（合规确认）："
            "请用同一账号在 jimeng.jianying.com 网页端手动用一次该模型，再回来重试"
        )
    return text


# ---------- 子命令分派 ----------


def dispatch_subcommand(
    *,
    first_frame: Path | None = None,
    last_frame: Path | None = None,
    reference_paths: list[Path] | None = None,
    reference_video_paths: list[Path] | None = None,
) -> str:
    """按输入选即梦视频子命令。

    - 首帧（±尾帧）→ ``image2video``：即梦的图生视频只收 1 张首帧，带尾帧或
      附加参考图/参考视频直接报错（不静默丢弃 —— 与 OpenRouter 的 frame 优先
      语义不同，这里用户没有「自动不送」的选项，宁可拦下）；
    - 尾帧单独给 → 报错（frames2video 未接入）；
    - 参考图/参考视频 → ``multimodal2video``（全能参考）；
    - 都没有 → ``text2video``。
    """
    if first_frame is not None or last_frame is not None:
        if last_frame is not None:
            raise DreaminaParamError(
                "即梦通路暂不支持尾帧图（首尾帧 frames2video 未接入）；请去掉尾帧或改用 openrouter 通路"
            )
        if reference_paths or reference_video_paths:
            raise DreaminaParamError(
                "即梦图生视频只收 1 张首帧：参考图/参考视频请留空，或去掉首帧单发（走全能参考）"
            )
        return "image2video"
    if reference_paths or reference_video_paths:
        return "multimodal2video"
    return "text2video"


# ---------- 提交与查询 ----------


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
) -> dict:
    """提交一次视频任务，返回任务 JSON（submit_id / gen_status / result_json）。

    ``--poll=0`` 只提交不等待：web 提交是「提交即返回 pending」，等待节奏由
    调用方（adhoc_video 的 wait 路径 / refresh 轮询）控制，不在子进程里睡。
    任务状态是异步的：``gen_status=querying`` 只代表提交被受理，终态只有
    success / fail。

    Raises:
        DreaminaParamError: 提示词为空 / 比例·分辨率·时长·模型组合不合法 /
            首尾帧组合不支持 / 输入文件缺失。
        DreaminaAuthError: 登录态无效（提示先 dreamina login）。
        DreaminaCliError: CLI 缺失 / 非零退出 / 输出解析失败。
    """
    text = str(prompt or "").strip()
    if not text:
        raise DreaminaParamError("prompt 不能为空（即梦视频无提示词长度预检上限，上游报错后再校准）")
    subcommand = dispatch_subcommand(
        first_frame=first_frame,
        last_frame=last_frame,
        reference_paths=reference_paths,
        reference_video_paths=reference_video_paths,
    )
    if not str(model or "").strip():
        raise DreaminaParamError("model 不能为空（charvar 显式传 --model_version，别依赖 CLI 默认）")
    resolution = resolve_resolution(resolution)
    resolution = validate_video(model, resolution, duration, subcommand, ratio=ratio)
    ratio = validate_ratio(ratio)
    entry_ = _VIDEO_MODELS.get(str(model).strip().lower()) or {}
    if entry_.get("ratio_match_frame") and ratio and first_frame is not None:
        _validate_ratio_matches_frame(first_frame, ratio)
    args = [subcommand, "--prompt", text]
    # CLI 各子命令默认模型不同：charvar 约定必须显式传
    args.extend(["--model_version", str(model).strip()])
    args.extend(["--video_resolution", resolution])
    if duration is not None:
        args.extend(["--duration", str(int(duration))])
    if ratio:
        args.extend(["--ratio", ratio])
    args.extend(["--poll", str(int(poll_s))])
    if subcommand == "multimodal2video":
        lo, hi = entry_.get("input_media_durations") or (2, 15)
        for path in list(reference_video_paths or []):
            seconds = probe_media_seconds(Path(path).expanduser())
            if seconds is not None and not (lo <= seconds <= hi):
                raise DreaminaParamError(
                    f"参考视频时长须 {lo}-{hi}s（实测约 {seconds:.1f}s）：{Path(path).name}"
                )
    for flag, human, paths in (
        ("--image", "参考图", reference_paths),
        ("--video", "参考视频", reference_video_paths),
    ):
        for path in paths or []:
            resolved = Path(path).expanduser()
            if not resolved.is_file():
                raise DreaminaParamError(f"{human}不存在：{resolved}")
            args.extend([flag, str(resolved)])
    if first_frame is not None:
        resolved = Path(first_frame).expanduser()
        if not resolved.is_file():
            raise DreaminaParamError(f"首帧图不存在：{resolved}")
        args.extend(["--image", str(resolved)])
    proc = dreamina_image._run_cli(args, timeout_s=max(60.0, poll_s + 60.0))
    task = parse_task_output(proc.stdout)
    gen_status, reason = task_status(task)
    if gen_status == "fail":
        raise DreaminaTaskError(f"即梦视频提交被拒：{explain_fail(reason)}", submit_id=str(task.get("submit_id") or ""))
    return task


def query(submit_id: str, *, download_dir: Path | None = None, timeout_s: float = 120.0) -> dict:
    """查一次任务状态（``query_result`` 与子命令无关，直接复用生图通路）。"""
    return dreamina_image.query(submit_id, download_dir=download_dir, timeout_s=timeout_s)


def task_status(task: dict) -> tuple[str, str]:
    """(gen_status, fail_reason) 元组；querying 只是受理不算终态。"""
    return dreamina_image._task_status(task)


def poll(submit_id: str, *, timeout_s: float = 120.0) -> dict:
    """轮询一次作业状态（不给 download_dir：成片等到终态再单独下载）。"""
    return query(submit_id, timeout_s=timeout_s)


def wait_for_video(
    submit_id: str,
    *,
    first_result: dict | None = None,
    poll_interval: float = 5.0,
    timeout_s: float = 900.0,
    sleep: Any = time.sleep,
    timeout_error: type[Exception] | None = None,
) -> dict:
    """阻塞轮询到终态（success/fail）返回最终任务 JSON。

    ``sleep`` 给测试替换；``timeout_error`` 给调用方（adhoc_video）换成自己的
    异常类型（AdhocVideoTimeout，作业还在跑、refresh 可续）。默认超时抛
    DreaminaTimeoutError（带 submit_id，可 ``dreamina query_result`` 续追）。
    """
    if not str(submit_id or "").strip():
        raise DreaminaCliError("submit_id 不能为空")
    deadline = time.monotonic() + max(1.0, timeout_s)
    task = first_result if isinstance(first_result, dict) else poll(submit_id)
    while True:
        status, reason = task_status(task)
        if status in ("success", "fail"):
            return task
        if not str(task.get("submit_id") or "").strip():
            raise DreaminaCliError(f"即梦任务没有 submit_id：{json.dumps(task, ensure_ascii=False)[:300]}")
        if time.monotonic() >= deadline:
            message = (
                f"即梦视频作业超时未完成（>{int(timeout_s)}s，gen_status={status or 'querying'}）。"
                f"可稍后用 `dreamina query_result --submit_id={submit_id}` 追结果"
            )
            if timeout_error is not None:
                raise timeout_error(message)
            raise DreaminaTimeoutError(message, submit_id=submit_id)
        sleep(max(0.5, poll_interval))
        task = poll(submit_id, timeout_s=min(120.0, max(15.0, deadline - time.monotonic())))


def extract_video_paths(payload: dict) -> list[Path]:
    """从 ``query_result --download_dir`` 的输出里取本地产物路径（视频优先）。

    本地 tasks.db 里的 result_json 不含 path —— path 只出现在 stdout 的 JSON
    里，由 CLI 落盘后回填（``<submit_id>_video_1.mp4`` 之类）。视频任务扫
    ``videos[]``；videos 里没有就兜底扫 ``images[]``；逐个 is_file() 校验。
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
            f"即梦任务成功但未落盘视频文件：{json.dumps(payload, ensure_ascii=False)[:300]}"
        )
    paths = [Path(str(item["path"])).expanduser() for item in items]
    for path in paths:
        if not path.is_file():
            raise DreaminaCliError(f"即梦产物文件不存在：{path}")
    return paths


def video_file_ext(path: Path) -> str:
    """即梦产物文件 → 扩展名（按魔数嗅探，不信任 CLI 落盘名）。"""
    from charvar import openrouter_video

    head = Path(path).expanduser().read_bytes()[:64]
    return openrouter_video.sniff_video_ext(head)


def _validate_ratio_matches_frame(first_frame: Path, ratio: str) -> None:
    """legacy 模型（1.0fast/1.5pro）：显式 ratio 必须与首帧一致（±2%），否则上游拒。

    解析不了（PIL 打不开 / ratio 不是 w:h）就放行，交给上游裁决 —— 别用错误
    的预检拦掉合法请求。
    """
    try:
        from PIL import Image

        width, height = Image.open(Path(first_frame).expanduser()).size
        parts = str(ratio).strip().split(":")
        rw, rh = float(parts[0]), float(parts[1])
    except Exception:  # noqa: BLE001
        return
    if width <= 0 or height <= 0 or rw <= 0 or rh <= 0:
        return
    if abs((width / height) - (rw / rh)) > 0.02 * (rw / rh):
        raise DreaminaParamError(
            f"模型该档要求显式画幅与首帧一致：首帧 {width}x{height} 与 ratio {ratio} 不匹配；"
            "建议把画幅留「自动（按首帧推断）」"
        )


def probe_media_seconds(path: Path) -> float | None:
    """ffprobe 读媒体时长（秒）；ffprobe 不在/读不出来 → None（**不拦**，交上游）。

    只用于参考视频的提交前预检（multimodal2video 的输入媒体时长 2-15s / 2.5: 2-30s）。
    """
    import shutil as _shutil
    import subprocess as _sp

    try:
        from charvar.video import _ffmpeg_bin

        candidate = Path(_ffmpeg_bin()).with_name("ffprobe")
        ffprobe = str(candidate) if candidate.is_file() else (_shutil.which("ffprobe") or "")
    except Exception:  # noqa: BLE001
        ffprobe = _shutil.which("ffprobe") or ""
    if not ffprobe:
        return None
    try:
        proc = _sp.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(Path(path).expanduser())],
            capture_output=True, text=True, timeout=30,
        )
        value = float(proc.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return None
    return value if value > 0 else None


# ---------- CLI `charvar video models --provider dreamina` 用 ----------


def model_list() -> list[dict[str, Any]]:
    """静态模型档位表 → CLI 列表输出形态（与生图先例一致：枚举随 CLI -h 同步）。"""
    out: list[dict[str, Any]] = []
    for model, entry in _VIDEO_MODELS.items():
        out.append(
            {
                "id": model,
                "resolutions": list(entry["resolutions"]),
                "durations": list(entry["durations"]),
                "ratios": list(_VIDEO_RATIOS),
                "subcommands": list(entry["subcommands"]),
                "vip_only": bool(entry.get("vip_only")),
            }
        )
    return out


def submit_id_of(task: dict) -> str:
    """任务 JSON → submit_id（缺了报错：它是 web 续查与 CLI 追查的唯一凭据）。"""
    value = str(task.get("submit_id") or "").strip()
    if not value:
        raise DreaminaCliError(
            f"即梦任务没有 submit_id：{json.dumps(task, ensure_ascii=False)[:300]}"
        )
    return value