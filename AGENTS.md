# AGENTS.md

本仓库给编码代理的工作约定。**只写当前有效的规则**——不记版本历史、不记改了什么、不记日期。
历史在 git log 与各包自己的 `manifest.json` 里。

## 启动 / 重启 / 停止

- 启动：`./scripts/start.sh`（后台，端口 `CHARVAR_WEB_PORT`，默认 8710，仅监听 127.0.0.1）
- 停止：`./scripts/stop.sh`（默认 8710；只杀 `python3 -m web.app` 的本仓库进程）
- 没有热重载：改了 `charvar/**`、`web/app.py` 或直接改环境变量后必须先停再起；只改 `web/static/**` 刷新页面即可。
- 后台启动用 screen 会话 `charvar-8710`；stop.sh 优先读 `.charvar-web-8710.pid`，否则按端口 + 本仓库 cwd 匹配。
- 手动验证：`curl -s --noproxy '*' http://127.0.0.1:8710/api/version`
- `start.sh` 顺带跑 `scripts/install_cli.sh`（幂等，见「仓库里的 skills」）；失败不阻断启动。

## Playground（跨项目调用）

`charvar/adhoc.py` 是「给一段提示词出一张图」的独立通路：不进主体档案 5 槽位、不跑预设批量、不落 runs 表。

- CLI：`charvar image comfyui|openrouter|dreamina|models`（`--prompt`/`--prompt-file`/`--ref`/`--aspect`/`--seed`/`--out`/`--json`；`dreamina` 子命令没有 `--seed`，即梦不暴露）。产物默认落 `data/generated/adhoc/{id}/`（图 + 缩略图 + manifest.json）。
- **网页入口**：顶栏「Playground」页签（`web/static/adhoc.js`），后端 `/adhoc/*`（`charvar/api.py`）。走的是**同一个** `charvar/adhoc.py` —— 网页与 CLI 是同一套参数语义、同一套落盘、同一份 manifest。改动 `charvar/adhoc.py` 的参数名或输出格式时，CLI、网页、skill 三处都要同步。
- 网页的参考图只认三种来源：上传暂存（`/adhoc/references`）、主体档案里已就绪的档案图、Playground「最近生成」的原图（spec 带 `run_id`；run_id 过该模块自己的严格正则，文件名由服务端从该 run 的 `original/` 目录按落盘布局定 —— 浏览器永远不给路径）。**不接受任意路径** —— 那是把文件读权限开给了浏览器。
- 它给本项目外的项目/其他 agent 用；外部调用方依赖它的参数名与输出格式，改动要兼容。

### Playground 视频（OpenRouter 通路）

`charvar/adhoc_video.py` 是「给一段提示词出一段视频」的同构通路，只接 OpenRouter
（`charvar/openrouter_video.py` 客户端）；`charvar/video.py` 是另一回事（静态图轮播合成）。

- CLI：`charvar video openrouter`（`--prompt`/`--first-frame`/`--ref`/`--video-ref`/`--aspect`/`--duration`/`--resolution`/`--seed`/`--model`/`--out`/`--json`）与 `charvar video models`（列模型 + 时长/分辨率/画幅枚举 + 按秒价目）。
  产物落 `data/generated/adhoc_video/{id}/`（original/ 成片 + thumbs/ 海报帧 + manifest.json）。
  参考视频（`--video-ref`，≤2）与首帧/参考图同给时官方语义 frame_images 优先；参考视频只有 Seedance gen2+ 系认（其它 provider 忽略）。
- **网页入口**：顶栏「视频」页签（`web/static/video.js`），后端 `/videos/*`（`charvar/api.py`），同一套 `charvar/adhoc_video.py`；改参数名/输出格式时 CLI、网页、skill 三处同步。
- 视频是**异步作业**（`POST /api/v1/videos` → 202 + job id → 轮询 → `…/content` 下载）：web 提交即返回 pending，前端每 8s 打 `GET /videos/{run_id}/status`（引擎是 `adhoc_video.refresh`，幂等，终态后不打 OpenRouter）；CLI `--timeout`（默认 900s）内阻塞轮询，超时作业仍在跑（manifest 保持 in_progress，网页可续查）。
- 参数枚举按 `GET /api/v1/videos/models` 目录**提交前校验**（超枚举 400 白扣一次提交费），目录缓存在 `data/openrouter/video-models.json`（与生图 api-response.json 是两份缓存）；提交时 provider 拒参自动降级重试（复用 openrouter_image 的报错话式识别）。
- 首帧图 = `frame_images`（图生视频，主体一致性靠它；**别用 character_sheet，那是四视图拼图会出多人**——用单人全身正面/front 槽位）；参考图 = `input_references`（≤4）；两者同给时官方语义首帧优先（API 层原样放行，网页 UI 图生视频页签不并送参考图）。
- 模型默认链：请求显式 > 设置页「OpenRouter 视频」组（DB） > `OP_VIDEO_MODEL` env > heygen/heygen-video-1；API key 与生图同链路（`OP_IMAGE_API_KEY`/`LLM_API_KEY`）。
- seed：目录标 `seed: false` 的模型不发；时长的 bool 会过 pydantic int 校验，`validate` 里明确拦。
- 按秒计费（面板按 pricing_skus 出「预计 ≈」只做展示）；heygen-video-1 的便宜档是 480p $0.02/s。

