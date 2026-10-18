"""ComfyUI 集成（设计文档 §8，调用约定对齐 shared/comfy_client）。

- 服务地址来自环境变量（COMFYUI_HOST / COMFYUI_PORT / COMFYUI_TIMEOUT），
  默认 http://127.0.0.1:8188；显式 comfy_url 参数优先。
- 请求一律 bypass 代理（httpx trust_env=False）：本机 shell 的 ALL_PROXY
  会劫持局域网地址，ComfyUI 走内网直连。
- workflow 采用成对文件约定：{workflows_dir}/{name}.json + {name}.mapping.json；
  mapping.json 使用 shared 客户端的字段注入格式（mappings[] + output_nodes）。
- 队列：逐条 POST /prompt 记录 prompt_id；可选 wait=true 轮询 /history/{prompt_id}
  直到完成并记录输出文件名（用于以后关联图片与 caption）。
"""
from __future__ import annotations

import copy
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from charvar.composer import render
from charvar.models import GenerateResult, PromptItem

_FILENAME_PREFIX_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_COMFY_TIMEOUT_DEFAULT = 180


def workflow_json_name(path: Path) -> str:
    stem = path.stem
    if stem.endswith(".mapping"):
        raise ValueError(f"workflow 文件不能是 mapping sidecar：{path.name}")
    return stem


