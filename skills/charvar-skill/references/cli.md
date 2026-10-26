# charvar image / video 命令参考

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

## charvar image dreamina

用即梦（Dreamina）官方 `dreamina` CLI 出图。前置：本机装好官方 CLI（`curl -s
https://jimeng.jianying.com/cli | bash`）并完成 `dreamina login`（登录态存在
`~/.dreamina_cli/`，`dreamina user_credit` 能返回积分即就绪；**授权要让用户手动
在浏览器完成**，agent 代登录可能拿到错误 URL）。

带参考图自动走 `image2image`，不带走 `text2image`（即梦的模型都有编辑能力，
不区分 t2i/i2i 配置组）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--prompt` | str | — | 图片提示词 |
| `--prompt-file` | str | — | 提示词文件路径；`-` 表示读 stdin |
| `--ref` | list | — | 参考图路径，可重复，最多 4 张 |
| `--aspect` | str | `3:4` | 同上（即梦原生 8 档比例，与该列表一致） |
| `--resolution` | str | 空 | `2k` `4k`（5.0Pro 另有 `1.5k`）；空 = 设置页/env 默认 2k |
| `--model` | str | — | 即梦 model_version（`3.0` `3.1` `4.0` `4.1` `4.5` `4.6` `4.7` `5.0` `5.0Pro`）；默认 5.0 |
| `--out` / `-o` | path | — | 另外落一份到这个路径 |
| `--json` | flag | — | 结果以 JSON 输出 |

**没有 `--seed`**：即梦不暴露 seed，同提示词每次出图都不同；`prompt_id` 字段
存的是即梦 submit_id（`dreamina query_result --submit_id=<id>` 可追溯）。

- 提示词硬上限：text2image 约 1600 字符、image2image 约 2000 字符（超限上游
  硬失败）；charvar 会在提交前报错，别浪费积分。
- model_version 与分辨率档配对（CLI 1.4.20）：3.0/3.1 → 1k/2k（且只能
  text2image）；4.0–5.0 → 2k/4k；5.0Pro → 1.5k/2k/4k。配错在提交前报错。
- 每张消耗即梦积分/会员权益（仅限会员可用），出图前先确认用户额度。
- 生成是异步的：submit → 轮询 → 下载，单张约 40–90 秒；等待总预算默认 300s
  （`DREAMINA_IMAGE_TIMEOUT` 可调）。

## charvar image models

列 OpenRouter 可用的生图模型（`id<TAB>名称`）。加 `--json` 输出 JSON。
读本地缓存 `data/openrouter/api-response.json`，无缓存时才联网拉。

## charvar video openrouter

用 OpenRouter 出一段视频。**异步作业**：提交成功就按秒计费，所以提交前先想清楚
时长、分辨率和画幅。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--prompt` | str | — | 视频提示词 |
| `--prompt-file` | str | — | 提示词文件路径；`-` 表示读 stdin |
| `--first-frame` | path | — | 首帧图（1 张）。图生视频，**主体一致性靠它** |
| `--ref` | list | — | 参考图路径，可重复，最多 4 张（`input_references`） |
| `--video-ref` | list | — | 参考视频路径，可重复，≤2 条 mp4/mov/webm |
| `--aspect` | str | `9:16` | 画幅；合法值随所选模型变（heygen：21:9/16:9/4:3/1:1/3:4/9:16） |
| `--duration` | int | 不发送 | 时长秒数；合法值随模型变（heygen 支持 5–15） |
| `--resolution` | str | 不发送 | 分辨率档（480p/768p/2K…）；合法值随模型变 |
| `--seed` | int | `0` | 采样种子（0 = 固定复现） |
| `--model` | str | 设置页/`OP_VIDEO_MODEL`/兜底 `heygen/heygen-video-1` | 模型 slug |
| `--timeout` | float | `900` | 轮询超时秒数；超时作业**仍在跑**（网页可续查） |
| `--poll-interval` | float | `5` | 轮询间隔秒数 |
| `--out` / `-o` | path | — | 另外落一份到这个路径 |
| `--json` | flag | — | 结果以 JSON 输出到终端 |

