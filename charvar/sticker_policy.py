"""带字表情包（sticker）共享策略：文字编译 + 冲突检测 + meta 快照。

classic 与 llm 两条生成路径共用本模块，避免两份文案拼接逻辑：

- render_lettering_block：把 display_text.content 原样编译成英文绘字指令。
  中文不交给 LLM 翻译/改写；content 是字面量——不展开 {a|b} wildcard、
  不做占位符解析、不执行模板代码，即使含引号/反斜杠/换行也用
  json.dumps(ensure_ascii=False) 可靠界定（双引号是提示词界定符，不许画出来）。
- validate_sticker_conflicts / find_negative_bans / llm_response_bans：只做有限
  字符串检测（明确的全局禁字指令与负向词阻断 token），不声称识别所有中英文矛盾；
  watermark/texture 不会被误报，"no extra text" 这类只禁多余文字的条款会放行。
  required_label 锚定句尾自带的 "render no other text…" 属同一族放行写法——模型
  被要求逐字复述锚定句，回声不能被输出过滤器判成「疑似绘字指令句」（否则 LLM
  模式每条都被拒，见 tests/test_sticker_policy.py 的锚定句回声回归测试）。
- sticker_meta：两种模式的 PromptItem.meta 都快照策略与文案；历史重放/再次入队
  读生成时快照，不依赖后来改动的 YAML。
"""
from __future__ import annotations

import json
import re

from charvar.models import DisplayText, LetteringStyle, SubjectProfile
from charvar.preset_loader import CommonPreset, ListPreset

# ---- 明确的全局禁字指令（提示词正文；限定作用域的写法不算） ----
# 词语边界/形态控制：不命中 texture（"text" 后接字母无边界）、watermark 等相近词；
# "no extra/unrelated/other text" 是「只禁多余文字」的兼容写法，先剥掉再检测。
ALLOWED_SCOPED_BANS = re.compile(
    r"\b(?:no|without|except|besides)\s+(?:the\s+)?(?:other|extra|additional|unrelated|specified)\s+"
    r"(?:text|writing|lettering|words|letters)\b",
    re.IGNORECASE,
)
# 同为「只禁多余文字」的否定式写法：do not render any other text / never write extra words 等。
# 模型被要求逐字采用 required_label 锚定句（自带 render no other text…），输出里出现
# 这类句子是复述指令而不是自己写绘字内容——不能反过来把锚定句判成违规。
NEGATED_RENDER_SCOPED_BANS = re.compile(
    r"\b(?:do(?:es)?\s+not|don'?t|not|never|no|without|avoid|except|besides)"
    r"\s+(?:\w+\s+){0,2}?(?:render|draw|write|letter)\b[^\n]{0,30}?"
    r"\b(?:other|extra|additional|unrelated|specified)\s+(?:text|writing|lettering|words|letters)\b",
    re.IGNORECASE,
)
GLOBAL_TEXT_BANS = (
    re.compile(r"\bno\s+text\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+text\b", re.IGNORECASE),
    re.compile(r"\btext[-\s]free\b", re.IGNORECASE),
    re.compile(r"\bfree\s+of\s+(?:any\s+)?text\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:any\s+)?(?:letters|lettering|writing|words)\b", re.IGNORECASE),
    re.compile(r"不要?出现文字"),
    re.compile(r"不(得|要|能)有文字"),
    re.compile(r"无文字"),
    re.compile(r"不含(任何)?文字"),
    re.compile(r"禁止(出现)?文字"),
)

# ---- 负向词/内置默认里的阻断 token：独立词（word 边界），"texture" 不受影响 ----
NEGATIVE_BAN_TOKENS = (
    re.compile(r"\btext\b", re.IGNORECASE),
    re.compile(r"\bletters?\b", re.IGNORECASE),
    re.compile(r"chinese\s+characters", re.IGNORECASE),
    re.compile(r"文字"),
)

# LLM 输出里出现的禁字句子（required_label 下拒绝该条并补调，不能全局替换蒙混）
LLM_RESPONSE_BAN_PATTERNS = GLOBAL_TEXT_BANS

_CJK_RE = re.compile(r"[一-鿿]")

_LANG_LABELS: dict[str, str] = {"zh": "Chinese", "zh-Hans": "Chinese", "zh-Hant": "Traditional Chinese"}


def is_sticker_preset(preset: object) -> bool:
    """新 sticker 合同只由 schema 2 声明；旧预设一律按无字 dataset 处理。"""
    return isinstance(preset, ListPreset) and getattr(preset, "purpose", "dataset") == "sticker"


def _lang_label(language: str) -> str:
    return _LANG_LABELS.get((language or "").strip(), (language or "").strip() or "target-language")


