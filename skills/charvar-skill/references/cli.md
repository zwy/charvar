# charvar image 命令参考

完整参数说明。日常用 `SKILL.md` 里的速查即可。

## charvar image comfyui

用 ComfyUI 出图。无参考图走文生图 workflow，有参考图自动切图像编辑 workflow
（workflow 名从 charvar「设置」页取，可用 `--workflow` 覆盖）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--prompt` | str | — | 图片提示词 |
| `--prompt-file` | str | — | 提示词文件路径；`-` 表示读 stdin |
| `--ref` | list | — | 参考图路径，可重复，最多 4 张 |
| `--aspect` | str | `3:4` | `1:1` `3:4` `16:9` `4:3` `9:16` `2:3` `3:2` `21:9` |
| `--resolution` | str | `1k` | `1k` `2k` |
| `--seed` | int | `0` | 采样种子。0 = 固定复现 |
| `--negative` | str | — | 负向提示词。给空串 `--negative ""` = 明确不要负向词 |
| `--workflow` | str | — | workflows 目录里的名称，或 workflow JSON 路径 |
| `--comfy-url` | str | — | 覆盖 `COMFYUI_HOST`/`COMFYUI_PORT` 组装的地址 |
| `--out` / `-o` | path | — | 另外落一份到这个路径（后缀按你给的写） |
| `--wait` / `--no-wait` | flag | wait | `--no-wait` 只入队并回 prompt_id |
| `--json` | flag | — | 结果以 JSON 输出 |

`--prompt` 与 `--prompt-file` 只能给一个。

参考图按顺序对应提示词里的标签：第 1 张 → `<image1>`，第 2 张 → `<image2>`，依此类推。
走 OpenRouter 时这些标签会被自动换成序数词（"the first reference image"）。

## charvar image openrouter

用 OpenRouter 在线生图。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--prompt` | str | — | 图片提示词 |
| `--prompt-file` | str | — | 提示词文件路径；`-` 表示读 stdin |
| `--ref` | list | — | 参考图路径，可重复，最多 4 张 |
| `--aspect` | str | `3:4` | 同上 |
| `--resolution` | str | `1k` | 1k/2k 或模型声明枚举值（512/1K/1.5K/2K/4K…）；空串 = 用设置页/env 的模式默认 |
| `--seed` | int | `0` | 采样种子 |
| `--model` | str | — | 模型 slug（默认按模式取 `OP_IMAGE_T2I_MODEL` / `OP_IMAGE_I2I_MODEL` 或设置页） |
| `--quality` | str | — | 质量档（模型声明支持 quality 才生效；空 = 设置页/env 模式默认） |
| `--out` / `-o` | path | — | 另外落一份到这个路径 |
| `--json` | flag | — | 结果以 JSON 输出 |

API Key 取值顺序：显式参数 > 设置页 > `OP_IMAGE_API_KEY` > `LLM_API_KEY`。
画幅走显式像素 `size`（provider 的 `aspect_ratio` 枚举不含 21:9）。

## charvar image models

列 OpenRouter 可用的生图模型（`id<TAB>名称`）。加 `--json` 输出 JSON。
读本地缓存 `data/openrouter/api-response.json`，无缓存时才联网拉。

## 产物落盘

不给 `--out` 时：

```
data/generated/adhoc/{时间戳}_{短id}/
├── original/000000.png    ← 产物
├── thumbs/000000.webp     ← 缩略图
├── _ref_cache/            ← 参考图预处理缓存（长边 1536 / JPEG q88）
└── manifest.json          ← 完整请求与结果，便于复现
```

临时生图**不落 runs 表** —— 不进生成历史、不进图片墙。

## JSON 输出格式

```json
{
  "image_path": "/abs/path/out.png",
  "thumb_path": "/abs/path/out.webp",
  "provider": "comfyui",
  "manifest_path": "/abs/path/manifest.json",
  "width": 1024,
  "height": 1024,
  "seed": 0,
  "model": null,
  "prompt_id": "abc-123",
  "cost": null,
  "duration_ms": 21345,
  "status": "ready"
}
```

`--no-wait` 时 `status` 是 `queued`、`image_path` 为空、`prompt_id` 有值。

## 确定性

同提示词 + 同 seed → 同样的画面。ComfyUI 出的 PNG 嵌了当次的 workflow 元数据，
所以**文件 md5 每次不同，但像素完全一致**。要判断是不是同一张图，比像素而不是比 md5。

## 相关环境变量

| 变量 | 用途 |
|---|---|
| `COMFYUI_HOST` / `COMFYUI_PORT` | ComfyUI 地址（默认 `127.0.0.1:8188`） |
| `COMFYUI_WORKFLOWS_DIR` | workflow + mapping 成对文件目录（默认 `data/workflows`） |
| `OP_IMAGE_API_KEY` / `LLM_API_KEY` | OpenRouter key |
| `OP_IMAGE_T2I_MODEL` | 文生图默认模型（默认 `meta/muse-image`） |
| `OP_IMAGE_T2I_RESOLUTION` / `OP_IMAGE_T2I_QUALITY` | 文生图分辨率/质量默认（模型声明枚举时才发送） |
| `OP_IMAGE_I2I_MODEL` / `OP_IMAGE_I2I_RESOLUTION` / `OP_IMAGE_I2I_QUALITY` | 图片编辑（带参考图）默认模型 / 分辨率 / 质量 |
| `CHARVAR_DATA_DIR` | 数据目录（默认仓库 `data/`） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | subject derive / verify 的 LLM（也可在网页设置页配置） |

## charvar subject derive〈MD 模板 → 档案〉

手里只有「角色全场景通用提示词模板」.md 时，用它派生主体档案 YAML
（LLM 抽取 + 引用核对 + 独立审计；锁定项必须能回指模板原文，防编造）：

```bash
~/.local/bin/charvar subject derive template.md --id yoga_teacher --json
# 默认只写 presets/subjects/<id>.yaml（带出处注释头），不碰 DB；
# 加 --apply 才入库；同 id 已存在要 --overwrite；多版本模板要 --version <关键词>。
```

- `--lang`：`zh`（默认，模板原文摘录，防幻觉核对最强）/ `en`（翻译成英文）。
- 输出报告含质量警告与审计问题单；`--json` 的字段：profile / draft_rounds / verify_pass / citation_* 等。

## charvar subject verify〈档案 ↔ 模板审计〉

### 对已入库的主体

```bash
~/.local/bin/charvar subject verify <id> --json
```

读 DB 里已入库档案 + 它的模板（网页「主体档案」上传的），检查漏抽（omission）、
与模板矛盾、把背景布光写成锁定。exit 0 = 通过，exit 1 = 有问题单。

## charvar subject export〈DB 快照 → 仓库〉

对已入库的主体（含只在 DB 里建过、仓库没有 YAML 的），把档案回写仓库做备份：

```bash
~/.local/bin/charvar subject export <id>            # → presets/subjects/<id>.yaml + <id>.md（模板逐字）
```

目标文件已存在要 `--overwrite`；没有模板时只导出 YAML 并警告。DB 是运行时真源，
新建/网建档案后记得 export，否则清库即失。
