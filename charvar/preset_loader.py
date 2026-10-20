"""读取/校验 YAML 预设（设计文档 §5）。

- `type: list`：逐条写死（兼容原工作流，q21_default_30 逐字保留）；
- `type: matrix`：维度组合 + rules 笛卡尔积 + exclude；
- 背景/光照：背景用 `backgrounds:`/`lighting:` 内联，或 `backgrounds_ref:`/`lighting_ref:`
  引用相对预设文件的通配符 YAML（items: [...]）；
- `caption_tags` 覆盖 caption 标签映射；`caption_background_suffix: false` 时
  caption 只写背景描述本身（不加 " background" 后缀，长句背景形态）；
- `schema_version` 是配置格式版本（默认 1，只接受 1/2，未知版本报错）；`version`
  是预设内容版本，两者不混用。schema 2 本期仅支持 sticker 合同（详见下方注释）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from charvar import paths
from charvar.models import LetteringStyle, VariantSpec

VALID_FRAMINGS = ("full", "chest", "head")

# 出图画幅白名单：与 exporter 的 SUPPORTED_ASPECT_RATIOS 一致（auto = 按景别推导）
VALID_ASPECT_RATIOS = ("auto", "1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9")

SUPPORTED_SCHEMA_VERSIONS = (1, 2)

# schema 2（sticker）本期合同：list/sticker/required_label/variant/1:1，逐条必带 display_text
_STICKER_SCHEMA_2_REQUIRED = {
    "purpose": "sticker",
    "text_policy": "required_label",
    "composition_policy": "variant",
    "preferred_aspect_ratio": "1:1",
}

_SCHEMA_2_ROOT_KEYS = frozenset(
    {
        "schema_version", "name", "type", "version", "purpose", "text_policy",
        "composition_policy", "preferred_aspect_ratio", "lettering",
        "master_template", "suffix_template", "backgrounds", "lighting",
        "backgrounds_ref", "lighting_ref", "caption_tags", "caption_background_suffix",
        "reference_order", "variants",
    }
)

_SCHEMA_2_VARIANT_KEYS = frozenset(
    {"id", "framing", "angle", "expression", "pose", "text", "tags", "display_text"}
)


class PresetError(ValueError):
    """预设缺失（status=404）或配置/格式错误（status=400）。"""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


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
    # ---- schema 2（带字表情包）新增：schema_version=配置格式版本，version=内容版本 ----
    schema_version: int = 1
    purpose: Literal["dataset", "sticker"] = "dataset"
    text_policy: Literal["forbid", "required_label"] = "forbid"
    composition_policy: Literal["legacy", "variant"] = "legacy"
    preferred_aspect_ratio: str | None = None
    lettering: LetteringStyle | None = None


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


def _check_schema_version(raw: dict[str, Any], path: Path) -> int:
    """识别 schema_version：未知版本显式报错（先升级程序再导入），不静默降级。"""
    raw_version = raw.get("schema_version", 1)
    try:
        schema_version = int(raw_version)
    except (TypeError, ValueError):
        raise PresetError(f"预设 {path.name} 的 schema_version 非法：{raw_version!r}") from None
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise PresetError(
            f"预设 {path.name} 的 schema_version={schema_version} 不受支持"
            f"（本程序只支持 {SUPPORTED_SCHEMA_VERSIONS}；请先升级程序再导入）"
        )
    # schema 1 文件携带 schema 2 新字段会被静默丢弃——显式报错而不是当作没看见。
    # preferred_aspect_ratio 不在此列：它只是画幅声明，与 sticker 合同无关，
    # schema 1 的动作/场景预设也能用它固定出图画幅。
    new_keys = sorted(set(raw) & {"purpose", "text_policy", "composition_policy",
                                  "lettering", "schema_version"})
    new_keys = [k for k in new_keys if not (k == "schema_version" and raw[k] == 1)]
    if schema_version == 1 and new_keys:
        raise PresetError(
            f"预设 {path.name} 携带 schema 2 字段 {new_keys} 但 schema_version=1；"
            "请设置 schema_version: 2（新结构需要新版加载逻辑）"
        )
    return schema_version


def _validate_sticker_contract(preset: ListPreset, schema_version: int, path: Path) -> None:
    """schema 2 = sticker 合同（本期唯一形态）；schema 1 禁止携带 display_text。"""
    if schema_version == 1:
        if any(v.display_text is not None for v in preset.variants):
            raise PresetError(
                f"预设 {path.name} 的 variants 带有 display_text，但 text_policy=forbid（schema 1 不支持图片文案）"
            )
        return
    bad = [_STICKER_SCHEMA_2_REQUIRED[k] for k, want in _STICKER_SCHEMA_2_REQUIRED.items()
           if getattr(preset, k) != want]
    if bad:
        raise PresetError(
            f"schema 2 sticker 预设 {path.name} 需要满足 {'/'.join(bad)}；"
            "matrix 等扩展本期不支持，请先用 list 预设"
        )
    if preset.lettering is None:
        raise PresetError(f"sticker 预设 {path.name} 缺少 lettering（绘字样式）配置")
    for spec in preset.variants:
        if spec.display_text is None:
            raise PresetError(f"sticker 预设 {path.name} 的 {spec.id} 缺少 display_text")
        if spec.framing is not None or spec.angle is not None:
            raise PresetError(
                f"sticker 预设 {path.name} 的 {spec.id} 应保持 framing/angle 为空"
                "（构图以 text 为准，不套用景别裁切/正面站姿规范）"
            )


def _aspect_ratio_value(value: Any) -> str | None:
    """画幅值归一成字符串。

    YAML 会把未加引号的 `4:3` 读成 sexagesimal 整数（4*60+3=243），所以这里
    拒绝数字并给出明确提示——让作者去加引号，而不是把 243 当成画幅。
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise PresetError(
            f"preferred_aspect_ratio 必须写成带引号的字符串（如 '4:3'），当前读成 {value!r}"
            "（YAML 会把未加引号的 w:h 解析成 sexagesimal 数字）"
        )
    return value.strip() or None


