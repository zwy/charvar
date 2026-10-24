# AGENTS.md

本仓库给编码代理的工作约定。

## 启动 / 重启 / 停止

- 启动：`./scripts/start.sh`（后台，端口 `CHARVAR_WEB_PORT`，默认 8710，仅监听 127.0.0.1）
- 停止：`./scripts/stop.sh`（默认 8710；只杀 `python3 -m web.app` 的本仓库进程）
- 没有热重载：改了 `charvar/**`、`web/app.py` 或直接改环境变量后必须先停再起；只改 `web/static/**` 刷新页面即可。
- 后台启动使用 screen 会话 `charvar-8710`；stop.sh 优先读 `.charvar-web-8710.pid`，也支持按端口 + 本仓库 cwd 匹配修复前的旧进程。
- 手动验证：`curl -s --noproxy '*' http://127.0.0.1:8710/api/version`
- `start.sh` 会顺带跑 `scripts/install_cli.sh`（幂等）：把 `charvar` 装到 `~/.local/bin/`、把 `skills/charvar-skill` 软链到 `~/.claude/skills/`。安装失败不阻断 web 启动。

## Playground（跨项目调用）

- `charvar/adhoc.py` 是「给一段提示词出一张图」的独立通路：不进主体档案 5 槽位、不跑预设批量、不落 runs 表。
- CLI：`charvar image comfyui|openrouter|dreamina|models`（`--prompt`/`--prompt-file`/`--ref`/`--aspect`/`--seed`/`--out`/`--json`；`dreamina` 子命令没有 `--seed`，即梦不暴露）。产物默认落 `data/generated/adhoc/{id}/`（图 + 缩略图 + manifest.json）。

### Playground 视频（OpenRouter 通路，2026-10-23 接入）

- `charvar/adhoc_video.py` 是「给一段提示词出一段视频」的同构通路，这一轮只接 OpenRouter
  （`charvar/openrouter_video.py` 客户端）；`charvar/video.py` 是另一回事（静态图轮播合成）。
- CLI：`charvar video openrouter`（`--prompt`/`--first-frame`/`--ref`/`--video-ref`/`--aspect`/`--duration`/
  `--resolution`/`--seed`/`--model`/`--out`/`--json`）与 `charvar video models`。
  产物落 `data/generated/adhoc_video/{id}/`（original/ 成片 + thumbs/ 海报帧 + manifest.json）。
  参考视频（`--video-ref`，≤2）与首帧/参考图同给时官方语义 frame_images 优先；
  参考视频只有 Seedance gen2+ 系认（其它 provider 忽略）。
- **网页入口**：顶栏「视频」页签（`web/static/video.js`），后端 `/videos/*`（`charvar/api.py`），
  同一套 `charvar/adhoc_video.py`；改参数名/输出格式时 CLI、网页、skill 三处同步。
- 视频是**异步作业**（`POST /api/v1/videos` → 202 + job id → 轮询 → `…/content` 下载）：
  web 提交即返回 pending，前端每 8s 打 `GET /videos/{run_id}/status`（引擎是
  `adhoc_video.refresh`，幂等，终态后不打 OpenRouter）；CLI `--timeout`（默认 900s）内
  阻塞轮询，超时作业仍在跑（manifest 保持 in_progress，网页可续查）。
- 参数枚举按 `GET /api/v1/videos/models` 目录**提交前校验**（超枚举 400 白扣一次提交费），
  目录缓存在 `data/openrouter/video-models.json`（与生图 api-response.json 是两份缓存）；
  提交时 provider 拒参自动降级重试（复用 openrouter_image 的报错话式识别）。
- 首帧图 = `frame_images`（图生视频，主体一致性靠它；**别用 character_sheet，那是四视图
  拼图会出多人**——用单人全身正面/front 槽位）；参考图 = `input_references`（≤4）；
  两者同给时官方语义首帧优先（API 层原样放行，网页 UI 图生视频页签不并送参考图）。
- 模型默认链：请求显式 > 设置页「OpenRouter 视频」组（DB） > `OP_VIDEO_MODEL` env >
  heygen/heygen-video-1；API key 与生图同链路（`OP_IMAGE_API_KEY`/`LLM_API_KEY`）。
