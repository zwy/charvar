"""路径与 ComfyUI 连接配置。

环境变量（ComfyUI 部分与 shared/comfy_client 同名兼容）：
- CHARVAR_PRESETS_DIR：预设目录（默认 <项目根>/presets）
- CHARVAR_DATA_DIR：数据目录（默认 <项目根>/data，含 charvar.db）
- COMFYUI_HOST / COMFYUI_PORT / COMFYUI_TIMEOUT：ComfyUI 服务地址
- COMFYUI_WORKFLOWS_DIR：workflow + mapping 成对文件目录（默认 <data>/workflows）

读法：先读取仓库根 .env（不存在则跳过），已存在的环境变量优先。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 读仓库根 .env（已存在的环境变量优先）；dotenv 自算 ROOT，避免循环导入
from charvar import dotenv as _dotenv  # noqa: E402

_load_env = _dotenv.load_env
_load_env()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def presets_dir() -> Path:
    env = os.environ.get("CHARVAR_PRESETS_DIR")
    return Path(env).expanduser() if env else ROOT / "presets"


def data_dir() -> Path:
    env = os.environ.get("CHARVAR_DATA_DIR")
    return Path(env).expanduser() if env else ROOT / "data"


def db_path() -> Path:
    return data_dir() / "charvar.db"


def out_dir() -> Path:
    return ROOT / "out"


# ---------- ComfyUI ----------

def comfy_host() -> str:
    return _env("COMFYUI_HOST", "127.0.0.1")


def comfy_port() -> int:
    try:
        return int(os.environ.get("COMFYUI_PORT", "8188"))
    except ValueError:
        return 8188


def comfy_timeout() -> float:
    raw = os.environ.get("COMFYUI_TIMEOUT", "180")
    try:
        return float(raw)
    except ValueError:
        return 180.0


def comfy_base_url(explicit: str | None = None) -> str:
    """显式传入 > COMFYUI 显式 URL > host:port 组装。"""
    if explicit:
        return explicit
    env_url = os.environ.get("COMFYUI_URL")
    if env_url:
        return env_url
    return f"http://{comfy_host()}:{comfy_port()}"


def comfy_workflows_dir() -> Path:
    env = os.environ.get("COMFYUI_WORKFLOWS_DIR")
    if env:
        return Path(env).expanduser()
    return data_dir() / "workflows"
