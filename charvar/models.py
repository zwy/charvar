"""Pydantic 数据模型（设计文档 §4）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SubjectProfile(BaseModel):
    """主体档案：一个角色/人物/物体。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$", description="slug，如 silver_witch")
    name: str
    subject_noun: str = "person"
    trigger_word: str | None = None
    reference_tag: str = "<image1>"
    style_lock: list[str] = Field(default_factory=list)
    identity_lock: list[str] = Field(default_factory=list)
    outfit_lock: list[str] = Field(default_factory=list)
    extra: str = ""


class LetteringStyle(BaseModel):
    """带字表情包的绘字样式（schema 2 预设专用；字段均来自 YAML 文本）。"""

    model_config = ConfigDict(extra="forbid")

    language: str
    style: str
    fill: str
    outline: str
    accent: str
    rules: str
    safe_margin: float = Field(ge=0.0, le=0.25, description="画布边长占比的安全边距")


class DisplayText(BaseModel):
    """图片上的中文文案（唯一真源）：content 原样保留，不放翻译/改写。

    emphasis 是设计提示（如 "second 来啦" 指第二组），不作为整句子串验证。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="非空：图片中的文字，逐字保留")
    layout: Literal["voice", "reaction", "state", "interaction"]
    placement: str
    flow: str
    emphasis: str

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content 不能为空白")
        return value


class VariantSpec(BaseModel):
    """一条变体的结构化描述（组合前）。

    framing/angle/expression 为可选结构化字段，caption 与元数据用它；
    pose 供动作类预设（体式/姿势）标注用，与 expression 分开——不占用表情语义；
    text 是拼进最终提示词的原始片段（list 预设逐字保留，matrix 预设展开产物）。
    display_text 仅 schema 2 sticker 预设携带（图片文案，schema 1 不得出现——
    由 preset_loader 校验，避免静默丢弃）。
    """

    id: str
    framing: Literal["full", "chest", "head"] | None = None
    angle: str | None = None
    expression: str | None = None
    pose: str | None = None
    text: str
    tags: list[str] = Field(default_factory=list)
    display_text: DisplayText | None = None


class GenerateRequest(BaseModel):
    subject_id: str | None = None
    subject: SubjectProfile | None = None
    preset: str = "q21_default_30"
    seed: int = 0
    count: int | None = None
    sampling: Literal["all", "first", "random"] = "all"
    randomize_background: bool = True
    randomize_lighting: bool = True
    fixed_background: str | None = None
    fixed_lighting: str | None = None
    separator: str = " "
    # LLM 优化模式（需求 §生成优化）：LLM 结果完全替代 master_template 拼接
    mode: Literal["classic", "llm"] = "classic"
    reference_kinds: list[str] = Field(default_factory=list)  # 1–4 个主体图类型
    llm_instructions: str | None = None


class PromptItem(BaseModel):
    index: int
    variant_id: str
    prompt: str
    master: str
    variant_text: str
    background: str | None
    lighting: str | None
    caption: str
    meta: dict = Field(default_factory=dict)


class GenerateResult(BaseModel):
    run_id: str
    subject_id: str
    preset: str
    seed: int
    items: list[PromptItem]
    # 生成期非阻断提示（如 sticker 模板里疑似禁字条款需人工确认）；旧记录缺省为空
    warnings: list[str] = Field(default_factory=list)
