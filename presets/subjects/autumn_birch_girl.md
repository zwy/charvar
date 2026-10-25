# 秋日枫林女孩｜全场景通用提示词模板

版本：V1.0 · imaishi 风格手绘漫画插画版
日期：2026-10-20
用途：文生图、角色定妆、多视角设定图、跨场景漫画分格。
边界：本文只覆盖下列已测试场景。未经测试的换装、用户照片扮演、双主体、视频不在已验证范围。

---

## 一、角色与创作边界

原创角色，成年女性。造型来源为用户提供的参考图（1024×1536 竖幅漫画分格，画面中角色被画幅截断在髋部）。

识别锚点共五组：

| 锚点 | 内容 |
|---|---|
| 头部 | 棕色短发 pixie cut，齐刘海，发尾外翘；棕色大眼；左耳小黑耳钉；开心大笑张口；腮红 |
| 头饰 | 芥黄针织毛线帽，带绒球与皮质标牌 |
| 上身 | 棕／芥黄格纹翻领衬衫（塞进裤腰）；敞穿红色工装夹克，深棕罗纹领口／袖口／下摆，露拉链与贴袋 |
| 配件 | 右手棕色皮质工装手套；右胯棕色帆布邮差包；棕皮带＋方形黄铜扣 |
| 下身 | 高腰中蓝牛仔（直筒，小腿略收，平口停在脚踝上方）；棕色皮面系带踝靴，深棕厚橡胶齿底；丰满腿型 |

**重要边界：下身与背部是设计补全，不是原图还原。** 参考图在髋部以下完全不可见，臀腿比例、裤装版型、鞋履全部由本项目设计；背面（夹克素面后片、牛仔裤后中缝／后育克／两个后袋、靴跟、邮差包背带过左肩）采用保守延续原则，无参照物。两者经用户认可后成为本项目设定，但不等于从参考图「还原」而来。

未确认设定：不绑定具体年龄数值、职业、身份、年代、地区与故事背景。Tim Hortons 为参考图与原提示词中已有的品牌道具，仅作为既有视觉元素沿用。

---

## 二、使用方式与证据状态

### 2.1 组合方式

```text
一个固定前缀（正常版或精简版，二选一）
+
一个场景模块（a／b／c／d）
```

两段之间空一行。提交时直接复制展开后的完整正文，不要把占位符交给模型。

### 2.2 字符预算

口径为 Python len 计 Unicode 码点，含空格与换行，不含 Markdown 代码围栏。**上限 2000 字符**（用户指定）。

| 组合 | 字符数 | 组合 | 字符数 |
|---|---:|---|---:|
| 正常版 + a | 1729 | 精简版 + a | 1303 |
| 正常版 + b | 1996 | 精简版 + b | 1570 |
| 正常版 + c | 1983 | 精简版 + c | 1557 |
| 正常版 + d | 1458 | 精简版 + d | 1032 |

固定前缀本身：正常版 1222，精简版 796。正常版 + b／c 已接近上限，新增长场景时优先改用精简版。

### 2.3 证据状态

| 场景 | 证据 | 说明 |
|---|---|---|
| a 全身定妆 | user_reported | 用户回复「OK，不错，继续下一步」＝提示词方案认可，执行待测 |
| b 多视角设定 | user_reported | 用户回复「不错，继续吧」＝布局与背面设计方案认可，执行待测 |
| c 桦树林对白分格 | user_reported | 用户回复「非常不错，继续吧」＝场景方案认可，执行待测 |
| d 头肩特写 | proposed | 本轮新加，尚未测试 |

全部四条都是**提示词层面的认可**。本项目未读取任何生成结果图，因此没有图像级检查证据，也没有跨模型、跨种子或批量稳定性结论。

---

## 三、固定前缀

### 正常版（1222 字符）

```text
a stylish comic illustration in the style of imaishi hiroyuki, hand-inked linework over flat colour areas with soft textured shading and a faint grain.

a busty well-endowed woman with short brown hair in a pixie cut with bangs and brown eyes, smiling happily with her mouth open, light blush on her cheeks, a small black stud earring in her left ear, and a mustard yellow knitted toque with a pom-pom and a leather patch label.

she wears a brown and mustard plaid buttoned collared shirt tucked in, a red work jacket with a dark brown ribbed collar, cuffs and hem band, a visible zip and patch pockets, worn open, a brown leather work glove on her right hand, a brown canvas messenger bag at her right hip, and a brown leather belt with a rectangular brass buckle.

she wears high-rise blue jeans in medium blue denim, fitted through the hip and upper thigh and falling into a straight leg ending in a clean flat hem just above the ankle, with golden-tan topstitching. she wears brown leather lace-up ankle boots with tan-brown uppers, dark laces and a thick dark rubber lugged sole, the toes angled slightly outward. her legs are full and shapely with rounded thighs, defined knees, proportional calves and slim ankles.
```

