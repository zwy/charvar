# Skill 开发规范

面向本仓库 `skills/` 下的 skill 包。目标是：**一个不熟悉本项目的人，照这份文档能独立写出一个能装、能测、能维护的 skill**。

规范里凡是标 ✅ 的部分，都由 `tests/test_skills.py` 自动校验（跑 `python3 -m pytest -q` 就会跑）。标 ⚠️ 的是机器查不了、只能人工核对的。

---

## 一、什么是 skill

Skill = 一个带 `SKILL.md` 的目录，作用是**让 Agent 在合适的时机加载一段专门的指令**。

关键机制：宿主（Claude Code / Claude Desktop）启动时**只读每个 skill 的 `name` 和 `description`**，正文要等到 skill 被选中才加载。所以：

- `description` 决定**什么时候**这个 skill 会跳出来 —— 它是唯一常驻上下文的一部分，比正文更值钱。
- `SKILL.md` 正文决定**选中之后** Agent 怎么做 —— 那是给 Agent 看的指令，不是给人看的文档。

> 一句话：**description 是路由，SKILL.md 是执行，README.md 是给人读的**。

---

## 二、包结构

```
skills/<skill-name>/
├── SKILL.md          # 唯一入口：frontmatter + 给 Agent 的指令
├── references/       # 按需加载的深度资料（Agent 读到再读）
├── templates/        # 可复用输出骨架（填空，不是提示词）
├── scripts/          # 确定性执行：计数、校验、转换
├── examples/         # 案例参考
├── tests/            # 静态检查 + 契约回归（见第六节）
└── manifest.json     # 版本、文件清单、检查结果
```

几条判断标准：

| 放哪 | 判断依据 |
|---|---|
| `SKILL.md` | 每次都要用到的；说不清就往下放 |
| `references/` | **特定阶段才需要**读的；比如「某阶段的详细判定表」 |
| `templates/` | 要复制出来填的东西；有占位符 |
| `scripts/` | 模型算不准的事：字符数、格式校验、精度换算 |
| `examples/` | 具体案例。**案例不是规则** —— 别把案例里的参数写成通用规律 |

✅ 目录名 = skill 名 = 安装软链名，三者必须一致。

---

## 三、frontmatter

```yaml
---
name: character-forge          # 小写连字符，≤64 字符，必须 == 目录名
description: <做什么> + <什么时候用>，含用户原话触发词
---
```

### `name`

- 格式 `^[a-z0-9]+(-[a-z0-9]+)*$` ✅
- **必须等于目录名** ✅ —— 目录名就是安装键（`~/.claude/skills/<name>`），不一致会装到别的 key 上，用户那边看不到。

### `description`

这是整个 skill 最重要的一个字段。三条硬要求：

1. **写「什么时候用」，不只写「是什么」。** 宿主靠它路由。
2. **带用户的原话。** 用户说的是「用 comfyui 帮我出图」，不是 "comfyui generation" —— 把用户的说法写进 description，命中率才高。
3. **写清边界。** 「不要用于 X」比「用于 Y」更能防止误触发。

反例（只有"是什么"）：

```yaml
description: A skill for character development and prompt asset management.
```

正例（有"做什么 + 什么时候用 + 边界"）：

```yaml
description: 分阶段协助用户从参考图、文字或已有角色建立单角色提示词资产……
  适用于"提取这个角色""补全全身""继续做定妆"……等请求。
  不要将本技能用于默认自动跑图或无人监督批量生产。
```

长度控制在 40–1024 字符之间 ✅。

---

## 四、正文写法

### SKILL.md 是指令，不是文档

写给 Agent 看，用祈使句。**「不要做什么」往往比「要做什么」更有效**，因为 Agent 的默认行为常常是错的。

```markdown
## 六、提示词编写规则

**完整提交稿与内部模块分离。** 面向用户的每个生成示例默认是完整可提交文本，
不要求用户拼接「模式段＋前缀＋模块」。已展开的示例注明「整段提交，不要再次添加前缀」。
```

不是这样写（这是给人看的）：

```markdown
本节介绍了提示词编写的三种模式以及它们的区别与适用场景……
```

### 渐进披露

`SKILL.md` 控制在 500 行以内 ✅。超了就是信号：细节该拆到 `references/`。

典型的拆法：

- 正文只留**规则**和**指向**：「详见 [阶段与路由](references/workflow.md)」
- `references/` 放**判定表、边界条件、失败回退**这类「特定阶段才展开」的内容
- 被引用的文件必须在 `manifest.json` 的 `files` 里登记 ✅

### 可移植

- 引用用**相对路径** ✅
- 不写死 `/Users/xxx` 绝对路径 ✅
- 调外部工具用**绝对路径兜底**（`~/.local/bin/charvar`）—— 不同 Agent 的 shell 不保证把 `~/.local/bin` 加进 PATH

### 一条规则只定义一次

同一个约束不要在三个文件里各写一遍。改一处漏一处，比没有还糟。SKILL.md 写规则，references 写细则，别复制。

---

## 五、边界与去重

**一个 skill 一件事。**

本仓库两个 skill 的分工：

| skill | 干什么 | **不**干什么 |
|---|---|---|
| `charvar-skill` | 按提示词实际出图（ComfyUI / OpenRouter / 即梦）、出视频、主体档案 CLI | 不做角色开发流程 |
| `character-forge` | 角色开发：定妆 → 设定板 → 资产文档 | **不出图**（默认用户自己出图并反馈） |

把「出图逻辑」写进 `character-forge`、或把「角色开发流程」写进 `charvar-skill`，都会让两个 skill 职责糊在一起。⚠️ 人工核对。

