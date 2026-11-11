"""即梦画布（``dreamina-canvas`` CLI）传输基座：唯一的 canvas 子进程通路。

即梦官方把主推 CLI 转向画布工作流（图片/视频模型目录都比旧 ``dreamina``
CLI 新），charvar 的即梦生图 / 生视频通路整体换到它。本模块负责：

- 子进程调用与异常映射：所有命令统一带 ``--format json --non-interactive --
  yes --region cn``，按 CLI 退出码 {0,1,2,10,11,13,20} 判型到 charvar 的
  即梦异常族（定义在本模块，``dreamina_image``/``dreamina_video`` 原名引用）；
- 模型目录：静态表 ``_IMAGE_MODELS`` / ``_VIDEO_MODELS`` 镜像
  ``dreamina-canvas model list --type image|video`` 快照（1.3.0，2026-11-11
  实测）——**CLI 升级后要跑一次 model list 重新同步这两张表**，各模型
  resolution/ratio 字面值大小写不一致、提交时按原样透传；
- ``CanvasRunner``：一个 run 一张画布的编排器（建画布 → 上传参考图 → 建节点 →
  报价 → 批准 → 运行 → 查态 → 下载），全部写操作显式 ``--project-id``、
  全部幂等键为客户端生成的 UUID。

与旧 ``dreamina`` CLI 的关键差异：
- 参考图/参考视频不直接传本地路径，先 ``resource upload`` 登记成资源、
  等到 ``success`` 再以 ``--ref res:<uuid>`` 挂到节点；
- 出图前要过积分闸门：``node quote`` 拿报价 → ``node confirm`` 按上限签发
  token → ``node run --credit-token``。charvar 的语义是「用户点生成 = 授权
  扣费」，闸门由程序自动走完（ceiling = 报价 × ``_CREDIT_CEILING_PAD``）；
- 上传/下载走 CDN，本机代理会劫持（实测 ALL_PROXY 下上传直接「data stream
  ended unexpectedly」且资源 id 卡死）—— 子进程 env 里剥掉全部代理变量。
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 异常族（历史上定义在 dreamina_image，迁移到这里统一归属 canvas 基座；
# dreamina_image / dreamina_video 继续按原名字引用，调用方零改动）
# ---------------------------------------------------------------------------


class DreaminaImageError(RuntimeError):
    """即梦生图/生视频失败（基类）。"""


class DreaminaCliError(DreaminaImageError):
    """CLI 不可用（找不到可执行文件 / 非零退出 / 输出解析失败）。"""


class DreaminaAuthError(DreaminaImageError):
    """登录态无效：先在本机跑 ``dreamina-canvas auth login``。"""


class DreaminaParamError(DreaminaImageError):
    """入参在提交前就能判失败（提示词超限 / 枚举不匹配 / 参考图数量超限）。"""


class DreaminaTaskError(DreaminaImageError):
    """任务终态失败或服务端拒绝，message 带原因。"""

    def __init__(self, message: str, *, submit_id: str = "") -> None:
        super().__init__(message)
        self.submit_id = submit_id


class DreaminaTimeoutError(DreaminaImageError):
    """等待超时，任务仍在云端（带 submit_id；画布 id 在 project_id 上）。"""

    def __init__(self, message: str, *, submit_id: str = "", project_id: str = "") -> None:
        super().__init__(message)
        self.submit_id = submit_id
        self.project_id = project_id


# ---------------------------------------------------------------------------
# 二进制解析 / 子进程
# ---------------------------------------------------------------------------

_INSTALL_HINT = (
    "即梦画布 CLI 未安装或不在 PATH："
    "`curl -fsSL https://jimeng.jianying.com/canvas-cli/install.sh | bash` 安装，"
    "或用 env DREAMINA_CLI 指定 dreamina-canvas 路径"
)


def resolve_binary() -> str:
    """canvas 二进制：DREAMINA_CLI env > PATH > ~/.local/bin/dreamina-canvas。"""
    from_env = os.environ.get("DREAMINA_CLI", "").strip()
    if from_env:
        Path(from_env).expanduser().resolve()  # 存在性由调用方 subprocess 报错暴露
        return str(Path(from_env).expanduser())
    found = shutil.which("dreamina-canvas")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "dreamina-canvas"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise DreaminaCliError(_INSTALL_HINT)


# canvas 上传/下载走 CDN，被本机代理劫持会传输中断且幂等 id 卡死 —— 全剥
_PROXY_ENV_KEYS = frozenset(
    {
        "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
        "HTTPS_PROXY", "https_proxy", "SOCKS_SERVER", "SOCKS5_SERVER",
    }
)

_GLOBAL_FLAGS = ("--format", "json", "--non-interactive", "--yes", "--region", "cn")

# 各命令默认子进程预算（控制面命令很轻；上传/下载给大文件的余量）
_CONTROL_TIMEOUT_S = 60.0
_UPLOAD_TIMEOUT_S = 300.0
_DOWNLOAD_TIMEOUT_S = 600.0


def _error_message(payload: dict[str, Any], fallback: str) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return fallback
    parts = [str(error.get("message") or fallback)]
    for diag in error.get("diagnostics") or []:
        if isinstance(diag, dict) and diag.get("detail"):
            parts.append(str(diag["detail"]))
    return "；".join(parts)


def _run_canvas(args: list[str], *, timeout_s: float) -> dict[str, Any]:
    """跑一条 canvas 命令并解析 JSON（schemaVersion 2 契约）。

    退出码 → 异常：11 需登录、13 策略阻断、2 参数错、10 需确认（自动闸门下
    不该出现）、20 操作未完成、1 其他失败；0 时 ``ok=false`` 同样判型。
    """
    cmd = [resolve_binary(), *args, *_GLOBAL_FLAGS]
    env = {k: v for k, v in os.environ.items() if k not in _PROXY_ENV_KEYS and k.upper() not in _PROXY_ENV_KEYS}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired as exc:
        raise DreaminaCliError(f"dreamina-canvas 子进程超时（{timeout_s:.0f}s）：{' '.join(args[:2])}") from exc
    except OSError as exc:
        raise DreaminaCliError(f"dreamina-canvas 子进程启动失败：{exc}") from exc

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise DreaminaCliError(
            f"dreamina-canvas 输出不是 JSON（exit={proc.returncode}）：{proc.stdout[:200]!r} {proc.stderr[:200]!r}"
        ) from None

    if not isinstance(payload, dict):
        raise DreaminaCliError(f"dreamina-canvas 输出不是 JSON 对象：{proc.stdout[:200]!r}")

    if payload.get("ok") is True and proc.returncode == 0:
        return payload

    message = _error_message(payload, f"dreamina-canvas exit={proc.returncode}：{proc.stdout[:200]!r}")
    code = proc.returncode
    if code == 11:
        raise DreaminaAuthError(f"即梦画布登录态无效：{message}；先在终端跑 `dreamina-canvas auth login`")
    if code == 2:
        raise DreaminaParamError(f"即梦画布参数被拒：{message}")
    if code == 10:
        raise DreaminaTaskError(f"即梦画布要求人工确认（自动闸门未兜住）：{message}")
    if code == 20:
        raise DreaminaTimeoutError(f"即梦画布操作未完成（exit=20）：{message}")
    raise DreaminaTaskError(f"即梦画布请求失败（exit={code}）：{message}")


def _ok_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# 静态模型目录（镜像 `model list` 快照；CLI 升级后同步）
# ---------------------------------------------------------------------------

_RATIOS_8 = ("1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9")
_RATIOS_5PRO = ("1:1", "9:16", "16:9", "3:4", "4:3", "2:3", "3:2", "9:21", "21:9")
_RATIOS_MJ82 = (
    "1:1", "1:2", "2:1", "9:16", "16:9", "3:4", "4:3", "2:3", "3:2",
    "4:5", "5:4", "9:21", "21:9", "1:3", "3:1",
)
_RATIOS_VIDEO_9 = ("16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9")
_RATIOS_VIDEO_6 = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
# wan_3.0 只收 5 档（1.3.0 实测 model list；其余视频模型是 6/9 档，别混用）
_RATIOS_VIDEO_5 = ("16:9", "4:3", "1:1", "3:4", "9:16")

# 图片条目：label / prompt_limit / resolutions / ratios / ratio_required /
# count_min / count_max / ref_max {"t2i","i2i"}（0 = 该模式不收参考图）
_IMAGE_MODELS: dict[str, dict[str, Any]] = {
    "seedream_4.0":           dict(label="Seedream 4.0",        prompt_limit=2000, resolutions=("2K", "4K"),      ratios=_RATIOS_8,    ratio_required=True,  count_max=8, ref_max=dict(t2i=0,  i2i=6)),
    "seedream_4.1":           dict(label="Seedream 4.1",        prompt_limit=2000, resolutions=("2K", "4K"),      ratios=_RATIOS_8,    ratio_required=True,  count_max=8, ref_max=dict(t2i=0,  i2i=6)),
    "seedream_4.5":           dict(label="Seedream 4.5",        prompt_limit=2000, resolutions=("2K", "4K"),      ratios=_RATIOS_8,    ratio_required=True,  count_max=8, ref_max=dict(t2i=0,  i2i=6)),
    "seedream_4.6":           dict(label="Seedream 4.6",        prompt_limit=2000, resolutions=("2K", "4K"),      ratios=_RATIOS_8,    ratio_required=True,  count_max=8, ref_max=dict(t2i=14, i2i=6)),
    "seedream_4.7":           dict(label="Seedream 4.7",        prompt_limit=2000, resolutions=("1K", "2K", "4K"), ratios=_RATIOS_8,   ratio_required=False, count_max=8, ref_max=dict(t2i=14, i2i=6)),
    "seedream_5.0_lite":      dict(label="Seedream 5.0 Lite",   prompt_limit=2000, resolutions=("2K", "4K"),      ratios=_RATIOS_8,    ratio_required=False, count_max=8, ref_max=dict(t2i=0,  i2i=6)),
    "seedream_5.0_pro":       dict(label="Seedream 5.0 Pro",    prompt_limit=5000, resolutions=("1.5K", "2K", "4K"), ratios=_RATIOS_5PRO, ratio_required=False, count_max=4, ref_max=dict(t2i=14, i2i=10)),
    "seedream_5.5":           dict(label="Seedream 5.5",        prompt_limit=5000, resolutions=("1.5K", "2K"),    ratios=_RATIOS_8,    ratio_required=False, count_max=8, ref_max=dict(t2i=14, i2i=10)),
    "high_aes_general_v50_flash": dict(label="智能图片V2.5 Pro", prompt_limit=5000, resolutions=("1.5K", "2K"),   ratios=_RATIOS_8,    ratio_required=False, count_max=4, ref_max=dict(t2i=0,  i2i=10)),
    "jimeng_smart_image_v1":  dict(label="即梦 ImageMax 1.0",   prompt_limit=5000, resolutions=("1K", "2K", "4K"), ratios=_RATIOS_8,  ratio_required=True,  count_max=4, ref_max=dict(t2i=0,  i2i=4)),
    "jm_image_model_yc_mj82": dict(label="图片美学模型 V8.2",   prompt_limit=1024, resolutions=("1K", "2K"),      ratios=_RATIOS_MJ82, ratio_required=True,  count_min=4, count_max=4, ref_max=dict(t2i=0, i2i=20)),
}

# 视频条目：resolutions（字面值透传，大小写按模型）/ durations（下限,上限）/
# ratios / modes 允许集合 / m2v 参考上限 {"total","image","video"}（happyhorse
# 不收参考视频）/ first_last {"total","单帧"} —— seedance 2.5_draft（样片）
# 不进 charvar 目录
_VIDEO_MODELS: dict[str, dict[str, Any]] = {
    "seedance_2.0_fast_vip": dict(
        label="即梦 Seedance 2.0 Fast VIP", resolutions=("720p",), durations=(4, 15),
        ratios=_RATIOS_VIDEO_6, count_max=4,
        modes=("t2v", "m2v", "first_last_frame"), m2v_ref=dict(total=12, image=9, video=3),
    ),
    "seedance_2.0_mini": dict(
        label="即梦 Seedance 2.0 mini", resolutions=("720p",), durations=(4, 15),
        ratios=_RATIOS_VIDEO_6, count_max=4, modes=("t2v", "m2v", "first_last_frame"),
        m2v_ref=dict(total=12, image=9, video=3),
    ),
    "seedance_2.0_vip": dict(
        label="即梦 Seedance 2.0 VIP", resolutions=("720p", "1080p", "4k"), durations=(4, 15),
        ratios=_RATIOS_VIDEO_6, count_max=4, modes=("t2v", "m2v", "first_last_frame"),
        m2v_ref=dict(total=12, image=9, video=3),
    ),
    "seedance_2.5": dict(
        label="即梦 Seedance 2.5", resolutions=("480p", "720p", "1080p"), durations=(4, 30),
        ratios=_RATIOS_VIDEO_6, count_max=4, modes=("t2v", "m2v", "first_last_frame"),
        m2v_ref=dict(total=50, image=30, video=10),
    ),
    "happyhorse_1.1": dict(
        label="HappyHorse 1.1", resolutions=("720P", "1080P"), durations=(4, 15),
        ratios=_RATIOS_VIDEO_9, count_max=4, modes=("t2v", "m2v", "first_last_frame"),
        m2v_ref=dict(total=9, image=9, video=0),
    ),
    "wan_3.0": dict(
        label="Wan 3.0", resolutions=("720P", "1080P"), durations=(4, 15),
        ratios=_RATIOS_VIDEO_5, count_max=4, modes=("t2v", "m2v", "first_last_frame"),
        m2v_ref=dict(total=20, image=10, video=5),
    ),
    "seedance_pro_fast": dict(
        label="即梦 Seedance 1.0 Fast", resolutions=("720p", "1080p"), durations=(5, 10),
        ratios=_RATIOS_VIDEO_6, count_max=4, modes=("t2v", "first_last_frame"),
        m2v_ref=dict(total=0, image=0, video=0),
    ),
}

# 旧 dreamina CLI 的模型名 → canvas 模型 id（设置页/env 存量值迁移；
# 映射不到的回落默认并记 warning）
LEGACY_ALIAS_IMAGE: dict[str, str] = {
    "3.0": "seedream_4.7", "3.1": "seedream_4.7",
    "4.0": "seedream_4.0", "4.1": "seedream_4.1", "4.5": "seedream_4.5",
    "4.6": "seedream_4.6", "4.7": "seedream_4.7",
    "5.0": "seedream_5.0_pro", "5.0pro": "seedream_5.0_pro", "5.0 pro": "seedream_5.0_pro",
}
LEGACY_ALIAS_VIDEO: dict[str, str] = {
    "seedance2.0": "seedance_2.0_fast_vip", "seedance2.0fast": "seedance_2.0_fast_vip",
    "seedance2.0_vip": "seedance_2.0_vip", "seedance2.0fast_vip": "seedance_2.0_fast_vip",
    "seedance2.0mini": "seedance_2.0_mini", "seedance2.5": "seedance_2.5",
    "seedance1.0fast": "seedance_pro_fast", "seedance1.5pro": "seedance_2.0_fast_vip",
}

_DEFAULT_IMAGE_MODEL = "seedream_4.7"
_DEFAULT_RESOLUTION = "2K"
_DEFAULT_VIDEO_MODEL = "seedance_2.0_fast_vip"
_DEFAULT_VIDEO_RESOLUTION = "720p"
_DEFAULT_DURATION_S = 5

# 积分上限：报价 maxCredits 是服务端的上界口径（报价与实扣都以它计），
# 报价后 confirm 的 ceiling 在其基础上按 pad 放大，兜住报价与运行之间的漂移
_CREDIT_CEILING_PAD = 1.2


def normalize_image_model(model: str | None) -> str:
    """存量旧名 / 大小写 → canvas 图模型 id；未知值回落默认（调用方记日志）。"""
    raw = str(model or "").strip()
    if not raw:
        return _DEFAULT_IMAGE_MODEL
    lowered = raw.lower()
    if lowered in _IMAGE_MODELS:
        return lowered
    if raw in _IMAGE_MODELS:
        return raw
    mapped = LEGACY_ALIAS_IMAGE.get(lowered) or LEGACY_ALIAS_IMAGE.get(raw)
    return mapped or _DEFAULT_IMAGE_MODEL


def normalize_video_model(model: str | None) -> str:
    raw = str(model or "").strip()
    if not raw:
        return _DEFAULT_VIDEO_MODEL
    if raw in _VIDEO_MODELS:
        return raw
    mapped = LEGACY_ALIAS_VIDEO.get(raw) or LEGACY_ALIAS_VIDEO.get(raw.lower())
    return mapped or _DEFAULT_VIDEO_MODEL


def normalize_resolution(value: str | None, model: dict[str, Any]) -> str:
    """分辨率归一：大小写不敏感地匹配模型枚举（命中后按目录字面值透传）；
    显式给了但不匹配则按用户给的值透传（交服务端严校验），没给回落首个。"""
    raw = str(value or "").strip()
    if not raw:
        return model["resolutions"][0]
    for res in model["resolutions"]:
        if raw.lower() == res.lower():
            return res
    return raw


def image_model_list() -> list[dict[str, Any]]:
    """给 CLI / 端点枚举用：id + 展示名 + 能力。"""
    return [
        dict(
            id=model_id, label=entry["label"], prompt_limit=entry["prompt_limit"],
            resolutions=list(entry["resolutions"]), ratios=list(entry["ratios"]),
            ratio_required=entry["ratio_required"], count_max=entry["count_max"],
            ref_max=entry["ref_max"],
        )
        for model_id, entry in _IMAGE_MODELS.items()
    ]


def video_model_list() -> list[dict[str, Any]]:
    return [
        dict(
            id=model_id, label=entry["label"], resolutions=list(entry["resolutions"]),
            durations=list(entry["durations"]), ratios=list(entry["ratios"]),
            count_max=entry["count_max"], modes=list(entry["modes"]),
            m2v_ref=entry["m2v_ref"],
        )
        for model_id, entry in _VIDEO_MODELS.items()
    ]


# ---------------------------------------------------------------------------
# CanvasRunner：一个 run 一张画布
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _ceiling_for(total_max_credits: float) -> int:
    """报价上界 × pad 的积分 ceiling（向上取整）。"""
    return max(1, math.ceil(total_max_credits * _CREDIT_CEILING_PAD))


class CanvasRunner:
    """一张画布的完整生命周期（生图 run / 生视频 run 各一个实例）。

    project_id 为空 = 建新画布（客户端 UUID 即幂等键，重试复用同实例即复用
    键）；给出 = 接管已有画布（跨进程续跑用，配 manifest.canvas）。
    """

    def __init__(self, name: str, *, project_id: str | None = None) -> None:
        self.name = name
        self._given_project_id = project_id or ""
        self.project_id = project_id or ""
        self.web_url = ""

    # ---- 画布与资源 ----

    def ensure_canvas(self) -> str:
        """确保画布存在（幂等）；返回 project_id，顺带留 web_url。"""
        if self.project_id:
            return self.project_id
        create_id = self._given_project_id or _new_uuid()
        payload = _run_canvas(
            ["canvas", "create", self.name, "--project-id", create_id],
            timeout_s=_CONTROL_TIMEOUT_S,
        )
        project = _ok_data(payload).get("project") or {}
        self.project_id = str(project.get("projectId") or create_id)
        self.web_url = str(project.get("webUrl") or "")
        return self.project_id

    def upload(self, path: Path) -> str:
        """上传本地文件登记为资源（幂等键 = 新随机 UUID；失败换新 id 重试）。"""
        self.ensure_canvas()
        resource_id = _new_uuid()
        payload = _run_canvas(
            ["resource", "upload", "--file", str(path), "--project-id", self.project_id,
             "--resource-id", resource_id, "--import-kind", "local_upload"],
            timeout_s=_UPLOAD_TIMEOUT_S,
        )
        rid = _ok_data(payload).get("resourceId")
        if not rid:
            raise DreaminaCliError(f"即梦资源上传未返回 resourceId：{json.dumps(payload, ensure_ascii=False)[:200]}")
        return str(rid)

    def resource_get(self, resource_id: str) -> dict[str, Any]:
        payload = _run_canvas(
            ["resource", "get", resource_id, "--project-id", self.project_id],
            timeout_s=_CONTROL_TIMEOUT_S,
        )
        return _ok_data(payload)

    def wait_resource(self, resource_id: str, *, timeout_s: float = 120.0, poll_s: float = 1.0) -> dict[str, Any]:
        """等资源登记处理到 success（失败态显式报错，不死轮）。"""
        deadline = time.monotonic() + max(1.0, timeout_s)
        last: dict[str, Any] = {}
        while True:
            try:
                last = self.resource_get(resource_id)
            except DreaminaTaskError:
                pass  # 刚上传的瞬间可能查不到（unavailable），等下一拍
            status = str(last.get("status") or "")
            if status == "success":
                return last
            if status in ("failed", "error", "canceled"):
                raise DreaminaTaskError(f"即梦参考素材登记失败（status={status or '未知'}）：{resource_id}")
            if time.monotonic() >= deadline:
                raise DreaminaTimeoutError(
                    f"即梦参考素材处理超时（{timeout_s:.0f}s，status={status or '处理中'}）：{resource_id}",
                    project_id=self.project_id,
                )
            time.sleep(max(0.2, poll_s))

    # ---- 节点：创建 / 闸门 / 运行 / 查看 ----

    def create_node(
        self, *, kind: str, model: str, mode: str, prompt: str, title: str,
        ratio: str | None, resolution: str | None, ref_ids: list[str],
        duration: float | None = None, count: int | None = None,
    ) -> str:
        """建 typed 节点（不触发生成）；返回 node_id。"""
        self.ensure_canvas()
        args = [
            "node", "create", kind, "--project-id", self.project_id,
            "--model", model, "--mode", mode, "--prompt", prompt,
            "--title", title,
        ]
        if ratio:
            args += ["--ratio", ratio]
        if resolution:
            args += ["--resolution", resolution]
        if duration is not None:
            args += ["--duration", str(int(duration))]
        if count is not None and count > 1:
            args += ["--count", str(count)]
        for rid in ref_ids:
            args += ["--ref", f"res:{rid}"]
        payload = _run_canvas(args, timeout_s=_CONTROL_TIMEOUT_S)
        node_id = str((_ok_data(payload).get("node") or {}).get("nodeId") or "")
        if not node_id:
            raise DreaminaCliError(f"即梦节点创建未返回 nodeId：{json.dumps(payload, ensure_ascii=False)[:200]}")
        return node_id

    def quote(self, node_ids: list[str]) -> dict[str, Any]:
        args = ["node", "quote"]
        for nid in node_ids:
            args += ["--node-id", nid]
        args += ["--project-id", self.project_id]
        return _ok_data(_run_canvas(args, timeout_s=_CONTROL_TIMEOUT_S))

    def confirm(self, node_ids: list[str], *, credit_ceiling: int) -> str:
        """按上限批准扣费，返回 creditConfirmationToken（不扣费）。"""
        args = ["node", "confirm", "--credit-ceiling", str(credit_ceiling)]
        for nid in node_ids:
            args += ["--node-id", nid]
        args += ["--project-id", self.project_id]
        data = _ok_data(_run_canvas(args, timeout_s=_CONTROL_TIMEOUT_S))
        token = str(data.get("creditConfirmationToken") or "")
        if not token:
            raise DreaminaCliError("即梦积分批准未返回 token")
        return token

    def run(self, node_ids: list[str], submit_ids: list[str], credit_token: str) -> list[str]:
        """运行节点（真实扣费）；返回与服务端受理的 submitId 列表。"""
        if len(node_ids) != len(submit_ids):
            raise DreaminaParamError("node run 的 node 与 submit id 数量不一致")
        args = ["node", "run", "--credit-token", credit_token]
        for nid in node_ids:
            args += ["--node-id", nid]
        for sid in submit_ids:
            args += ["--submit-id", sid]
        args += ["--project-id", self.project_id]
        data = _ok_data(_run_canvas(args, timeout_s=_CONTROL_TIMEOUT_S))
        items = data.get("items") or []
        submit_ids_out = [str(item.get("submitId") or "") for item in items]
        if not submit_ids_out or any(not sid for sid in submit_ids_out):
            raise DreaminaCliError(f"即梦节点运行未返回 submitId：{json.dumps(data, ensure_ascii=False)[:200]}")
        return submit_ids_out

    def create_and_run(
        self, *, kind: str, model: str, mode: str, prompt: str, title: str,
        ratio: str | None, resolution: str | None, ref_ids: list[str],
        duration: float | None = None, count: int | None = None,
        submit_id: str | None = None,
    ) -> tuple[str, str]:
        """create → quote → confirm → run 全链（报价落日志做审计）。"""
        self.ensure_canvas()
        node_id = self.create_node(
            kind=kind, model=model, mode=mode, prompt=prompt, title=title,
            ratio=ratio, resolution=resolution, ref_ids=ref_ids,
            duration=duration, count=count,
        )
        quote = self.quote([node_id])
        total = float(quote.get("totalMaxCredits") or 0)
        logger.info("dreamina_canvas.quote | node=%s | total_max_credits=%s | required=%s",
                    node_id, total, quote.get("confirmationRequired"))
        token = self.confirm([node_id], credit_ceiling=_ceiling_for(total))
        submit = submit_id or _new_uuid()
        accepted = self.run([node_id], [submit], token)
        return node_id, accepted[0]

    def status(self, submit_id: str) -> dict[str, Any]:
        """单次查询运行态（data.state: running / succeeded / failed / canceled）。"""
        payload = _run_canvas(
            ["operation", "status", submit_id, "--project-id", self.project_id],
            timeout_s=_CONTROL_TIMEOUT_S,
        )
        return _ok_data(payload)

    def node_outputs(self, node_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """读节点产物（resourceId/尺寸/状态），按 node_id 分组。"""
        args = ["node", "show"]
        for nid in node_ids:
            args += ["--node-id", nid]
        args += ["--project-id", self.project_id]
        data = _ok_data(_run_canvas(args, timeout_s=_CONTROL_TIMEOUT_S))
        outputs: dict[str, list[dict[str, Any]]] = {}
        for entry in data.get("nodes") or []:
            node = entry.get("node") or {}
            outputs[str(node.get("nodeId") or "")] = [
                res for res in node.get("resources") or [] if isinstance(res, dict)
            ]
        return outputs

    def download(self, resource_id: str, dest_dir: Path) -> dict[str, Any]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        payload = _run_canvas(
            ["resource", "download", resource_id, "-o", str(dest_dir),
             "--project-id", self.project_id],
            timeout_s=_DOWNLOAD_TIMEOUT_S,
        )
        data = _ok_data(payload)
        path = Path(str(data.get("path") or ""))
        if not path.is_file():
            raise DreaminaCliError(f"即梦产物下载未落盘：{dest_dir}/{resource_id}")
        return data
