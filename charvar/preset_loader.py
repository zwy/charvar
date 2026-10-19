"""读取/校验 YAML 预设（设计文档 §5）。

- `type: list`：逐条写死（兼容原工作流，q21_default_30 逐字保留）；
- `type: matrix`：维度组合 + rules 笛卡尔积 + exclude；
- 背景/光照：背景用 `backgrounds:`/`lighting:` 内联，或 `backgrounds_ref:`/`lighting_ref:`
  引用相对预设文件的通配符 YAML（items: [...]）；
- `caption_tags` 覆盖 caption 标签映射；`caption_background_suffix: false` 时
  caption 只写背景描述本身（不加 " background" 后缀，长句背景形态）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from charvar import paths
from charvar.models import VariantSpec

VALID_FRAMINGS = ("full", "chest", "head")


class CommonPreset(BaseModel):
    name: str
    version: int = 1
    master_template: str
    suffix_template: str
    backgrounds: list[str] = Field(default_factory=list)
    lighting: list[str] = Field(default_factory=list)
    caption_tags: dict[str, dict[str, str]] | None = None
    # caption 里背景 token 是否拼 " background" 后缀（新预设为完整描述句式时关掉）
    caption_background_suffix: bool = True
    # 预设文本若按固定参考图顺序逐字描述（如 <image1>=headshot…），声明推荐顺序供前端默认勾选与提示
    reference_order: list[str] = Field(default_factory=list)


class ListPreset(CommonPreset):
    type: Literal["list"]
    variants: list[VariantSpec]


class MatrixPreset(CommonPreset):
    type: Literal["matrix"]
    dimensions: dict[str, dict[str, str]]
    rules: list[dict[str, dict[str, Any]]]
    exclude: list[dict[str, Any]] = Field(default_factory=list)
    join: str = ", "
    end: str = "."


Preset = Annotated[ListPreset | MatrixPreset, TypeAdapter]
_PRESET_ADAPTER = TypeAdapter(ListPreset | MatrixPreset)


class PresetError(ValueError):
    """预设缺失或格式错误。"""


def _wildcard_items(path: Path, field: str) -> list[str]:
    if not path.exists():
        raise PresetError(f"通配符文件不存在：{path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    items = data.get("items")
    if not isinstance(items, list) or not all(isinstance(v, str) for v in items):
        raise PresetError(f"通配符文件 {path} 缺少 items: [..]（{field}）")
    return [item.strip() for item in items if item.strip()]


def _derive_structured_fields(spec: dict[str, Any]) -> dict[str, Any]:
    """list 预设缺省 framing/angle/expression 时按 id 解析。

    expr_* → 景别 head + angle front；“a_b” → framing=a, angle=b。
    """
    out = dict(spec)
    if out.get("framing") is None or out.get("angle") is None or ("expression" not in out):
        spec_id = str(out.get("id") or "")
        if spec_id.startswith("expr_"):
            out.setdefault("framing", "head")
            out.setdefault("angle", "front")
            out.setdefault("expression", spec_id[len("expr_"):])
        else:
            parts = spec_id.split("_")
            if len(parts) >= 2 and parts[0] in VALID_FRAMINGS:
                out.setdefault("framing", parts[0])
                out.setdefault("angle", parts[1])
    out["framing"] = out.get("framing") if out.get("framing") in VALID_FRAMINGS else None
    return out


def load_file(path: Path) -> ListPreset | MatrixPreset:
    if not path.exists():
        raise PresetError(f"预设文件不存在：{path}")
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise PresetError(f"预设 {path} 不是合法 YAML 映射")

    kind = raw.get("type")
    if kind not in ("list", "matrix"):
        raise PresetError(f"预设 {path} 的 type 必须是 list 或 matrix，当前为 {kind!r}")
    common: dict[str, Any] = {
        "type": kind,
        "name": raw.get("name") or path.stem,
        "version": raw.get("version", 1),
        "master_template": raw.get("master_template"),
        "suffix_template": raw.get("suffix_template"),
        "caption_tags": raw.get("caption_tags"),
        "caption_background_suffix": raw.get("caption_background_suffix", True),
        "reference_order": [str(v).strip() for v in (raw.get("reference_order") or [])],
    }
    # 背景/光照：内联优先，其次 _ref 相对路径
    for key, ref_key in (("backgrounds", "backgrounds_ref"), ("lighting", "lighting_ref")):
        if isinstance(raw.get(key), list):
            common[key] = [str(v).strip() for v in raw[key]]
        elif raw.get(ref_key):
            ref = Path(str(raw[ref_key]))
            common[key] = _wildcard_items(ref if ref.is_absolute() else path.parent / ref, key)

    try:
        if kind == "list":
            variants = []
            for i, item in enumerate(raw.get("variants") or []):
                if not isinstance(item, dict):
                    raise PresetError(f"{common['name']} variants[{i}] 不是映射")
                variants.append(_derive_structured_fields(item))
            common["variants"] = variants
            return ListPreset.model_validate(common)
        if kind == "matrix":
            common["dimensions"] = raw.get("dimensions")
            common["rules"] = raw.get("rules")
            common["exclude"] = raw.get("exclude", [])
            common["join"] = raw.get("join", ", ")
            common["end"] = raw.get("end", ".")
            return MatrixPreset.model_validate(common)
    except ValidationError as exc:
        raise PresetError(f"预设 {path} 校验失败：\n{exc}") from exc


def resolve(name: str) -> Path:
    """按预设名定位 YAML：支持子目录相对路径，如 wildcards/xxx。"""
    base = paths.presets_dir()
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"
    path = base / name
    return path


def load(name: str) -> ListPreset | MatrixPreset:
    path = resolve(name)
    if not path.exists():
        raise PresetError(f"预设不存在：{name}（查找路径 {path}）")
    preset = load_file(path)
    return preset


def list_presets() -> list[dict[str, Any]]:
    base = paths.presets_dir()
    result: list[dict[str, Any]] = []
    if not base.exists():
        return result
    from charvar.composer import count_matrix_variants  # 延迟导入避免循环依赖

    for path in sorted(base.glob("*.yaml")):
        try:
            preset = load_file(path)
        except PresetError:
            continue
        if preset.type == "list":
            count = len(preset.variants)
        else:
            count = count_matrix_variants(preset)
        result.append(
            {
                "name": preset.name,
                "type": preset.type,
                "version": preset.version,
                "count": count,
                "reference_order": preset.reference_order,
            }
        )
    return result
