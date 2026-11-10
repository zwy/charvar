# random_cat_video · 随机荒诞猫视频

五步的**生成配方**（末步 `video_model`）：随机掷出**一只随机生成的猫**（外观池掷骰）
× 一个**猫荒诞动作**（自建文本池）→ 无参考图出一张猫首帧 → 直出一段 8 秒搞笑猫咪视频。
**针对池而不是某条**——每跑一次换一批（动作换、猫也换）；YAML 里不写死任何一条动作/外观。

| 步 | 干什么 | manual |
|---|---|---|
| `pick` 随机选库 | **只抽动作**：`studio` 库 `荒诞猫挑战` 分类的文本动作（`allow_text: true`，22 条混合池：拟人社 × 自然行为夸张）掷 1 条；没有 face/costume 目标——猫外观不在库里 | false |
| `scene_design` 场景设计 | LLM 按动作描述+动作名出场景/光照文本；`hint:` 钉方向——猫日常可及的真实室内/户外场地（客厅/窗边/厨房台面/社区广场），开阔无遮挡 | false |
| `combo_image` 首帧图 | **猫版无参考图首帧**（`subject_kind: cat`）：`appearances:` 外观池**掷骰出一只随机猫**（12 种毛色体型，掷骰 key 固定 = 首帧与脚本同一只）+ 猫动作文本 = 全部锚（`build_cat_prompt`，**没有任何 <imageN> 槽位**，ComfyUI 纯文生图 2k 9:16）；`framing_extra` 猫全身含尾完整入镜 | **true** |
| `video_script` 视频脚本 | 看图脚本（`frame_from: 首帧图`）+ 大幅档 + `subject_kind: cat`（**猫版措辞**：「全程同一只猫」「不出现人类」等按真实首帧续编）；类型池掷骰喜剧标签 | false |
| `video_model` 成片 | 脚本 + 首帧 → OpenRouter 视频异步作业（`heygen/heygen-video-1`，8s 768p；实测 ≈$0.018/s，一条约 $0.14） | **true** |

## 运行

```bash
python3 -m charvar.cli recipes run random_cat_video            # 选动作 + 场景，停在「首帧图」
python3 -m charvar.cli recipes run random_cat_video --continue # 出猫首帧 + 写脚本，停在「成片」
python3 -m charvar.cli recipes run random_cat_video --continue # 提交视频作业并轮询（真金白银）
```

或顶栏「配方」页 → 「random_cat_video」→ 逐步「继续」。

## 猫主角机制（没有猫库怎么随机）

- **外观 = 文本池掷骰**：`combo_image.appearances:` 就是猫外观池（品种+体型+毛色+眼色，
  每条 20–30 字，**不写性格**——性格由动作带）。`wildcard.rng_for(seed, "{run_id}:appearance")`
  掷一条并记进步 outputs；脚本步 `subject_kind: cat` 从首帧步 outputs **继承同一只猫**
  （brief 里写【主角·外观（与首帧是同一只猫）】段）。加猫 = 改 YAML 池，零库零状态。
- **一致性只在一条视频内成立**：每跑换一只猫（与三库配方「每跑换批」同语义）；
  要跨视频同一只猫（IP 猫系列）是后续方向——先出猫定妆图存档、后续步骤拿它当参考图。
- **首帧与动作全部文本锚**（`pose_prompt.build_cat_prompt`）：外观段 + 「让主角猫做出
  下面的动作」+ 构图/场景/禁令；无参考图 = ComfyUI 纯文生图（Playground 同通路）。

## 安全层（防封）

动物内容的平台雷区是**伤害/猎奇**，条款针对这个（`safety_lock` 两头铆，同荒诞人版机制）：

1. **动作白名单**：`presets/poses/studio/` 的 `cat_*` 条目人工准入；每条起势写明身体
   事实（尾/耳状态），不写痛苦与受惊。
2. **safety_lock 猫版**：毛发完整、无受伤痕迹、玩闹表演语境、猫科敏捷落地稳当——
   铆进首帧提示词与脚本 brief、「避免出现」节。
3. **发布人工把关**；标题用「挑战/运动会」语境包装。

## 动作池结构（22 条混合）

- 拟人社（11）：后腿站起开步走 / 后腿摇摆舞 / 疯狂踩奶 / 敲碗催饭 / 键盘滑字 / 琴键踩奏 /
  扫帚当马 / 顶托盘滑步 / 对镜哈气被弹开 / 跑步机狂奔 / 扫地机巡游
- 自然行为夸张（11）：追尾转圈 / 之字飞奔 / 飞扑纸箱 / 连扑空气猎物 / 扭屁股蓄力起跳 /
  猫式蛙跳 / 扑追空气红点 / 疯狂甩毛 / 侧滚三圈翻肚皮 / 炸毛横跳表演 / 猫爬架旋转下扑

带器械的条目（跑步机/扫地机/电子琴/键盘/扫帚/托盘/纸箱/猫爬架/镜子）在起势文本里
点明道具，场景步会带进画面。新条目照现有写法（一只猫 + 身体事实 + 机位事实 + 蓄势句），
写好灌库：`python3 -m charvar.cli pose import-studio`。

## 换池 / 调流程

- 更换外观池：`combo_image.appearances:`；改喜剧标签：`video_script.types:`。
- 质感/安全条款：`style_lock:` / `safety_lock:` 两处（combo_image 与 video_script）同改。
- 视频模型：`video_model.model:`（动物表现不满意 → 换即梦 Seedance 或 OpenRouter 目录其他模型；
  换了模型注意该模型的时长/分辨率枚举）。
- 关检查点：对应步 `manual: false`（首帧崩了也会烧视频费，斟酌）。

## 已知风险

- **heygen 对猫主体的表现力是首期最大未知**：先单条验证；不行换模型（`model:` 一行）。
- 首帧是纯文生图（无参考图）：猫外观由外观描述控制，偶尔品种特征会被弱化——不满意
  就是首帧检查点重掷（删运行重建）。
- 道具类动作（跑步机/扫地机器人等）依赖场景步与生图模型把道具画对；首帧不对就重掷或改该条目措辞。
