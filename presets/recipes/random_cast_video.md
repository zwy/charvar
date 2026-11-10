# random_cast_video · 随机三库组合视频

五步的**生成配方**（末步 `video_model`）：从**三个库**随机挑一套组合出一张首帧图，再直出一段 8 秒视频。
**针对库而不是针对某条**——每跑一次运行换一批选角（见下「随机语义」）；YAML 里不写死任何一条脸/动作/服饰。

| 步 | 干什么 | manual |
|---|---|---|
| `pick` 随机选库 | 人脸三库（face-db-01 / 02-cn / 03-cn 的 face 正面像）选 1 条 + **深度库全身单人动作**（pose-db-02，`全身1人`两条线，正面视角，`single: true` 排双人）选 1 个 + 服饰库选 1 条（实写/自建，有图条目）；选中动作没有「动作描述」时按 **A（另一库同动作复制，twin_copy）→ B（LLM 看深度图起稿，ai_draft 落库）** 补齐 | false |
| `scene_design` 场景设计 | LLM 按 动作描述+服饰要点 出一段场景/光照文本（替换固定背景句）；`hint:` 钉方向——与服饰/动作气质相符的有信息量真实场景（街头/咖啡馆/海边/和室…），不进纯摄影棚。没有场景库 —— 文本现配、随 manifest 记档 | false |
| `combo_image` 首帧图 | 三库组合流出图（同 `charvar pose prompt <动作> --face <脸> --costume <服饰>`）：人脸=身份锚、动作=姿势锚、服饰=换装锚；ComfyUI 2k 9:16（1536×2752，尺寸注入要求设置页编辑 workflow 用显式画布版——`*_edit_easy_api`；latent 跟参考图的 `*_edit_true` 分支不接受注入，成图跟人脸图尺寸）；`style_lock` 铆真人实拍质感 | **true** |
| `video_script` 视频脚本 | **看图脚本**（`frame_from: 首帧图`）：附着真实首帧交给视觉 LLM，从图中实际姿态接续编排；LLM 按 动作+服饰+场景 写 8 秒脚本（结构同 dance 系列配方）；片段类型从池子里掷骰（跳舞/原地律动/慢速转身/摆拍/走向镜头/伸展/回眸/拨头发）；`style_lock` 同一句两头铆（brief 的【全局质感】段） | false |
| `video_model` 成片 | 脚本当 prompt + 首帧当 frame → OpenRouter 视频异步作业（`model: heygen/heygen-video-1` 写死在步里，不跟设置页默认链；本配方 **8s 768p**——480p 实测出真人偏软；实测价 ≈$0.018/s，heygen 实收恰为目录价一半） | **true** |

## 运行

```bash
charvar recipes run random_cast_video            # 随机选库 + 场景设计，停在「首帧图」检查点
charvar recipes run random_cast_video --continue # 出首帧 + 写脚本，停在「成片」检查点
charvar recipes run random_cast_video --continue # 提交视频作业并轮询（真金白银）
```

或顶栏「配方」页 → 「random_cast_video」→ 开始运行 → 弹层里逐步「继续」（每个检查点都能先看产物再放行）。

## 随机语义

- 掷骰子种子 = 运行种子 + `recipe_run_id`：**每次运行换一批**；同一运行内 continue/重试固定同一批。
- 想复现某批：记下运行的 seed 与 run_id；`charvar recipes run random_cast_video --seed <n>` 换种子重掷。
- 动作池锁**深度库全身单人**（`pose-db-02` 的 `全身1人` 两条线；`lines:` 可调，`上半身1人` 不进池）；缺省排除 2人絡み 双人（`pose.single: true`）；深度库条目没有描述时 A 从线稿库同 id 复制（twin_copy），再没有由 LLM 看深度图起稿（ai_draft）。

## 换池 / 调流程（改 YAML 后重建运行）

- 各库过滤器：`face`（sources/section/hair）、`pose`（sources/lines/categories/angle/single）、`costume`（sources/categories/media）—— sources 必须是已登记的库 id，写错启动即报。人脸三库全开（`face-db-01/02-cn/03-cn` 的 face 正面像，约 1293 条；想收窄删 `sources` 项）；动漫服饰库（costume-anime-db-01）默认不进池（真人脸配动漫服装会画风混搭），要就加进 `costume.sources`。
- 关掉场景设计：把 `combo_image` 与 `video_script` 的 `scene_from:` 行删掉（回退固定背景句）。
- 场景方向由 `scene_design.hint:` 控制（现钉「有信息量的真实场景，不进纯摄影棚」）；质感由 `combo_image`/`video_script` 的 `style_lock:` 两头铆（现钉 cinematic live-action / high-key bright，换调性两处同步改）；视频模型写在 `video_model.model:`（删行回设置页默认链）。
- 关掉检查点：对应步写 `manual: false`（全自动模式：首帧崩了也会烧视频费，斟酌）。
- LLM（描述起稿 B / 场景 / 脚本）走 设置页「LLM」组 > env 的默认链；看图脚本与描述起稿都要求视觉模型，可按步 `model:` 覆盖。

## 注意

- 中途某步 failed：修好（配 LLM key / 启动 ComfyUI）后「继续」会**重跑该步**，不会换批。
- `video_model` 提交即 202 作业；`wait_s: 600` 内收片，超时转 waiting 下次继续续查（作业本身不浪费）。
- 服饰条目换装语义（`subject_policy`）与配件锚由三库组合流内建；纯文本（无图）条目不会进池。