def list_workflows(workflows_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """列出 workflows_dir 下的 workflow + mapping 成对文件。"""
    from charvar import paths

    base = Path(workflows_dir) if workflows_dir else paths.comfy_workflows_dir()
    if not base.is_dir():
        return []
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(base.iterdir()):
        if not f.is_file() or not f.name.lower().endswith(".json"):
            continue
        if f.name.lower().endswith(".mapping.json"):
            stem = f.name[: -len(".mapping.json")]
            entry = out.setdefault(stem, {"name": stem, "has_workflow": False, "has_mapping": False})
            entry["has_mapping"] = True
        else:
            stem = f.stem
            entry = out.setdefault(stem, {"name": stem, "has_workflow": False, "has_mapping": False})
            entry["has_workflow"] = True
    return [out[k] for k in sorted(out)]


def load_workflow_pair(name: str, workflows_dir: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """按名称加载 workflow + mapping 成对文件；支持直接给 workflow JSON 路径。"""
    from charvar import paths

    base = Path(workflows_dir) if workflows_dir else paths.comfy_workflows_dir()
    workflow_path = Path(name)
    if not workflow_path.exists():
        workflow_path = base / f"{name}.json"
    if not workflow_path.exists():
        available = ", ".join(w["name"] for w in list_workflows(base)) or "（目录为空）"
        raise FileNotFoundError(f"找不到 workflow：{name}（查找 {base}；可用：{available}）")
    mapping_path = workflow_path.parent / f"{workflow_json_name(workflow_path)}.mapping.json"
    if not mapping_path.exists():
        raise FileNotFoundError(f"workflow 需要同名 mapping 配置：{mapping_path}")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict) or not isinstance(mapping.get("mappings"), list):
        raise ValueError(f"mapping 格式错误：{mapping_path} 需要 {{\"mappings\": [...]}}")
    return workflow, mapping


class InlineWorkflow(BaseModel):
    """直传 workflow dict + mapping dict（绕过 workflows 目录）。"""

    model_config = ConfigDict(extra="forbid")

    workflow: dict[str, Any]
    mapping: dict[str, Any]


def filename_prefix(result: GenerateResult, item: PromptItem) -> str:
    safe_subject = _FILENAME_PREFIX_RE.sub("_", result.subject_id)
    return f"{safe_subject}_{item.index:03d}_{item.variant_id}"


SUPPORTED_ASPECT_RATIOS = (
    "auto",
    "1:1",
    "3:4",
    "16:9",
    "4:3",
    "9:16",
    "2:3",
    "3:2",
    "21:9",
)

_RESOLUTION_LONG_SIDE = {"1k": 1024, "2k": 2048, "4k": 4096}


def parse_aspect_ratio(value: str | None) -> tuple[int, int] | None:
    """解析 16:9 型比例，空值和 auto 由调用方处理。"""
    if not value or value == "auto" or not isinstance(value, str):
        return None
    parts = value.strip().lower().replace("×", ":").replace("x", ":").split(":")
    if len(parts) != 2:
        return None
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None


def _smart_ratio(item: PromptItem) -> str:
    """按景别自动选比例：全身 2:3，近景/头像/表情 3:4。"""
    framing = str((item.meta or {}).get("framing") or (item.meta or {}).get("tags", [None])[0] or "")
    return "2:3" if framing == "full" else "3:4"


def dimensions_for(aspect_ratio: str | None, resolution_tier: str | None, item: PromptItem | None = None) -> tuple[int, int]:
    """按比例和分辨率档位计算宽高；auto 时按景别映射，短边取 64 倍数。"""
    ratio = str(aspect_ratio or "").strip().lower()
    if ratio == "auto":
        ratio = _smart_ratio(item) if item is not None else "3:4"
    if ratio not in tuple(SUPPORTED_ASPECT_RATIOS[1:]):
        raise ValueError(f"aspect_ratio 只支持 {'/'.join(SUPPORTED_ASPECT_RATIOS)}，当前：{aspect_ratio}")
    parsed = parse_aspect_ratio(ratio)
    if parsed is None:
        raise ValueError(f"aspect_ratio 非法：{aspect_ratio}")
    w_ratio, h_ratio = parsed
    tier = (resolution_tier or "1k").strip().lower()
    if tier not in _RESOLUTION_LONG_SIDE:
        raise ValueError(f"resolution 只支持 {'/'.join(_RESOLUTION_LONG_SIDE)}，当前：{resolution_tier}")
    long_side = _RESOLUTION_LONG_SIDE[tier]
    short_side = max(64, int(round(long_side * min(w_ratio, h_ratio) / max(w_ratio, h_ratio) / 64.0)) * 64)
    return (long_side, short_side) if w_ratio >= h_ratio else (short_side, long_side)


def dimensions_for_item(aspect_ratio: str | None, resolution_tier: str | None, item: PromptItem) -> tuple[int, int]:
    return dimensions_for(aspect_ratio, resolution_tier, item)


def _injected_values(
    result: GenerateResult,
    item: PromptItem,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    return {
        "prompt": item.prompt,
        "seed": result.seed,
        "index": item.index,
        "variant_id": item.variant_id,
        "subject": result.subject_id,
        "background": item.background,
        "lighting": item.lighting,
        "caption": item.caption,
        "filename_prefix": filename_prefix(result, item),
        **overrides,
    }


def build_payload(
    workflow: dict[str, Any],
    mapping: dict[str, Any],
    result: GenerateResult,
    item: PromptItem,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按 mapping.json 注入字段，返回可直接 POST /prompt 的 payload。

    mapping 条目：
      {"field": "prompt", "node_id": "11", "input_key": "text", "required": true}
      {"field": "seed",   "node_id": "70", "input_key": "seed",
       "mode": "increment"|"fixed", "default": -1, "template": "{seed}+{index}"}
    - mode 只对 seed 生效：increment → result.seed + index；fixed → result.seed；
    - template 存在时优先用模板渲染（变量见 _injected_values）；-1 保留由
      ComfyUI 侧随机（与 shared/comfy_client 语义一致）；
    - field 不在 values 且无 default → None，按 required 决定跳过或报错。
    """
    wf = copy.deepcopy(workflow)
    values = _injected_values(result, item, overrides or {})

    for entry in mapping.get("mappings", []):
        field = str(entry.get("field"))
        node_id = str(entry.get("node_id"))
        input_key = str(entry.get("input_key"))
        node = wf.get(node_id)
        if node is None:
            raise ValueError(f"workflow 缺少 mapping 指向的节点：{node_id}（field={field}）")

        template = entry.get("template")
        if template is not None:
            value: Any = render(str(template), values)
        elif field == "seed":
            mode = entry.get("mode")
            if mode == "fixed":
                value = values["seed"]
            elif explicit_default_is_random := (mode is None and "default" in entry and entry["default"] == -1):
                value = -1  # 无 mode 且 default=-1 → 交给 ComfyUI 侧随机
            else:
                value = values["seed"] + values["index"]  # 默认 increment
        else:
            value = values.get(field, entry.get("default"))

        if value is None:
            if entry.get("required"):
                raise ValueError(f"required 字段缺失：{field}（无 default）")
            continue  # optional 且无值 → 保留 workflow 内置默认
        if field == "seed" and value == -1:
            continue  # 交给 ComfyUI 侧随机
        node.setdefault("inputs", {})[input_key] = value
    return wf


def _output_filenames(history_entry: dict[str, Any], output_nodes: list[str] | None) -> list[dict[str, str]]:
    outputs = history_entry.get("outputs") or {}
    if output_nodes:
        allowed = {str(n) for n in output_nodes}
        outputs = {nid: out for nid, out in outputs.items() if nid in allowed}
    files: list[dict[str, str]] = []
    for _nid, node_output in outputs.items():
        for img in node_output.get("images", []):
            files.append(
                {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
            )
    return files


def _httpx_client(**kw: Any) -> httpx.Client:
    # trust_env=False：忽略 shell 的 ALL_PROXY 等环境代理（内网直连，同 shared/comfy_client）
    return httpx.Client(trust_env=False, **kw)


def submit(
    result: GenerateResult,
    workflow: dict[str, Any],
    mapping: dict[str, Any],
    comfy_url: str | None = None,
    overrides: dict[str, Any] | None = None,
    wait: bool = False,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    on_submitted=None,
    poll_interval: float = 1.5,
) -> list[dict[str, Any]]:
    """逐条入队，返回每条 {index, variant_id, filename_prefix, prompt_id?, ok, error?, files?}。

    - wait=False：只排队（默认，30 条一次性进队列）；
    - wait=True：等待每条执行完成并记录输出文件名（output_nodes 里配置的节点）；
    - 单条失败不中断整批。
    """
    from charvar import paths

    settings_url = paths.comfy_base_url()
    base = (comfy_url or settings_url).rstrip("/")
    client_id = f"charvar-{uuid.uuid4().hex[:8]}"
    submissions: list[dict[str, Any]] = []

    timeout = paths.comfy_timeout()
    with _httpx_client(timeout=httpx.Timeout(timeout, write=60.0)) as client:
        for item in result.items:
            prefix = filename_prefix(result, item)
            submission: dict[str, Any] = {
                "index": item.index,
                "variant_id": item.variant_id,
                "filename_prefix": prefix,
            }
            try:
                item_overrides = dict(overrides or {})
                if aspect_ratio:
                    width, height = dimensions_for_item(aspect_ratio, resolution, item)
                    item_overrides["width"] = width
                    item_overrides["height"] = height
                payload = {
                    "prompt": build_payload(workflow, mapping, result, item, item_overrides),
                    "client_id": client_id,
                }
                resp = client.post(f"{base}/prompt", json=payload, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    raise ValueError(f"ComfyUI 未返回 prompt_id：{data}")
                submission["prompt_id"] = prompt_id
                if on_submitted:
                    on_submitted(item, prompt_id, prefix)
                if wait:
                    files = _poll_entry(client, base, prompt_id, timeout, poll_interval, mapping.get("output_nodes"))
                    submission["files"] = files
                submission["ok"] = True
            except ValueError as exc:
                submission["ok"] = False
                submission["error"] = str(exc)
            except httpx.HTTPError as exc:
                submission["ok"] = False
                submission["error"] = f"{type(exc).__name__}: {exc}"
            submissions.append(submission)
    return submissions


def _poll_entry(
    client: httpx.Client,
    base: str,
    prompt_id: str,
    timeout: float,
    interval: float,
    output_nodes: list[str] | None,
) -> list[dict[str, str]]:
    """轮询 /history/{prompt_id} 直到完成或超时（对齐 shared/comfy_client）。"""
    url = f"{base}/history/{prompt_id}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            entry = data.get(prompt_id)
            if entry is not None:
                status = entry.get("status") or {}
                for msg_type, msg_data in status.get("messages", []):
                    if msg_type == "execution_error":
                        raise ValueError(f"ComfyUI execution error: {msg_data}")
                if status.get("completed") or "outputs" in entry:
                    return _output_filenames(entry, output_nodes)
        time.sleep(interval)
    raise TimeoutError(f"ComfyUI 任务 {prompt_id} 在 {timeout}s 内未完成")
