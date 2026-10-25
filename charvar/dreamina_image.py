"""即梦（Dreamina）生图客户端：封装官方 ``dreamina`` CLI（子进程调用）。

与 OpenRouter 通路的差异（charvar 侧约束）：

- 鉴权走本机登录态（``dreamina login`` 的 OAuth 状态，``~/.dreamina_cli/``），
  没有 API Key —— 没登录时 subprocess 会报错，这里映射成带指引的 DreaminaAuthError；
- 生成是异步任务：submit + query 两步。这里用 ``--poll=N``（CLI 每 1s 查询一次）
  等首轮，仍未终态就由本模块用 ``query_result`` 轮询到 ``gen_status=success/fail``
  或超时；成功后用 ``query_result --download_dir`` 落盘本地图并在结果里给路径；
- 不暴露 seed：请求里的 seed 一律不发送（即梦侧无此参数），manifest 照记；
- 不区分文生图 / 图片编辑配置组：即梦所有模型都有编辑能力，带参考图就发
  ``image2image``，不带就发 ``text2image``，模型/分辨率共用一组配置；
- 画幅用 ``--ratio``（charvar 的 8 个比例与 CLI 枚举一致），不走像素 width/height；
- 提示词长度上限先于提交校验（超限是硬失败，白扣一次提交）：
  text2image 实测 1600 字符、image2image 2000 字符（image-engine 项目实测，
  CLI -h 不列这条），以 Unicode 码点计（与 Python len 一致）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 提示词长度上限（字符，Unicode 码点）。image-engine 项目实测：
# text2image 超过 1600 硬失败（prompt len is larger than limited len）；
# image2image 至少 2000。留 8 字符余量防 CLI 计数口径（字节/码点）差异。
_T2I_PROMPT_LIMIT = 1600 - 8
_I2I_PROMPT_LIMIT = 2000 - 8

# text2image / image2image 都支持的比例（CLI 1.4.20），与 charvar 的 8 档一致
_SUPPORTED_RATIOS = ("1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9")

# 各 model_version 的 --resolution_type 枚举（CLI 1.4.20 -h；3.0/3.1 仅 text2image）
_MODEL_RESOLUTIONS: dict[str, tuple[str, ...]] = {
    "3.0": ("1k", "2k"),
    "3.1": ("1k", "2k"),
    "4.0": ("2k", "4k"),
    "4.1": ("2k", "4k"),
    "4.5": ("2k", "4k"),
    "4.6": ("2k", "4k"),
    "4.7": ("2k", "4k"),
    "5.0": ("2k", "4k"),
    "5.0pro": ("1.5k", "2k", "4k"),
}
_DEFAULT_MODEL = ""  # 空串 = 跟随 CLI 默认（text2image/image2image 默认 5.0）
_DEFAULT_RESOLUTION = "2k"
_DEFAULT_POLL_S = 60.0
_DEFAULT_TIMEOUT_S = 300.0
_QUERY_INTERVAL_S = 3.0

# CLI 报错里认得出「没登录 / 登录态失效」的片段（出现任一即给登录指引）
_AUTH_ERROR_MARKERS = (
    "未登录",
    "请先登录",
    "请重新登录",
    "登录态",
    "not logged in",
    "login required",
    "please login",
)


class DreaminaImageError(RuntimeError):
    """即梦生图失败（基类）。"""


class DreaminaCliError(DreaminaImageError):
    """CLI 不可用（找不到可执行文件 / 非零退出 / 输出解析失败）。"""


class DreaminaAuthError(DreaminaImageError):
    """登录态无效：先在本机跑 ``dreamina login``，或在网页端完成一次生成。"""


class DreaminaParamError(DreaminaImageError):
    """入参在提交前就能判失败（提示词超限 / 比例·分辨率·模型不匹配）。"""


class DreaminaTaskError(DreaminaImageError):
    """任务终态失败（gen_status=fail），message 带 fail_reason。"""

    def __init__(self, message: str, *, submit_id: str = "") -> None:
        super().__init__(message)
        self.submit_id = submit_id


class DreaminaTimeoutError(DreaminaImageError):
    """等待超时：任务仍在云端跑，凭 submit_id 可稍后 ``dreamina query_result`` 追。"""

    def __init__(self, message: str, *, submit_id: str = "") -> None:
        super().__init__(message)
        self.submit_id = submit_id


@dataclass
class DreaminaGenerateResult:
    """一次即梦生图的结果。

    Fields:
        submit_id:   任务 id（也是云端任务的可追溯凭据）。
        gen_status:  终态（success / fail；调用方只拿得到成功或异常，一般 success）。
        fail_reason: 失败原因（成功时为空）。
        images:      下载到本地的产物路径（``download_dir`` 下）。
        width/height: 上游回报的像素尺寸（拿不到时 0）。
        model / ratio / resolution: 实际发送的请求参数（manifest 用）。
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


