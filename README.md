# 角色变体提示词生成器 charvar

本地运行的角色变体提示词工厂：输入一个「主体档案」，一键生成 N 条（默认 30）变体提示词。变体由**维度组合**（景别 × 朝向 × 表情）驱动，背景/光照按种子稳定随机，每条输出附带结构化元数据与训练 caption。

本模块只负责**生成提示词**，不负责出图；输出格式可直接被 ComfyUI 消费（Text Multiline 逐行粘贴，或 API 排队）。

> 抽离自 ComfyUI 工作流 `characterDataset_q21Chardatasetbuilder.json`（Character Dataset Builder – Q21），并修复其已知缺陷：拼接分隔符缺失导致 `camera.put them` 粘连、主体称谓硬编码、随机背景/光照不可复现、caption 未接线。

## 功能

- **生成**：选主体 + 预设 + 种子 → 30 条变体（可设条数/采样），表格逐条展示提示词、背景、光照、caption
- **主体档案 CRUD**：id/称谓/触发词/风格锁定/外貌锁定/服饰锁定/自由补充，支持粘贴 YAML 导入
- **历史记录**：每次生成存 SQLite，可回看、重新下载
- **导出**：TXT（每行一条贴 ComfyUI）、JSONL、captions.zip（`000_full_front.txt …` 与图片同名）、ComfyUI API 逐条入队
- **内置预设**：`q21_default_30`（原工作流 30 条，逐字保留）、`q21_legacy_30`（工作流残留旧版 30 条）、`matrix_basic`（3 景别 × 8 朝向 + 6 表情的矩阵版）、`yoga_basic_9`（瑜伽动作 9 式：全身景别，角度/表情留空，由各条 text 自述观察方向）、`yoga_complete_14`（同上九式 + 手倒立/下犬式/侧板式/船式/屈膝头倒立共 14 式）

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
- 推送前可选择 `智能 / 1:1 / 3:4 / 16:9 / 4:3 / 9:16 / 2:3 / 3:2 / 21:9` 与 `1K / 2K`；`智能` 会按景别使用全身 `2:3`、胸像/头像/表情 `3:4`。
- **2K 用 Qwen-Image-2.1 官方像素**（面积恒定 ~4.2MP，如 16:9 = 2752×1536、1:1 = 2048×2048）——模型原生的就是这个尺寸；早期按「长边 2048」算的 2K 只有原生面积的 56%，图会糊。4K 档已下线：远超模型 2K 训练尺寸会丢提示词遵循度。

## OpenRouter 生图（可选）

除 ComfyUI 外还有第二条出图通路：直接调 OpenRouter 的在线生图 API，不用自建 workflow、不用排队。

```env
OP_IMAGE_API_KEY=sk-or-v1-******      # 不配则回退 LLM_API_KEY（同一家 OpenRouter）
OP_IMAGE_MODEL=meta/muse-image        # 默认模型
OP_IMAGE_TIMEOUT=300                 # 秒，可选
```

- 写 `.env`，或在网页「设置 → OpenRouter 生图」填模型 / Key（DB 设置优先于环境变量）。Key 读接口永远掩码，留空 = 用环境变量。
- 网页「生成结果 → 推送 OpenRouter…」面板：画幅比例、参考图沿用生成页勾选。分辨率/质量档位按当前模型的能力动态显示（模型不支持就整个不显示，避免给出选了也不生效的选项）。
- 网页「主体档案 → 档案图片」每个槽位卡片上有「用 OpenRouter 生成」按钮：跟 ComfyUI 的「生成」按钮并列，两条通路互不影响，槽位状态机和落盘位置完全一样（`data/subjects/{id}/{kind}.{ext}`）。两步流程也一致——先就绪「人物半身像＋三视图」，其余 4 种以它为参考图。槽位徽章会标出这张图是哪个后端出的（已生成（OpenRouter））。
- 模型清单在 `data/openrouter/api-response.json`（可点设置页「拉取模型清单」强制刷新）。**清单是参数能力的唯一标准**——每个模型的 `supported_parameters` 决定它能用哪些参数、用哪些枚举值。面板按它生成可选项。
- 产物落在和 ComfyUI 同一个目录（`data/generated/{run_id}/original/`），图片墙 / contact sheet / 合成视频全部复用；单条「重新生成」按上次用的通路（provider）重放。

