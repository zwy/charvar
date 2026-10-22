"""MD 模板 → 主体档案（SubjectProfile）候选的派生流程。

需求：外部项目/agent 手里往往只有一份「角色全场景通用提示词模板」.md，
要从它派生出 YAML 档案。LLM 只当抽取器，不当编辑：锁定项必须能回指模板
原文（防幻觉），派生结果还要过一轮独立审计，最后由人或 agent 终审。

四层闸门：
① 机器校验：pydantic 严格校验 + ``quality_issues`` 软校验（单条长度/条数/
   跨组重复/subject_noun/锁内场景词/extra 长度）；
② 引用核对（``--lang zh`` 默认）：每条锁定项与模板做规整化 shingle 覆盖率
   匹配，未命中退回 LLM 改为逐字摘录——锁定项只能是「从模板里挑句子」，
   不能编造设定（``--lang en`` 无机械核对，靠审计 + YAML 注释出处兜底）；
③ 回读审计：第二次 LLM 只报 omission / contradiction / violation，不过则
   带问题单重抽（保险上限 3 轮，常规 1 轮即过）；
④ 终审：derive 默认只写 YAML 文件 + 报告，不落库；``--apply`` 才入库。

所有 LLM 调用走 charvar.llm.chat_json（bypass 代理、structured outputs），
config 语义与 llm.get_llm_config 相同（显式 override > 环境变量/.env）。
无随机源：LLM 是起草性质调用（suggest-prompt 同性质），不进确定性组装链路。
"""
from __future__ import annotations

import json
import re
import yaml
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from charvar import llm
from charvar.llm_prompts import _TEMPLATE_MAX_CHARS, _clip_template
from charvar.models import SubjectProfile

DEFAULT_REFERENCE_TAG = "<image1>"
SUGGESTED_NOUNS = frozenset(
    {"person", "woman", "man", "girl", "boy", "character", "creature", "animal"}
)
# 锁定项场景词（瘦身红线：背景/布光/镜头归场景采样，不该变成身份锁定；告警不阻断）
_SCENE_LOCK_RE = re.compile(
    r"\bbackground\b|\bbackdrop\b|\blighting\b|\bcamera\b|\bframing\b|背景|布光|灯光|打光|镜头",
    re.I,
)

# 设定板/多视图版面结构词：出现在锁定项或 extra 里就是污染（模板场景模块章节混入的版面描述）
_SHEET_LAYOUT_RE = re.compile(
    r"设定板|三视图|多视角|视图|版面|分格|并排|同页|同一页|panel|character sheet|\bviews?\b"
    r"|[三个四个两个两]\s*个?[^，。;；]{0,6}(?:全身|视图|立绘)"
    r"|(?:左|右)侧[^，。;；]{0,8}全身",
    re.I,
)

MAX_LOCK_CHARS = 200  # 单条锁定项软上限
MAX_LOCK_COUNT = 10   # 每组条数软上限
MAX_EXTRA_CHARS = 300  # extra 软上限
CITATION_THRESHOLD = 0.8  # shingle 覆盖率阈值（zh 抽取模式）

_TEMPLATE_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

# ---- structured outputs（strict 兼容：全属性 required、空串表示可选项） ----

DRAFT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "subject_noun": {"type": "string"},
        "trigger_word": {"type": "string"},
        "style_lock": {"type": "array", "items": {"type": "string"}},
        "identity_lock": {"type": "array", "items": {"type": "string"}},
        "outfit_lock": {"type": "array", "items": {"type": "string"}},
        "extra": {"type": "string"},
        "candidates": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": [
        "id", "name", "subject_noun", "trigger_word", "style_lock",
        "identity_lock", "outfit_lock", "extra", "candidates", "notes",
    ],
    "additionalProperties": False,
}

VERIFY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "quote": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["severity", "quote", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pass", "issues"],
    "additionalProperties": False,
}


class DeriveError(ValueError):
    """派生流程的业务错误（CLI 转 exit 1）。"""


# ---------- 模板标题 ----------

def template_title(template_md: str) -> str | None:
    """取模板第一个 H1（# 瑜伽老师｜全场景通用提示词模板）。"""
    if match := _TEMPLATE_TITLE_RE.search(template_md or ""):
        title = match.group(1).strip()
        return title or None
    return None