# ---------- CLI 可执行文件与配置 ----------


def resolve_binary() -> str:
    """即梦 CLI 路径：env DREAMINA_CLI > PATH 里的 dreamina > ~/.local/bin/dreamina。

    skill 约定用绝对路径 ``~/.local/bin/dreamina`` —— 有些 agent shell 的 PATH
    不含 ``~/.local/bin``，兜底一份防误报「未安装」。
    """
    for candidate in (
        _env("DREAMINA_CLI"),
        shutil.which("dreamina") or "",
        str(Path.home() / ".local" / "bin" / "dreamina"),
    ):
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_file():
                return str(path)
    raise DreaminaCliError(
        "找不到 dreamina CLI：先安装（curl -s https://jimeng.jianying.com/cli | bash）"
        "或用 DREAMINA_CLI 环境变量指定路径"
    )


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def resolve_model(model: str | None = None) -> str:
    """生效的 model_version：显式传入 > env DREAMINA_IMAGE_MODEL > 空（CLI 默认 5.0）。"""
    for candidate in (model, _env("DREAMINA_IMAGE_MODEL")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return _DEFAULT_MODEL


def resolve_resolution(resolution: str | None = None) -> str:
    """生效的 --resolution_type：显式传入 > env DREAMINA_IMAGE_RESOLUTION > 2k。

    统一归一成小写（CLI 枚举是小写 2k/4k/1.5k；设置页/env 按 2K/4K 写）。
    """
    for candidate in (resolution, _env("DREAMINA_IMAGE_RESOLUTION", "")):
        if candidate and str(candidate).strip():
            return str(candidate).strip().lower()
    return _DEFAULT_RESOLUTION


def resolve_poll(poll_s: float | None = None) -> float:
    if poll_s is not None:
        return float(poll_s)
    raw = _env("DREAMINA_POLL")
    try:
        return float(raw) if raw else _DEFAULT_POLL_S
    except ValueError:
        return _DEFAULT_POLL_S


def resolve_timeout(timeout_s: float | None = None) -> float:
    """总等待预算（提交 --poll + query_result 轮询）：env DREAMINA_IMAGE_TIMEOUT 或 300s。"""
    if timeout_s is not None:
        return float(timeout_s)
    raw = _env("DREAMINA_IMAGE_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def validate_prompt(prompt: str, *, has_reference: bool) -> str:
    """长度预检：超限直接报错，别白提交一次（上游硬失败还扣权益）。"""
    text = str(prompt or "").strip()
    if not text:
        raise DreaminaParamError("prompt 不能为空")
    limit = _I2I_PROMPT_LIMIT if has_reference else _T2I_PROMPT_LIMIT
    if len(text) > limit:
        raise DreaminaParamError(
            f"提示词 {len(text)} 字符超过即梦"
            f"{'image2image' if has_reference else 'text2image'}上限 {limit} 字符"
            f"（上游硬上限约 {limit + 8}，留了余量防计数口径差异）；请先压缩提示词"
        )
    return text


def validate_ratio(ratio: str | None) -> str:
    """比例校验：charvar 的 8 档与 CLI 枚举一致，超出的在提交前报错。"""
    value = str(ratio or "").strip()
    if value and value not in _SUPPORTED_RATIOS:
        raise DreaminaParamError(
            f"即梦画幅只支持 {'/'.join(_SUPPORTED_RATIOS)}，当前：{value}"
        )
    return value


def validate_model_resolution(model: str, resolution: str, *, has_reference: bool) -> str:
    """model_version 与 resolution_type 的组合校验（CLI -h 声明的枚举，1.4.20）。

    3.0/3.1 只能文生图；image2image 从 4.0 起。配不上的组合在提交前报错，
    免得任务提交后按 fail 终态白等。表外模型（CLI 升级新增）不拦，交给 CLI 裁决。
    """
    if not resolution:
        raise DreaminaParamError("resolution 不能为空（--resolution_type 是即梦必填参数）")
    allowed = _MODEL_RESOLUTIONS.get(model.lower())
    if allowed is None:
        return resolution
    if resolution not in allowed:
        raise DreaminaParamError(
            f"模型 {model} 的分辨率档只支持 {'/'.join(allowed)}，当前：{resolution}"
        )
    if has_reference and model.lower() in ("3.0", "3.1"):
        raise DreaminaParamError(f"模型 {model} 不支持带参考图（image2image 从 4.0 起）")
    return resolution


# ---------- 子进程 ----------


def _run_cli(args: list[str], *, timeout_s: float) -> subprocess.CompletedProcess:
    """跑一条 dreamina 子命令；非零退出归为 CLI 错误（鉴权类单独判型）。"""
    binary = resolve_binary()
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise DreaminaCliError(f"dreamina {' '.join(args[:1])} 子进程超时（{timeout_s:.0f}s）") from exc
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " / ".join(line.strip() for line in message[-4:] if line.strip()) or f"exit {proc.returncode}"
        low = tail.lower()
        if any(marker in tail or marker in low for marker in _AUTH_ERROR_MARKERS):
            raise DreaminaAuthError(
                f"即梦登录态无效：{tail}。请先在终端跑 `dreamina login` 完成授权"
                "（CLI 打印的授权 URL 建议手动在浏览器打开）"
            )
        raise DreaminaCliError(f"dreamina {' '.join(args[:1])} 失败（exit {proc.returncode}）：{tail}")
    return proc


def parse_task_output(stdout: str) -> dict:
    """从 CLI stdout 里抠出任务 JSON。

    stdout 混着进度行（如 ``download image 1 URL: https://…``），JSON 主体在后面；
    以第一行 ``{``/``[`` 开头处起算。顶层是 list（list_task 风格）就取第一条。
    """
    lines = str(stdout or "").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.lstrip()[:1] in ("{", "[")),
        None,
    )
    if start is None:
        raise DreaminaCliError(f"dreamina 输出里没有任务 JSON：{(stdout or '').strip()[-300:]}")
    try:
        data = json.loads("\n".join(lines[start:]))
    except json.JSONDecodeError as exc:
        raise DreaminaCliError(f"dreamina 输出不是合法 JSON：{exc}") from exc
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise DreaminaCliError("dreamina 输出的任务 JSON 不是对象")
    return data