def _clean_tail_period(text: str) -> str:
    """去掉片段尾部句号：外层骨架统一补 "."，避免 "set.." 双句号。"""
    return (text or "").strip().rstrip(".").rstrip("。").strip()


def render_lettering_block(
    display_text: DisplayText,
    lettering: LetteringStyle | None,
) -> str:
    """display_text + lettering → 固定英文骨架的绘字指令段（中文原样在双引号内）。

    - 不抽样、不调 LLM、与随机无关（同一 content 同一输出）；
    - rules/placement/flow/emphasis 视为可信预设文本原样嵌入（不执行）；
    - 引号只出现在 json.dumps 的界定里，并显式禁止画出来。
    """
    quoted = json.dumps(display_text.content, ensure_ascii=False)
    parts: list[str] = [
        f"Render exactly the {_lang_label(lettering.language if lettering else 'zh-Hans')} "
        f"text {quoted} once as the only visible lettering.",
        "Preserve every character and punctuation mark exactly as written; do not translate, "
        "romanize, paraphrase, or invent extra words.",
        "Do not render the quotation marks themselves, and do not add any other text, "
        "captions, logos, signatures, or watermarks anywhere.",
        f"The lettering is a {display_text.layout}-type label.",
        f"Place it {display_text.placement}, arranged with {display_text.flow}.",
    ]
    if display_text.emphasis.strip():
        parts.append(
            f"Apply the accent treatment only to the intended emphasis ({display_text.emphasis}); "
            "the main text keeps its base style."
        )
    if lettering is not None:
        style_bits = [f"Lettering style: {_clean_tail_period(lettering.style)}"]
        if lettering.fill.strip():
            style_bits.append(f"fill {_clean_tail_period(lettering.fill)}")
        if lettering.outline.strip():
            style_bits.append(f"outline {_clean_tail_period(lettering.outline)}")
        if lettering.accent.strip():
            style_bits.append(f"accent {_clean_tail_period(lettering.accent)}")
        parts.append(". ".join(style_bits) + ".")
        percent = int(round(lettering.safe_margin * 100))
        parts.append(
            f"Keep all lettering at least {percent}% of the canvas length away from the image "
            "edges (safe margin) so no character is clipped or touches the border."
        )
        if lettering.rules.strip():
            parts.append(f"Lettering rules: {lettering.rules}")
    return " ".join(part.strip() for part in parts if part.strip())


# LLM 正文里的 affirmed 绘字指令（如 render the text / render the Chinese text…）：
# required_label 下绘字段由程序附加，指令重复引入翻译风险 → 拒绝该条并补调。
# 检测同样在剥掉「只禁多余文字」写法之后的文本上做，避免与锚定句放行冲突。
RENDER_TEXT_INSTRUCTION = re.compile(
    r"\brender\b[^\n]{0,60}\b(?:the\s+)?(?:chinese\s+)?text\b|写.{0,6}文字",
    re.IGNORECASE,
)


def _strip_scoped(text: str) -> str:
    """剥掉「只禁多余文字」的兼容写法（no other text / do not render extra words 等）。"""
    if not text:
        return ""
    candidate = ALLOWED_SCOPED_BANS.sub(" ", text)
    return NEGATED_RENDER_SCOPED_BANS.sub(" ", candidate)


def _find_bans(text: str, patterns: tuple) -> list[str]:
    """_strip_scoped 之后，返回命中的禁字模式（片段定位）。"""
    if not text:
        return []
    candidate = _strip_scoped(text)
    found = []
    for pattern in patterns:
        for match in pattern.finditer(candidate):
            found.append(match.group(0))
    return found


def _scan_strings(values: list[str]) -> list[str]:
    return _find_bans(" ".join(values), GLOBAL_TEXT_BANS)


def subject_text_bans(subject: SubjectProfile) -> list[str]:
    """主体档案锁定项里的明确全局禁字指令（冲突 → 阻断，要求用户改写）。"""
    values = [
        *subject.style_lock,
        *subject.identity_lock,
        *subject.outfit_lock,
        subject.extra,
    ]
    return _scan_strings(values)


def template_text_bans(template_md: str | None) -> list[str]:
    """模板里疑似禁字条款：作用域无法机械判断 → 不阻断，提示人工确认。"""
    if not template_md:
        return []
    return _scan_strings([template_md])


def find_negative_bans(negative_prompt: str | None) -> list[str]:
    """负向词阻断 token：独立 text/letters、chinese characters、文字（watermark 保留不误判）。"""
    if not negative_prompt:
        return []
    candidate = _strip_scoped(negative_prompt)
    found = []
    for pattern in NEGATIVE_BAN_TOKENS:
        for match in pattern.finditer(candidate):
            found.append(match.group(0))
    return found


