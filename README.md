# 角色变体提示词生成器 charvar

本地运行的角色变体提示词工厂：输入一个「主体档案」，一键生成 N 条（默认 30）变体提示词。变体由**维度组合**（景别 × 朝向 × 表情）驱动，背景/光照按种子稳定随机，每条输出附带结构化元数据与训练 caption。

本模块只负责**生成提示词**，不负责出图；输出格式可直接被 ComfyUI 消费（Text Multiline 逐行粘贴，或 API 排队）。

> 抽离自 ComfyUI 工作流 `characterDataset_q21Chardatasetbuilder.json`（Character Dataset Builder – Q21），并修复其已知缺陷：拼接分隔符缺失导致 `camera.put them` 粘连、主体称谓硬编码、随机背景/光照不可复现、caption 未接线。

## 功能

- **生成**：选主体 + 预设 + 种子 → 30 条变体（可设条数/采样），表格逐条展示提示词、背景、光照、caption
- **主体档案 CRUD**：id/称谓/触发词/风格锁定/外貌锁定/服饰锁定/自由补充，支持粘贴 YAML 导入
- **历史记录**：每次生成存 SQLite，可回看、重新下载
- **导出**：TXT（每行一条贴 ComfyUI）、JSONL、captions.zip（`000_full_front.txt …` 与图片同名）、ComfyUI API 逐条入队
- **内置预设**：`q21_default_30`（原工作流 30 条，逐字保留）、`q21_legacy_30`（工作流残留旧版 30 条）、`matrix_basic`（3 景别 × 8 朝向 + 6 表情的矩阵版）、`yoga_basic_9`（瑜伽动作 9 式：全身景别，角度/表情留空，由各条 text 自述观察方向）

## 页面一览

| 页面 | 说明 |
|---|---|
| 生成 | 生成参数 + 结果表（每条可点击复制）+ 全量导出 + ComfyUI 入队 |
| 主体档案 | 档案列表 + 详情表单 + 粘贴 YAML 导入 |
| 历史记录 | 历次生成，查看 / 删除，导出链接长期有效 |

## ComfyUI 配置（.env）

```env
COMFYUI_HOST=127.0.0.1
COMFYUI_PORT=8188
COMFYUI_TIMEOUT=180
COMFYUI_WORKFLOWS_DIR=./data/workflows
```

- `.env` 不进 git（`.env.example` 给模板）；已存在的环境变量优先。
- 请求一律绕过代理直连（trust_env=False），shell 的 `ALL_PROXY` 不影响内网调用。
- workflow 采用与 `shared/comfy_client` 相同的成对约定：`data/workflows/{name}.json` + `{name}.mapping.json`；mapping 格式见 `presets/comfy_mapping.example.json`（`mappings` 列表 + `output_nodes`）。
- 网页「生成 → 推送 ComfyUI」面板与 CLI 共用这套配置。
- 推送前可选择 `智能 / 1:1 / 3:4 / 16:9 / 4:3 / 9:16 / 2:3 / 3:2 / 21:9` 与 `1K / 2K / 4K`；4K 长边上限 4096，短边取 64 倍数；`智能` 会按景别使用全身 `2:3`、胸像/头像/表情 `3:4`。

## LLM 配置（可选）

「LLM 优化」生成模式与主体图「一键 AI 起草」需要一个 OpenAI 规范 chat completions 的 API；不配置则网页两处 LLM 功能不可用（经典模板模式不受影响）。

```env
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-******
LLM_MODEL=deepseek/deepseek-v4.1-flash
```

- 写 `.env`（不进 git，`.env.example` 是模板），或直接在网页「设置」页填 Base URL / 模型 / Key——DB 设置优先于环境变量，改后重启生效。
- LLM 都要求返回 JSON：优先发 `json_schema`（strict 结构化输出），供应商不支持时自动降级 `json_object`；改写输出不设 max_tokens，截断时自动抢救已完整条目并对缺失条目缩批补调。

## 安装

```bash
pip install -r requirements.txt   # 项目根目录执行；或 pip install -e . 安装 CLI（可用 charvar 命令）
brew install ffmpeg               # 可选：合成视频需要（macOS Homebrew）
```

## 启动 / 停止

```bash
./scripts/start.sh            # 后台启动（screen 会话 charvar-8710），打开 http://127.0.0.1:8710
./scripts/stop.sh             # 停止；./scripts/stop.sh 9000 可停指定端口
CHARVAR_WEB_PORT=8799 ./scripts/start.sh   # 换端口多开；停止时同款环境变量或传端口参数
```

- `CHARVAR_WEB_PORT`（默认 8710）：`start.sh` / `stop.sh` 只认 shell 环境变量；`python3 -m web.app` 前台模式也会读 `.env` 里的该键。换端口建议统一用 `CHARVAR_WEB_PORT=8799 ./scripts/start.sh`。
- stop.sh 优先按 PID 文件停止，再按「端口 + 本仓库 cwd」安全匹配进程，不会误杀无关进程；无论哪种路径命中都会顺带清理同名 screen 会话。
- 手动前台运行 / 热重载开发：

