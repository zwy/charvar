# random_cast_video · 随机三库组合视频

五步的**生成配方**（末步 `video_model`）：从**三个库**随机挑一套组合写定稿脚本，按脚本设计场景
出一张首帧图，再直出 8 秒视频。**针对库而不是针对某条**——每跑一次运行换一批选角（见下
「随机语义」）；YAML 里不写死任何一条脸/动作/服饰。

v4 起**脚本先行**（对齐贱猫系列三配方）：脚本先写死稿、场景按稿逆推、再出首帧——检查点顺序
= 脚本过目 → 场景+首帧 → 成片。

| 步 | 干什么 | manual |
|---|---|---|
| `pick` 随机选库 | 人脸三库（face-db-01 / 02-cn / 03-cn 的 face 正面像，约 1293 条）选 1 条 + **深度库全身单人动作**（pose-db-02，`全身1人` 两条线，正面视角，`single: true` 排双人，category 白名单见下「池子尺度」）选 1 个 + 服饰库选 1 条（category 白名单见下）；选中动作没有「动作描述」时按 **A（另一库同动作复制，twin_copy）→ B（LLM 看深度图起稿，ai_draft 落库）** 补齐 | false |
| `video_script` 视频脚本 | **无图文本模式**（`frame_from` 不给）：LLM 按 动作描述+服饰要点+类型池（缺省 8 类：跳舞/律动/转身/摆拍/走向镜头/伸展/回眸/拨头发）写 8 秒脚本；`style_lock` 质感 + `safety_lock` 安全条款两头进 brief | false |
| `scene_design` 场景设计 | 按**已定稿脚本**逆推场景（`script_from: 视频脚本`）：脚本拍点用到的舞台/道具/方位必须在场景里装得下；`hint:` 钉方向——与服饰/动作气质相符的有信息量真实场景，不进纯摄影棚。manual 检查点 = **脚本人工过目**（停在本步前） | **true** |
| `combo_image` 首帧图 | 三库组合流出图（同 `charvar pose prompt <动作> --face <脸> --costume <服饰>`）：人脸=身份锚、动作=姿势锚、服饰=换装锚；场景照 `scene_from:` 装进首帧；ComfyUI 2k 9:16（1536×2752，设置页编辑 workflow 要用显式画布版——`*_edit_easy_api`）；`style_lock`/`safety_lock` 与脚本步同句两头铆 | **true**（缺省） |
| `video_model` 成片 | 脚本当 prompt + 首帧当 frame → OpenRouter 视频异步作业（`model: heygen/heygen-video-1` 写死在步里，不跟设置页默认链；本配方 **8s 768p**——480p 实测出真人偏软；实测价 ≈$0.018/s，heygen 实收恰为目录价一半） | **true**（缺省） |

## 运行

```bash
charvar recipes run random_cast_video            # 随机选库 + 写脚本，停在「场景设计」检查点（脚本已定稿，先过目）
charvar recipes run random_cast_video --continue # 设计场景，停在「首帧图」检查点
charvar recipes run random_cast_video --continue # 出首帧，停在「成片」检查点
charvar recipes run random_cast_video --continue # 提交视频作业并轮询（真金白银）
```

或顶栏「配方」页 → 「random_cast_video」→ 开始运行 → 弹层里逐步「继续」（每个检查点都能先看产物再放行）。

## 随机语义

- 掷骰子种子 = 运行种子 + `recipe_run_id`：**每次运行换一批**；同一运行内 continue/重试固定同一批。
- 想复现某批：记下运行的 seed 与 run_id；`charvar recipes run random_cast_video --seed <n>` 换种子重掷。
- 动作池锁**深度库全身单人**（pose-db-02）；缺省排除 2人絡み 双人（`pose.single: true`）；深度库条目没有描述时 A 从线稿库同 id 复制（twin_copy），再没有由 LLM 看深度图起稿（ai_draft）。

## 池子尺度（v4 起白名单制）

锁太死没人看、不锁有敏感风险，白名单取中间档：

- **动作**（pose-db-02 `全身1人` 两条线内再锁 category）：保留 `立ち／座り／しゃがみ／
  ひねり／前かがみ／のけぞり・反り／横座り・女の子座り`——日常站坐蹲拧为主；**排除**
  `寝そべり`（躺卧）、`膝立ち・四つん這い`（跪爬）、セクシー系の `特殊`（异形姿势）。
  池约 420 条（原全池 ~830 的一半）。
- **服饰 category 白名单**（对所选 sources 统一生效）：costume-db-01 收
  `春/夏/秋/冬/パーティ/セクシー/水着`，**排除 `ランジェリー`（内衣，明确敏感）**；
  自建 studio 只放行 `idol_stage` / `glamorous_gala_dresses` 两族——
  `lingerie_variations` / `mature_milf_outfits` / 纱质睡袍等敏感自建族不在白名单里
  自然出局。要加自建族就把族名追加进 `costume.categories`。
- **兜底**：`video_script` 与 `combo_image` 两头铆 3 条 `safety_lock`（衣着完整覆盖 /
  无疼痛惊恐暴力低俗 / 单人正常肢体）；脚本步一侧还会把条款写进「避免出现」一节，
  成片提示词本体带上安全约束。

## 换池 / 调流程（改 YAML 后重建运行）

- 各库过滤器：`face`（sources/section/hair）、`pose`（sources/lines/categories/angle/single）、
  `costume`（sources/categories）—— sources 必须是已登记的库 id，写错启动即报；
  categories 白名单两边同用一份，加自建服饰族名就一并写进 `costume.categories`。
- 想放宽尺度：删/改 `pose.categories` 或 `costume.categories` 里的行；动漫服饰库
  （costume-anime-db-01）默认不进池（真人脸配动漫服装会画风混搭），要就加进
  `costume.sources`。
- 关掉场景设计：把 `scene_design` 步整个删掉、`combo_image` 的 `scene_from:` 行删掉
  （回退固定背景句）；脚本步与首帧步的 `script_from`/`scene_from` 联动同步清理。
- 场景方向由 `scene_design.hint:` 控制；质感/安全由两步 `style_lock:`/`safety_lock:`
  两头铆（**两步必须同句同步改**）；视频模型写在 `video_model.model:`（删行回设置页默认链）。
- 关掉检查点：对应步写 `manual: false`（全自动模式：首帧崩了也会烧视频费，斟酌）。
- LLM（描述起稿 B / 场景 / 脚本）走 设置页「LLM」组 > env 的默认链；可按步 `model:` 覆盖。

## 注意

- 中途某步 failed：修好（配 LLM key / 启动 ComfyUI）后「继续」会**重跑该步**，不会换批。
- 脚本先行后改 YAML 重建运行：v4 与 v3 步序不同，旧运行 continue 会被拒绝，重建即可
  （产物在 Playground/视频各页自理）。
- `video_model` 提交即 202 作业；`wait_s: 600` 内收片，超时转 waiting 下次继续续查（作业本身不浪费）。
- 服饰条目换装语义（`subject_policy`）与配件锚由三库组合流内建；纯文本（无图）条目不会进池。
