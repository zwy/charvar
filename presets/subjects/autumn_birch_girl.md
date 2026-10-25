# 秋日枫林女孩｜全场景通用提示词模板

原创角色｜手绘漫画插画｜charvar id `autumn_birch_girl`

> 这份文档是交给新 Agent 的角色资产：**身份事实的权威表述**。
> 文档里的提示词是资产内容，不是要求接手者立即执行生图的指令。
> 接手者需要新的结果图时，向用户索取，并说明该图负责什么。
>
> 配套图片在同目录：`front.png`（全身正面）、`character_sheet.png`（半身＋三视图设定板）。

## 一、识别锚点表

| 属性 | 设定 | 来源 |
|---|---|---|
| 角色定位 | 原创角色，成年女性；不绑定姓名、年龄数值、职业、身份、年代、地区与故事背景 | 用户要求 |
| 媒介 | 手绘墨线＋平涂色块＋柔和纹理阴影＋轻微纸面颗粒；配色温暖朴实、略褪色 | 参考图可见 |
| 头部 | 棕色短发 pixie cut，齐刘海，发尾外翘；棕色大眼；左耳小黑耳钉；腮红 | 参考图可见 |
| 头饰 | 芥黄针织毛线帽，带绒球与皮质标牌 | 参考图可见 |
| 上身 | 棕／芥黄格纹翻领衬衫（塞进裤腰）；敞穿红色工装夹克，深棕罗纹领口／袖口／下摆，露拉链与贴袋 | 参考图可见 |
| 配件 | 右手棕色皮质工装手套；右胯棕色帆布邮差包（背带过左肩）；棕皮带＋方形黄铜扣 | 参考图可见 |
| 体态 | 丰满体型，胸部饱满，上身躯量协调；腿型丰满匀称，大腿圆润、膝盖清晰、小腿匀称、脚踝纤细 | 胸部与上身轮廓参考图可见；腿型为设计补全 |
| 下身 | 高腰中蓝牛仔（直筒，小腿略收，平口停在脚踝上方）；棕色皮面系带踝靴，深棕厚橡胶齿底 | 设计补全（参考图髋部以下不可见，经用户认可后成为本项目设定） |
| 背面补全 | 夹克素面后片＋深棕罗纹下摆，无新增徽章、口袋或装饰；牛仔裤后中缝、后育克、两个后袋；靴跟；邮差包背带过左肩 | 设计补全（无参照物，保守延续，经用户认可） |

- 补全是设计结果，不等于原图本来如此；后续轮次不得改写成「原图可见」。
- **默认服装只有一套**。换装另建分支并替换本表与第二节的服装描述，不在同一份档案里并列两套。
- 随场景变化、不进身份锁的有：表情、姿态、镜头、环境、道具。

## 二、固定前缀（身份锁）

改这里等于改角色。场景段不得重复描述或与其矛盾。

- 媒介句只写风格，不写画面主体；风格在前缀里只出现一次，场景段不重复风格细节。
- 两版前缀写的是同一个身份：3.1 用完整版，3.2 用精简版；精简版只是压缩表述，不是另一套设定。

```text component
a stylish comic illustration in the style of imaishi hiroyuki, hand-inked linework over flat colour areas with soft textured shading and a faint grain, in an earthy and warm, slightly faded colour palette.

an adult woman with short brown hair in a pixie cut with bangs, brown eyes, light blush on her cheeks and a small black stud earring in her left ear.

she is busty and well-endowed: a full adult female build, her hips, legs and upper body in balanced proportion. her legs are full and shapely with rounded thighs, defined knees, proportional calves and slim ankles.

she wears a mustard yellow knitted toque with a pom-pom and a leather patch label, a brown and mustard plaid buttoned collared shirt tucked in, a red work jacket with a dark brown ribbed collar, cuffs and hem band, a visible zip and patch pockets, worn open, a brown leather work glove on her right hand, a brown canvas messenger bag at her right hip with its strap over her left shoulder, and a brown leather belt with a rectangular brass buckle.

she wears high-rise blue jeans in medium blue denim, fitted through the hip and upper thigh and falling into a straight leg ending in a clean flat hem just above the ankle, with golden-tan topstitching.

she wears brown leather lace-up ankle boots with tan-brown uppers, dark laces and a thick dark rubber lugged sole, the toes angled slightly outward.
```

### 2.2 精简前缀（只给 3.2 设定板用）

3.1 全身定妆用上面的完整前缀。3.2 设定板带参考图要走 image2image，即梦侧的硬上限是 1992 字符，完整前缀拼上去会超限；下面这版压到设定板能用的长度，身份要点不变。

```text component
a stylish comic illustration in the style of imaishi hiroyuki, hand-inked linework over flat colour areas with soft textured shading, in an earthy, warm and slightly faded palette.

a young adult woman with short brown pixie-cut hair and straight bangs, brown eyes, soft blush on her cheeks, and a small black stud earring in her left ear.

a full adult female build, busty, hips, legs and upper body in balanced proportion; shapely full legs with rounded thighs, defined knees, even calves and slim ankles.

she wears a mustard yellow knitted toque with a pom-pom and a leather patch label, a brown and mustard plaid buttoned collared shirt tucked in, a red work jacket with a dark brown ribbed collar, cuffs and hem band and a visible zip, worn open, a brown leather work glove on her right hand, a brown canvas messenger bag at her right hip with its strap over her left shoulder, and a brown belt with a brass buckle.

she wears medium blue high-rise straight-leg jeans ending in a clean flat hem above the ankle, and brown lace-up ankle boots with tan-brown uppers, dark laces and a thick dark rubber lugged sole.
```