def _validate_aspect_ratio(preset: "ListPreset | MatrixPreset", path: Path) -> None:
    """preferred_aspect_ratio 必须是白名单里的画幅；非法值显式报错（否则出图时才炸）。"""
    ratio = str(preset.preferred_aspect_ratio or "").strip().lower()
    if ratio and ratio not in VALID_ASPECT_RATIOS:
        raise PresetError(
            f"预设 {path.name} 的 preferred_aspect_ratio 非法：{preset.preferred_aspect_ratio!r}"
            f"（只支持 {'/'.join(VALID_ASPECT_RATIOS)}）"
        )


def load_file(path: Path) -> ListPreset | MatrixPreset:
    if not path.exists():
        raise PresetError(f"预设文件不存在：{path}", status=404)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise PresetError(f"预设 {path} 不是合法 YAML 映射")

    schema_version = _check_schema_version(raw, path)
    if schema_version == 2:
        unknown_roots = sorted(set(raw) - _SCHEMA_2_ROOT_KEYS)
        if unknown_roots:
            raise PresetError(f"预设 {path.name} 有未知根字段：{unknown_roots}（schema 2 校验为严格模式）")
        if raw.get("type") != "list":
            raise PresetError(f"schema 2 sticker 预设 {path.name} 本期只支持 type: list")
        for i, item in enumerate(raw.get("variants") or []):
            if isinstance(item, dict):
                unknown = sorted(set(item) - _SCHEMA_2_VARIANT_KEYS)
                if unknown:
                    raise PresetError(f"预设 {path.name} variants[{i}]（{item.get('id')}）有未知字段：{unknown}")

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
        "schema_version": schema_version,
        "purpose": raw.get("purpose", "dataset"),
        "text_policy": raw.get("text_policy", "forbid"),
        "composition_policy": raw.get("composition_policy", "legacy"),
        "preferred_aspect_ratio": _aspect_ratio_value(raw.get("preferred_aspect_ratio")),
        "lettering": raw.get("lettering"),
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
            preset = ListPreset.model_validate(common)
            _validate_sticker_contract(preset, schema_version, path)
            _validate_aspect_ratio(preset, path)
            return preset
        if kind == "matrix":
            common["dimensions"] = raw.get("dimensions")
            common["rules"] = raw.get("rules")
            common["exclude"] = raw.get("exclude", [])
            common["join"] = raw.get("join", ", ")
            common["end"] = raw.get("end", ".")
            preset = MatrixPreset.model_validate(common)
            _validate_aspect_ratio(preset, path)
            if schema_version == 2:
                raise PresetError(f"schema 2 sticker 预设 {path.name} 本期只支持 type: list")
            return preset
    except ValidationError as exc:
        raise PresetError(f"预设 {path} 校验失败：\n{exc}") from exc
    raise PresetError(f"预设 {path} 类型无法识别")  # pragma: no cover


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
        raise PresetError(f"预设不存在：{name}（查找路径 {path}）", status=404)
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
                "purpose": preset.purpose,
                "text_policy": preset.text_policy,
                "preferred_aspect_ratio": preset.preferred_aspect_ratio,
            }
        )
    return result