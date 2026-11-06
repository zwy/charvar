# random_mv_video_up · 随机选角 MV 升级版（特写开场 + 文字特效）

`random_mv_video` 的**升级版**：同样五步、同为日系女团定向，但**第 1 镜从首席脸部大特写起拍**（眼神/表情戏，样例 MV 的 0:01 帧），并编入**两处背景大字文字特效**（中段齐舞爆点一处 + 末镜亮相一处——对应样例的「HIT」「OWN IT」帧）。词对从池子掷骰、每跑换一对。

| 步 | 干什么 | manual |
|---|---|---|
| `pick` 随机选库 | 同 `random_mv_video`（人脸 face-db-01 + 深度库站姿两条线 + 服饰锁 **idol_stage 偶像台装 8 套**）；动作描述缺失按 A（同动作复制）→ B（LLM 起稿）补齐 | false |
| `scene_design` 场景设计 | 同 hint（舞台礼堂/白栅栏天台/舞蹈镜室，明亮青春可爱系舞台光） | false |
| `combo_image` 首帧图 | **特写模式**（`close_up: true`）：`build_closeup_prompt`——**纯脸参考**（人脸为唯一参考图，不传动作/服饰图），面部大特写构图 + 场景虚化背景；ComfyUI 2k 16:9。特写里的发型/妆面随脸库条目 | **true** |
| `video_script` 视频脚本 | 看图 + 多镜 + **特写开场**（`opening: face`）：第 1 镜保持面部特写（眼神/表情微动作 + **一道暖金光带单次横扫过脸**，扫光节奏校准自原片 0–2.5s），第 2 镜起切全身、伴舞第 3 镜起入画；**文字特效**（`text_fx: true`）：中段爆点镜与末镜各编入一处背景大字（超大无衬线、闪粉质感、贴布景墙面）——**词对从 `text_pool` 掷骰**（如 HIT + OWN IT），逐词锁进脚本校验 | false |
| `video_model` 成片 | 特写首帧当 frame + 脚本当 prompt → 15s 16:9（**768p**，实测 $0.018/s ≈$0.27；480p 是调试档 ≈$0.15 可随时回降）；模型走设置页默认链（现阶段 heygen） | **true** |

## 运行

```bash
charvar recipes run random_mv_video_up            # 随机选库 + 场景设计，停在「首帧图」检查点
charvar recipes run random_mv_video_up --continue # 出特写首帧 + 看图写多镜脚本，停在「成片」检查点
charvar recipes run random_mv_video_up --continue # 提交视频作业并轮询（真金白银，15s 768p）
```

或顶栏「配方」页 → 「random_mv_video_up」→ 开始运行 → 弹层里逐步「继续」。

## 特写与文字特效参数（改 YAML 后重建运行）

- **质感锁**：首帧步与脚本步都有 `style_lock:`（同一句两头铆钉——分别写进特写首帧提示词与脚本 brief 的【全局质感】段）：现铆 **cinematic live-action / high-key bright lighting**（真人实拍电影感、高调明亮打光，校准自原 MV 样片的整体调性）；想换调性（暗调/赛博等）时两处条目同步改。
- **词池**：`text_pool:` 就是背景大字词池（超短大写英文词出字最稳），想增删直接改（加「GO!」「WOW」等）；掷出的词对记在运行 manifest 的 `text_words` 里，可复现。池子删空则回退内置词池（HIT/OWN IT/SHINE/GO GO/LOVE）。
- **特写首帧**：`close_up: false`（或删行）回全身首帧（此时可重新用 `strip_accessories`/`framing_extra`）；`opening:` 改回 `body` 则第 1 镜直接从首帧姿态起跳，不保持特写。
- **文字风险**：大字由视频模型渲染——heygen 出短英文词成功率较高但非 100%，崩字/乱码时换种子重跑脚本步（掷新词对）。
- **时长/镜头/阵容**：`duration: 15` + `shots: 4`（特写 0–4s、全身编舞 4–15s 的典型分布，切点由 LLM 均匀排）；其他同 `random_mv_video.md`。

## 注意

- **特写首帧不吃服饰**：首席的服装从第 2 镜起由脚本文本展开（服饰关键词照常进脚本步输入），图与文字的服装若有偏差以脚本描述为准。
- **扫光在脚本里不在静帧里**：首帧是中性定妆光的纯脸特写，光带横扫发生在第 1 镜内（脚本条款承担）——heygen 出镜光不稳时，试改首帧带入半侧暖光再跑。
- **看图脚本要求设置页 LLM 为视觉模型**；特写帧也走同一压缩线（长边 ≤1280）。
- 声场/配乐两段照常写入（heygen 无声、当氛围读；切有声模型见 `random_mv_video.md` 的「换有声模型」节——gemini-omni 等 `generate_audio` 模型真出声）。
- 重跑/复现/换批语义同家族配方（种子 + run_id 掷骰；continue 不换批）。