- seed：目录标 `seed: false` 的模型不发；时长的 bool 会过 pydantic int 校验，`validate` 里明确拦。
- 按秒计费（面板按 pricing_skus 出「预计 ≈」只做展示）；heygen-video-1 的便宜档是 480p $0.02/s。
- **网页入口**：顶栏「Playground」页签（`web/static/adhoc.js`），后端 `/adhoc/*`（`charvar/api.py`）。
  走的是**同一个** `charvar/adhoc.py` —— 网页与 CLI 是同一套参数语义、同一套落盘、同一份 manifest。
  改动 `charvar/adhoc.py` 的参数名或输出格式时，CLI、网页、skill 三处都要同步。
- 网页的参考图只认三种来源：上传暂存（`/adhoc/references`）、主体档案里已就绪的档案图、
  Playground「最近生成」的原图（spec 带 `run_id`；run_id 过该模块自己的严格正则，
  文件名由服务端从该 run 的 `original/` 目录按落盘布局定 —— 浏览器永远不给路径）。
  **不接受任意路径** —— 那是把文件读权限开给了浏览器。
- 这是给本项目之外的项目和其他 agent 用的；改动它要考虑外部调用方依赖的参数名与输出格式。
- `skills/charvar-skill/` 是配套的 Claude Code skill，改 CLI 参数时要同步改 skill 里的命令示例。
  要「全局可用」得装到**两处**（`scripts/install_cli.sh` 负责，幂等，`start.sh` 会调）：
  - `~/.claude/skills/charvar-skill`（软链到仓库）→ Claude Code CLI + Claude Desktop 的 **Code 标签页**；
  - Claude Desktop 的**聊天面**（3p 部署的 local agent mode）**不读 `~/.claude/skills/`** —— 它用自己的一套：
    `<Application Support>/Claude*/local-agent-mode-sessions/skills-plugin/<workspace>/<account>/`，
    技能 = `skills/` 下的**真实目录** + `manifest.json` 里一条 `creatorType: "user"` 记录
    （`ensurePluginStructure` 会清掉 app 没登记过的东西，所以必须同时写 manifest）。
- skill 里调 CLI 一律用绝对路径 `~/.local/bin/charvar` —— Desktop 聊天面的 shell 不保证把
  `~/.local/bin` 加进 PATH。

## 测试

- `python3 -m pytest -q` 必须全绿再交付。
- 与原工作流逐字相关的红线：`presets/q21_default_30.yaml` 的 30 条 variants 文本不许改动。第三方工作流 JSON 已移出仓库，tests/test_presets.py 改用 sha256 文本基准锁定（不得把原工作流 JSON 加回仓库根）。

## 实现红线

- 随机一律从 `charvar.wildcard.seed_for(seed, key)` 派生，禁止全局 `random` / `os.urandom` 参与提示词内容。
- 模板渲染只允许 `charvar.composer.render`（缺失占位符保留原样并记 warning），禁止 eval / Jinja 执行任意代码。
- ComfyUI 节点 id 一律来自用户提供的 mapping.json，不许硬编码。
- ComfyUI 请求必须 bypass 代理（exporter 里 httpx trust_env=False）：本机 shell 的 ALL_PROXY 会劫持内网地址。
- OpenRouter 生图（`charvar/openrouter_image.py`）同样 bypass 代理；画幅默认走 `size`
  显式像素（像素精确；tencent 的 `aspect_ratio` 枚举不含 21:9），但清单**声明了参数却没列
  `size`** 的模型（flux-3 只收「分辨率档 + aspect_ratio」，发 size 直接 400）改发
  `aspect_ratio`（值须在枚举内），探测缓存记过 size 被拒的一并换道，清单为空（muse）仍发
  size —— 别把这条「清单没列 size」扩大成一律不发 size。`seed` / `resolution` / `quality`
  同理只在声明支持时才发；seed 收敛到 ≤ 2^53-1（charvar 的 subseed 是 64 位 sha256，不收敛直接 400）。