```bash
python3 -m web.app                             # 前台运行（export CHARVAR_WEB_PORT=8710 换端口）
uvicorn web.app:app --reload --port 8710       # 热重载（先 stop.sh 释放端口）
```

- 改了 Python 代码（`charvar/**`、`web/app.py`）后必须重启才生效；只改 `web/static/**` 刷新页面即可。
- 服务只监听 `127.0.0.1`。日志：`/tmp/charvar-web-8710.log`（端口跟随 `CHARVAR_WEB_PORT`）。

## 命令行

```bash
python3 -m charvar.cli subject add presets/subjects/silver_witch.yaml   # 建档（--overwrite 覆盖）
python3 -m charvar.cli subject list | show <id> | remove <id>
python3 -m charvar.cli presets list | show <name> [--limit N]
python3 -m charvar.cli gen --subject silver_witch --preset q21_default_30 --seed 42 --out ./out
#  → out/silver_witch_42/prompts.txt / prompts.jsonl / captions/000_full_front.txt …

# sampling：all（默认顺序）/ first（前 N 条）/ random（按种子抽取）
python3 -m charvar.cli gen -s silver_witch --count 10 --sampling random --seed 1

# 推送 ComfyUI：--workflow 用 data/workflows 里的名称（或 workflow JSON 路径）
# 默认只排队并记录 prompt_id；--wait 会轮询每条直到完成并记录输出文件名
python3 -m charvar.cli gen -s silver_witch --format comfyui --workflow <name> --count 30
python3 -m charvar.cli gen -s silver_witch --format comfyui --workflow <name> --wait --count 3
```

`pip install -e .` 后上述命令可用 `charvar …` 直接调用。

## 预设

YAML 预设放 `presets/*.yaml`，两种类型：

- `type: list`：逐条 `variants` 写死（原工作流 30 条逐字保留）。缺省 framing/angle/expression 会由 id 解析（`full_l45` → full+l45；`expr_smile` → head+front+smile）。
- `type: matrix`：`dimensions` 定义维度文案，`rules` 按顺序做笛卡尔积（`"*"` = 该维度全量），`exclude` 按维度取值排除（`{angle: back, expression: "*"}` 只排除「背面 + 表情」组合）。

背景/光照用 `presets/wildcards/backgrounds.yaml` / `lighting.yaml`（`items` 列表），也可在任意变体文本内使用 Impact Pack 风格的 `{a|b|c}` 内联通配符（按条目种子稳定展开）。

变体可带结构化字段：`framing`（full/chest/head）、`angle`、`expression`（表情）、`pose`（体式/动作，独立于表情语义），后两者分别写进 caption。

预设根字段 `preferred_aspect_ratio`（如 `'4:3'`）声明出图画幅：智能画幅按它出图，不按景别推导；UI 手选的比例仍然优先。**画幅值必须加引号**——YAML 会把未加引号的 `4:3` 读成 sexagesimal 数字。

## 可复现与子种子

- 同一档案 + 同一预设 + 同一种子 → 输出完全一致；
- 每条变体的子种子 = `sha256(f"{seed}:{variant_id}")` 前 8 字节，**增删变体不影响其他条目的背景/光照**（与原 Impact Pack 按行自增的方式不同，这是刻意的改进）；
- 全程不使用全局 `random`。

## caption 规则

```
{trigger_word}, {framing_tag}, {angle_tag}[, {pose_tag}], {expression_tag}, {background} background, {lighting_short}
例：sw1tch, full body, three-quarter view from left, gray background, soft natural window light
```

- `lighting_short` 取逗号前的短句；
- angle ∈ {l135, r135, back} 时省略表情标签；
- `pose`（体式/动作标签）写在角度之后、表情之前，映射取 `caption_tags.pose`；
- 标签映射可用预设 `caption_tags` 字段覆盖。

## ComfyUI 集成

- 推送前端已内置比例与分辨率：`智能 / 1:1 / 3:4 / 16:9 / 4:3 / 9:16 / 2:3 / 3:2 / 21:9` + `1K / 2K / 4K`；4K 长边上限 4096，短边取 64 的整数倍。`智能` 会按景别使用全身 `2:3`、近景 `3:4`。