# ---------- 提交与查询 ----------


def submit(
    *,
    prompt: str,
    resolution: str,
    model: str = "",
    ratio: str | None = None,
    reference_paths: list[Path] | None = None,
    poll_s: float = 0,
) -> dict:
    """提交一次生图任务，返回任务 JSON（submit_id / gen_status / result_json）。

    ``--poll=0`` 只提交不等待：等待节奏由 :func:`generate` 统一控制。
    任务状态是异步的：`gen_status=querying` 只代表提交被受理。
    """
    has_refs = bool(reference_paths)
    prompt = validate_prompt(prompt, has_reference=has_refs)
    resolution = validate_model_resolution(model, resolution, has_reference=has_refs)
    ratio = validate_ratio(ratio)
    args = [
        "image2image" if has_refs else "text2image",
        "--prompt", prompt,
        "--resolution_type", resolution,
        "--poll", str(int(poll_s)),
    ]
    if model:
        args.extend(["--model_version", model])
    if ratio:
        args.extend(["--ratio", ratio])
    for path in reference_paths or []:
        resolved = Path(path).expanduser()
        if not resolved.is_file():
            raise DreaminaParamError(f"参考图不存在：{resolved}")
        args.extend(["--images", str(resolved)])
    proc = _run_cli(args, timeout_s=max(60.0, poll_s + 60.0))
    return parse_task_output(proc.stdout)


def query(submit_id: str, *, download_dir: Path | None = None, timeout_s: float = 120.0) -> dict:
    """查一次任务状态；给 download_dir 时把产物下载进那个目录。"""
    args = ["query_result", "--submit_id", str(submit_id)]
    if download_dir is not None:
        download_dir = Path(download_dir).expanduser()
        download_dir.mkdir(parents=True, exist_ok=True)
        args.extend(["--download_dir", str(download_dir)])
    proc = _run_cli(args, timeout_s=timeout_s)
    return parse_task_output(proc.stdout)