- OpenRouter 生图按**模式分两组配置**（对齐 ComfyUI 文生图 / 图片编辑 workflow）：
  带参考图 = `i2i`（图片编辑），不带 = `t2i`（文生图）；能否带参考图按清单
  `architecture.input_modalities` 判断（没有 `image` 就不能当图片编辑模型）。
  env 为 `OP_IMAGE_T2I_MODEL/RESOLUTION/QUALITY` + `OP_IMAGE_I2I_MODEL/…`
  （旧 `OP_IMAGE_MODEL` 保留为兜底）；设置页同名两组（DB > env）。
  三个生图环节的默认链一律是：**请求显式 > 设置页 > env > 不发送**，
  且值要在所选模型声明的枚举里才真正发（设置页/env 的 RESOLUTION/QUALITY
  只是「请求没显式给时的默认」）。
- OpenRouter 生图的参数以 `data/openrouter/api-response.json` 里每个模型的
  `supported_parameters` 为准（`resolution` / `quality` / `aspect_ratio` 的枚举值都从那里取，模型没声明就不发）。清单是目录级声明、未必准（muse-image 声明为空但实际接受 `size`；grok 声明 `quality` 但 provider 拒收），所以 provider 拒收参数时自动降级重试并把结果记进 `data/openrouter/capabilities.json`。
  OpenRouter 通路的面板档位可用 provider 枚举值（512/1K/1.5K/2K/4K…，
  1K=1024 长边）—— 这不是开 4K 档：主体图的 charvar 档位仍只有 1k/2k。
- 参考图统一走 `charvar/reference_images.py` 预处理（长边 1536 / JPEG q88 / 透明白底合成），ComfyUI 上传与 OpenRouter 发送共用同一份缓存 —— 别再写新的压缩逻辑。
- 即梦生图（`charvar/dreamina_image.py`，2026-10-22 接入第三条通路）走本机官方
  `dreamina` CLI（子进程，不是 HTTP）：鉴权是本机 `dreamina login` 的登录态（`~/.dreamina_cli/`），
  **没有 API Key 可配；CLI 找不到用 `DREAMINA_CLI` 指路径**。授权必须用户手动在浏览器完成
  （agent 代登可能拿到错误 URL，官方文档有「非法应用」案例）。
  - 不区分文生图 / 图片编辑配置组（即梦模型都有编辑能力）：带参考图 = `image2image`
    （`--images`，最多 10 张但 charvar 上限 4），不带 = `text2image`；设置页/env 共用一组：
    `DREAMINA_IMAGE_MODEL`（默认 5.0，值 `3.0`–`5.0Pro`）+ `DREAMINA_IMAGE_RESOLUTION`
    （2K|4K，5.0Pro 另有 1.5K），同样走 请求显式 > 设置页 > env > 缺省 的默认链
    （settings_service.dreamina_config）。
  - 即梦不暴露 seed：AdhocRequest.seed 等请求值只记 manifest，**绝不发送**。
  - 提示词硬上限先于提交校验（`dreamina_image.validate_prompt`）：text2image 实测
    1600 字符、image2image 2000；`--resolution_type` 必填且 model_version 与档位配对
    （3.0/3.1→1k/2k 仅文生图；4.0–5.0→2k/4k；5.0Pro→1.5k/2k/4k），配错提交前报错。
    规格（模型/比例/分辨率枚举）以 `dreamina <subcommand> -h` 为准，CLI 升级后要同步 `_MODEL_RESOLUTIONS` 表。
  - 画幅用 `--ratio`（charvar 8 档与即梦枚举一致）；生成是异步 submit+query，
    stdout 混着「download image N URL:」进度行，解析要从首个 `{`/`[` 行起
    （`parse_task_output`），产物路径读 `result_json.images[].path`。
- 档案图片有三条生图通路：ComfyUI（`subject_images.generate_subject_image`）、OpenRouter
  （`generate_subject_image_openrouter`）与即梦（`generate_subject_image_dreamina`）。三者同语义：
  两步流程（character_sheet 先出，其余 4 种以它为参考图）、同槽位状态机、同落盘位置
  （`data/subjects/{id}/{kind}.{ext}`）。产出哪个后端记在 `subject_images.provider` 列
  （`comfyui` / `openrouter` / `dreamina` / 空=上传）。