限制（与 ComfyUI 的差异，先知道再用）：

- **没有负向提示词**：OpenRouter Images API 无此字段，推送面板不提供负向词栏。
- **画幅走 `size` 显式像素**：`size` 是 OpenRouter 层的通用参数，provider 实测都吃，且像素精确（qwen / tencent 原样输出）。各模型自己的 `aspect_ratio` 枚举不全（tencent 不含 `21:9`，会 400），所以按 ComfyUI 同一套 `dimensions_for()` 算出宽高后传 `size`。
- **参数会被自动降级**：provider 拒收某个参数时（实测 grok 声明支持 `quality` 但 provider 拒收），自动摘掉该参数重试，并记进 `data/openrouter/capabilities.json`，下次同一模型直接不发了。`size` 被拒则退到 `aspect_ratio`（画幅从像素精确退化为比例枚举）。
- **清单不是永远准**：`supported_parameters` 是目录级声明。muse-image 声明为空但实际接受 `size` / `aspect_ratio` / `resolution` / `output_format` / `seed`；反过来 grok 声明 `quality` 但 provider 拒收。**以清单为基准 + 探测修正**。
- **参考图按顺序对应**：勾选的参考图依次对应提示词里的 the first / second… reference image；提示词里的 `<imageN>` 标签会被替换成序数词（不替换的话模型会把字面标签画进图里）。没有参考图时指向句整段摘掉。只支持文生图的模型（如 `recraft/recraft-v4.1-flash`，清单里没有 image 输入）不发送参考图。
- **参考图统一压缩**：主体档案里的参考图普遍是 2.4–3.5MB 的全尺寸 PNG。统一压到长边 1536 / JPEG q88（带透明通道的先白底合成——直接转 RGB 会把透明变黑污染参考图），缓存到 `data/subjects/{id}/_ref_cache/`，**ComfyUI 上传和 OpenRouter 发送共用这一份**。实测 3460KB → 195KB，请求体 517KB → 283KB，出图耗时基本不变。
- **seed 不保证确定性**：同一 seed 两次请求可能返回不同的图（provider 侧不保证），「重新生成」不保证同种子出同图。
- **没有排队**：一次请求内并发 4 路同步出图。单价约 **$0.01/张**（muse-image）、$0.024/张（hy-image）；30 条约 $0.3。
- **分辨率档位可能无效**：muse-image 接受 `resolution` 但输出像素不变（1K 与 4K 同为 1920×1280），所以面板按清单隐藏该档位——清单没声明就不发。
- **输出格式随模型**：muse-image 默认返回 webp（charvar 全链路已支持），ComfyUI 产物是 png。

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

## 临时生图（跨项目调用）

给一段提示词直接出一张图 —— 不进主体档案槽位、不跑预设批量、不进生成历史。
给本项目之外的项目 / 其他 agent 用。

```bash
# ComfyUI（本机或局域网）
charvar image comfyui --prompt "a red apple on a wooden table" --aspect 1:1 --out out.png

# OpenRouter（在线）
charvar image openrouter --prompt "a red apple on a wooden table" --aspect 1:1 --out out.png

# 带参考图（最多 4 张，按序对应提示词里的 <image1>…）
charvar image comfyui --prompt "same character, different pose" --ref ref.png --out out.png

# 长提示词走文件（- 表示读 stdin）
charvar image openrouter --prompt-file prompt.md --out out.png

charvar image models    # 列 OpenRouter 可用模型
```

- `--seed` 默认 `0`（同提示词每次出同一张图）；要新图就换种子。
- `--out` 给的是最终产物（后缀与内容一致，不一致时自动转格式）。
- 不给 `--out` 时产物落 `data/generated/adhoc/{时间戳}/`（图 + 缩略图 + `manifest.json` 记录完整请求）。
- 命令可全局调用：`./scripts/install_cli.sh` 装到 `~/.local/bin/charvar`（`scripts/start.sh` 会顺带装）。

