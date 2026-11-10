# random_cat_naughty_video · 随机贱猫捣乱视频

五步的**生成配方**（末步 `video_model`），**脚本先行**（与 `random_cat_skit_video` 同架构）：
随机掷出**一只随机生成的猫**（外观池掷骰）× 一段**四足真猫捣乱剧本**（自建文本池）→
**先写视频脚本（人工过目，零成本）** → LLM 按已定稿脚本设计场景 → 按场景+起手出**猫首帧**
（人工过目）→ 直出 8 秒视频。与两个兄弟配方的分工：`random_cat_video`（夸张动作，
`amplitude: large`）、`random_cat_skit_video`（双腿拟人小品，`amplitude: skit`）、
本配方是**真猫捣乱**（`amplitude: naughty`）——观众最熟的「猫咪捣乱」梗：推杯、扒纸、
翻桶、藏赃，笑点在**盯上→得手→装无辜**的表情反差，不是动作幅度也不是拟人身手。

| 步 | 干什么 | manual |
|---|---|---|
| `pick` 随机选库 | **只抽动作**：`studio` 库 `捣乱猫现场` 分类（`allow_text: true`，10 条捣乱池）掷 1 条；没有 face/costume 目标 | false |
| `video_script` 视频脚本 | **无图写稿**（无 `frame_from`）：视觉 LLM 只吃动作全文（含三拍尾段）+ `amplitude: naughty` 恶作剧三拍规则 + 外观自掷（**掷骰 key 与首帧步同一把 = 同一只猫**）；类型池掷骰喜剧标签。**0–8s 分秒脚本在这里定稿，人工过目/改稿** | **true**（门停在场景步） |
| `scene_design` 场景设计 | LLM 按动作 + **`script_from: 视频脚本`（已定稿脚本为最高依据）** + hint 逆推规则出场景/光照文本 | false |
| `combo_image` 首帧图 | 猫版无参考图首帧：外观掷骰（同 key 复现脚本那只猫）+ `action_anchor: setup`（**只锚起手盯上段**，三拍尾段不进图）+ 场景 + 双锁 → ComfyUI 纯文生图 2k 9:16；**首帧必须装得下脚本的 0 秒** | **true** |
| `video_model` 成片 | 脚本 + 首帧 → heygen-video-1，8s 768p（实测 ≈$0.018/s，一条约 $0.14） | **true** |

## 捣乱池结构（10 条真猫三拍）

每条 `action_text` = 起手盯上定格（首帧锚：舞台+眯眼蓄势+机位）+ 三拍剧本句
（「下一拍将开始捣乱…得手…装无辜定格」）。全部是**居家捣乱舞台**（客厅茶几/厨房
台面/玄关鞋柜/窗台花架/书桌）：推杯下桌/掀饭碗/卷纸巾/挖花盆/翻垃圾桶/偷袜藏赃/
拨窗帘/扫桌沿/纸团排球/藏玩具。脚本规则要求被捣乱物品**软着陆**（掉进地毯/软垫/纸堆，
不碎不飞溅）——掉落是笑点、碎裂不是。

## 机制要点（与贱猫小品同源）

- 外观掷骰 key 固定（`{run_id}:appearance`）：脚本步与首帧步各滚同一把骰子，同一只猫；
  `video_script.appearance` 记进步 outputs。
- `action_anchor: setup`：首帧只锚首个「下一拍」之前的**盯上蓄势段**——得手的拍/推
  动作与翻倒后的物品不进首帧（首帧里物品还是完整的）。
- `crowd` 不设（全居家场景走缺省 solo）：捣乱的观众就是镜头（镜头=主人视角），
  装无辜是对镜头装的。
- `safety_lock` 捣乱版：软着陆、不碎裂、危险物品不出画；「掉落」写笑点不写破坏。

## 运行

```bash
python3 -m charvar.cli recipes run random_cat_naughty_video            # 掷动作 + 写脚本，停在「场景设计」——先过目脚本（零成本）
python3 -m charvar.cli recipes run random_cat_naughty_video --continue # 按脚本设计场景 + 出首帧，停在「成片」——过目首帧
python3 -m charvar.cli recipes run random_cat_naughty_video --continue # 提交视频作业并轮询（真金白银）
```

停点语义 = 过目**上一步的成品**（gate 停在 manual 步之前）：第一次停点看的是脚本，
第二次看首帧，最后放行成片。网页顶栏「配方」页 → 「random_cat_naughty_video」→ 逐步「继续」。

## 换池 / 调流程

- 更换外观池：`combo_image.appearances:`；改喜剧标签：`video_script.types:`。
- 质感/安全条款：`style_lock:` / `safety_lock:` 两处（video_script 与 combo_image）同改。
- 视频模型：`video_model.model:`（换即梦 Seedance 或 OpenRouter 目录其他模型；注意该模型的
  时长/分辨率枚举）。

## 已知风险

- 捣乱的「得手」一拍发生得快（拍/推一次到位），heygen 对短促动作的节奏还原有波动；
  效果不满意重掷即换一版（重计费）。
- 被捣乱物品的位置连续性（桌沿→地毯）依赖模型对软着陆的还原；首帧或成片里物品
  轨迹不对就重掷（首帧重掷免费，成片重掷重计费）。
- 表情反差（盯上的坏主意→装无辜）是本配方的核心卖点，依赖模型对猫脸微表情的还原。
