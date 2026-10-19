"""全局设置（需求：设置页 + SQLite；env 只作初始默认）。

- settings 表按顶层键一行 JSON 存储，partial 更新不会互踩其他键；
- AppSettings 是类型化门面：get_settings 把 DB 行合并到默认值上；
- LLM 三项（base_url/model/api_key）为空串时回退环境变量 LLM_*。
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, Field

from charvar import storage

# 与 charvar.subject_images.IMAGE_KINDS 保持一致（避免循环导入，此处显式列出）
IMAGE_KIND_KEYS = ("character_sheet", "headshot", "front", "side", "behind")

_DEFAULT_WORKFLOW_T2I = "image_qwen_image_2_1_turbo_t2i_easy_api"
_DEFAULT_WORKFLOW_EDIT = "image_qwen_image_2_1_turbo_image_edit_easy_api"


class ImageSize(BaseModel):
    aspect_ratio: str = "3:4"
    resolution: Literal["1k", "2k", "4k"] = "1k"


class LlmSettings(BaseModel):
    base_url: str = ""  # 空串 = 用 env LLM_BASE_URL
    model: str = ""  # 空串 = 用 env LLM_MODEL
    api_key: str = ""  # 空串 = 用 env LLM_API_KEY；读接口永远掩码


class AppSettings(BaseModel):
    comfy_t2i_workflow: str = _DEFAULT_WORKFLOW_T2I
    comfy_edit_workflow: str = _DEFAULT_WORKFLOW_EDIT
    image_sizes: dict[str, ImageSize] = Field(default_factory=dict)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    default_negative_prompt: str = ""


def default_image_sizes() -> dict[str, ImageSize]:
    """默认尺寸：三视图/半身 3:4，全身 2:3；统一 1k 档。"""
    sizes = {kind: ImageSize() for kind in IMAGE_KIND_KEYS}
    for kind in ("front", "side", "behind"):
        sizes[kind] = ImageSize(aspect_ratio="2:3")
    return sizes


def get_settings(conn: sqlite3.Connection) -> AppSettings:
    data = storage.list_settings(conn)
    sizes = default_image_sizes()
    raw_sizes = data.get("image_sizes")
    if isinstance(raw_sizes, dict):
        for kind, value in raw_sizes.items():
            if kind in sizes and isinstance(value, dict):
                try:
                    sizes[kind] = ImageSize.model_validate(value)
                except Exception:  # noqa: BLE001 - 脏数据回退默认
                    continue
    llm_raw = data.get("llm")
    llm = LlmSettings.model_validate(llm_raw) if isinstance(llm_raw, dict) else LlmSettings()
    return AppSettings(
        comfy_t2i_workflow=str(data.get("comfy_t2i_workflow") or _DEFAULT_WORKFLOW_T2I),
        comfy_edit_workflow=str(data.get("comfy_edit_workflow") or _DEFAULT_WORKFLOW_EDIT),
        image_sizes=sizes,
        llm=llm,
        default_negative_prompt=str(data.get("default_negative_prompt") or ""),
    )


def save_settings(conn: sqlite3.Connection, patch: dict) -> AppSettings:
    """partial 合并：只更新 patch 里出现的顶层键。

    llm 三项整行覆盖，但 api_key 空串 = 保留已存值：
    读接口永远掩码不回显 key，前端无法回填，空串只能是「没改」。
    """
    for key, value in patch.items():
        if key not in ("comfy_t2i_workflow", "comfy_edit_workflow", "image_sizes", "llm", "default_negative_prompt"):
            continue
        if key == "llm" and isinstance(value, dict):
            value = {**value}
            if not str(value.get("api_key") or "").strip():
                value["api_key"] = get_settings(conn).llm.api_key
        storage.set_setting(conn, key, value)
    return get_settings(conn)


def llm_overrides(conn: sqlite3.Connection) -> dict[str, str] | None:
    """把 DB 设置里非空的 LLM 三项整理成 llm.get_llm_config 的 override dict。"""
    settings = get_settings(conn)
    overrides: dict[str, str] = {}
    for key in ("base_url", "model", "api_key"):
        value = getattr(settings.llm, key)
        if value.strip():
            overrides[key] = value.strip()
    return overrides or None