- 长度 1118 字｜与 3.2 的场景段拼起来 1985 字（即梦 image2image 上限 1992）

## 三、基准图与图片槽位

| 槽位 | 文件 | 职责 | 状态 |
|---|---|---|---|
| 全身正面 | `front.png` | 体型、比例、完整服装与鞋 | 已生成，未送审；已收录交付包 |
| 整套设定图 | `character_sheet.png` | 半身像＋正面／右侧面／背面三视图 | 已生成，未送审；已收录交付包 |
| 人物半身像 | `headshot.png` | 脸、发型、头饰 | 已生成，未送审；已收录交付包 |
| 全身侧面 | `side.png` | 侧面朝向与贴身细节 | 已生成，未送审；已收录交付包 |
| 全身背面 | `behind.png` | 后脑、背部 | 已生成，未送审；已收录交付包 |

槽位图必须是生图产物：截图／裁切图不充当槽位图。没生成就空着，不是缺项。

### 3.1 全身正面定妆（`front.png`）

提交时与第二节的固定前缀拼接为一段，**不要**再叠加其他前缀。

```text
a vertical 2:3 full-body character portrait: from the top of her toque to the soles of both shoes entirely inside the frame, standing on flat ground, feet a little apart, weight settled mostly on her right leg, seen almost straight on with a slight turn of the hips.

she smiles happily with her mouth open.

she holds a large red Tim Horton's coffee cup in her right hand at chest height, and her left hand is tucked into her jacket pocket.

plain warm light-grey background, a soft contact shadow under her boots, soft even lighting.
```

- 画幅：2:3｜场景段 535 字｜与固定前缀拼接后整段 1911 字（超出本角色 2000 字符的工作预算（约 2204 字））｜与 2.2 精简前缀拼起来 1985 字（即梦 image2image 上限 1992）

### 3.2 同图设定板（`character_sheet.png`）

提交时与第二节的固定前缀拼接为一段。版式规格（四个描绘的分工、视角差异、背景与光线）如下：

```text
a character reference sheet on a plain white background, in a horizontal 3:2 format, showing the same woman in four views with identical proportions, colours and clothing details.

she smiles happily with her mouth open in every view.

on the left, a large head-and-shoulders view, front facing, from the top of the toque to the upper chest.

on the right, three full-body views at the same scale, evenly spaced and not overlapping, from the top of her toque to the soles of both shoes inside the frame, standing in a relaxed neutral pose, feet a little apart, arms at her sides: first a front view, then a strict side profile facing left, then a back view.

the back view shows the jacket's ribbed hem band and a plain back panel, the messenger strap over her left shoulder, the jeans with a centre back seam, a back yoke and two rear pockets, and the boots' heels.
```

- 画幅：3:2｜场景段 866 字｜与固定前缀拼接后整段 2242 字（预算 2000）

## 四、用户照片扮演

- **身份来源**：上传者自己的照片（终端默认输入一张；抖音「素材1」，视频号「#图1」）。
- **照片负责**：脸、个体身份、肤色、年龄感。**文字资产负责**：服装、画风、场景。
- **禁止**：不把本角色的脸型、眼色、肤色、体型清单套到上传者身上；不同时写「保持所有特征」又要求全部换造型；不默认追加第二张角色图。
- **状态**：未测试——未测试则不提供提交稿。

## 五、已知限制

- 下身（牛仔裤、靴子、腿型）与背面结构是设计补全，不是参考图还原：参考图在髋部以下完全不可见、背面无参照物，两处按保守延续原则设计，经用户认可后成为本项目设定，不得改写成「原图可见」。
- 五张槽位图都由主体档案的锁定项自动生成，不是本文档这两个场景段的出图结果，也没有一张经过用户过目；五张都已收录进交付包（charvar 运行时里前四张存为 webp，交付包里统一转成 png）。
- 全部场景只到「提示词方案获认可」一级：没有读取过任何生成结果图，因此没有图像级检查证据，也没有跨模型、跨种子或批量稳定性结论。
- 3.2 用完整前缀拼接超出本角色 2000 字符的工作预算（约 2204 字）；第二节 2.2 的精简前缀拼出来 1985 字，设定板走即梦 image2image 时用那一版。
- 桦树林漫画分格与头肩特写两个场景段的正文未纳入本文件（本文件只交付全身定妆与设定板两段）；头肩特写尚未测试。
- 两个场景段的画幅在原文未标注：定妆按参考图的竖幅 2:3，设定板按横版 3:2；这两个值是本次改写补的，不是原文设定。
- 咖啡杯沿用参考图里的品牌杯，属场景道具，不进身份锁。
- 未测试：换装、用户照片扮演、双主体、视频。