- **模式 A（零改动）**：`prompts.txt` 每行一条完整提示词，直接贴进原工作流的 `Text Multiline`（把 Master Prompt 清空、wildcard 节点旁路），队列次数 = 行数。
- **模式 B（API 排队，推荐）**：workflow 用 ComfyUI「导出 (API)」保存到 `data/workflows/{name}.json`，并写同名 `{name}.mapping.json` 指明注入节点（示例见 `presets/comfy_mapping.example.json`）。`filename_prefix` 默认写成 `{subject}_{index:03d}_{variant_id}`，与 captions 文件一一对应；逐条 `POST /prompt`，返回的 `prompt_id`（以及 `--wait` 时的输出文件名）记入 SQLite `comfy_prompts` 表，供后续关联图片与 caption。
- 连接检查：`GET /comfy/status`（网页面板「检查连接」）；队列记录：`GET /runs/{run_id}/comfyui`。
- 已验证链路：charvar → ComfyUI（0.40.0 / win32），单条排队 → 轮询完成 → 拿到输出文件名；`192.168.x.x` 属于你的本地网段，请自行替换。
- 支持 `aspect_ratio` + `resolution`：比例可选拍档里的 `auto / 1:1 / 3:4 / 16:9 / 4:3 / 9:16 / 2:3 / 3:2 / 21:9`，分辨率支持 `1k / 2k / 4k`，长边按档位计算（4K 上限 4096），短边取 64 的整数倍；`auto` 按景别自动用 2:3（全身）或 3:4（胸像/头像/表情）。

## HTTP API（127.0.0.1:8710）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/presets` | 预设列表（名称/类型/条数） |
| GET | `/presets/{name}?expand=false` | 预设详情（可展开变体列表） |
| GET/POST/PUT/DELETE | `/subjects[/{id}]` | 主体档案 CRUD |
| POST | `/generate` | body = GenerateRequest，返回 GenerateResult，自动存历史 |
| GET | `/runs`、`/runs/{run_id}`、`DELETE /runs/{run_id}` | 历史记录 |
| GET | `/runs/{run_id}/export?format=txt\|jsonl\|captions_zip` | 导出（`&download=1` 触发下载） |
| GET/POST | `/runs/{run_id}/comfyui` | 查看 ComfyUI 提交记录 / 逐条入队（`workload` + 可选 `wait`） |
| GET | `/comfy/workflows`、`/comfy/status` | workflows 目录清单 / ComfyUI 连接检查 |
| POST | `/runs/{run_id}/sync-outputs` | 按 ComfyUI history 同步输出图，生成缩略图和 contact sheet |
| GET | `/runs/{run_id}/images` | 图片墙数据（本地原图/缩略图/sheet 状态） |
| GET | `/runs/{run_id}/comfy-image/{item_index}` | 单条输出图（优先本地资产，缺失时代理 ComfyUI） |
| GET | `/runs/{run_id}/assets/{scene}/{filename}`、`/runs/{run_id}/sheets/{kind}` | 本地原图/缩略图/拼图资产 |
| POST | `/runs/{run_id}/contact-sheet` | 手动生成 `contact_sheet.png` |

## 数据文件

| 路径 | 说明 |
|---|---|
| `data/charvar.db` | 主体档案、生成记录、comfy_prompts（JSON 全量存储，可整库备份） |
| `data/workflows/` | ComfyUI API workflow + mapping 成对文件（README 说明约定） |
| `out/` | CLI 默认输出目录（`--out` 可改） |
| `presets/` | YAML 预设、通配符、主体档案样例、mapping 示例 JSON |

删除 `data/charvar.db` 即重置（预设是代码外文件，不受影响）。

## 目录结构

```
charvar/            核心包：models/preset_loader/wildcard/composer/caption
│                   exporters（txt/jsonl/captions/comfyui）/storage/api/cli
presets/            q21_default_30、q21_legacy_30、matrix_basic、wildcards/、subjects/
web/                web/app.py（uvicorn 启动）+ web/static/（无构建步骤）
tests/              pytest：逐字一致性/可复现/子种子/拼接/矩阵/导出/API
scripts/            start.sh / stop.sh（默认端口 8710）
```

## 测试

```bash
python3 -m pytest -q
```

覆盖：`q21_default_30` 与原 JSON 逐字一致、matrix 30 条 id 唯一且顺序稳定、同种子完全相等、删除变体不影响其他条目背景/光照、无 `.`/`..`/双空格/花括号残留、`subject_noun=man` 不出现 woman、ComfyUI payload 注入与 filename_prefix 唯一、API 冒烟。

## 致谢

- [Character Dataset Builder - Q21](https://civitai.red/models/2719177/character-dataset-builder)（作者 [AirbagGuy](https://civitai.com/user/AirbagGuy)，Qwen 2.1 Workflows）——本项目的提示词生成体系抽离并改造自该 ComfyUI 工作流：`q21_default_30`（V1 30 条变体）与 `q21_v2_30`（V2 30 条）的逐条文本、景别/朝向/表情编排与参考图分工结构均逐字保留自其 Text Multiline 设计，并在此之上修复了分隔符缺失、随机不可复现、caption 未接线等已知缺陷（见文档开头）。
- 原工作流 JSON 文件不随本仓库分发（第三方产物，版权归原作者）；预设文本的回归测试以 sha256 基准锁定逐字一致性，仓库内不保留其文件副本。

## 许可证

- 本项目代码与配置按 [MIT License](LICENSE) 发布（`Copyright (c) 2026 zwy`）。
- q21 系预设中逐字保留的原工作流变体文本不属于本许可证覆盖范围，版权归原工作流作者。