## 仓库里的 skills（`skills/`）

**开发或改 skill 前先读 `docs/skill 开发规范.md`** —— 包结构、frontmatter、渐进披露、版本三处同步、兄弟 skill 去重、测试分类（哪些机器查得到、哪些只能人工核）。规范里 ✅ 的部分由 `tests/test_skills.py` 自动校验（`python3 -m pytest -q` 会跑）。

| skill | 干什么 | 不干什么 |
|---|---|---|
| `charvar-skill` | 按提示词实际出图（ComfyUI / OpenRouter / 即梦）、出视频、主体档案 CLI | 不做角色开发流程 |
| `character-forge` | 角色开发：全身正面定妆 → 同图半身＋正侧背设定板 → 自包含「角色提示词资产文档」 | **不出图**（默认由用户自己出图并反馈） |

- 两个 skill 配套：character-forge 产提示词与资产文档，charvar-skill 负责把提示词变成图。**别把出图逻辑写进 character-forge**，也别把角色开发流程写进 charvar-skill。
- `skills/charvar-skill/` 改 CLI 参数时要同步改 skill 里的命令示例。
- **安装**：`scripts/install_cli.sh`（幂等，`start.sh` 会调）按 `SKILL_NAMES` 数组装**所有** skill，装法**三处**一致：
  1. `~/.claude/skills/<name>`（软链到仓库）→ Claude Code CLI + Claude Desktop 的 **Code 标签页**；
  2. `<repo>/.claude/skills/<name>`（相对软链）→ 本项目内 Claude Code 自动发现；
  3. Claude Desktop 的**聊天面**（3p 部署的 local agent mode）**不读 `~/.claude/skills/`** —— 它用自己的一套：
     `<Application Support>/Claude*/local-agent-mode-sessions/skills-plugin/<workspace>/<account>/`，
     技能 = `skills/` 下的**真实目录** + `manifest.json` 里一条 `creatorType: "user"` 记录
     （`ensurePluginStructure` 会清掉 app 没登记过的东西，所以必须同时写 manifest）。
- 改名/合并后要把旧名写进 `RETIRED_SKILL_NAMES`，否则新旧两个 skill 描述几乎相同，会抢同一个请求。
- skill 里调 CLI 一律用绝对路径 `~/.local/bin/charvar` —— Desktop 聊天面的 shell 不保证把 `~/.local/bin` 加进 PATH。
- `character-forge` 自带静态检查（`python3 skills/character-forge/tests/run_checks.py`，`--record` 回写 manifest），已接进仓库门禁（`tests/test_skills.py` 会调起它），**不用**单独记着跑。
- 改了任一 skill 的 SKILL.md / README.md / manifest.json 里的**版本号**要三处同步，否则检查会失败。
- **character-forge 的交付校验有坑**：交付模板里提示词代码块预设为 `text template`（不计入预算），填完必须改成 `text submit`；`prompt_budget.py` 校验文档里的提示词块**必须加 `--markdown`**，不加会把整份文档当一条提示词而误判失败。

## 预设页（生成工作流）

页面按 Playground 的交互排成三步：

1. **① 生图设置**：主体档案 + 供应商（ComfyUI / OpenRouter / 即梦）+ 该供应商的生图参数（画幅 / 分辨率 / 质量 / 模型版本 / **种子** / 负向 / 等待完成）+ 参考图。
2. **② 生成提示词**：预设（可叠服装预设）+ 生成模式（经典模板 / LLM 优化）+ 模板参数（条数 / 采样 / 分隔符 / 背景光照）。
3. **③ 生成结果**：「推送生图」**单一**按钮，随后是结果表 + 导出。卡标题行给当前批的只读摘要（主体 · 预设 · 种子 · 条数）。