# ---------- 闸门① 质量软校验 ----------

def quality_issues(profile: SubjectProfile) -> list[str]:
    """软校验（警告不阻断）：单条太长、条数太多、跨组重复、noun 生僻、extra 过长、场景词/设定板结构词。"""
    warnings: list[str] = []
    lock_groups = {"style_lock": profile.style_lock, "identity_lock": profile.identity_lock, "outfit_lock": profile.outfit_lock}
    for group, items in lock_groups.items():
        if len(items) > MAX_LOCK_COUNT:
            warnings.append(f"{group} 有 {len(items)} 条（软上限 {MAX_LOCK_COUNT}）：锁定项会逐条拼进每张图，考虑瘦身")
        for item in items:
            if len(item) > MAX_LOCK_CHARS:
                warnings.append(f"{group} 单条超过 {MAX_LOCK_CHARS} 字符：{item[:60]}…")
            match = _SCENE_LOCK_RE.search(item)
            if match:
                warnings.append(f"{group} 含场景词「{match.group(0)}」（背景/布光/镜头归场景采样，不建议写成锁定项）：{item[:60]}…")
            match = _SHEET_LAYOUT_RE.search(item)
            if match:
                warnings.append(
                    f"{group} 含设定板结构词「{match.group(0)}」（模板设定板/场景模块章节的版面描述混进来了，"
                    f"单人档案不认这些）：{item[:60]}…"
                )
    seen: dict[str, list[str]] = {}
    for group, items in lock_groups.items():
        for item in items:
            seen.setdefault(item, []).append(group)
    for item, groups in seen.items():
        if len(groups) > 1:
            warnings.append(f"同一条锁定项出现在 {('、').join(groups)} 组：{item[:60]}…")
    noun = profile.subject_noun.strip().lower()
    if noun and noun not in SUGGESTED_NOUNS:
        warnings.append(
            f"subject_noun「{profile.subject_noun}」不在建议范围（{'/'.join(sorted(SUGGESTED_NOUNS))}）：会原样拼进提示词"
        )
    if profile.extra:
        match = _SHEET_LAYOUT_RE.search(profile.extra)
        if match:
            warnings.append(
                f"extra 含设定板结构词「{match.group(0)}」：模板设定板章节（如「四个视图…四处一致」）"
                "不许摘成 extra——那是版面描述，拼进单人变体提示词就是污染"
            )
    if len(profile.extra) > MAX_EXTRA_CHARS:
        warnings.append(f"extra 超过 {MAX_EXTRA_CHARS} 字符（现 {len(profile.extra)}）：兜底句建议短")
    return warnings


# ---------- 闸门② 引用核对（zh 抽取模式） ----------

# 删空白与中英文标点，只留实质字符：摘录时标点/换行差异不误伤
_STRIP_RE = re.compile(
    r"[\s,，。.、:：;；!！?？·\"'“”‘’()（）\[\]【】《》…\-=—/|<>%*#+~·\\]+"
)


def _norm(text: str) -> str:
    return _STRIP_RE.sub("", text or "").lower()


def citation_coverage(entry: str, template_norm: str, template_shingles: set[str]) -> float:
    """条目与模板的 2-gram 覆盖率（子串命中=1.0；规整化后短条目按子串判）。"""
    entry_norm = _norm(entry)
    if not entry_norm:
        return 0.0
    if len(entry_norm) < 6:
        return 1.0 if entry_norm in template_norm else 0.0
    grams = {entry_norm[i:i + 2] for i in range(len(entry_norm) - 1)}
    return sum(g in template_shingles for g in grams) / len(grams)


def citation_misses(template_md: str, profile: SubjectProfile) -> list[tuple[str, str]]:
    """闸门②：返回 [(组名, 条目)] —— 覆盖率不达标的锁定条目（zh 抽取模式）。"""
    template_norm = _norm(template_md)
    template_shingles = {template_norm[i:i + 2] for i in range(len(template_norm) - 1)}
    misses: list[tuple[str, str]] = []
    for group in ("style_lock", "identity_lock", "outfit_lock"):
        for entry in getattr(profile, group):
            if citation_coverage(entry, template_norm, template_shingles) < CITATION_THRESHOLD:
                misses.append((group, entry))
    return misses


