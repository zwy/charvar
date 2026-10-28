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
    # 可选体形锁定（LLM 改写专用）：non-empty 时逐条追加进【参考图分工锚定块】，
    # 目的是换装时给胸/腰/臀体量留显式文本锚（classic 模式不使用，identity_lock 不动）。
    # 建议写英文短句（en/any 输出直接逐字采用；zh 模式由 LLM 译写）。
    body_lock: list[str] = Field(default_factory=list)
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
    # 服装组合（outfit.combine 产物）专用：服装 id + 中文展示名（贴字/展示用）。
    # YAML 预设不声明这两个字段；由服装组合时程序填入。
    outfit: str | None = None
    outfit_label: str | None = None
    # 服装组合：动作原文与服装英文描述段分列。text 仍是两段合并后的完整文本
    # （classic 模式照旧直接拼）；分列是为了 LLM 模式能把服装单独喂给模型
    # ——合并时服装段夹在动作文本尾部，模型常把它当成「参考图自带服装」整段丢弃。
    action_text: str | None = None
    outfit_text: str | None = None
    # 服装组合：这条服装对主体档案锁定项的处理策略（换装 / 清除衣物）。
    # YAML 预设不声明这个字段；由服装组合时程序从服装条目抄进变体。
    outfit_policy: dict | None = None
    # 发型组合（hairstyle.combine 产物）专用：发型 id + 中文展示名。
    # 与服装分列同一套逻辑：LLM 模式发型段走独立字段逐字英文；
    # 层级固定 动作→发型→服装（发型层先并、服装层后并）。
    hairstyle: str | None = None
    hairstyle_label: str | None = None
    hairstyle_text: str | None = None


class ReferenceSpec(BaseModel):
    """参考图规格：来源 + 在提示词里的分工（role）。

    来源四种（与 Playground 同源）：
    - ``staged_id``：上传暂存的图（``/adhoc/references`` 暂存池）；
    - ``subject_id`` + ``kind``：主体档案的图片槽位；
    - ``run_id``：Playground「最近生成」的原图；
    - ``pose_id`` + ``angle``：姿势库（pose-db-01）的人偶线稿 —— 只锚姿势构图，
      不锚任何身份外观（脸/发型/服装/配色来自主体档案或其它参考图）。

    ``role`` 决定这张图在 LLM 改写的锚定句里管哪一摊
    （front/headshot/side/behind/character_sheet/pose）；pose 来源缺省 ``pose``，
    其余缺省按 ``front`` 处理。不接受任意路径 —— 那是把文件读权限开给了浏览器。
    """

    staged_id: str | None = None
    subject_id: str | None = None
    kind: str | None = None
    run_id: str | None = None
    role: str | None = None
    # charvar run 的第几张原图（run_id 来源时用；缺省第 1 张）
    index: int | None = None
    # 姿势库来源：动作 id（如 full_sexy_P001）+ 视角（缺省正面）
    pose_id: str | None = None
    angle: str | None = None


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
    reference_kinds: list[str] = Field(default_factory=list)  # 1–4 个主体图类型（兼容旧调用）
    # 参考图（新流程，Playground 式来源）：提示词阶段只用 role 定锚定句分工，
    # 出图阶段按来源解析成本地文件。给了它就不再用 reference_kinds。
    references: list[ReferenceSpec] = Field(default_factory=list)
    llm_instructions: str | None = None
    # LLM 改写的输出语言（仅 mode=llm 生效）：
    #   zh  = 强制中文（中文模板/即梦等中文优先的后端）
    #   en  = 强制英文（SD 类只认英文的老模型）
    #   any = 不做强制性限制（缺省）：提示词里不写语言规定，输出语言由模型自行决定
    llm_language: Literal["zh", "en", "any"] = "any"
    # 服装组合：动作/表情预设 × 服装预设（presets/outfits/）笛卡尔积。
    # outfit_order 决定变体顺序（也即后续 reel 合成的播放顺序）。
    outfit_preset: str | None = None
    outfit_order: Literal["outfit_first", "pose_first"] = "outfit_first"
    # 发型组合：动作/表情预设 × 发型预设（presets/hairstyles/）笛卡尔积。
    # 层级固定 动作→发型→服装（发型层先并、服装层后并），无发型单独的顺序开关。
    hairstyle_preset: str | None = None


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
    # 本次用的参考图规格（来源 + role）：推送生图时按它取图，页面刷新也不丢
    references: list[dict] = Field(default_factory=list)