- **不保留文生图 / 图片编辑切换**：有参考图 = 图片编辑（ComfyUI 走图片编辑 workflow / OpenRouter 走 `i2i` 组 / 即梦走 `image2image`），无参考图 = 纯文生图，后端按「有没有参考图」自动分流。
- **主体档案只有一个选择器（在①）**：提示词主体与参考图主体必须一致（锁定项说红发、参考图是黑发就出戏）。「从主体档案加入」复用①的主体 + 槽位下拉；**换主体时旧主体的档案参考图自动清掉**（`GenRefs.dropSubjectRefs()`，上传暂存 / 最近生成的不受主体约束，保留）。
- **参考图来源与 Playground 同源**（`ReferenceSpec`）：`staged_id`（上传暂存）/ `subject_id`+`kind`（主体档案槽位）/ `run_id`（+`index`，最近生成原图；`adhoc_*` 取唯一原图，charvar run 按 index 取第 N 张）。**只认 id 不认路径**。
- **role（分工）**：每张参考图一个 role（`front`/`headshot`/`side`/`behind`/`character_sheet`，缺省 `front`），决定 LLM 锚定句里这张图管哪一摊；主体档案来源自动取槽位 kind 当 role。
- references 随 `/generate` 存进 run（`GenerateResult.references`），推送生图与单条「重新生成」都按它取图；`regenerate` 优先重放快照里的 references。
- `reference_kinds` 参数与 CLI 调用路径**仍受支持**（向后兼容），CLI `charvar gen --outfit` 同一套修复生效。
- OpenRouter 参数档位按 `data/openrouter/api-response.json` 的 `supported_parameters` 动态加载（同 Playground）；即梦分辨率表放 `web/static/tables.js`（app.js 与 adhoc.js 共用，别再各写一份）。
- **supported parameters 的几条约定**（两边页面一致）：
  - ComfyUI 没有 provider 枚举，档位固定 charvar 的 `1k`/`2k`（2k = Qwen 官方 ~4.2MP 像素）。
  - OpenRouter 的模型**不在面板里选**，跟设置页的 `t2i`/`i2i` 组默认（带参考图 = i2i 组）；分辨率 / 质量取该模型清单的枚举，没有就不显示。
  - 即梦：模型版本下拉空值 = 跟随设置页（`DREAMINA_IMAGE_MODEL`）；**「跟随设置默认」要真跟随**（空值发出去，后端才用设置页/env），不能名义上跟随、实际发硬编码的 2K。
  - 即梦 **3.0/3.1 只有 text2image**：带参考图（即 image2image）时从模型下拉里去掉（`DREAMINA_T2I_ONLY_MODELS`，放 `tables.js` 共享）。
  - 档位选择要记住：切供应商再切回来别重置（`state.comfyTier` / `adhocState.comfyTier` / `state.orResolution`）。
- ComfyUI 上传的 `remote_name` = `charvar_{run_id}_{index}{suffix}`：`upload_image` 发 `overwrite: true`，同名会互相覆盖。
- 前端文件：`web/static/genrefs.js`（参考图选择器，导出 `GenRefs`）、`web/static/app.js`（供应商面板 + 推送生图）、`web/static/tables.js`。
  脚本加载顺序：`tables.js` → `media.js` → `genrefs.js` → `app.js` → `adhoc.js` → `video.js` → `recipes.js`（app.js 的 `init()` 在文件末尾，必须在 `GenRefs` 之后；`tables.js` 必须在 app.js 之前 —— 共享表是全局 const，顺序错会 TDZ 报错）。

## 全站视觉分组与文案

- **分组容器全站唯一** = `.form-section`（flex column + gap sp-2），组标题 `.form-section-title`（右侧可放状态，如「已选 0 张」）；组间用 `margin-top sp-3 + padding-top sp-3 + 1px 分隔线`，**不要**给 `.card` 的直接子元素手写 margin（见 CSS 注释）。
  预设页① 拆三组（主体与种子 / 供应商参数 / 参考图），② 拆两组（预设与服装 / 提示词参数）；Playground = 供应商与参数 / 提示词 / 参考图；视频 = 模式与参数 / 提示词 / 首帧图 / 参考图 / 参考视频；主体档案 = 基本信息 / 锁定项 / 补充；设置页一卡一主题。**别新造 `.gen-group` 这类第二套分组类。**