跨项目用还有一个 skill：`skills/charvar-skill/`（装到 `~/.claude/skills/` 后，
Claude Code 在任何项目里都能按提示词调用这条生图通路）。

### 网页入口

网页顶栏「临时生图」页签是同一条通路的图形界面（后端 `/adhoc/*`，代码走同一个
`charvar/adhoc.py`）：选后端（ComfyUI / OpenRouter）、画幅、分辨率、种子，
写提示词，加参考图（上传本地图，或直接从主体档案里挑已就绪的档案图），出图后
显示结果并可下载原图 / manifest.json。页面下方是「最近生成」墙，条目可以
「复用提示词」再改。

## 预设

YAML 预设放 `presets/*.yaml`，两种类型：

- `type: list`：逐条 `variants` 写死（原工作流 30 条逐字保留）。缺省 framing/angle/expression 会由 id 解析（`full_l45` → full+l45；`expr_smile` → head+front+smile）。
- `type: matrix`：`dimensions` 定义维度文案，`rules` 按顺序做笛卡尔积（`"*"` = 该维度全量），`exclude` 按维度取值排除（`{angle: back, expression: "*"}` 只排除「背面 + 表情」组合）。

背景/光照用 `presets/wildcards/backgrounds.yaml` / `lighting.yaml`（`items` 列表），也可在任意变体文本内使用 Impact Pack 风格的 `{a|b|c}` 内联通配符（按条目种子稳定展开）。

变体可带结构化字段：`framing`（full/chest/head）、`angle`、`expression`（表情）、`pose`（体式/动作，独立于表情语义），后两者分别写进 caption。

预设根字段 `preferred_aspect_ratio`（如 `'4:3'`）声明出图画幅：智能画幅按它出图，不按景别推导；UI 手选的比例仍然优先。**画幅值必须加引号**——YAML 会把未加引号的 `4:3` 读成 sexagesimal 数字。

## 主体档案

档案分两处：`presets/subjects/*.yaml`（随仓库走的样例档案，`charvar subject add <file>` 建档）与 SQLite `subjects` 表（实际生效的档案）。每份档案还能带一份**全场景通用提示词模板**（Markdown，存 `subject_templates` 表，网页「主体档案 → 编辑模板」或「从 .md 文件载入」维护），作为 LLM 起草角色提示词时的参考资料。

仓库内置档案：

| id | 角色 | 媒介 |
|---|---|---|
| `autumn_birch_girl` | 秋日枫林女孩 | imaishi 风格手绘漫画 |
| `mustard_cap_girl` | 黄帽红夹克女孩 | 二维手绘漫画 |
| `yoga_teacher` | 瑜伽老师 | 自然真实摄影 |
| `silver_witch` / `silver_girl_v1` | 银白女巫 / 银白女孩 | 水彩混合媒介 |
| `tvelo_illustration` / `tvelo_photoreal` | 缇珞（星际迷航同人 OC） | 插画版 / 真人版 |
| `pingpong_kid_v1` / `pingpong_kid_v2` | 乒乓小子 | 极简平涂 / 数字绘本 |
| `black_cat_v1` / `black_cat_v21` | 黑白燕尾服猫 / 黑白猫 | 水彩混合媒介 |

锁定项（`style_lock` / `identity_lock` / `outfit_lock`）是**英文**条目：每条会被拼成一句 `Maintain ...`，再与 `extra` 一起进主提示词。中文锁定项会让生成的提示词中英夹杂。

> `autumn_birch_girl` 与 `mustard_cap_girl` 是同一角色的两份资产文档（都源自同一张海报参考），按用户决定拆成两份独立档案：二者在**右手手套、包的肩侧（左肩 vs 右肩）、耳钉侧、帽上皮质标牌**上互相矛盾，各自只对自己的模板负责，不互相继承。

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
- **caption 标签一律用英文**（`tree pose` / `warrior II pose`）：caption 是训练标签，中英夹杂会污染训练信号。