- `web/static/style.css` 里 `label { display: flex }` 会盖掉浏览器默认的 `[hidden] { display: none }`，所以有 `[hidden] { display: none !important }` 这条兜底。新增按条件显隐的控件时，`<select>` 自身带 `hidden` 的话，JS 要同时清掉 select 和它外层 label 上的 `hidden`（`applyOpenRouterCapabilities` 就是这么做的）。
- `.env`（COMFYUI_HOST/PORT 等）不进 git；改 .env 后要重启 web 服务。
- 主体档案的锁定项中英文皆可（2026-10-22 放开，此前只许英文）：`style_lock` / `identity_lock` / `outfit_lock`
  每条会被拼成一句 `Maintain ...` 进提示词——classic 模式下**原样进提示词**（中文条目就是中文进图，
  Qwen-Image / OpenRouter 这类双语或 LLM 系模型没问题）；LLM 模式下锁定项只作为 LLM 输入、由
  LLM 重写成英文，语言无所谓。目标后端要纯英文提示词时（SD 类老模型）建议仍写英文。
  导入/校验工具不得把非 ASCII 锁定项当错误。
- 主体档案的 Markdown 模板上限 24,000 字符（LLM 输入截断阈值，`charvar.llm_prompts._TEMPLATE_MAX_CHARS`）；超长模板尾部会被静默截掉，别指望模型看到。
- Playground（`charvar/adhoc.py`）的红线：参考图槽位名从 mapping 读（`image`/`image2`/…），不许写死节点 id；ComfyUI 上传的 remote_name 必须带唯一前缀（`upload_image` 发 `overwrite: true`，同名会互相覆盖 → 队列里的旧 prompt 取到新图）；`--out` 指定的文件后缀与内容必须一致（不一致时转格式，别让后缀骗人）；adhoc 不读 `settings.image_sizes`（那是主体档案槽位的尺寸）；不引入随机源（seed 由调用方显式给）。
- Playground 的 run_id 直接拼进文件路径（`/adhoc/{run_id}/image` 直接 serve 磁盘文件）：
  `charvar.adhoc.run_dir()` 用自己的正则校验（`^adhoc_[A-Za-z0-9_.-]+$` 且不含 `..`），
  比 `assets.safe_run_id` 严 —— 后者放行 `..`。**别退回用 safe_run_id**。
- 上传的参考图按**实际图片格式**判后缀（用 PIL 读，不信文件名），落 `data/generated/adhoc/_refs/`；
  出图成功后自动清掉，失败时保留以便重试。
- **2K 必须用 Qwen-Image-2.1 官方像素表**（`charvar/exporters/comfyui.py` 的 `_QWEN_2K_SIZES`：
  1:1=2048×2048、4:3=2400×1792、3:4=1792×2400、3:2=2528×1696、2:3=1696×2528、
  16:9=2752×1536、9:16=1536×2752）。模型原生是**恒定 ~4.2MP 的 2K**（按面积定义，不是长边）——
  早期按「长边 2048」归一算出的 2K 只有原生面积的 56%（16:9 才 2048×1152），
  模型被迫上采样 → 图糊。**别退回长边归一**。前端 `web/static/app.js` 的 `QWEN_2K_SIZES`
  是同一张表，改一处要同步另一处，否则预览像素和实际不符。
- **4K 档已下线**（`1k` / `2k` 两档）。官方明说远超 2K 训练尺寸会丢提示词遵循度；
  实测 4096×4096 耗时 75s（2K 只要 10s）且细节崩坏。`settings_service.ImageSize`
  有 validator 把存量设置里的 "4k" 降级到 "1k"（别删 —— 存量 DB 里可能还有）。
- 2K 官方表**只给 ComfyUI 通路**（跑的是 Qwen-Image-2.1）。OpenRouter 通路
  （muse/grok/gpt-image）传 `use_model_native=False` 走通用长边归一 —— 那边的模型
  不认 Qwen 的尺寸表。

## 导入主体档案（外部 Markdown → 系统）