- **间距只在 4/8/12/16 阶梯内收紧**：`.card` padding `14px 16px`、`.card > * + *` 用 sp-3、`.form-grid` 行距 sp-2（列距仍 sp-4）；`.segmented .seg` padding `4px 12px`。500px 断点要同步。
- **灰字只写「控件里看不到的信息」**：当前生效的 workflow / 模型 / 枚举、当前输出像素、已选参考图数量、不可逆后果（超限硬失败、暂存 24 小时清理、按秒计费、消耗积分）。**删掉**复述控件含义的说明、描述「先做 A 再做 B」的导航文案、重复当前主体的摘要行、以及讲落盘路径 / CLI 用法 / 数据库表这类**实现细节**的长段落（那属于 README）。**每块最多一句灰字**，长说明拆不成一句时宁可删。
- 参考图区统一形态：组标题 + 右侧「已选 N 张」+ `.ref-empty` 虚线空态框（`#gen-ref-count` / `#adhoc-ref-count` / `#video-ref-count`）。
- 参考图条目宽度限 `max-width: 240px` + 名字换行；「随机背景 / 随机光照」两个 checkbox 挪到固定背景 / 固定光照之后单独成行（`label.check` 是行内排版，跟普通 label 同排时底边会错位）。
- `.gen-mode-row`（小标题 + 分段控件同一行）、`.form-grid-2`（只有两个字段的组固定 2 列）。
- 视觉方向与文案策略固化在 `.superdesign/design-system.md`（Superdesign 工具产出）+ `.superdesign/init/*.md`；**改任何页面的分组 / 间距 / 文案前先读 design-system.md**，别自创一套。

## 测试

- `python3 -m pytest -q` 必须全绿再交付。
- 与原工作流逐字相关的红线：`presets/actions/q21_default_30.yaml` 的 30 条 variants 文本不许改动。`tests/test_presets.py` 用 sha256 文本基准锁定，**不得把原工作流 JSON 加回仓库根**。

## 预设存储（`presets/`）

布局与命名规范见 `presets/README.md`，结构契约由 `tests/test_presets_structure.py` 校验。

- **名是标识，路径不是**：yaml 的 `name:` 就是标识（配方 `presets:`、历史 run 的 `preset` 列都存它）——改路径零影响，**改名等于换标识**。
- 动作预设只放 `presets/actions/`；条目数不进文件名（count 现算），版本在 `version:`、文件名 `_vN` 与之一致；`subject:` 标归属主体，前端按它分「通用 / 主体专属」。
- `presets/subjects/<id>.md` 是**交付文档**：只写当前态、身份只写一次、≤ 10,000 字符（模板见 `skills/character-forge/templates/character-document.md`）。
- 主体档案**一个主体一套默认服装**：`outfit_lock` 不并列第二套（`_MULTI_OUTFIT_RE` 告警 + `tests/test_presets_structure.py` 拦截）。

## 实现红线

- 随机一律从 `charvar.wildcard.seed_for(seed, key)` 派生，禁止全局 `random` / `os.urandom` 参与提示词内容。
- 模板渲染只允许 `charvar.composer.render`（缺失占位符保留原样并记 warning），禁止 eval / Jinja 执行任意代码。
- ComfyUI 节点 id 一律来自用户提供的 mapping.json，不许硬编码。
- ComfyUI 请求必须 bypass 代理（exporter 里 httpx trust_env=False）：本机 shell 的 ALL_PROXY 会劫持内网地址。
- OpenRouter 生图（`charvar/openrouter_image.py`）同样 bypass 代理；画幅默认走 `size` 显式像素（像素精确；tencent 的 `aspect_ratio` 枚举不含 21:9），但清单**声明了参数却没列 `size`** 的模型（flux-3 只收「分辨率档 + aspect_ratio」，发 size 直接 400）改发 `aspect_ratio`（值须在枚举内），探测缓存记过 size 被拒的一并换道，清单为空（muse）仍发 size —— 别把这条扩大成一律不发 size。`seed` / `resolution` / `quality` 同理只在声明支持时才发；seed 收敛到 ≤ 2^53-1（subseed 是 64 位 sha256，不收敛直接 400）。
- OpenRouter 生图按**模式分两组配置**（对齐 ComfyUI 文生图 / 图片编辑 workflow）：带参考图 = `i2i`（图片编辑），不带 = `t2i`（文生图）；能否带参考图按清单 `architecture.input_modalities` 判断（没有 `image` 就不能当图片编辑模型）。
  env 为 `OP_IMAGE_T2I_MODEL/RESOLUTION/QUALITY` + `OP_IMAGE_I2I_MODEL/…`（`OP_IMAGE_MODEL` 保留为兜底）；设置页同名两组（DB > env）。
  三个生图环节的默认链一律是：**请求显式 > 设置页 > env > 不发送**，且值要在所选模型声明的枚举里才真正发（设置页/env 的 RESOLUTION/QUALITY 只是「请求没显式给时的默认」）。
- OpenRouter 生图的参数以 `data/openrouter/api-response.json` 里每个模型的 `supported_parameters` 为准（`resolution` / `quality` / `aspect_ratio` 的枚举值都从那里取，模型没声明就不发）。清单是目录级声明、未必准（muse-image 声明为空但实际接受 `size`；grok 声明 `quality` 但 provider 拒收），所以 provider 拒收参数时自动降级重试并把结果记进 `data/openrouter/capabilities.json`。
  OpenRouter 通路的面板档位可用 provider 枚举值（512/1K/1.5K/2K/4K…，1K=1024 长边）—— 这不是开 4K 档：主体图的 charvar 档位仍只有 1k/2k。