### 精简版（796 字符）

```text
a stylish comic illustration in the style of imaishi hiroyuki, hand-inked linework over flat colour areas with soft textured shading and a faint grain.

a busty well-endowed woman with short brown hair in a pixie cut with bangs and brown eyes, smiling with her mouth open, in a mustard yellow knitted toque with a pom-pom, a brown and mustard plaid buttoned collared shirt tucked into high-rise blue jeans with a brown leather belt and a rectangular brass buckle, a red work jacket worn open with a dark brown ribbed collar, cuffs and hem band, and patch pockets, a brown leather work glove on her right hand, a brown canvas messenger bag at her right hip, and brown leather lace-up ankle boots with tan-brown uppers, dark laces and a thick dark rubber lugged sole. her legs are full and shapely.
```

精简版删去的项：腮红、帽上皮质标牌、牛仔裤的明线／腰头／裤口结构说明、腿部细项（膝盖／小腿／脚踝比例）、鞋头朝向、牛仔的 denim 与 upper thigh 描述、衬衫的 buttoned 与夹克的 worn／visible zip。身份与服装主干（帽、格纹衬衫、红夹克、手套、邮差包、皮带、牛仔裤、皮靴）全部保留。

**精简版没有独立的通过状态**，需按所用工具复测。

---

## 四、参考图与身份优先级

| 模式 | 做法 |
|---|---|
| 纯文字（推荐） | 直接用固定前缀＋场景模块。无需上传参考图。 |
| 附参考图 | 把用户提供的原图作为身份与画风参考一并提交，正文前加一句：`以输入参考图为身份与画风依据，保留其头部造型、格纹衬衫、红色夹克与配色。` 平台引用编号按实际提交顺序统一，不要在正文里混用「图1／素材1／#图1」多套叫法。 |

参考图只负责身份、头部造型、上身服装、配色与画风。**下身与背面不由参考图提供**，只能由本文的固定前缀提供，不要写成「忠实保留参考图的下半身」。

若后续跑出满意的全身图或设定图，用它替换本文作为新的身份基准，新图优先于文字描述。

---

## 五、默认造型与视角结构

身体形态：成年女性，丰满体型，胸部饱满，臀腿与上身躯量协调。四视图设定图中三个全身必须同高、同比例。

服装层次（由内到外）：棕色／芥黄格纹衬衫（塞进裤腰）→ 棕皮带＋黄铜方扣 → 中蓝高腰直筒牛仔裤（平口在脚踝上方）→ 红色工装夹克敞穿 → 芥黄针织帽 → 右手棕皮手套。棕色帆布邮差包背带斜跨至右胯。

侧面与背面连续性：夹克为素面后片＋深棕罗纹下摆，无新增徽章、口袋或装饰；牛仔裤后中缝、后育克、两个后袋；靴子露出靴跟；邮差包背带过左肩。这些是保守延续的设计，不是原图还原。

自然遮挡：手插口袋时前臂与口袋口的关系应成立；帽檐遮住部分刘海；邮差包背带在胸前斜穿；夹克敞开时露出衬衫。

---

## 六、已测试场景模块

### 模块 a：全身定妆（简背景）

```text
full body, from the top of her toque to the soles of both shoes entirely inside the frame, standing on flat ground, feet a little apart, weight settled mostly on her right leg, seen almost straight on with a slight turn of the hips. she holds a large red Tim Horton's coffee cup in her right hand at chest height, and her left hand is tucked into her jacket pocket. plain warm light-grey background, a soft contact shadow under her boots, soft even lighting, earthy and warm slightly faded colour palette.
```

证据：user_reported（方案认可，执行待测）。用途：定妆主图、后续多视角的身份基准候选。

### 模块 b：多视角设定图

```text
a character reference sheet on a plain white background, the same woman in four views with identical proportions, colours and clothing details. on the left, a large head-and-shoulders view, front facing, from the top of the toque to the upper chest. on the right, three full-body views at the same scale, evenly spaced and not overlapping, from the top of her toque to the soles of both shoes inside the frame, standing in a relaxed neutral pose, feet a little apart, arms at her sides: first a front view, then a strict side profile facing left, then a back view. the back view shows the jacket's ribbed hem band and a plain back panel, the messenger strap over her left shoulder, the jeans with a centre back seam, a back yoke and two rear pockets, and the boots' heels.
```

证据：user_reported（方案认可，执行待测）。用途：确定背面设计、验证跨视角一致性。此模块中姿态为中立站姿、不持杯，与模块 a 的持杯姿态不同属有意区分。