**首帧图与参考图不是一回事。**

- `--first-frame` = `frame_images`，**图生视频**。主体一致性能保住，**用单人全身正面照**。
  别用 character_sheet（四视图拼图会出多人）。
- `--ref` = `input_references`（≤4 张），只做风格／内容参考。
- 两者同给时官方语义**首帧优先**。
- `--video-ref` 只有 Seedance gen2+ 系认，其它 provider 忽略。

时长／分辨率／画幅**超枚举会在提交前被拒**（不扣费），所以提交前先列一次目录：

```bash
~/.local/bin/charvar video models
~/.local/bin/charvar video models --json     # 原始目录
~/.local/bin/charvar video models --refresh   # 忽略本地缓存重拉
```

输出里每个模型带**时长／分辨率／画幅枚举**和**按秒价目**（`480p $0.0200/s` 这类）。
价目来自 `pricing_skus`，各 provider 键名不同（heygen 美元/秒、grok 美分/秒），
charvar 已统一换算成美元/秒。

## 产物落盘（生图）

不给 `--out` 时：

```
data/generated/adhoc/{时间戳}_{短id}/
├── original/000000.png    ← 产物
├── thumbs/000000.webp     ← 缩略图
├── _ref_cache/            ← 参考图预处理缓存（长边 1536 / JPEG q88）
└── manifest.json          ← 完整请求与结果，便于复现
```

Playground **不落 runs 表** —— 不进生成历史、不进图片墙。

## 产物落盘（视频）

不给 `--out` 时：

```
data/generated/adhoc_video/{id}/
├── original/000000.mp4    ← 成片
├── thumbs/000000.jpg      ← 海报帧
└── manifest.json          ← 完整请求 + 作业 id，便于复现与续查
```

视频同样**不落 runs 表**。暂存参考图在 `_refs/`，参考视频在 `_vrefs/`。

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
| `OP_VIDEO_MODEL` | 视频默认模型（默认 `heygen/heygen-video-1`；设置页 DB 值优先） |
| `OP_VIDEO_TIMEOUT` | 视频单次提交轮询超时秒数 |
| `DREAMINA_IMAGE_MODEL` | 即梦 model_version 默认（默认 5.0） |
| `DREAMINA_IMAGE_RESOLUTION` | 即梦分辨率档默认（2K/4K，默认 2K；小写大写都收） |
| `DREAMINA_IMAGE_TIMEOUT` | 即梦单次生图总等待预算秒数（默认 300） |
| `DREAMINA_POLL` | 即梦提交后 CLI 内首轮等待秒数（默认 60） |
| `DREAMINA_CLI` | 覆盖 dreamina 可执行文件路径（默认 PATH 或 `~/.local/bin/dreamina`） |
| `CHARVAR_DATA_DIR` | 数据目录（默认仓库 `data/`） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | subject derive / verify 的 LLM（也可在网页设置页配置） |

## charvar subject add〈YAML 档案 → 入库（档案＋模板）〉

把仓库里的主体档案导入 DB。**档案与通用提示词模板是两张表**（`subjects` / `subject_templates`），
本命令两张都写：

```bash
~/.local/bin/charvar subject add presets/subjects/<slug>.yaml --overwrite
# 模板来源：--template <路径>，缺省自动取档案同目录的同名 <slug>.md
# 找不到模板 → 告警，只导档案（DB 里原有模板保持不变，可能已过期）
# 档案头里的 sha256 指纹与模板实际内容不符 → 告警（溯源链断了）
# --images <目录>：同时把交付包里的槽位图装进运行时（落盘 + 缩略图 + 建槽位行）
# --json：{"subject_id","name","template_path","template_chars","template_updated","fingerprint_mismatch","images"}
```

同 id 已存在要 `--overwrite`，否则报错退出 1。

**DB 是运行时真源**：只改 `presets/subjects/` 下的文件而不跑这条命令，网页与出图仍读 DB 旧档案。

## charvar subject image import〈交付包槽位图 → 运行时〉

把交付包目录（`final/<id>/`）里的槽位图装进 `data/subjects/<id>/`：