- 参考图统一走 `charvar/reference_images.py` 预处理（长边 1536 / JPEG q88 / 透明白底合成），ComfyUI 上传与 OpenRouter 发送共用同一份缓存 —— 别再写新的压缩逻辑。
- 即梦生图（`charvar/dreamina_image.py`）走本机官方 `dreamina` CLI（子进程，不是 HTTP）：鉴权是本机 `dreamina login` 的登录态（`~/.dreamina_cli/`），**没有 API Key 可配；CLI 找不到用 `DREAMINA_CLI` 指路径**。授权必须用户手动在浏览器完成（agent 代登可能拿到错误 URL，官方文档有「非法应用」案例）。
  - 不区分文生图 / 图片编辑配置组（即梦模型都有编辑能力）：带参考图 = `image2image`（`--images`，最多 10 张但 charvar 上限 4），不带 = `text2image`；设置页/env 共用一组：`DREAMINA_IMAGE_MODEL`（默认 5.0，值 `3.0`–`5.0Pro`）+ `DREAMINA_IMAGE_RESOLUTION`（2K|4K，5.0Pro 另有 1.5K），同样走 请求显式 > 设置页 > env > 缺省 的默认链（settings_service.dreamina_config）。
  - 即梦不暴露 seed：AdhocRequest.seed 等请求值只记 manifest，**绝不发送**。
  - 提示词硬上限**先于提交校验**（`dreamina_image.validate_prompt`）：**text2image 1592 字符、image2image 1992 字符**（上游硬上限 1600/2000，charvar 留 8 字符余量）—— 按 1592 / 1992 写，别按 1600 / 2000。`--resolution_type` 必填且 model_version 与档位配对（3.0/3.1→1k/2k 仅文生图；4.0–5.0→2k/4k；5.0Pro→1.5k/2k/4k），配错提交前报错。规格（模型/比例/分辨率枚举）以 `dreamina <subcommand> -h` 为准，CLI 升级后要同步 `_MODEL_RESOLUTIONS` 表。
  - 画幅用 `--ratio`（charvar 8 档与即梦枚举一致）；生成是异步 submit+query，stdout 混着「download image N URL:」进度行，解析要从首个 `{`/`[` 行起（`parse_task_output`），产物路径读 `result_json.images[].path`。
- 档案图片有三条生图通路：ComfyUI（`subject_images.generate_subject_image`）、OpenRouter（`generate_subject_image_openrouter`）与即梦（`generate_subject_image_dreamina`）。三者同语义：两步流程（character_sheet 先出，其余 4 种以它为参考图）、同槽位状态机、同落盘位置（`data/subjects/{id}/{kind}.{ext}`）。产出哪个后端记在 `subject_images.provider` 列（`comfyui` / `openrouter` / `dreamina` / 空=上传）。
- `web/static/style.css` 里 `label { display: flex }` 会盖掉浏览器默认的 `[hidden] { display: none }`，所以有 `[hidden] { display: none !important }` 这条兜底。新增按条件显隐的控件时，`<select>` 自身带 `hidden` 的话，JS 要同时清掉 select 和它外层 label 上的 `hidden`（`applyOpenRouterCapabilities` 就是这么做的）。
- `.env`（COMFYUI_HOST/PORT 等）不进 git；改 .env 后要重启 web 服务。
- 主体档案的锁定项中英文皆可：`style_lock` / `identity_lock` / `outfit_lock` 每条拼成一句 `Maintain ...` 进提示词——classic 模式**原样进提示词**（中文条目就是中文进图，Qwen-Image / OpenRouter 这类双语或 LLM 系模型没问题）；LLM 模式只作为 LLM 输入、由 LLM 重写成英文。目标后端要纯英文提示词时（SD 类老模型）建议仍写英文。导入/校验工具不得把非 ASCII 锁定项当错误。
- **`body_lock`（主体档案可选字段）**：体形锚，专门给换装场景用。LLM 改写有参考图时逐条编成 `keep the figure exactly as shown in <imageN>: …` 分句追加进【参考图分工锚定块】（继承「逐字采用、全批相同」硬规则）；`identity_lock` 保持权威不动，classic 模式不用这个字段。建议写英文短句、1–2 条为宜（写长了稀释动作段）。实测缺失时遮盖性服装会让整批胸部缩水（换装批的「身材没控制住」大多是这个）。Web 主体表单有「体型锁定」文本域。
- 主体档案的 Markdown 模板上限 24,000 字符（`charvar.llm_prompts._TEMPLATE_MAX_CHARS`），超出尾部被静默截掉；交付文档按 ≤ 10,000 字符写（见「预设存储」）。
- **LLM 改写的输出语言**（`GenerateRequest.llm_language`，网页「生成参数 → LLM 优化 → 提示词语言」）：`zh`=强制中文 / `en`=强制英文 / `any`=**不做强制性限制（缺省）**。措辞差异集中在 `charvar/llm_prompts.py` 的 `_LANG_PHRASES`（锚定句/视角规范/全身句/framing_guard/背景光照）。**`en` 分支的系统提示词是冻结文本，不得改写**——改 `_LANG_PHRASES["en"]` 或 `_prompt_string_rule` 时要用旧文件做字节级对比（有快照回归测试）。
  - 改写管线注入的材料**全是英文**（视角规范句、背景/光照池、参考图锚定句），所以 `zh` 不是"禁止英文"而是"把这些英文材料译写成中文再采用"；`any` 不写语言规定，多数仍出英文（跟随注入材料）。
  - **即梦字数**：英文改写实测 1656–2538 字符，**超即梦 text2image 上限 1592**（`dreamina_image.validate_prompt` 提交前就拒，白扣一次提交费）；中文改写实测 527–636 字符，稳过。推即梦时选 `zh`。
  - **带字表情包预设强制英文**（`text_policy=required_label`）：中文文案由程序按 display_text 原文追加，LLM 段出中文会被 sticker 禁字规则判为不合格（补调也救不回来）。此时选「中文」会被忽略并在 run 的 `warnings` 声明——`api.py` 的 warnings 是**合并**不是覆盖。