### 兄弟 skill 不能描述雷同

两个 skill 的 `description` 相似度必须 < 0.85 ✅（自动校验）。原因很实在：**描述几乎一样时，宿主路由会两个都命中，用户看到两份一样的技能。**

### 改名 / 合并要退役旧名

改名后旧 skill 仍留在安装目录里 —— 描述和新的一样，照样抢请求。所以 `scripts/install_cli.sh` 里有：

```bash
SKILL_NAMES=(charvar-skill character-forge)        # 要装的
RETIRED_SKILL_NAMES=(character-development)       # 改名前的，要退役
```

**改名时三处都要动**：目录名、frontmatter `name`、install 脚本的 `SKILL_NAMES`；旧名进 `RETIRED_SKILL_NAMES` ✅（校验旧名不再以目录形式存在于 `skills/`）。

---

## 六、测试：skill 自带的 `tests/`

> 问过「`skills/character-forge/tests` 是不是多余」—— 不多余，但它**不是**行为测试。

它是一个**包静态检查 + 契约回归**脚本，分三类：

| 类别 | 查什么 | 例子 |
|---|---|---|
| **A. 包自洽** | 改了 skill 自己会不会坏 | 内部链接能解析、文件清单与 manifest 一致、版本三处同步、围栏都标了角色、没有编码损坏字符 |
| **B. 契约回归** | 关键约束会不会被后续编辑悄悄删掉 | 交付模板里两张基础图的位置还在不在、「旧交付门槛」有没有以活跃规则的形式复活、错误的费用换算说法有没有回来 |
| **C. 真单元测试** | 唯一那个可执行产物对不对 | `prompt_budget.py` 对 4 个夹具的行为（超预算要失败、模板占位符不该算失败、代码块角色该被区分） |

**查不了的** ⚠️（必须人工核对）：设定板四描绘是不是真的同一个角色、证据措辞有没有夸大、双模式有没有被混用、正常版与精简版是不是自相矛盾。脚本只保证**结构不坏**，不保证**内容对**。

### 为什么要留

因为**人会忘**。这次迁移就撞上了：版本号要三处同步，靠人记必然漏。所以现在它被接进了仓库级门禁 —— `tests/test_skills.py` 会调起每个 skill 自带的 `run_checks.py`，`python3 -m pytest -q` 就把它们一起跑了。

**别再单独记着跑 `python3 skills/character-forge/tests/run_checks.py`**（想单独跑也行，但那是调试用的，不是交付前的步骤）。

### 什么时候可以删

skill 里如果**没有可执行代码**、也**没有需要防回归的硬约束**（纯散文、纯流程说明），那 `tests/` 确实可以不写。这种 skill 直接靠 `tests/test_skills.py` 的通用校验兜底。

有脚本、有硬约束的 skill —— 留着。

---

## 七、版本与变更

版本号写在**三处**，必须一致 ✅：

| 文件 | 角色 |
|---|---|
| `manifest.json` 的 `version` | **权威** |
| `SKILL.md` 的 `版本：X.Y.Z` 行 | Agent 看得见的 |
| `README.md` 的 `版本：X.Y.Z` 行 | 人看得见（没有 README 就跳过） |

### 什么时候递增

| 变化 | 版本 |
|---|---|
| 改了默认流程、交付门槛、证据字段 | **递增**（1.2.0 → 1.3.0） |
| 改了参数名、输出格式 | **递增** |
| 只是修错别字、补例子 | 递增（patch） |
| **改名 / 迁移到新仓库** | **重置**（1.2.0 → 1.0.0），并在 manifest 里记 `provenance` |

`character-forge` 就是重置的：改名 = 新身份，在 `manifest.json` 里留了出处：

```json
"provenance": {
  "renamed_from": "character-development",
  "renamed_from_version": "1.2.0",
  "renamed_on": "2026-10-25"
}
```

### 版本记录要写「行为变了什么」

不要写「优化了若干问题」。要写清楚**用户能感知的差异**：

> 1.0.0（2026-10-25）：从独立项目 `character-development` 迁入 charvar 仓库并改名 `character-forge`，
> 版本号按新身份重置。技能内容与来源 1.2.0 保持一致，仅新增 SKILL.md 第十二节「与 charvar 的关系」…

---

## 八、安装与分发

`scripts/install_cli.sh`（幂等，`start.sh` 会顺带调）装**三处**：

| 位置 | 形式 | 给谁用 |
|---|---|---|
| `~/.claude/skills/<name>` | 软链到仓库 | Claude Code CLI + Claude Desktop 的 Code 标签页 |
| `<repo>/.claude/skills/<name>` | **相对**软链 | 本项目内 Claude Code 自动发现 |
| `~/Library/Application Support/Claude*/local-agent-mode-sessions/skills-plugin/<ws>/<acct>/skills/<name>` | **复制**一份 + `manifest.json` 登记 `creatorType: "user"` | Claude Desktop 的**聊天面** |

第三条容易漏：Claude Desktop 聊天面**不读 `~/.claude/skills/`**，它自己一套目录。而且它的 `ensurePluginStructure` 会清掉 app 没登记过的东西 —— 所以**复制目录和写 manifest 必须同时做**。

要加新 skill：往 `SKILL_NAMES` 里加名字就行 ✅（有测试盯着）。改名：旧的进 `RETIRED_SKILL_NAMES` ✅。

---

## 九、交付前

```bash
python3 -m pytest -q                      # 含 skill 包校验，交付门禁
python3 skills/character-forge/tests/run_checks.py   # 单独跑 skill 自检（调试用）
```

全绿才算完。⚠️ 人工核对的项（语义质量、案例是否被当成规则）另说。