### 模块 c：桦树林漫画分格

```text
a vertical comic panel. full body, toque to the soles of both boots in frame, standing on a leaf-covered forest floor, feet a little apart, seen from a slightly low angle, her right arm raised holding a large red Tim Horton's coffee cup with a white plastic lid toward the viewer, and her left hand tucked into her jacket pocket. the background is an autumnal birch forest: white birch trunks with dark bark scars, branches carrying golden and orange leaves, pale blue sky above the canopy, fallen leaves over the ground. soft even daylight. earthy and warm slightly faded colour palette. a white comic speech bubble with a rounded black outline at upper left, tail to her mouth, containing black hand-lettered text that reads "Heya bud. Wanna go fer a rip?".
```

证据：user_reported（方案认可，执行待测）。用途：还原参考图场景、跨场景身份延续测试。

### 模块 d：头肩特写（未测试）

```text
a head-and-shoulders portrait, from the top of the toque to the upper chest, facing the viewer. the background is a plain warm light-grey, soft even lighting, a soft shadow under the jaw, earthy and warm slightly faded colour palette.
```

证据：proposed，尚未测试。用途：S2 身份细化。

### 待测扩展

- 分格标题条（参考图顶部的红色 "OPTION 1: WAIFU"）——属分格设计不是角色要素，需要时单独立项，并会占用字符预算
- 其他环境与动作——未测，暂不提供

---

## 七、完整可复制示例与字符预算

### 示例 1：正常版 + 模块 c（桦树林对白分格，1983 字符）

```text
a stylish comic illustration in the style of imaishi hiroyuki, hand-inked linework over flat colour areas with soft textured shading and a faint grain.

a busty well-endowed woman with short brown hair in a pixie cut with bangs and brown eyes, smiling happily with her mouth open, light blush on her cheeks, a small black stud earring in her left ear, and a mustard yellow knitted toque with a pom-pom and a leather patch label.

she wears a brown and mustard plaid buttoned collared shirt tucked in, a red work jacket with a dark brown ribbed collar, cuffs and hem band, a visible zip and patch pockets, worn open, a brown leather work glove on her right hand, a brown canvas messenger bag at her right hip, and a brown leather belt with a rectangular brass buckle.

she wears high-rise blue jeans in medium blue denim, fitted through the hip and upper thigh and falling into a straight leg ending in a clean flat hem just above the ankle, with golden-tan topstitching. she wears brown leather lace-up ankle boots with tan-brown uppers, dark laces and a thick dark rubber lugged sole, the toes angled slightly outward. her legs are full and shapely with rounded thighs, defined knees, proportional calves and slim ankles.

a vertical comic panel. full body, toque to the soles of both boots in frame, standing on a leaf-covered forest floor, feet a little apart, seen from a slightly low angle, her right arm raised holding a large red Tim Horton's coffee cup with a white plastic lid toward the viewer, and her left hand tucked into her jacket pocket. the background is an autumnal birch forest: white birch trunks with dark bark scars, branches carrying golden and orange leaves, pale blue sky above the canopy, fallen leaves over the ground. soft even daylight. earthy and warm slightly faded colour palette. a white comic speech bubble with a rounded black outline at upper left, tail to her mouth, containing black hand-lettered text that reads "Heya bud. Wanna go fer a rip?".
```

### 示例 2：精简版 + 模块 b（多视角设定图，1570 字符）

```text
a stylish comic illustration in the style of imaishi hiroyuki, hand-inked linework over flat colour areas with soft textured shading and a faint grain.

a busty well-endowed woman with short brown hair in a pixie cut with bangs and brown eyes, smiling with her mouth open, in a mustard yellow knitted toque with a pom-pom, a brown and mustard plaid buttoned collared shirt tucked into high-rise blue jeans with a brown leather belt and a rectangular brass buckle, a red work jacket worn open with a dark brown ribbed collar, cuffs and hem band, and patch pockets, a brown leather work glove on her right hand, a brown canvas messenger bag at her right hip, and brown leather lace-up ankle boots with tan-brown uppers, dark laces and a thick dark rubber lugged sole. her legs are full and shapely.

a character reference sheet on a plain white background, the same woman in four views with identical proportions, colours and clothing details. on the left, a large head-and-shoulders view, front facing, from the top of the toque to the upper chest. on the right, three full-body views at the same scale, evenly spaced and not overlapping, from the top of her toque to the soles of both shoes inside the frame, standing in a relaxed neutral pose, feet a little apart, arms at her sides: first a front view, then a strict side profile facing left, then a back view. the back view shows the jacket's ribbed hem band and a plain back panel, the messenger strap over her left shoulder, the jeans with a centre back seam, a back yoke and two rear pockets, and the boots' heels.
```

