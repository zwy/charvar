# presets/ 存储规范

`presets/` 是**代码外的数据目录**：程序只按「名」查找预设，文件路径只是存储布局。
本文是这套布局的当前态契约——改布局前先读这里。

## 一条铁律：预设名是稳定标识，路径不是

- 预设的 `name:`（yaml 根字段）就是它的唯一标识。**改名 = 换标识**，会让引用它的
  配方、旧 run 记录失效；**移动文件（改路径）零影响**。
- 一切引用都写名，不写路径：
  - 配方 `presets: [white_skirt_stockings_poses]`
  - 历史 run 的 `preset` 列存的是 yaml 的 `name:`（`charvar/storage.py`）
  - 前端 `/presets` 列表按名取详情
- 因此：**先想好名字，再想放哪**。名字定了就别动，目录可以随时重排。

查找规则（`charvar/preset_loader.py`）：

| 名字形态 | 解析方式 |
|---|---|
| 不含 `/`（`q21_default_30`） | 在 `actions/` → 顶层 依次找，取第一个存在的 |
| 含 `/`（`wildcards/xxx.yaml`） | 按字面相对 presets 根目录解析 |

## 目录约定

| 目录 | 放什么 | 加载入口 |
|---|---|---|
| `actions/` | **动作/表情预设**（主预设下拉的全部来源） | `preset_loader.list_presets()` |
| `wildcards/` | 背景 / 光照通配符池（`items:` 列表） | 预设的 `backgrounds_ref:` / `lighting_ref:` |
| `subjects/` | 主体档案样例（`<id>.yaml`）+ 模板导出（`<id>.md`） | `charvar subject add/export` |
| `costumes/` | 服饰库 DB 镜像（`<source>/<item>.yaml`，DB 是运行时真源） | `charvar costume export/import-studio` |
| `hairstyles/` | 发型预设（独立家族，**不进主预设下拉**） | `charvar/hairstyle.py` |
| `overlays/` | 贴字样式预设（PIL 后处理，不改原图） | `charvar/overlay.py` |
| `overlays/fonts/` | 贴字字体文件（可选，只放可再分发的开源字体） | 字体解析的第一优先位 |
| `video/` | 视频合成预设 + `music/` 音频 | `charvar/video.py` |
| `recipes/` | 配方定义（`<slug>.yaml` + `<slug>.md` 成对） | `charvar/recipes.py` |
| `recipes/assets/` | 配方素材（图片/音频等） | 配方步引用 |
| `workflows/` | ComfyUI mapping 示例 JSON（真 workflow 在 `data/workflows/`） | 文档参考 |

**顶层不放动作预设**——`actions/` 是动作预设的唯一归属目录。顶层只允许 `README.md`。

## 命名规则

| 类别 | 规则 | 例子 |
|---|---|---|
| 动作预设 | 小写 snake_case，`<用途>` | `q21_default_30`、`yoga_complete`、`three_pose_pack` |
| 通配符池 | `<用途>_v<版本>.yaml`（与动作预设对称） | `backgrounds_v1.yaml`、`lighting_v2.yaml` |
| 主体档案 | `<id>.yaml` + `<id>.md`，id 全仓库唯一 | `red_bob_glasses.yaml` |
| 服饰 / 发型 / 贴字 / 视频 | `<用途>.yaml` | `stockings_8.yaml`、`label_topleft_shadow.yaml` |
| 配方 | `<slug>.yaml` + `<slug>.md` 成对 | `stockings_showcase.yaml` |

两条容易踩的：

1. **条目数不进文件名**。`count` 是派生属性（`list_presets()` 现算），写死进文件名
   必然和实际条数漂移。
2. **版本不重复编码**。内容版本在 `version:` 字段；文件名里的 `_v1` 标记**必须与
   `version:` 一致**（有测试钉住）。文件名带版本号 = 这套预设会演进；不带 = 冻结。

条目数后缀（`_30`）只保留在**冻结**预设上——它们的条数不会变，留在文件名里是准确信息
（`q21_default_30` / `q21_legacy_30` / `q21_v2_30`）。

## 各目录 schema

### 动作预设（`actions/*.yaml`）

两种 `type`：

- `type: list` —— 逐条 `variants` 写死（`id` / `text` / `framing` / `angle` /
  `expression` / `pose` / `tags`）。缺省 framing/angle/expression 由 id 解析：
  `full_l45` → full + l45；`expr_smile` → head + front + smile。
- `type: matrix` —— `dimensions` 定义维度文案，`rules` 按顺序做笛卡尔积
  （`"*"` = 该维度全量），`exclude` 按维度取值排除。

根字段：

