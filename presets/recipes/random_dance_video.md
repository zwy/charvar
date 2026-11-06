# random_dance_video · 随机三库舞蹈视频

`random_cast_video` 的**舞蹈定向变体**：同样五步、每跑一次换一批选角，但全链为目标
「**一段 8 秒、多风格、大动作幅度的舞蹈视频**」服务 —— 首帧按视频需要倒推设计
（舞蹈起势站姿 / 服饰去包帽 / 余白框景），脚本**看着真实首帧图**编舞。

| 步 | 干什么 | manual |
|---|---|---|
| `pick` 随机选库 | 人脸三库全开（face 段正脸 1293 条）+ 深度库（pose-db-02）**站姿（立ち）两条线**单人全身 29 条当舞蹈起势 + 实人服饰库（costume-db-01，剔除ランジェリー 内衣类，约 1208 条）；动作描述按 A（pose-db-01 同动作复制）→ B（LLM 起稿）补齐 —— 站姿池描述 100% 走 A 路 | false |
| `scene_design` 场景设计 | LLM 按 动作+服饰 设计场景/光照；`hint:` 固定给舞蹈方向：开阔、地面可见、四周留大幅动作空间、无前景遮挡 | false |
| `combo_image` 首帧图 | 三库组合流出图 + 两个舞蹈开关：`strip_accessories: true`（**服饰图锚去包/帽/墨镜/手持物**，围巾手套鞋袜保留）+ `framing_extra`（全身入镜、头顶与四周留余白、地面完整可见、双手不持物）；ComfyUI 2k 9:16 | **true** |
| `video_script` 视频脚本 | **看图模式**（`frame_from: 首帧图`）：附着真实首帧图交给视觉 LLM，从图中实际姿态接续编舞；`amplitude: large`（大幅规则：跳跃/转体/甩头/大幅挥臂 + 起势→主段→收尾结构）；**风格池掷骰**（`types:` 6 种，每次运行抽 1 种写进脚本） | false |
| `video_model` 成片 | 脚本当 prompt + 首帧当 frame → OpenRouter 视频异步作业（模型走设置页默认链；本配方 **8s 768p** 9:16 —— 480p 实测 0.01/s 出真人舞蹈偏软，768p 目录价 0.036/s） | **true** |

## 运行

```bash
charvar recipes run random_dance_video            # 随机选库 + 场景设计，停在「首帧图」检查点
charvar recipes run random_dance_video --continue # 出首帧 + 看图写脚本，停在「成片」检查点
charvar recipes run random_dance_video --continue # 提交视频作业并轮询（真金白银，768p 8s）
```

或顶栏「配方」页 → 「random_dance_video」→ 开始运行 → 弹层里逐步「继续」。

## 舞蹈参数（改 YAML 后重建运行）

- **风格池**：`video_script` 步 `types:` 列表就是随机风格池，想增删直接改（如加「宅舞」「广场舞」）；抽中的风格会写进脚本开头与【本段视频类型】。
- **动作用品**：pose 过滤器锁「深度库 + 两条线站姿 + 正面 + 单人」；想只留標準系，把 `lines: [全身1人・セクシー系]` 删掉（余 14 条）。
- **服饰品类**：costume `categories:` 是允许清单（春/夏/秋/冬/パーティ/水着/セクシー），删项即缩池；要进动画库就把 `sources` 加 `costume-anime-db-01`。
- **去配件范围**：`strip_accessories` 剥离的是包/帽/墨镜/手持物，围巾、手套、鞋袜保留——措辞在 `charvar/pose_prompt.py`（`_COSTUME_ANCHOR_STRIP` 族）。
- **时长/档位**：`video_script.duration` 与 `video_model.duration` 要一起改；分辨率支持档见所选模型清单（设置页「OpenRouter 视频」组的默认模型支持 5–30s）。

## 注意

- **看图脚本要求设置页 LLM 为视觉模型**（与动作描述起稿同一要求）；站姿描述走 A 路复制、不需要视觉起稿。
- 首帧图必须**全身入镜且手脚完整**，否则大幅动作会被画面裁掉——检查点看到脚被裁就重跑该步（换种子）。
- 服饰条目按图锚定换装，条目自带包/帽时首帧也会穿（锚句已要求剥离，偶发残留换种子重跑 combo_image 即可）。
- `video_model` 提交即 202 作业；`wait_s: 600` 内收片，超时转 waiting 下次 continue 续查。
- 重跑/复现/换批语义与 `random_cast_video` **完全一致**（种子 + run_id 掷骰；continue 不换批）。