# ---------- prompt builders ----------

def _lock_groups_rule(lang: str) -> str:
    if lang == "en":
        return "- style_lock/identity_lock/outfit_lock 三组用英文（翻译模板对应句，保留专有名词与服装细节，不丢信息）。"
    return (
        "- style_lock/identity_lock/outfit_lock 三组用模板原文（模板是中文就出中文条目）："
        "每条必须是模板正文的近逐字摘录，允许删词收紧，禁止改写、翻译或引申。extra 除外（形态见字段语义）。"
    )


def _field_semantics() -> str:
    return (
        "- id：slug（^[a-zA-Z0-9_-]+$）。未指定时从模板标题推导稳定 slug（如《# 瑜伽老师》→ yoga_teacher）。\n"
        "- name：显示名（中文可），取模板标题/正文中的角色名，不改写。\n"
        "- subject_noun：单数英文提示词名词，优先从 person/woman/man/girl/boy/character/creature/animal 选。\n"
        "- trigger_word：训练 caption 首位标签，不进出图提示词；给与 id 同源的短词或留空，"
        "**绝不装身份细节**（那要写进 identity_lock/outfit_lock）。\n"
        f"- reference_tag：固定 {DEFAULT_REFERENCE_TAG}，不要改。\n"
        "- style_lock：画风/媒介/渲染质感（如真实摄影 vs 插画平涂、禁止 3D/蜡感皮肤）。\n"
        "- identity_lock：脸型/五官/发型/肤色（识别锚点表对应行）。\n"
        "- outfit_lock：服装/配饰（识别锚点表的上装/下装行）。\n"
        "- extra：给跨姿态/跨场景出图用的兜底一致性句（Keep the same ... in every pose 式）："
        "只强调身份特征与同一套服装在任意新姿态/新场景中保持一致；"
        "**不复述服装清单**（outfit_lock 已有，复述即污染），"
        "**绝对不提多视图/设定板结构**（如四个视图、右侧三个全身、同页、并排、分格——"
        "那是设定图的版面描述，拼进单人场景提示词就是污染），也不提具体姿态。\n"
        "- candidates：仅当模板存在多套候选（正常/精简版、多套服装、双主体分段）时，"
        "列出每个候选的一句话描述；无歧义为空数组。\n"
        "- notes：补充说明（选用了哪个版本、有无不确定），可空。"
    )


