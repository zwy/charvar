"""Pydantic 数据模型（设计文档 §4）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class VariantSpec(BaseModel):
    """一条变体的结构化描述（组合前）。

    framing/angle/expression 为可选结构化字段，caption 与元数据用它；
    text 是拼进最终提示词的原始片段（list 预设逐字保留，matrix 预设展开产物）。
    """

    id: str
    framing: Literal["full", "chest", "head"] | None = None
    angle: str | None = None
    expression: str | None = None
    text: str
    tags: list[str] = Field(default_factory=list)


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