1. 在 `presets/subjects/<id>.yaml` 写档案（id/名/锁定项/extra），随仓库走、可复现。
2. 建档：`python3 -m charvar.cli subject add presets/subjects/<id>.yaml`（同 id 覆盖加 `--overwrite`）。
3. 模板全文存 `subject_templates`：`PUT /subjects/<id>/template`（body `{"template_md": "..."}`），或网页「主体档案 → 从 .md 文件载入」。
4. 锁定项与模板全文逐字保留原文，不做改写或摘要。
5. 导入后跑一次 `python3 -m pytest -q`，并用 `/subjects/<id>/images/<kind>/suggest-prompt` 验一次 LLM 起草能跑通。

**只有模板 .md、还没写档案时**：`charvar subject derive <template.md> --id <slug>` 从模板派生候选
（`charvar/subject_derive.py`：LLM 抽取 → 引用核对（zh 模式锁定项必须回指模板原文，防编造）→
独立审计（omission/contradiction/violation）；默认只写 `presets/subjects/<id>.yaml` 不入库，
`--apply` 才入库，`--overwrite` 覆盖，多版本模板必须 `--version <关键词>`）。
已入库配对的审计：`charvar subject verify <id>`（exit 0 通过 / 1 有问题单，`--json` 供 agent）。
DB → 仓库对偶：`charvar subject export <id>` 把已入库档案快照成 `presets/subjects/<id>.yaml` +
`<id>.md`（模板逐字、无改写）。**DB 是运行时真源，仓库靠 export 保持镜像**——直接在 DB/网页建档后
要记得 export，仓库里没有备份的档案清库即失。

## 服装预设与贴字（2026-10-24 接入）

都是出图后的组合能力，服务于「视频配方」的合成流水线（出图批 → 贴字 → 本地合成 reel）。