def build_draft_messages(
    template_md: str,
    lang: str,
    *,
    locked: dict[str, str] | None = None,
    version_hint: str = "",
    repair_notes: list[str] | None = None,
) -> list[dict]:
    """起草（或带问题单重抽）的 messages → DRAFT_SCHEMA JSON。"""
    if lang not in ("zh", "en"):
        raise DeriveError(f"lang 只支持 zh/en，收到：{lang}")
    locked = locked or {}
    locked_lines = [f"- {key}：{value}（调用方指定，原样使用）" for key, value in locked.items()]
    rules = [
        _lock_groups_rule(lang),
        "- 锁定项只能来自模板正文：每条必须能在模板里找到对应原文；不得引入模板没有的设定。",
        "- 锁定项不写背景、布光、镜头类内容（那是场景采样/构图，不是身份锁定）。",
        "- 素材范围：身份/服装/画风事实优先取【识别锚点表】和【固定前缀】章节；"
        "其他章节（设定板/多视角/场景模块）只允许摘取其中**明确的服装或身份事实条目**"
        "（锚点表没有的服装细节就在这类章节里，不许因此遗漏），"
        "但摘录时必须剥掉版面与构图结构——视图数量（四个视图/左侧三个全身/三视图）、"
        "同页/并排/分格/入镜框线这类排布描述一条都不许进档案；"
        "「右肩背书包」这类穿着部位是服装事实，保留。",
        (f"- version_hint：「{version_hint}」——只对匹配该关键词的模板版本抽取，忽略其他版本，candidates 留空。"
         if version_hint else
         "- 模板存在多套候选（正常/精简、多套服装、双主体分段）时不要猜：输出 candidates 列出每个候选的一句话描述，等调用方裁定。"),
        "- 模板里的版本说明、字符预算、证据状态等元信息不要抄进锁定项。",
    ]
    repair_block = ""
    if repair_notes:
        repair_block = (
            "\n\n【上一稿的问题】（先修正这些，再输出一遍完整 JSON，所有字段都要给）：\n"
            + "\n".join(f"- {note}" for note in repair_notes)
        )
    system = (
        "你是角色档案建库员。输入「角色全场景通用提示词模板」（身份/服装/画风的唯一权威），"
        "任务是抽取一份 SubjectProfile 结构化候选（后续会机器校验与独立审计）。\n"
        "字段语义与消费关系（锁定项每条会被拼成一句 Maintain ... 进每张图的提示词，"
        "LLM 模式下作为重写依据）：\n" + _field_semantics() + "\n"
        "硬性规则：\n" + "\n".join(rules) + "\n"
        '只输出 JSON：{"id": ..., "name": ..., "subject_noun": ..., "trigger_word": ..., '
        '"style_lock": [...], "identity_lock": [...], "outfit_lock": [...], "extra": ..., '
        '"candidates": [...], "notes": ...}'
        + repair_block
    )
    user = (
        f"【模板标题】\n{template_title(template_md) or '（无 H1，从正文判断角色名）'}\n\n"
        f"【角色全场景通用提示词模板】\n{_clip_template(template_md) or '（空模板）'}"
        + (f"\n\n【调用方指定】\n" + "\n".join(locked_lines) if locked_lines else "")
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_verify_messages(profile: SubjectProfile, template_md: str) -> list[dict]:
    """闸门③ 审计的 messages → VERIFY_SCHEMA JSON。"""
    system = (
        "你是角色档案审计员。输入：角色全场景通用提示词模板（唯一权威）与一份从它抽取的"
        " SubjectProfile 候选。只检查这三类问题，其他一律不报：\n"
        "- omission：模板里明确的身份/服装/画风事实，档案缺失了关键条目（锚点表逐行核对）；\n"
        "- contradiction：档案条目与模板矛盾（颜色、款式、比例等）；\n"
        "- violation：把背景/布光/镜头写成了锁定项；把身份细节塞进了 name/trigger_word；"
        "extra 或锁定项残留多视图/设定板的版面结构语句（如「四个视图」「右侧三个全身…四处一致」"
        "「同页」「并排」「分格」）——那是设定图版面描述，不属于单人档案；"
        "extra 复述了整段服装清单（outfit_lock 已覆盖）。\n"
        "severity 取 omission/contradiction/violation；quote 引模板原文片段（找不到留空）；"
        "detail 用中文一句说清。轻微措辞差异、删词收紧不算问题；没有问题时 pass=true。\n"
        '只输出 JSON：{"pass": <bool>, "issues": [{severity, quote, detail}]}'
    )
    user = (
        f"【角色档案候选】\n{json.dumps(profile.model_dump(), ensure_ascii=False, indent=1)}\n\n"
        f"【角色全场景通用提示词模板】\n{_clip_template(template_md) or '（空模板）'}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------- 候选 → SubjectProfile ----------

def profile_from_draft(draft: dict, *, subject_id: str = "", name: str = "") -> SubjectProfile:
    """把 LLM 候选整成 SubjectProfile（校验失败抛 pydantic ValidationError，调用方修理）。"""
    locks = {
        group: [text for text in (str(item).strip() for item in draft.get(group) or []) if text]
        for group in ("style_lock", "identity_lock", "outfit_lock")
    }
    data = {
        "id": (subject_id or str(draft.get("id") or "")).strip(),
        "name": (name or str(draft.get("name") or "")).strip() or str(draft.get("id") or "").strip(),
        "subject_noun": str(draft.get("subject_noun") or "").strip() or "person",
        "trigger_word": str(draft.get("trigger_word") or "").strip() or None,
        "reference_tag": DEFAULT_REFERENCE_TAG,
        "extra": str(draft.get("extra") or "").strip(),
        **locks,
    }
    return SubjectProfile.model_validate(data)


def draft_is_ambiguous(draft: dict) -> bool:
    return bool(draft.get("candidates"))


# ---------- 编排 ----------

_MAX_ROUNDS = 3  # 起草/重抽保险上限（常规 1 轮：草 + 审计即过）


@dataclass
class DeriveOutcome:
    """派生结果与全程证据（报告打印 / YAML 注出处 / --json 输出都吃它）。"""

    profile: SubjectProfile
    lang: str
    draft_rounds: int
    verify_rounds: int
    verify_pass: bool
    verify_issues: list[dict]
    citation_misses: list[tuple[str, str]]  # 终稿剩余未命中（应为空；en 模式恒空）
    citation_repaired: list[tuple[str, str]]  # 重抽后修正的条目（zh 模式）
    quality_warnings: list[str]
    notes: str = ""

    def citation_summary(self) -> str:
        if self.lang == "en":
            return "en 翻译模式：无机械引用核对（依赖审计与人工比对）"
        if not self.citation_repaired:
            return f"引用核对：{self._lock_count()}/{self._lock_count()} 条命中，无需修复"
        fixed, left = len(self.citation_repaired), len(self.citation_misses)
        total = self._lock_count()
        return f"引用核对：命中 {total - fixed - left}/{total}，修复 {fixed} 条" + (
            f"，剩余未命中 {left} 条" if left else ""
        )

    def _lock_count(self) -> int:
        return (
            len(self.profile.style_lock)
            + len(self.profile.identity_lock)
            + len(self.profile.outfit_lock)
        )


def _ambiguous_error(draft: dict) -> DeriveError:
    candidates = [str(c) for c in draft.get("candidates") or [] if str(c).strip()]
    listing = "\n".join(f"  {i}. {c}" for i, c in enumerate(candidates, start=1))
    return DeriveError(
        "模板存在多套候选（多版本服装 / 正常精简双版本 / 双主体），无法替你裁定：\n"
        f"{listing}\n"
        "（用 --version <关键词> 指定抽取哪个版本，或确认模板版本后重跑）"
    )


def derive(
    template_md: str,
    *,
    lang: str = "zh",
    subject_id: str = "",
    name: str = "",
    version_hint: str = "",
    config: dict | None = None,
) -> DeriveOutcome:
    """MD 模板 → 档案候选：起草 → 引用核对（zh）→ 审计，不过带问题单重抽。

    config: llm.get_llm_config 的 override dict（CLI 传 DB 设置 + 环境变量兜底）。
    歧义（candidates 非空且无 version_hint）在第一轮就抛 DeriveError，不浪费调用。
    """
    if lang not in ("zh", "en"):
        raise DeriveError(f"lang 只支持 zh/en，收到：{lang}")
    if not (template_md or "").strip():
        raise DeriveError("模板为空：先提供「角色全场景通用提示词模板」.md")

    locked: dict[str, str] = {}
    if subject_id:
        locked["id"] = subject_id
    if name:
        locked["name"] = name

    repair_notes: list[str] = []
    citation_repaired: list[tuple[str, str]] = []
    drafts = 0
    verifies = 0
    warnings: list[str] = []
    last_profile: SubjectProfile | None = None
    last_issues: list[dict] = []

    for round_no in range(1, _MAX_ROUNDS + 1):
        drafts += 1
        try:
            draft = llm.chat_json(
                build_draft_messages(
                    template_md, lang, locked=locked, version_hint=version_hint,
                    repair_notes=repair_notes or None,
                ),
                schema=DRAFT_SCHEMA,
                schema_name="subject_profile_draft",
                config=config,
            )
        except llm.LLMParseError as exc:
            # 输出非法/被截断（chat_json 已试过截取修复）：作为可重试问题进入下一轮
            if round_no == _MAX_ROUNDS:
                raise DeriveError(f"LLM 两次输出都不是合法 JSON：{exc}") from exc
            repair_notes = ["上一稿输出不是合法 JSON（可能被截断或含未转义引号）：重新输出完整 JSON"]
            continue
        except llm.LLMError as exc:
            raise DeriveError(f"LLM 起草档案失败：{exc}") from exc
        if draft_is_ambiguous(draft) and not version_hint:
            raise _ambiguous_error(draft)
        try:
            profile = profile_from_draft(draft, subject_id=subject_id, name=name)
        except ValidationError as exc:
            if round_no == _MAX_ROUNDS:
                raise DeriveError(f"LLM 起草的档案字段不合规：{exc}") from exc
            repair_notes = [f"id 必须匹配 ^[a-zA-Z0-9_-]+$ 等字段规范（{exc}）；请修正后重新输出"]
            continue

        last_profile = profile
        warnings = quality_issues(profile)
        last_issues = []

        if lang == "zh":
            misses = citation_misses(template_md, profile)
            if misses:
                if round_no == _MAX_ROUNDS:
                    raise _citation_error(misses, fixed=citation_repaired)
                citation_repaired.extend(misses)
                repair_notes = [f"「{entry}」（{group}）未能在模板里找到对应原文；改为从模板逐字摘录，禁止凭印象改写" for group, entry in misses]
                continue  # 重抽

        verifies += 1
        verdict: dict | None = None
        for attempt in range(1, 3):  # 审计输出非法 JSON 就地重试一次（不值得为此重起草）
            try:
                verdict = llm.chat_json(
                    build_verify_messages(profile, template_md),
                    schema=VERIFY_SCHEMA,
                    schema_name="subject_profile_verify",
                    config=config,
                )
                break
            except llm.LLMParseError as exc:
                if attempt == 2:
                    raise DeriveError(f"LLM 审计输出不是合法 JSON：{exc}") from exc
        if verdict is None:  # pragma: no cover - 上面已保证
            raise DeriveError("LLM 审计未返回结果")
        issues = [i for i in (verdict.get("issues") or []) if (i.get("detail") or "").strip()]
        if verdict.get("pass") and not issues:
            return DeriveOutcome(
                profile=profile, lang=lang, draft_rounds=drafts, verify_rounds=verifies,
                verify_pass=True, verify_issues=[], citation_misses=[],
                citation_repaired=citation_repaired, quality_warnings=warnings,
                notes=str(verdict.get("notes") or ""),
            )
        last_issues = issues
        if round_no == _MAX_ROUNDS:
            break
        repair_notes = [f"审计问题（{i.get('severity') or 'issue'}）：{(i.get('detail') or '')[:200]}" for i in issues]

    raise _unverified_error(last_issues, citation_repaired)


def _citation_error(misses: list[tuple[str, str]], *, fixed: list[tuple[str, str]]) -> DeriveError:
    detail = "\n".join(f"  - {group}：{entry}" for group, entry in misses[:10])
    return DeriveError(
        f"引用核对两轮后仍有 {len(misses)} 条锁定项在模板中找不到对应原文（已修复 {len(fixed)} 条）：\n{detail}\n"
        "（LLM 可能在编造设定——人工核对模板后用 charvar subject add 直接导入，或改用 --lang en）"
    )


def _unverified_error(issues: list[dict], fixed: list[tuple[str, str]]) -> DeriveError:
    detail = "\n".join(f"  - [{i.get('severity')}] {i.get('detail')}" for i in issues[:10])
    return DeriveError(
        "审计两轮未通过（档案与模板仍有出入），未写出任何文件：\n"
        f"{detail}\n"
        "（人工核对上面问题单后改模板或手写 YAML；也可 --lang en 重跑）"
    )


# ---------- YAML 落盘 ----------

def write_profile_yaml(
    path: Path,
    profile: SubjectProfile,
    *,
    source_name: str,
    source_sha256: str,
    model: str,
    outcome: "DeriveOutcome",
) -> None:
    """档案 YAML + 出处注释头（LLM 产物必须能回指来源，审计信息写进注释）。"""
    body = yaml.safe_dump(
        profile.model_dump(exclude_none=True),  # type: ignore[arg-type]
        allow_unicode=True, sort_keys=False,
    )
    header = (
        f"# 本文件由 `charvar subject derive` 生成（LLM 抽取 + 引用核对 + 审计），可人工修订。\n"
        f"# 来源模板：{source_name}（sha256:{source_sha256[:12]}）\n"
        f"# 语言：{outline_lang(outcome.lang)}  模型：{model or '默认'}  时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"# 起草 {outcome.draft_rounds} 轮 · 审计 {outcome.verify_rounds} 轮 · "
        f"{'审计通过' if outcome.verify_pass else '审计未通过'} · {outcome.citation_summary()}\n"
    )
    path.write_text(header + body, encoding="utf-8")


def outline_lang(lang: str) -> str:
    return {"zh": "zh（模板原文摘录）", "en": "en（翻译）"}.get(lang, lang)