其余六个组合的字符数见 2.2 表。全部实测 ≤ 2000，无未替换占位符，无叠加重复前缀。

---

## 八、测试原文索引

| ID | 位置 | 字符 | 类型 | 证据 | 用户反馈与范围 |
|---|---|---:|---|---|---|
| P1 | 用户消息内联的原始英文提示词 | — | 他人已测原文 | user_reported | 用户提供，非本项目实测 |
| r1 | `prompt_r1.md` | 2216 | 实测原文 | proposed | 超出 2000 预算，被 r2 取代 |
| r2 | `prompt_r2.md` | 1990 | 实测原文 | user_reported | 「OK，不错，继续下一步」＝方案认可，执行待测 |
| r3 | `prompt_r3.md` | 1991 | 实测原文 | user_reported | 「不错，继续吧」＝多视角方案认可，执行待测 |
| r4 | `prompt_r4.md` | 1998 | 实测原文 | user_reported | 「非常不错，继续吧」＝跨场景方案认可，执行待测 |

**整理版与原文的关系**：本文第三、六、七节的固定前缀与场景模块，是对 r2／r3／r4 的**重排与合并**——把身份与服装段从各轮正文里抽出成为固定前缀，把动作／背景／构图段归入场景模块。内容用词基本一致，但排序不同，且为压进 2000 字符删去了少量修饰（例：r2 的 "matte cotton twill"、"mild fading across the thighs and the knees"、靴子的 "lighter welt"、r4 的 "tall"、"showing"、"scattered"）。

因此：**整理版是需要复测的新版本，不继承 r2／r3／r4 的通过状态**。上表的 user_reported 只覆盖 r2／r3／r4 的原文。

---

## 九、偏差与修正

已观察问题：无。本项目没有读取过任何生成结果图，因此没有可记录的实际偏差。

已执行的修正（针对原英文提示词，非针对出图）：

| 问题 | 修正 | 依据 |
|---|---|---|
| `an large red Tim Horton's coffee cup` 语法错误 | 改为 `a large` | 英文语法 |
| 参考图髋部以下完全不可见 | 补全下半身并标为设计提案 | 用户要求 |
| 顶部红字与气泡文字混入角色描述 | 从角色正文剥离，列为分格设计／场景元素 | 参考职责区分 |

候选限制（未启用）：目前没有基于实际偏差启用的限制清单。若后续发现特定偏差，应逐条增加并单独测试，不预先堆砌。

---

## 十、验收与维护

### 基准索引

| 资产 | 位置 | 职责 | 状态 |
|---|---|---|---|
| B1 参考图 | 用户上传 `ComfyUI_temp_debog_00130_.jpg`（1024×1536） | 身份、头部、上身服装、配色、画风 | 唯一身份基准，已检查 |
| B2 全身定妆图 | 未产生 | — | 用户报告已认可方案，执行待测 |
| B3 多视角设定图 | 未产生 | — | 用户报告已认可方案，执行待测 |
| B4 桦树林分格图 | 未产生 | — | 用户报告已认可方案，执行待测 |

未产生实际附件前，不在后续会话假装能够读取这些结果图。

### 验收清单（每轮三至五项）

1. 身份：是否同一人、同一套衣服（帽型、格纹走向、纽扣数最容易漂）
2. 形态：腿型与丰满体型是否协调，三全身是否同高等距
3. 造型：配色、服装层次、配件位置、材质是否延续
4. 本轮目标：景别、视角、接触关系、表情或背景是否达成
5. 媒介：人物、道具、环境是否一致采用手绘墨线＋平涂

### 版本记录

| 版本 | 日期 | 变化 |
|---|---|---|
| V1.0 | 2026-10-20 | 首版。从 r2／r3／r4 重排为固定前缀＋场景模块，全部组合压进 2000 字符；新增未测试的模块 d |

### 维护规则

- 正常版与精简版必须同步：修改任何一方后，另一方需同步修改并各自重算字符数
- 改动前缀或场景模块后，把对应组合标为「待复测」，不沿用旧证据
- 新增场景模块时，先算字符数；正常版 + b／c 接近上限，优先用精简版
- 跑出的满意结果图一旦产生，应升级为新身份基准，并在本文登记为 B2／B3／B4 的实际位置

### 扩展边界

用户照片扮演、双主体、视频为预留入口，本项目未开展、未验证，不写成已实现能力。

---

## 十一、已用提示词原文（留档）

本文只收录可直接提交的正文体。转换说明、修改清单与操作步骤不计入提交内容。

所有提示词均为英文正文，与参考图原提示词语言一致。平台未指定，字符预算按 2000 执行。