def llm_response_bans(prompt: str | None) -> list[str]:
    """required_label 下 LLM 正文不得自带绘字相关内容：命中则拒绝该条（有界补调）。

    - 禁字禁句（no text 等）与中文字符都拒绝：最终中文由程序按原文附加，
      LLM 输出必须保持纯英文画面描述；错误信息可定位（引用命中片段）。
    - 「只禁多余文字」的兼容写法放行（no other text / do not render any other
      text 等）——required_label 锚定句自带 render no other text…，模型被要求
      逐字复述它，回声不算违规；真正的绘字指令（render the text…）仍拒绝。
    """
    if not prompt:
        return []
    candidate = _strip_scoped(prompt)
    issues: list[str] = []
    for pattern in LLM_RESPONSE_BAN_PATTERNS:
        for match in pattern.finditer(candidate):
            issues.append(match.group(0))
    if _CJK_RE.search(prompt):
        issues.append("中文字符（绘字段由程序附加，LLM 描述不得含中文）")
    if RENDER_TEXT_INSTRUCTION.search(candidate):
        issues.append("疑似绘字指令句")
    return issues


def workflow_default_negative_bans(workflow: dict, mapping: dict) -> list[str]:
    """workflow/mapping 自带默认值里的禁字 token（sticker 入队预检）。

    有限检测，不做全文解析：
    - mapping 里 negative_prompt 字段的 default 值（提交时会被注入值覆盖，但列入检查
      以便在 missing 注入字段时给出可读错误）；
    - mapping 缺 negative_prompt 注入字段时，启发式扫描 workflow 节点的纯字符串输入
      （真实负向文本通常以字符串输入存在；链式引用无法静态求值，检测不到则不报）。
    """
    has_negative_mapping = any(
        isinstance(entry, dict) and str(entry.get("field")) == "negative_prompt"
        for entry in (mapping or {}).get("mappings", [])
    )
    values: list[str] = []
    for entry in (mapping or {}).get("mappings", []):
        if isinstance(entry, dict) and str(entry.get("field")) == "negative_prompt":
            default = entry.get("default")
            if isinstance(default, str):
                values.append(default)
    if not has_negative_mapping:
        for node in (workflow or {}).values():
            if isinstance(node, dict):
                for value in (node.get("inputs") or {}).values():
                    if isinstance(value, str):
                        values.append(value)
    return find_negative_bans(" ".join(values))


def sticker_meta(preset: CommonPreset, spec) -> dict:
    """生成时策略快照（两种模式一致）：扁平写进 item.meta（schema_version/purpose/
    text_policy/composition_policy/preferred_aspect_ratio/display_text/lettering），
    历史重放/再次入队读它，不依赖后来改动的 YAML；老记录无这些键 → 回 legacy。"""
    if not is_sticker_preset(preset):
        return {}
    return {
        "schema_version": preset.schema_version,
        "purpose": preset.purpose,
        "text_policy": preset.text_policy,
        "composition_policy": preset.composition_policy,
        "preferred_aspect_ratio": preset.preferred_aspect_ratio,
        "display_text": spec.display_text.model_dump() if spec.display_text is not None else None,
        "lettering": preset.lettering.model_dump() if preset.lettering is not None else None,
    }


def validate_sticker_conflicts(
    subject: SubjectProfile,
    template_md: str | None = None,
    effective_negative: str | None = None,
) -> dict[str, list[str]]:
    """sticker 必需文案与现有禁字条款的冲突检查（有限字符串检测）。

    返回 {"errors": [...], "warnings": [...]}：
    - errors：主体锁定项/负向词的明确全局禁字 → 阻断生成，提示改写为「仅禁止多余文字」；
    - warnings：模板条款作用域无法机械判断 → 不阻断，列出片段请人工确认。
    """
    errors: list[str] = []
    warnings: list[str] = []
    subject_hits = subject_text_bans(subject)
    if subject_hits:
        errors.append(
            "主体档案锁定项含明确禁字条款：" + "；".join(f"「{hit}」" for hit in subject_hits)
            + "。带字表情包需要允许指定中文文案：请删除或改写为「除指定文案外无其他文字」"
        )
    template_hits = template_text_bans(template_md)
    if template_hits:
        warnings.append(
            "角色模板含疑似禁字条款：" + "；".join(f"「{hit}」" for hit in template_hits)
            + "。若它是全局禁句请先修改模板；「仅禁止多余文字」写法可以保留"
        )
    negative_hits = find_negative_bans(effective_negative)
    if negative_hits:
        errors.append(
            "负向提示词含禁字 token：" + "；".join(f"「{hit}」" for hit in negative_hits)
            + "。带字预设要求允许指定文案（quality/watermark 等无关负向词不受影响）"
        )
    return {"errors": errors, "warnings": warnings}