- **服装预设** = `presets/outfits/*.yaml`（`charvar/outfit.py`）：每条 = 英文服装描述段 `text`
  + 中文 `label`（贴字/展示用）+ 可选 `caption_tags`（id → 英文 caption token，缺省 = id）。
  子目录家族，**不进主预设下拉**（`list_presets` 只扫 presets/*.yaml 顶层）。
- **跨预设笛卡尔积**：出图请求带 `outfit_preset`（+ `outfit_order`：`outfit_first`/`pose_first`，
  缺省 outfit_first = 每件服装连播全部动作）。服装段**追加在动作段之后**——服装段必须短
  （白裙丝袜动作预设实测：前置长文本把动作稀释到 14%，动作不执行；这条红线对服装段同样成立，
  建议每条 ≤200 字符，先小批验证动作还成立）。变体 id = `{outfit_id}__{pose_id}`
  （pose_first 时反序），caption 在 pose 之后插 outfit token，meta 带 `outfit`/`outfit_label`。
  语义：`POST /generate`（带 outfit_preset）= `charvar gen --outfit … --outfit-order …`，
  网页生成页「服装预设 + 组合顺序」同源。变体顺序就是后续 reel 合成的播放顺序。
- **贴字** = `presets/overlays/*.yaml`（`charvar/overlay.py`，PIL 确定性后处理，**不改原图**）：
  产物落 `<data>/generated/<run_id>/labeled/{index:06d}.png`（scene 白名单加了 labeled）。
  字段：position/margin_x/y/font/size（短边占比）/color/stroke/plate=pill/opacity/rotate/
  max_width（超宽换行，CJK 按字、拉丁按词）/align/line_gap。文本来源 `text_from` =
  `outfit_label` / `pose_tag` / `variant_id` / `fixed`。
  语义：`POST /runs/{id}/overlay` = `charvar overlay <run_id> --overlay <preset> [--text-from …] [--items 0,1]`。
- **字体**：解析顺序 = `presets/overlays/fonts/` 里的文件 > 系统候选（macOS PingFang /
  Hiragino、Linux Noto CJK、Windows 雅黑）；`font: ""` = 自动。要跨机稳定就把字体文件放进
  `fonts/`，**只放可再分发的开源字体（OFL 系安全）；花字/美术字体的版权要单独确认**。
- 与 **sticker（带字表情包）** 是两套机制：sticker 的字是提示词驱动（模型原生画字，
  `charvar/sticker_policy.py` 的 display_text/lettering）；贴字是确定性 PIL 后处理。
  两者共存不冲突；**sticker 预设拒绝服装组合**（语义冲突，400）。

## 视频配方（2026-10-24 接入）

- **配方定义** = `presets/recipes/<slug>.yaml` + `<slug>.md` 成对（`charvar/recipes.py`；文档
  `doc:` 相对 recipes/ 目录，读不到不阻塞）。大类由末步推导：合成配方（末步 video_synth，已支持）/
  生成配方（末步 video_model，⑤期才接 kind）。YAML 严格模式：未知根/步字段显式报错。
- **线性步骤**：`images`（主体+预设[+服装预设]笛卡尔积建 run，classic 缺省、`mode: llm` 走 LLM 改写
  （硬前提：主体模板+参考图槽位就绪，LLM 输出替代 master 拼接，预设文本只做改写输入）；`push: comfyui`
  自动提交+等待+同步成品，`reference_kinds` 注入 image1..N；`run_id: <既有 run>` = 绑定不建批；
  `fixed_background`/`fixed_lighting` 固定背景/光照，覆盖通配符池——轮播类成片跨帧一致用，转面
  橱窗 turnaround_showcase 即此用法）→
  `overlay`（贴字步：`input`=前面 images 步 label，缺省最近一个）→ `video_synth`
  （presets/video 预设合成本地 MP4；`use_labeled: true` 时同一来源 run 的贴字产物优先
  ——`render_video` 加了可选 `images_dir`，缺省 original/ 不变）。消耗步统一 gate：
  来源 run 成品没齐停在 waiting 并写原因，不算失败。
- **检查点**：step.manual 缺省 images=False、overlay/video_synth=True。执行到 pending 的
  manual 步停（status=waiting）；`--continue` / `POST /recipe-runs/{id}/continue` 批准当前
  manual 步推进，之后的 manual 步再停；failed 步 continue 直接重试。
  **`--continue` 是「续跑已有运行」——首次必须不带它**（`charvar recipes run <名>` 建运行并停在
  首个检查点），否则报「没有可继续的运行」。
- **状态**：`data/generated/recipe_runs/<recipe_id>/manifest.json`（recipe_id 白名单
  `^recipe_[A-Za-z0-9_.-]+$` 且无 `..`，同 adhoc 严格度），**不建 DB 表**、不落 runs 表；
  配方定义改版（步数不一致）时 continue 拒绝，要求重建。
- 入口同语义三处：`charvar recipes list|run|status`（`--continue` 续最近一次）、
  `/recipes` `/recipes/{name}` `/recipes/{name}/run` `/recipe-runs*` API、
  顶栏「配方」页签（`web/static/recipes.js`：列表/详情/文档/开始运行/检查点继续/成片预览）。
  示例配方：`presets/recipes/stockings_showcase.yaml`（自动推 ComfyUI，跑一次会真出 27 张图——
  **跑之前想清楚**；demo 不真跑，测试里全链用绑定 run 的临时配方验证过）。

## 生成配方与参考视频（2026-10-24 ⑤⑥ 接入）

配方执行器新增两类**生成步**（`charvar/recipes.py`），配方大类由末步推导：
末步 `video_model` = 生成配方（OpenRouter 视频模型直出）；其余 = 合成配方。

- **`adhoc_image` 步**（单图，Playground 通路 `charvar.adhoc`）：`prompt` 必填
  （原样使用，不做模板替换）+ `backend`（comfyui/openrouter/dreamina，缺省
  openrouter）+ `model`/`aspect`/`resolution`/`quality`（仅 openrouter）/
  `negative_prompt`（仅 comfyui）/`seed`（缺省配方级种子）/`workflow`（comfyui）。
  参考图 = `reference_labels`（引用配方 `refs:`，见下）。产物落
  `data/generated/adhoc/<id>/`，outputs 记 `image_run_id` + `/adhoc/<id>/image`；
  retry 幂等（产物在就复用，不重复生成）。
- **`video_model` 步**（OpenRouter 视频异步作业，`charvar.adhoc_video`）：
  `prompt` 必填 + `model`/`duration`/`resolution`/`aspect`（不发送=留空）/
  `seed`。帧：`frame_from`（前面 images/adhoc_image/overlay 步 label）+
  `frame_index`（缺省 0）或 `frame_ref`（配方 refs 静态图）；`frame_slot`
  first_frame 缺省 / last_frame；`last_frame_from`(+`last_frame_index`) 补尾帧。
  参考素材：`reference_labels`（refs → input_references 图）+
  `video_ref_labels`（video_refs → input_references 的 video_url，Seedance
  gen2+ 才接）+ `video_from`（前面 video_synth/video_model 步的成片 → video_url）。
  异步语义：提交（`adhoc_video.generate(wait=False)`，run_id 即刻进 outputs）
  → 轮询 `adhoc_video.refresh` 直到 ready/failed 或超出 `wait_s`（缺省 300s；
  超时转 waiting，作业本身在 OpenRouter 继续，continue 续查不重复提交）。
  作业 failed → 步 failed；continue **清掉死作业重新提交**（重新计费，之前
  的失败原因在 outputs.previous_error… 失败 manifest 留在 Playground 列表）。
  提交被拒（枚举/key 错）= PresetError → failed，continue 前先修配置。
- **refs / video_refs**（配方级素材声明，步内按 `label` 引用；label 全局唯一）：
  - `refs:` 条目 `{from: subject|run|asset, …}` —— subject 必须配配方级
    `subject:`（槽位图走 prepared_reference 缓存）；run 取
    `original/{index:06d}.*`（`index: 0` 缺省）；asset =
    `presets/recipes/assets/<单文件名>`（禁路径穿越；图片后缀）。
  - `video_refs:` 条目同形但不允许 subject —— asset 视频或 `run` =
    **adhoc_video 作业**（`video_file()`，成片 original/ 下唯一文件）。
  - 缺素材/作业未完 → _GateStop（waiting 可 continue）；素材路径非法 → 解析期 400。
- 配方示例：`presets/recipes/model_clip_demo.yaml`（生成配方：adhoc_image 首
  帧[comfyui] → video_model 直出，heygen 480p $0.02/s）——两步都缺省 manual，
  `run` 一次停 2 次检查点；真跑要 OpenRouter key。

**参考视频通路（⑥，打在 Playground 视频上）**：

- `openrouter_video.build_body` 增 `reference_videos`（→ `input_references` 的
  `{"type":"video_url","video_url":{"url": data:…|https…}}`；官方只收
  mp4/mpeg/mov/webm，按 ftyp/EBML 魔数嗅探、原文件 base64 上限 48MB）与
  `last_frame_path`（frame_type=last_frame，first+last 成对或单给；模型声明
  supported_frame_images 不含则 ParamError）；`input_references` 合计 ≤4 项。
- `adhoc_video.VideoRequest` 增 `reference_video_paths` / `last_frame` /
  `staged_video_ref_ids`；**参考视频不做图像预处理**（没有压缩逻辑，原样发），
  提交前 existence+魔数双校验。
- 暂存视频池 = `data/generated/adhoc_video/_vrefs/`（与 `_refs` 图池分开，互不
  误删）：`save_staged_video_reference` / `staged_video_reference` /
  `clear_staged_video_references` / `prune_staged_video_references`（24h 兜底）。
- 入口同语义三处：CLI `charvar video openrouter --video-ref <file>`（可重复
  ≤2）；网页「视频」页参考视频区（上传暂存 ≤2，t2v/i2v 都发）；
  `/videos/video-references`+{ref_id} 暂存四件套与 `/videos/generate` 的
  `video_references:[{staged_id}]`。配方里走 `video_refs`+`video_ref_labels`/
  `video_from`（见上）。
- **终态语义修正**：OpenRouter 官方 202 语义 cancelled/expired 也是终态 ——
  `openrouter_video._TERMINAL_FAIL`（wait_for_job）与 `adhoc_video.refresh`
  都按 failed 收尾并记 error，不会无限轮询。
