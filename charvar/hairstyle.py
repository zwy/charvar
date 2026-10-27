"""发型预设（presets/hairstyles/*.yaml）——第二个可叠外观预设家族。

角色（与服装预设 charvar/outfit.py 同构）：
- 每条 = 英文发型描述段（text，只写长度/刘海/分缝/层次/卷度等**结构**，不写发色）
  + 中文展示名（label）+ caption token 映射（caption_tags，缺省 = 用 id 本身）。
- 发色继承主体档案或人物参考图，条目不绑定染发颜色；换发型批只放开头发结构，
  身份锁定保持权威（约定：identity_lock 里发型只写发色，不写结构）。
- 组合（combine）：动作/表情预设（list 或 matrix）× 发型预设 → 合成 list 预设。
  发型层先并、服装层后并（outfit.combine 把发型层产物当输入继续叠），
  自然产出层级 动作→发型→服装：变体 id = "{outfit_id}__{hairstyle_id}__{pose_id}"
  （仅发型 = "{hairstyle_id}__{pose_id}"）、text = 动作段 + 发型段 + 服装段。
- 文本顺序红线（同服装段）：发型段追加在动作段之后且必须短——动作段吃前部权重，
  前置长文本会把动作稀释到不执行。
- 独立家族：list_presets() 只扫 presets/*.yaml 顶层，发型预设
  在 presets/hairstyles/ 子目录，不会混进主预设下拉。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from charvar import paths
from charvar.composer import expand_matrix
from charvar.models import VariantSpec
from charvar.preset_loader import ListPreset, MatrixPreset, PresetError
# 覆盖判定与补写与服装家族共用同一实现（outfit.py：内容词覆盖率 ≥50% +
# 插在背景/光照句之前）。发型段与服装段同为「动作段之后的短英文描述段」，
# 判定/插入语义一致——别抄第二份逻辑。
from charvar.outfit import insert_outfit_sentence as insert_descriptor_sentence
from charvar.outfit import outfit_covered as descriptor_covered
from charvar.llm_prompts import BARE_HEAD_SENTENCE

# 发型 id 只进变体 id / tag / 展示名，不落盘文件名，但与服装同按 slug 白名单校验，
# 避免中文/杂字符混进变体 id 与顺序分隔符（__）。
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class HairstyleItem(BaseModel):
    """一条发型：英文结构描述段进提示词、中文 label 给展示、id 进变体名与 caption。"""

    id: str
    label: str
    text: str


class HairstylePreset(BaseModel):
    """发型预设（presets/hairstyles/*.yaml）。与动作预设组合时按 items 笛卡尔积。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["hairstyle"] = "hairstyle"
    version: int = 1
    description: str = ""
    items: list[HairstyleItem] = Field(min_length=1)
    # 发型 caption token 映射（id → 英文 token）；缺省 = 用 id 本身
    caption_tags: dict[str, str] | None = None


def hairstyles_dir() -> Path:
    return paths.presets_dir() / "hairstyles"


def parse_hairstyle_file(path: Path) -> HairstylePreset:
    if not path.exists():
        raise PresetError(f"发型预设文件不存在：{path}", status=404)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise PresetError(f"发型预设 {path} 不是合法 YAML 映射")
    try:
        preset = HairstylePreset.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError 等 → 统一 400
        raise PresetError(f"发型预设 {path} 校验失败：\n{exc}") from exc
    seen: set[str] = set()
    for item in preset.items:
        if not _ID_RE.match(item.id):
            raise PresetError(
                f"发型预设 {path.name} 的 id 非法：{item.id!r}"
                "（只允许字母/数字/下划线/连字符，且以字母或数字开头）"
            )
        if item.id in seen:
            raise PresetError(f"发型预设 {path.name} 的 item id 重复：{item.id}")
        seen.add(item.id)
        if not item.label.strip():
            raise PresetError(f"发型预设 {path.name} 的 {item.id} 缺少 label（中文展示名，展示用）")
        if not item.text.strip():
            raise PresetError(f"发型预设 {path.name} 的 {item.id} 缺少 text（英文发型描述段）")
    return preset


def resolve_hairstyle(name: str) -> Path:
    """按发型预设名定位 YAML（presets/hairstyles/<name>.yaml；可带 .yaml 后缀）。"""
    base = hairstyles_dir()
    if "/" in name or "\\" in name or name in (".", ".."):
        raise PresetError(f"发型预设名不合法：{name!r}")
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"
    return base / name


def load_hairstyle(name: str) -> HairstylePreset:
    path = resolve_hairstyle(name)
    return parse_hairstyle_file(path)


def list_hairstyle_presets() -> list[dict[str, Any]]:
    base = hairstyles_dir()
    result: list[dict[str, Any]] = []
    if not base.exists():
        return result
    for path in sorted(base.glob("*.yaml")):
        try:
            preset = parse_hairstyle_file(path)
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


def insert_hairstyle_sentence(prompt: str, hairstyle_text: str) -> str:
    """发型句兜底补写：发型原文 + 裸头声明同组插进提示词（插在背景/光照句之前）。

    单独补发型而不带裸头声明，帽子和头饰还会盖住刘海/发顶，预设照旧落空
    （2026-10-27 实测）。裸头声明与发型段共用一次插入，落在同一组。
    """
    merged = f"{hairstyle_text.strip()} {BARE_HEAD_SENTENCE}"
    return insert_descriptor_sentence(prompt, merged)


def combine(pose_preset: ListPreset | MatrixPreset, hairstyle: HairstylePreset) -> ListPreset:
    """动作/表情预设 × 发型预设 → 合成 list 预设（变体 = 笛卡尔积）。

    - 无独立顺序开关：发型固定为「服装外层的下层、动作外层的上层」，即
      都叠时由 outfit.combine 再外一层（id = outfit__hairstyle__pose）。
      要调播放顺序用现有 outfit_order，发型不单独给开关。
    - 文本顺序：发型段追加在动作段之后；输入若继续给 outfit.combine，
      服装段再追加在发型段之后（见 charvar/outfit.py 的组合注释）。
    - 分列留档：action_text 存动作原文、hairstyle_text 存发型段原文
      ——LLM 模式按这两段分开喂（发型段逐字英文，程序兜底补写也用原文）。
    - caption_tags：动作预设的映射 + {"hairstyle": 发型映射} 合并。
    """
    pose_specs = _pose_specs(pose_preset)
    if not pose_specs:
        raise PresetError(f"预设 {pose_preset.name} 没有任何变体")

    merged_specs: list[VariantSpec] = []
    seen: set[str] = set()
    # 发型层固定外层循环（每款发型连播全部动作）：与服装 outfit_first 同构。
    for item in hairstyle.items:
        for pose in pose_specs:
            spec_id = f"{item.id}__{pose.id}"
            if spec_id in seen:
                raise PresetError(f"发型组合产生重复变体 id：{spec_id}")
            seen.add(spec_id)
            merged_specs.append(
                VariantSpec(
                    id=spec_id,
                    framing=pose.framing,
                    angle=pose.angle,
                    expression=pose.expression,
                    pose=pose.pose,
                    text=f"{pose.text} {item.text.strip()}".strip(),
                    # 分列留档：text 是合并文本（classic 模式直接拼），LLM 模式取这两段分开喂。
                    action_text=pose.action_text if pose.action_text is not None else pose.text,
                    hairstyle_text=item.text.strip(),
                    tags=[*pose.tags, item.id],
                    hairstyle=item.id,
                    hairstyle_label=item.label,
                )
            )

    pose_tags = dict(pose_preset.caption_tags or {})
    pose_tags["hairstyle"] = dict(hairstyle.caption_tags or {})
    return ListPreset(
        type="list",
        name=f"{pose_preset.name}+{hairstyle.name}",
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