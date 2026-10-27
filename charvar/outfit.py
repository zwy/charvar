"""服装预设（presets/outfits/*.yaml）与跨预设笛卡尔积组合。

角色：
- 服装预设是新的 YAML 预设家族：每条 = 英文服装描述段（text）+ 中文展示名（label）
  + caption token 映射；与独立于主体的动作/表情预设配套使用。
- 组合（combine）：动作/表情预设（list 或 matrix）× 服装预设 → 合成 list 预设
  （变体 = 笛卡尔积），composer / llm_compose 拿到的仍是一张普通 ListPreset，
  两条出图路径零改动。
- 组合语义（与白裙丝袜实测红线一致）：
  - 服装段**追加在动作段之后**（动作段吃前部权重；服装段必须短，否则动作被稀释）；
  - caption 在角度/动作之后追加服装 token（映射取服装预设 caption_tags，缺省=id）；
  - 变体 id = "{outfit_id}__{pose_id}"（outfit_first）或 "{pose_id}__{outfit_id}"
    （pose_first）——顺序决定后续轮播合成（reel）的播放顺序。
- 独立家族：list_presets() 只扫 presets/*.yaml 顶层，服装预设
  在 presets/outfits/ 子目录，不会混进主预设下拉。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from charvar import paths
from charvar.composer import expand_matrix, terminate_sentence
from charvar.models import VariantSpec
from charvar.preset_loader import ListPreset, MatrixPreset, PresetError
from charvar.subject_policy import SubjectPolicy

# 服装 id 只进变体 id / tag / 展示名，不落盘文件名，但仍按 slug 白名单校验，
# 避免「白丝·蕾」之类进系统后与顺序分隔符（__）混排。
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

COMBINE_ORDERS = ("outfit_first", "pose_first")


class OutfitItem(BaseModel):
    """一条服装：描述段进提示词、中文 label 给贴字/展示、id 进变体名与 caption。

    text 的语言不限（中文 / 英文都合法）：服装段整段进提示词，语言跟随预设原文，
    不由程序翻译——译写会让版型走样，程序兜底补写用的也是原文。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    text: str
    # 可选：这条服装对主体档案锁定项的处理策略（换装 / 清除衣物用）
    subject_policy: SubjectPolicy | None = None