## ComfyUI 集成

- 推送前端已内置比例与分辨率：`智能 / 1:1 / 3:4 / 16:9 / 4:3 / 9:16 / 2:3 / 3:2 / 21:9` + `1K / 2K`；2K 走 Qwen-Image-2.1 官方像素（面积恒定 ~4.2MP）。`智能` 会按景别使用全身 `2:3`、近景 `3:4`。

- **模式 A（零改动）**：`prompts.txt` 每行一条完整提示词，直接贴进原工作流的 `Text Multiline`（把 Master Prompt 清空、wildcard 节点旁路），队列次数 = 行数。
- **模式 B（API 排队，推荐）**：workflow 用 ComfyUI「导出 (API)」保存到 `data/workflows/{name}.json`，并写同名 `{name}.mapping.json` 指明注入节点（示例见 `presets/comfy_mapping.example.json`）。`filename_prefix` 默认写成 `{subject}_{index:03d}_{variant_id}`，与 captions 文件一一对应；逐条 `POST /prompt`，返回的 `prompt_id`（以及 `--wait` 时的输出文件名）记入 SQLite `comfy_prompts` 表，供后续关联图片与 caption。
- 连接检查：`GET /comfy/status`（网页面板「检查连接」）；队列记录：`GET /runs/{run_id}/comfyui`。
- 已验证链路：charvar → ComfyUI（0.40.0 / win32），单条排队 → 轮询完成 → 拿到输出文件名；`192.168.x.x` 属于你的本地网段，请自行替换。
- 支持 `aspect_ratio` + `resolution`：比例可选拍档里的 `auto / 1:1 / 3:4 / 16:9 / 4:3 / 9:16 / 2:3 / 3:2 / 21:9`，分辨率支持 `1k / 2k`（2K 为 Qwen 官方像素，面积恒定 ~4.2MP）；`auto` 按景别自动用 2:3（全身）或 3:4（胸像/头像/表情）。

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
| POST | `/runs/{run_id}/openrouter` | 推送 OpenRouter 在线生图（`aspect_ratio` / `resolution` / `quality` / `reference_kinds` / `model`），并发 4 路同步出图 |
| GET | `/openrouter/models` | OpenRouter 可用生图模型清单（`?refresh=1` 强制重新拉取） |
| GET | `/openrouter/capabilities` | 某模型的参数能力（按模型清单推导，供面板动态生成可选项） |
| GET | `/comfy/workflows`、`/comfy/status` | workflows 目录清单 / ComfyUI 连接检查 |
| POST | `/runs/{run_id}/sync-outputs` | 按 ComfyUI history 同步输出图，生成缩略图和 contact sheet |
| GET | `/runs/{run_id}/images` | 图片墙数据（本地原图/缩略图/sheet 状态） |
| GET | `/runs/{run_id}/comfy-image/{item_index}` | 单条输出图（优先本地资产，缺失时代理 ComfyUI） |
| GET | `/runs/{run_id}/assets/{scene}/{filename}`、`/runs/{run_id}/sheets/{kind}` | 本地原图/缩略图/拼图资产 |
| POST | `/runs/{run_id}/contact-sheet` | 手动生成 `contact_sheet.png` |
| POST | `/subjects/{subject_id}/images/{kind}/generate-openrouter` | 档案图片槽位走 OpenRouter 出图（`prompt` / `seed` / `aspect_ratio` / `resolution` / `model` / `quality`），同步返回固化后的槽位 |

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
│                   exporters（txt/jsonl/captions/comfyui）/storage/api/cli/adhoc
presets/            q21_default_30、q21_legacy_30、matrix_basic、wildcards/、subjects/
web/                web/app.py（uvicorn 启动）+ web/static/（无构建步骤）
tests/              pytest：逐字一致性/可复现/子种子/拼接/矩阵/导出/API/adhoc/CLI
scripts/            start.sh / stop.sh（默认端口 8710）/ install_cli.sh（装全局 CLI + skill）
skills/             charvar-skill：跨项目调用 charvar 生图能力的 Claude Code skill
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