def _task_status(task: dict) -> tuple[str, str]:
    status = str(task.get("gen_status") or "").strip()
    reason = str(task.get("fail_reason") or "").strip()
    return status, reason


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
) -> DreaminaGenerateResult:
    """跑一次即梦生图到拿到本地产物为止（阻塞）。

    流程：submit（--poll=poll_s 等首轮）→ 未终态就 query_result 轮询 →
    成功后 query_result --download_dir 把产物落盘。

    Args:
        prompt: 提示词（提交前做长度预检）。
        download_dir: 产物下载目录（必须显式给；调用方通常传 run_dir/download）。
        resolution: --resolution_type（空 = env DREAMINA_IMAGE_RESOLUTION，再缺省 2k）。
        model: --model_version（空 = env DREAMINA_IMAGE_MODEL，再缺省 CLI 默认 5.0）。
        ratio: --ratio（charvar 档位与 CLI 枚举一致；空 = 不传，CLI 默认 16:9）。
        reference_paths: 参考图（有 = image2image，无 = text2image）。
        timeout_s: 总等待预算（秒，env DREAMINA_IMAGE_TIMEOUT，默认 300）。
        poll_s: 提交等待时长（秒，env DREAMINA_POLL，默认 60）。

    Raises:
        DreaminaParamError: 提示词超限 / 比例·分辨率·模型组合不合法 / 参考图缺失。
        DreaminaAuthError: 登录态无效（提示先 dreamina login）。
        DreaminaTaskError: 任务终态 fail（带 fail_reason）。
        DreaminaTimeoutError: 等待超时，任务仍在云端（带 submit_id）。
        DreaminaCliError: CLI 缺失 / 非零退出 / 输出解析失败。
    """
    model = resolve_model(model)
    resolution = resolve_resolution(resolution)
    timeout_s = resolve_timeout(timeout_s)
    poll_s = resolve_poll(poll_s)
    deadline = time.monotonic() + timeout_s

    # 首轮等 poll_s；总预算比 poll 短时让 CLI 只跑 poll=0（不等待，纯提交）
    effective_poll = min(poll_s, max(0.0, timeout_s - 5.0))
    task = submit(
        prompt=prompt,
        resolution=resolution,
        model=model,
        ratio=ratio,
        reference_paths=reference_paths,
        poll_s=int(effective_poll),
    )
    submit_id = str(task.get("submit_id") or "")
    status, reason = _task_status(task)

    while status not in ("success", "fail"):
        if not submit_id:
            raise DreaminaCliError(f"即梦任务没有 submit_id：{json.dumps(task, ensure_ascii=False)[:300]}")
        if time.monotonic() >= deadline:
            raise DreaminaTimeoutError(
                f"即梦任务等待超时（{timeout_s:.0f}s，gen_status={status or 'querying'}）。"
                f"可稍后用 `dreamina query_result --submit_id={submit_id} --download_dir={download_dir}` 追结果",
                submit_id=submit_id,
            )
        time.sleep(min(_QUERY_INTERVAL_S, max(0.5, deadline - time.monotonic())))
        task = query(submit_id, timeout_s=min(120.0, max(15.0, deadline - time.monotonic())))
        status, reason = _task_status(task)

    if not submit_id:
        raise DreaminaCliError(f"即梦任务没有 submit_id：{json.dumps(task, ensure_ascii=False)[:300]}")

    if status == "fail":
        raise DreaminaTaskError(f"即梦任务失败：{reason or '未知原因（fail）'}", submit_id=submit_id)

    # 下载落盘：即使总预算刚好用尽也再给一次下载窗口（产物 URL 会过期，别等）
    result = query(submit_id, download_dir=download_dir, timeout_s=180.0)
    gen_status, fail_reason = _task_status(result)
    if gen_status == "fail":
        raise DreaminaTaskError(f"即梦任务失败：{fail_reason or '未知原因（fail）'}", submit_id=submit_id)
    payload = result.get("result_json") if isinstance(result.get("result_json"), dict) else {}
    items = [
        item for item in payload.get("images") or []
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]
    if not items:
        raise DreaminaCliError(f"即梦任务成功但未落盘图片：{json.dumps(result, ensure_ascii=False)[:300]}")
    images = [Path(str(item["path"])).expanduser() for item in items]
    for image in images:
        if not image.is_file():
            raise DreaminaCliError(f"即梦产物文件不存在：{image}")
    first = items[0]
    result_model = model
    logger.info(
        "dreamina.image_generated | submit_id=%s | model=%s | files=%d",
        submit_id, result_model or "(default)", len(images),
    )
    return DreaminaGenerateResult(
        submit_id=submit_id,
        gen_status=str(gen_status or "success"),
        fail_reason=fail_reason,
        images=images,
        width=int(first.get("width") or 0),
        height=int(first.get("height") or 0),
        model=result_model,
        ratio=str(ratio or ""),
        resolution=resolution,
    )