- Playground（`charvar/adhoc.py`）的红线：参考图槽位名从 mapping 读（`image`/`image2`/…），不许写死节点 id；ComfyUI 上传的 remote_name 必须带唯一前缀（`upload_image` 发 `overwrite: true`，同名会互相覆盖 → 队列里的旧 prompt 取到新图）；`--out` 指定的文件后缀与内容必须一致（不一致时转格式，别让后缀骗人）；adhoc 不读 `settings.image_sizes`（那是主体档案槽位的尺寸）；不引入随机源（seed 由调用方显式给）。
- Playground 的 run_id 直接拼进文件路径（`/adhoc/{run_id}/image` 直接 serve 磁盘文件）：`charvar.adhoc.run_dir()` 用自己的正则校验（`^adhoc_[A-Za-z0-9_.-]+$` 且不含 `..`），比 `assets.safe_run_id` 严 —— 后者放行 `..`。**别退回用 safe_run_id**。
- 上传的参考图按**实际图片格式**判后缀（用 PIL 读，不信文件名），落 `data/generated/adhoc/_refs/`；出图成功后自动清掉，失败时保留以便重试。
- **2K 必须用 Qwen-Image-2.1 官方像素表**（`charvar/exporters/comfyui.py` 的 `_QWEN_2K_SIZES`：1:1=2048×2048、4:3=2400×1792、3:4=1792×2400、3:2=2528×1696、2:3=1696×2528、16:9=2752×1536、9:16=1536×2752）。模型原生是**恒定 ~4.2MP 的 2K**（按面积定义，不是长边）——按长边归一算出的 2K 只有原生面积的 56%（16:9 才 2048×1152），模型被迫上采样 → 图糊。**别退回长边归一**。前端 `web/static/app.js` 的 `QWEN_2K_SIZES` 是同一张表，改一处要同步另一处，否则预览像素和实际不符。
- **档位只有 `1k` / `2k`，没有 4K 档**。官方明说远超 2K 训练尺寸会丢提示词遵循度；实测 4096×4096 耗时 75s（2K 只要 10s）且细节崩坏。`settings_service.ImageSize` 有 validator 把存量设置里的 "4k" 降级到 "1k"（别删 —— 存量 DB 里可能还有）。
- 2K 官方表**只给 ComfyUI 通路**（跑的是 Qwen-Image-2.1）。OpenRouter 通路（muse/grok/gpt-image）传 `use_model_native=False` 走通用长边归一 —— 那边的模型不认 Qwen 的尺寸表。

## 导入主体档案（外部 Markdown → 系统）

存储位置、双向同步（DB ↔ 仓库）、sha256 溯源指纹、审计闸门与人工改 yaml 的坑见 `docs/主体档案.md`。这里只记 agent 必须知道的规则。