class OutfitPreset(BaseModel):
    """服装预设（presets/outfits/*.yaml）。与动作预设组合时按 items 笛卡尔积。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["outfit"] = "outfit"
    version: int = 1
    description: str = ""
    items: list[OutfitItem] = Field(min_length=1)
    # 服装 caption token 映射（id → 英文 token）；缺省 = 用 id 本身
    caption_tags: dict[str, str] | None = None


def outfits_dir() -> Path:
    return paths.presets_dir() / "outfits"


def parse_outfit_file(path: Path) -> OutfitPreset:
    if not path.exists():
        raise PresetError(f"服装预设文件不存在：{path}", status=404)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise PresetError(f"服装预设 {path} 不是合法 YAML 映射")
    try:
        preset = OutfitPreset.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError 等 → 统一 400
        raise PresetError(f"服装预设 {path} 校验失败：\n{exc}") from exc
    seen: set[str] = set()
    for item in preset.items:
        if not _ID_RE.match(item.id):
            raise PresetError(
                f"服装预设 {path.name} 的 id 非法：{item.id!r}"
                "（只允许字母/数字/下划线/连字符，且以字母或数字开头）"
            )
        if item.id in seen:
            raise PresetError(f"服装预设 {path.name} 的 item id 重复：{item.id}")
        seen.add(item.id)
        if not item.label.strip():
            raise PresetError(f"服装预设 {path.name} 的 {item.id} 缺少 label（中文展示名，贴字用）")
        if not item.text.strip():
            raise PresetError(f"服装预设 {path.name} 的 {item.id} 缺少 text（英文服装描述段）")
    return preset


def resolve_outfit(name: str) -> Path:
    """按服装预设名定位 YAML（presets/outfits/<name>.yaml；可带 .yaml 后缀）。"""
    base = outfits_dir()
    if "/" in name or "\\" in name or name in (".", ".."):
        raise PresetError(f"服装预设名不合法：{name!r}")
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"
    return base / name


def load_outfit(name: str) -> OutfitPreset:
    path = resolve_outfit(name)
    return parse_outfit_file(path)


def list_outfit_presets() -> list[dict[str, Any]]:
    base = outfits_dir()
    result: list[dict[str, Any]] = []
    if not base.exists():
        return result
    for path in sorted(base.glob("*.yaml")):
        try:
            preset = parse_outfit_file(path)
        except PresetError:
            continue
        result.append(
            {
                "name": preset.name,
                "type": preset.type,
                "version": preset.version,
                "count": len(preset.items),
                "description": preset.description,
                "items": [{"id": i.id, "label": i.label} for i in preset.items],
            }
        )
    return result


def _pose_specs(preset: ListPreset | MatrixPreset) -> list[VariantSpec]:
    if isinstance(preset, ListPreset):
        return list(preset.variants)
    return expand_matrix(preset)


def combine(
    pose_preset: ListPreset | MatrixPreset,
    outfit: OutfitPreset,
    order: str = "outfit_first",
) -> ListPreset:
    """动作/表情预设 × 服装预设 → 合成 list 预设（变体 = 笛卡尔积）。

    - order：outfit_first（默认，每件服装连播其全部动作）/ pose_first（每个动作
      连播其全部服装）；顺序即变体 index 顺序，也即后续 reel 合成的播放顺序。
    - 文本顺序：服装段**追加在动作段之后**（动作段独占提示词前部权重）。
    - caption_tags：动作预设的映射 + {"outfit": 服装映射} 合并。
    """
    if order not in COMBINE_ORDERS:
        raise PresetError(f"服装组合顺序只支持 {'/'.join(COMBINE_ORDERS)}，当前 {order!r}")
    pose_specs = _pose_specs(pose_preset)
    if not pose_specs:
        raise PresetError(f"预设 {pose_preset.name} 没有任何变体")

    merged_specs: list[VariantSpec] = []
    pairs: list[tuple[VariantSpec, OutfitItem]]
    if order == "outfit_first":
        pairs = [(pose, item) for item in outfit.items for pose in pose_specs]
    else:
        pairs = [(pose, item) for pose in pose_specs for item in outfit.items]

    seen: set[str] = set()
    for pose, item in pairs:
        spec_id = f"{item.id}__{pose.id}" if order == "outfit_first" else f"{pose.id}__{item.id}"
        if spec_id in seen:
            raise PresetError(f"服装组合产生重复变体 id：{spec_id}")
        seen.add(spec_id)
        merged_specs.append(
            VariantSpec(
                id=spec_id,
                framing=pose.framing,
                angle=pose.angle,
                expression=pose.expression,
                pose=pose.pose,
                text=f"{pose.text} {item.text.strip()}".strip(),
                # 分列留档：text 是合并文本（classic 模式直接拼），LLM 模式取这些段分开喂。
                # 输入若是发型组合产物（charvar/hairstyle.py 先于服装层组合，层级
                # 动作→发型→服装），动作原文取其 action_text、发型三字段原样带过。
                action_text=pose.action_text if pose.action_text is not None else pose.text,
                outfit_text=item.text.strip(),
                outfit_policy=(
                    item.subject_policy.model_dump() if item.subject_policy is not None else None
                ),
                tags=[*pose.tags, item.id],
                outfit=item.id,
                outfit_label=item.label,
                hairstyle=pose.hairstyle,
                hairstyle_label=pose.hairstyle_label,
                hairstyle_text=pose.hairstyle_text,
            )
        )

    pose_tags = dict(pose_preset.caption_tags or {})
    pose_tags["outfit"] = dict(outfit.caption_tags or {})
    return ListPreset(
        type="list",
        name=f"{pose_preset.name}+{outfit.name}",
        version=pose_preset.version,
        master_template=pose_preset.master_template,
        suffix_template=pose_preset.suffix_template,
        backgrounds=list(pose_preset.backgrounds),
        lighting=list(pose_preset.lighting),
        caption_tags=pose_tags,
        caption_background_suffix=pose_preset.caption_background_suffix,
        reference_order=list(pose_preset.reference_order),
        schema_version=1,
        purpose=pose_preset.purpose,
        text_policy=pose_preset.text_policy,
        composition_policy=pose_preset.composition_policy,
        preferred_aspect_ratio=pose_preset.preferred_aspect_ratio,
        variants=merged_specs,
    )


# ---- LLM 模式：服装是否已落实到提示词里的校验与补写 ----

# 服装描述段的实词（≥4 字符、去停用词）——用来判断改写结果里有没有这套衣服。
_OUTFIT_STOPWORDS = frozenset(
    """
    with that this from into and the are was were been being they them their there here
    worn wear wearing also plus over under near around each other such which while
    about above below very more most some any all both same than then when where
    """.split()
)

# 中文服装段没有空格分词，单字又没有区分度 → 按连续汉字块切二元组
# （顿号/逗号/分号会切断汉字块，二元组不跨标点）。
_CJK_RUN_RE = re.compile(r"[一-鿿]+")
_CJK_RE = re.compile(r"[一-鿿]")

# 两种文字各自的命中率下限。
_LATIN_MIN_RATIO = 0.5
_CJK_MIN_RATIO = 0.25


def outfit_keywords(text: str) -> list[str]:
    """服装描述段的内容词（判定「这套衣服写进去了没有」用）。

    拉丁文按单词切（≥4 字符、去停用词）；中文按连续汉字块的二元组切
    （中文没有空格分词，单字又没有区分度）。
    """
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", (text or "").lower())
    latin = [w for w in words if len(w) >= 4 and w not in _OUTFIT_STOPWORDS]
    cjk: list[str] = []
    for run in _CJK_RUN_RE.findall(text or ""):
        cjk.extend(run[i : i + 2] for i in range(len(run) - 1))
    return latin + cjk


def _coverage(keys: list[str], low: str, min_ratio: float) -> bool:
    if not keys:
        return True
    hit = 0
    for word in keys:
        base = word[:-1] if len(word) > 4 and word.endswith("s") else word
        if word in low or base in low:
            hit += 1
    return hit >= len(keys) * min_ratio


def outfit_covered(prompt: str, outfit_text: str) -> bool:
    """提示词里是否已落实这套服装。

    LLM 逐字抄服装原文时覆盖率 100%；整段被丢弃时接近 0%。
    拉丁词与中文二元组分别按各自阈值判定：拉丁词 50%（换同义词的改写也能过，
    只有真丢了才低于阈值）；中文二元组冗余度高，阈值放低到 25%，不然「写了但
    换了说法」会被误判成「整段丢了」而触发重复补写。
    """
    text = outfit_text or ""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", text.lower())
    latin = [w for w in words if len(w) >= 4 and w not in _OUTFIT_STOPWORDS]
    cjk: list[str] = []
    for run in _CJK_RUN_RE.findall(text):
        cjk.extend(run[i : i + 2] for i in range(len(run) - 1))
    low = (prompt or "").lower()
    return _coverage(latin, low, _LATIN_MIN_RATIO) and _coverage(cjk, low, _CJK_MIN_RATIO)


def insert_outfit_sentence(prompt: str, outfit_text: str) -> str:
    """把服装句补进提示词**最前面**。

    位置规则与 LLM 模式一致：服装是换装的主角，排在参考图锚定句、景别句、
    姿态句之前。程序兜底补写必须落同一个位置——模型写在前面、程序补在后面
    会变成两套服装互相打架。
    """
    sentence = terminate_sentence(outfit_text)
    if not sentence:
        return prompt
    return f"{sentence} {prompt.strip()}".strip()