| 字段 | 含义 |
|---|---|
| `name` | 预设标识，必须 == 文件名 stem |
| `type` | `list` / `matrix` |
| `version` | 内容版本（整数，默认 1） |
| `master_template` / `suffix_template` | 主提示词模板 / 追加段 |
| `backgrounds` / `lighting` | 内联池（与 `_ref` 二选一，内联优先） |
| `backgrounds_ref` / `lighting_ref` | 池文件路径，**以 presets 根目录为基准**（`wildcards/backgrounds_v1.yaml`）；兼容旧的「相对预设文件目录」写法 |
| `caption_tags` | caption 标签映射覆盖 |
| `caption_background_suffix` | caption 背景是否拼 ` background` 后缀 |
| `reference_order` | 推荐参考图顺序（如 `[front, headshot]`），供前端默认勾选 |
| `subject` | 归属主体 id（省略 = 通用预设）；必须指向 `subjects/<id>.yaml` |
| `schema_version` | 配置格式版本（1 或 2），与 `version` 不是一回事 |
| `purpose` / `text_policy` / `composition_policy` / `lettering` | schema 2（带字表情包）字段 |
| `preferred_aspect_ratio` | 声明出图画幅（**必须加引号**：`'4:3'`，否则 YAML 读成 sexagesimal 数字） |

### 通配符池（`wildcards/*.yaml`）

只有 `items: [..]`（字符串列表）。预设用 `backgrounds_ref:` / `lighting_ref:` 引用，
路径以 **presets 根目录**为基准。

### 主体档案（`subjects/<id>.yaml`）

`id` / `name` / `subject_noun` / `trigger_word` / `reference_tag` /
`style_lock` / `identity_lock` / `outfit_lock` / `extra`。
锁定项中英文皆可（每条拼成一句 `Maintain ...`）。

`<id>.md` 是同 id 的**模板导出**（`charvar subject export` 逐字快照），可选。

### 服饰家族（costume 双表，`costumes/<source>/` 镜像）

服饰家族住在服饰库 DB（`costume_items` studio 条目 + `costume_prompts` 文本层；
text = 文本层优先、回退 keywords，**语言不限**、整段逐字进提示词；`label` =
中文展示名；可选 `caption_tags` 与 `subject_policy`）。`charvar costume
export <ref>` 快照镜像、`import-studio` 重建；组合顺序由 `outfit_order` 决定；
**服饰段在最终提示词里排第一句**（与 `outfit_order` 无关）。
详见 `docs/服饰库.md` 与 `docs/贴字与服饰预设.md`。

### 发型预设（`hairstyles/*.yaml`）

`type: hairstyle`，`items: [{id, label, text}]`，同服饰预设结构：`text` = 英文发型
描述段（**只写头发结构**：长度/刘海/分缝/层次/卷度，不写发色——发色继承主体档案
或参考图），`label` = 中文展示名，`caption_tags`（可选）= caption token 映射。
层级固定 动作→发型→服饰（发型层先并、服饰层后并），无发型单独的顺序开关。
约定：identity_lock 里发型只写发色、不写结构；换发型批优先 LLM 模式。

### 贴字预设（`overlays/*.yaml`）

`type: overlay`：`position` / `margin_x` / `margin_y` / `font`（`""` = 自动）/
`size` / `color` / `stroke` / `stroke_color` / `plate` / `opacity` / `rotate` /
`max_width` / `align` / `line_gap`。只定义样式，**文本由调用方传**。

### 视频预设（`video/*.yaml`）

`type: video`：`aspect` / `resolution` / `fps` / `seconds_per_image` /
`include_contact_sheet` / `transition` / `fit` / `background` / `order_mode` /
`group_order` / `direction` / `music`（`music/` 下的文件名）/ `music_volume` /
`music_fade`。

### 配方（`recipes/<slug>.yaml`）

`type: recipe`，`doc:` 指向同目录 `<slug>.md`（**YAML 严格模式：未知根/步字段显式报错**）。
步类型：`images` / `overlay` / `video_synth` / `adhoc_image` / `video_model`。
详见 `docs/配方系统.md`。

## 新增一个动作预设

1. 放进 `presets/actions/`，文件名小写 snake_case。
2. `name:` 写成和文件名 stem **完全一样**（`tests/test_presets_structure.py` 会查）。
3. 主体专属的加 `subject: <id>`，且 `subjects/<id>.yaml` 必须存在。
4. 引用通配符池用**相对 presets 根**的路径：`backgrounds_ref: wildcards/backgrounds_v1.yaml`。
5. `version:` 写内容版本；文件名想带 `_vN` 就和它保持一致。
6. 跑 `python3 -m pytest -q`——结构校验 + 逐字基准都在测试里。

## 不变量（有测试钉住）

- 动作预设 `name:` == 文件名 stem；预设名全仓库唯一。
- `subject:` 指向存在的 `subjects/<id>.yaml`。
- 配方 `doc:` 指向存在的同名 `.md`。
- `backgrounds_ref:` / `lighting_ref:` 指向存在的池文件。
- `presets/` 顶层没有动作预设、没有 `.bak` / `~` 之类的垃圾文件。
- **`q21_default_30` / `q21_legacy_30` / `q21_v2_30` 的 30 条 variants 文本逐字冻结**
  ——`tests/test_presets.py` 用 sha256 基准锁定，改文本会让测试红。
  （`expressions` / `yoga_basic` / `yoga_complete` / `chat_replies` 也有 sha256 基准，同理。）
