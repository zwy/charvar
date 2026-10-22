"""全局设置（需求：设置页 + SQLite；env 只作初始默认）。

- settings 表按顶层键一行 JSON 存储，partial 更新不会互踩其他键；
- AppSettings 是类型化门面：get_settings 把 DB 行合并到默认值上；
- LLM 三项（base_url/model/api_key）为空串时回退环境变量 LLM_*。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from charvar import storage

# 与 charvar.subject_images.IMAGE_KINDS 保持一致（避免循环导入，此处显式列出）
IMAGE_KIND_KEYS = ("character_sheet", "headshot", "front", "side", "behind")

_DEFAULT_WORKFLOW_T2I = "image_qwen_image_2_1_turbo_t2i_easy_api"
_DEFAULT_WORKFLOW_EDIT = "image_qwen_image_2_1_turbo_image_edit_easy_api"


class ImageSize(BaseModel):
    aspect_ratio: str = "3:4"
    resolution: Literal["1k", "2k"] = "1k"

    @field_validator("resolution", mode="before")
    @classmethod
    def _drop_removed_tiers(cls, value: Any) -> Any:
        """4k 档已下线；存量设置里还留着 "4k" 的降级到 1k，别让整条 ImageSize 校验失败。"""
        if isinstance(value, str) and value.strip().lower() not in ("1k", "2k"):
            return "1k"
        return value


class LlmSettings(BaseModel):
    base_url: str = ""  # 空串 = 用 env LLM_BASE_URL
    model: str = ""  # 空串 = 用 env LLM_MODEL
    api_key: str = ""  # 空串 = 用 env LLM_API_KEY；读接口永远掩码


class OpenRouterSettings(BaseModel):
    """OpenRouter 在线生图配置（按模式分两组，对齐 ComfyUI 的 t2i / edit 两个 workflow）。

    - ``t2i_*``：文生图（不带参考图）；``i2i_*``：图片编辑 / 参考生图（带参考图）。
      带不带参考图由调用方决定（主体档案看槽位、批量推送看勾选、临时生图看参考图列表）。
    - model 空串 = 用 env ``OP_IMAGE_T2I_MODEL`` / ``OP_IMAGE_I2I_MODEL``
      （再回退旧的 ``OP_IMAGE_MODEL``，最后 meta/muse-image）
    - resolution / quality 空串 = 用 env ``OP_IMAGE_T2I_RESOLUTION`` 等。
      值是 provider 清单里的枚举写法（如 "1K" / "4K" / "medium"）：
      **请求显式给了就不用它，没给才作为默认**；能不能发还得以所选模型的
      supported_parameters 枚举为准（charvar/openrouter_image 在调用时校验）。
    - api_key 空串 = 用 env OP_IMAGE_API_KEY，再缺省回退 LLM_API_KEY（同一家 OpenRouter）
    """

    model: str = ""  # 旧字段：仅作 t2i/i2i_model 都为空时的迁移来源，新代码不再写它
    api_key: str = ""  # 读接口永远掩码
    t2i_model: str = ""
    t2i_resolution: str = ""  # provider 格式（1K/2K/4K…），charvar 档位 1k/2k 也会被归一
    t2i_quality: str = ""  # auto/low/medium/high…，"auto" = 不发送该参数
    i2i_model: str = ""
    i2i_resolution: str = ""
    i2i_quality: str = ""


class DreaminaSettings(BaseModel):
    """即梦（Dreamina）官方 dreamina CLI 生图配置。

    不像 OpenRouter 分 t2i/i2i 两组：即梦的模型都有编辑能力，
    带不带参考图由调用方决定（带 = image2image，不带 = text2image），
    配置共用一组。

    - model 空串 = 用 env ``DREAMINA_IMAGE_MODEL``（再缺省 CLI 默认 5.0）；
    - resolution 空串 = 用 env ``DREAMINA_IMAGE_RESOLUTION``（再缺省 2k）。
      值按设置页口径（2K/4K）存，调 CLI 时归一小写（``charvar.dreamina_image`` 做）；
    - 鉴权走本机 ``dreamina login`` 的登录态，这里没有 key 可配；
    - 即梦不暴露 seed，charvar 的 seed 只记 manifest，不发送。
    """

    model: str = ""
    resolution: str = ""


class AppSettings(BaseModel):
    comfy_t2i_workflow: str = _DEFAULT_WORKFLOW_T2I
    comfy_edit_workflow: str = _DEFAULT_WORKFLOW_EDIT
    image_sizes: dict[str, ImageSize] = Field(default_factory=dict)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    dreamina: DreaminaSettings = Field(default_factory=DreaminaSettings)
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
    or_raw = data.get("openrouter")
    openrouter = (
        OpenRouterSettings.model_validate(or_raw) if isinstance(or_raw, dict) else OpenRouterSettings()
    )
    dm_raw = data.get("dreamina")
    dreamina = (
        DreaminaSettings.model_validate(dm_raw) if isinstance(dm_raw, dict) else DreaminaSettings()
    )
    # 迁移：旧的单模型配置（DB 里的 openrouter.model）平移到两个模式；
    # 新字段各自可再覆盖。model 字段本体保留在返回值里，读接口不再展示。
    if not openrouter.t2i_model:
        openrouter.t2i_model = openrouter.model
    if not openrouter.i2i_model:
        openrouter.i2i_model = openrouter.model
    return AppSettings(
        comfy_t2i_workflow=str(data.get("comfy_t2i_workflow") or _DEFAULT_WORKFLOW_T2I),
        comfy_edit_workflow=str(data.get("comfy_edit_workflow") or _DEFAULT_WORKFLOW_EDIT),
        image_sizes=sizes,
        llm=llm,
        openrouter=openrouter,
        dreamina=dreamina,
        default_negative_prompt=str(data.get("default_negative_prompt") or ""),
    )


def save_settings(conn: sqlite3.Connection, patch: dict) -> AppSettings:
    """partial 合并：只更新 patch 里出现的顶层键。

    llm 三项整行覆盖，但 api_key 空串 = 保留已存值：
    读接口永远掩码不回显 key，前端无法回填，空串只能是「没改」。
    openrouter 同理。
    """
    for key, value in patch.items():
        if key not in (
            "comfy_t2i_workflow",
            "comfy_edit_workflow",
            "image_sizes",
            "llm",
            "openrouter",
            "dreamina",
            "default_negative_prompt",
        ):
            continue
        if key in ("llm", "openrouter") and isinstance(value, dict):
            value = {**value}
            if not str(value.get("api_key") or "").strip():
                value["api_key"] = getattr(get_settings(conn), key).api_key
        storage.set_setting(conn, key, value)
    return get_settings(conn)


def openrouter_mode_config(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """每个生图模式（t2i 文生图 / i2i 图片编辑）的生效配置：DB > env。

    - model 走 openrouter_image.resolve_model 的完整链路（DB 值 > 模式 env >
      旧 OP_IMAGE_MODEL > 默认）；
    - resolution / quality 这里只做 DB > env 的合并，**枚举校验留给调用时**
      （openrouter_image 按 api-response.json 动态判断，清单一句话说不准）。
    """
    from charvar import openrouter_image

    settings = get_settings(conn)
    ors = settings.openrouter
    modes: dict[str, dict[str, str]] = {}
    for mode, model_attr in (("t2i", "t2i_model"), ("i2i", "i2i_model")):
        modes[mode] = {
            "model": openrouter_image.resolve_model(
                getattr(ors, model_attr).strip(), mode=mode
            ),
            "resolution": getattr(ors, f"{mode}_resolution").strip()
            or openrouter_image.env_mode_value(mode, "resolution"),
            "quality": getattr(ors, f"{mode}_quality").strip()
            or openrouter_image.env_mode_value(mode, "quality"),
        }
    return modes


def dreamina_config(conn: sqlite3.Connection) -> dict[str, str]:
    """即梦生图的生效配置：DB > env > 缺省。

    - model 走 dreamina_image.resolve_model 的完整链路（DB 值 > env >
      空 = CLI 默认 5.0）；
    - resolution 同链路（DB > env DREAMINA_IMAGE_RESOLUTION > 2k，归一小写）；
    - 模型-分辨率枚举校验与提示词长度预检在 charvar.dreamina_image 调用时做。
    """
    from charvar import dreamina_image

    settings = get_settings(conn)
    ds = settings.dreamina
    return {
        "model": dreamina_image.resolve_model(ds.model),
        "resolution": dreamina_image.resolve_resolution(ds.resolution),
    }


def llm_overrides(conn: sqlite3.Connection) -> dict[str, str] | None:
    """把 DB 设置里非空的 LLM 三项整理成 llm.get_llm_config 的 override dict。"""
    settings = get_settings(conn)
    overrides: dict[str, str] = {}
    for key in ("base_url", "model", "api_key"):
        value = getattr(settings.llm, key)
        if value.strip():
            overrides[key] = value.strip()
    return overrides or None