1. 在 `presets/subjects/<id>.yaml` 写档案（id/名/锁定项/extra），随仓库走、可复现。模板另存同名 `<id>.md`。
2. 建档＋模板一起入库：`python3 -m charvar.cli subject add presets/subjects/<id>.yaml --overwrite`。
3. 模板也可单独维护：`PUT /subjects/<id>/template`，或网页「主体档案 → 从 .md 文件载入」。
4. 锁定项与模板全文逐字保留原文，不做改写或摘要。
5. 导入后跑 `python3 -m pytest -q`，并用 `/subjects/<id>/images/<kind>/suggest-prompt` 验一次 LLM 起草能跑通。
6. 交付包的槽位图用 `python3 -m charvar.cli subject image import <目录> --subject <id>` 装进运行时：落盘 + 生成 WebP 缩略图 + 建 `subject_images` 行（也可在 `subject add` 时加 `--images <目录>` 一次做完）。**手动把 PNG 拷进 `data/subjects/<id>/` 不会建槽位行**（网页仍显示空槽）；缩略图缺失会在读取时自动补，但槽位行只能由导入命令或网页上传产生。

**只有模板 .md、还没写档案时**：`charvar subject derive <template.md> --id <slug>` 从模板派生候选
（LLM 抽取 → 引用核对（zh 模式锁定项必须回指模板原文，防编造）→ 独立审计；默认只写 yaml 不入库，`--apply` 才入库，多版本模板必须 `--version <关键词>`）。
已入库配对的审计：`charvar subject verify <id>`（exit 0 通过 / 1 有问题单，`--json` 供 agent；**跑一次通过不算数**——每轮取样不同，要跑多次汇总去重）。
DB → 仓库对偶：`charvar subject export <id>` 快照成 `presets/subjects/<id>.yaml` + `<id>.md`。**DB 是运行时真源，仓库靠 export 保持镜像**——只改仓库文件不回灌，网页上看到的还是旧档案。

## 服装预设与贴字

字段参考见 `docs/贴字与服装预设.md`。这里只记 agent 必须知道的规则。

两者都是出图后的组合能力，服务于「视频配方」的合成流水线（出图批 → 贴字 → 本地合成 reel）。

- **服装预设** = `presets/outfits/*.yaml`（`charvar/outfit.py`）：每条 = 英文服装描述段 `text` + 中文 `label`（贴字/展示用）+ 可选 `caption_tags`（id → 英文 caption token，缺省 = id）。独立家族，**不进主预设下拉**（`list_presets` 只扫 `presets/actions/`）。
- **跨预设笛卡尔积**：出图请求带 `outfit_preset`（+ `outfit_order`：`outfit_first`/`pose_first`，缺省 outfit_first = 每件服装连播全部动作）。服装段**追加在动作段之后**——服装段必须短（白裙丝袜动作预设实测：前置长文本把动作稀释到 14%，动作不执行；这条红线对服装段同样成立，建议每条 ≤200 字符，先小批验证动作还成立）。变体 id = `{outfit_id}__{pose_id}`（pose_first 时反序），caption 在 pose 之后插 outfit token，meta 带 `outfit`/`outfit_label`。语义：`POST /generate`（带 outfit_preset）= `charvar gen --outfit … --outfit-order …`，网页生成页同源。变体顺序就是后续 reel 合成的播放顺序。
- **LLM 模式（mode=llm）下的服装是另一条通路，别退回合并文本的写法**。实测 LLM 会把服装段当成「参考图自带服装」整段丢弃（参考图自带白裙丝袜 + 服装预设两套指令互相打架）。三件事必须同时做，缺一件就复发：
  1. **动作段与服装段分列**：`outfit.combine` 把 `action_text`/`outfit_text` 分别留在 VariantSpec 上（`text` 仍是合并文本给 classic 用）；`collect_intent` 只把动作段塞进 intent 的 `text`，服装走独立字段 `outfit`（+`outfit_label`）。
  2. **锚定句在服装模式下换一套**（`REFERENCE_ROLE_CLAUSES_OUTFIT`）：参考图只提供身份/体型/画风，**不再**说「copy the complete outfit from <image1>」。
  3. **程序兜底**：改写完用 `outfit.outfit_covered`（内容词覆盖率 ≥50%）查这套衣服在不在提示词里，不在就用 `outfit.insert_outfit_sentence` 按原文补写（插在背景/光照句之前），并在 run 的 `warnings` 声明补了几条。

  服装段**任何语言模式下都逐字保留英文原文**（zh 分支的中文规则开了这个例外）——换装描述译写容易走样，程序兜底补写的也是英文原文。没有服装组合时，英文分支系统提示词**逐字不变**（有快照回归测试）。