```bash
~/.local/bin/charvar subject image import <目录> --subject <id>
# 目录导入缺省用目录名当主体 id（final/<id>/ → <id>）
# 单文件：--kind <kind> 指定槽位（character_sheet / headshot / front / side / behind）
# --json：{"subject_id","imported":[{"kind","source_file","file_path","thumb"}],"skipped":[...]}
```

- 按文件名认槽位，落盘 `data/subjects/<id>/<kind>.{ext}` + 生成 `thumbs/<kind>.webp` + 写
  `subject_images` 行（status=ready、source=uploaded）。
- 非槽位图（如模板 `.md`）跳过，在结果里列出。
- 装的是**同一张图**时保留原来的 `source`/`provider`/`prompt_id`；换成不同的图才回到
  `source=uploaded`、`provider` 清空。
- 手动拷 PNG 进 `data/subjects/<id>/` **不会建槽位行**（网页仍显示空槽）；缩略图缺失会在
  读取时自动补，但槽位行只能由本命令或网页上传产生。

## charvar subject prompt〈起草 5 槽位图片提示词〉

网页「主体档案图片 · AI 起草」的同一条链路（网页即时落库，CLI 缺省不落库）。
LLM 一次起草一个槽位的图片提示词：character_sheet / headshot / front / side / behind。
**没有主体档案、没有模板也能用** —— `--material` 给一段自定义参考资料
（角色描述、character_sheet 的文生图提示词、任何资料）：

```bash
# 单槽：stdout 只放提示词本身，可管道接 charvar image
~/.local/bin/charvar subject prompt front --material "一位银发巫女少女……"
# 5 槽全起草（5 次独立 LLM 调用）
~/.local/bin/charvar subject prompt --all --material-file 资料.md --json
```

| 参数 | 说明 |
|---|---|
| `<kind>` / `--all` | 槽位 5 选 1 或全拿；都缺报错，两者同给报错 |
| `--material` / `--material-file` | 自定义参考资料；`--material-file -` 读 stdin；两者二选一 |
| `--subject` | 可选：YAML 路径或已入库 id（给了在 LLM 输入里拼【角色档案】块，锁项加权） |
| `--template` | 可选：模板 .md；缺省链：显式 > 档案同目录同名 `<id>.md` > DB 模板 |
| `--lang` | 缺省 `any` = 不写语言规定（输出跟随材料语言）；`en` 强制英文（SD 类老模型）/ `zh` 强制中文 |
| `--apply` | 起草完写回 DB 槽位草稿（只写 prompt 不动状态机）。**需主体已入库；缺省不碰 DB** |
| `--out` / `--json` | 单槽=写文件；`--all` 时 `--out` 要目录（落 `<kind>.txt`）。`--json` 下 stdout 只有 JSON |

- `--json` 字段：单槽 `{"kind","prompt","db_saved","language","subject_id"(有 subject 时)}`；
  `--all` 为 `{"language","items":[{...},...],"subject_id"(同上)}`。材料三选至少一项
  （material / subject / template），全缺报错。
- 依赖 LLM 配置（同 `subject derive`，见「相关环境变量」的 `LLM_*` 行）。
- **4 个编辑槽是编辑指令**：草稿里的 `<image1>` 指设定图参考图，生成时必须带，否则标签悬空：
  `prompt front ... | charvar image comfyui --prompt-file - --ref <设定图.png> --out front.png`。
- **即梦长度**：草稿常超 text2image 1592 / image2image 1992 字符，推即梦先压缩过线。

## charvar subject derive〈MD 模板 → 档案〉

手里只有「角色全场景通用提示词模板」.md 时，用它派生主体档案 YAML
（LLM 抽取 + 引用核对 + 独立审计；锁定项必须能回指模板原文，防编造）：

```bash
~/.local/bin/charvar subject derive template.md --id yoga_teacher --json
# 默认只写 presets/subjects/<id>.yaml（带出处注释头），不碰 DB；
# 加 --apply 才入库（**档案与模板全文两张表都写**）；同 id 已存在要 --overwrite；多版本模板要 --version <关键词>。
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