- **贴字** = `presets/overlays/*.yaml`（`charvar/overlay.py`，PIL 确定性后处理，**不改原图**）：产物落 `<data>/generated/<run_id>/labeled/{index:06d}.png`。语义：`POST /runs/{id}/overlay` = `charvar overlay <run_id> --overlay <preset> [--text-from …] [--items 0,1]`。
- 字体解析顺序 = `presets/overlays/fonts/` 里的文件 > 系统候选（macOS PingFang / Hiragino、Linux Noto CJK、Windows 雅黑）；`font: ""` = 自动。跨机稳定就把字体文件放进 `fonts/`，**只放可再分发的开源字体（OFL 系安全）；花字/美术字体的版权要单独确认**。
- 与 **sticker（带字表情包）** 是两套机制：sticker 的字是提示词驱动（模型原生画字，`charvar/sticker_policy.py` 的 display_text/lettering）；贴字是确定性 PIL 后处理。两者共存不冲突；**sticker 预设拒绝服装组合**（语义冲突，400）。

## 视频配方

配方系统的 YAML 字段、步类型、素材声明详见 `docs/配方系统.md`。这里只记 agent 必须知道的规则。

- **配方定义** = `presets/recipes/<slug>.yaml` + `<slug>.md` 成对（`charvar/recipes.py`；文档 `doc:` 相对 recipes/ 目录，读不到不阻塞）。大类由末步推导：末步 `video_synth` = 合成配方，末步 `video_model` = 生成配方。YAML 严格模式：未知根/步字段显式报错。
- **线性步骤**：`images`（建 run，classic 缺省 / `mode: llm` 走 LLM 改写 / `push: comfyui` 自动提交+等待+同步成品 / `run_id: <既有 run>` 绑定不建批 / `fixed_background`+`fixed_lighting` 固定背景光照覆盖通配符池）→ `overlay`（贴字步，`input` 缺省最近一个）→ `video_synth`（本地合成本地 MP4；`use_labeled: true` 时同源 run 的贴字产物优先）。消耗步统一 gate：来源 run 成品没齐停在 waiting 并写原因，不算失败。字段级细节见 `docs/配方系统.md`。
- **检查点**：step.manual 缺省 images=False、overlay/video_synth=True。执行到 pending 的 manual 步停（status=waiting）；`--continue` / `POST /recipe-runs/{id}/continue` 批准当前 manual 步推进，之后的 manual 步再停；failed 步 continue 直接重试。
  **`--continue` 是「续跑已有运行」——首次必须不带它**（`charvar recipes run <名>` 建运行并停在首个检查点），否则报「没有可继续的运行」。
- **状态**：`data/generated/recipe_runs/<recipe_id>/manifest.json`（recipe_id 白名单 `^recipe_[A-Za-z0-9_.-]+$` 且无 `..`，同 adhoc 严格度），**不建 DB 表**、不落 runs 表；配方定义改版（步数不一致）时 continue 拒绝，要求重建。
- **入口同语义三处**：`charvar recipes list|run|status`（`--continue` 续最近一次）、`/recipes*` API、顶栏「配方」页签（`web/static/recipes.js`）。
- 示例配方：`presets/recipes/stockings_showcase.yaml`（自动推 ComfyUI，跑一次会真出 27 张图——**跑之前想清楚**）、`presets/recipes/model_clip_demo.yaml`（生成配方，heygen 480p $0.02/s；两步都缺省 manual，真跑要 OpenRouter key）。

### 生成步与参考视频

两类**生成步**：`adhoc_image`（单图，Playground 通路）与 `video_model`（OpenRouter 视频异步作业）。字段表见 `docs/配方系统.md`，这里只记规则：

- **`video_model` 步的异步语义**：提交（`adhoc_video.generate(wait=False)`）→ 轮询 `adhoc_video.refresh` 直到 ready/failed 或超出 `wait_s`（缺省 300s）。超时转 waiting，作业本身在 OpenRouter 继续，continue 续查**不重复提交**。作业 failed → 步 failed；continue **清掉死作业重新提交**（重新计费，失败原因在 outputs.previous_error…）。提交被拒（枚举/key 错）= PresetError → failed，continue 前先修配置。
- **`adhoc_image` 步的 retry 幂等**：产物在就复用，不重复生成。
- **参考视频不做图像预处理**（没有压缩逻辑，原样发），提交前 existence + 魔数双校验。暂存视频池 `data/generated/adhoc_video/_vrefs/` 与 `_refs` 图池分开，互不误删（24h 兜底）。
- 参考视频官方只收 mp4/mpeg/mov/webm（按 ftyp/EBML 魔数嗅探、原文件 base64 上限 48MB）；`input_references` 合计 ≤4 项；`last_frame_path` 要求模型声明 supported_frame_images 含 last_frame，否则 ParamError。
- **终态语义**：OpenRouter 官方 202 语义 cancelled/expired 也是终态——`openrouter_video._TERMINAL_FAIL`（wait_for_job）与 `adhoc_video.refresh` 都按 failed 收尾并记 error，不会无限轮询。
