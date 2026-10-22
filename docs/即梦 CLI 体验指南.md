# 即梦 CLI 体验指南

# 一、即梦 CLI 是什么

即梦 CLI 是面向 Agent 和自动化工作流的本地命令行工具。安装后，你可以在终端或 Agent 环境里调用即梦的图片生成、视频生成、结果查询、任务历史和账户查询能力。

**适合使用它的场景**

- 让 Agent 帮你批量生成图片或视频

- 把即梦生成能力接入脚本、自动化流程或测试流程

- 保存 submit\_id，稍后继续查询异步任务结果

**使用前需要知道**

- 生成任务会消耗账户权益或积分，目前**仅限会员可用**

- 您使用即梦 CLI 生成内容所需消耗的积分，与即梦网页端 Agent 模式下相同生成能力所消耗的积分标准一致，具体以产品规则及积分消耗记录为准

- 大多数生成任务是异步任务，提交和查询是两个步骤

- 由于合规要求，**视频生成需要先在网页端完成第一次生成**，之后才能在CLI内提交视频生成任务

---

更新日志（Update）

**更新方式：终端运行命令 **`curl -s ``https://jimeng.jianying.com/cli`` | bash`

**CLI入口移动至页面右上角头像 → 即梦插件与CLI**

【v1\.4\.18｜2026\-09\-10】🆕  

视频生成支持比例控制。

【v1\.4\.17｜2026\-08\-18】

为seedance 2\.5增加1080p分辨率的参数支持

【v1\.4\.16｜2026\-08\-14】

为seedream 5\.0 pro增加1\.5k分辨率的参数支持，同时移除了不再支持的1k分辨率参数

【v1\.4\.15｜2026\-08\-01】

新增：视频生成支持 Seedance 2\.5 模型。

- text2video、image2video、frames2video、multimodal2video 可通过 \-\-model\_version=seedance2\.5 使用

- 支持 480P、720P 和 4～30 秒输出

- 多模态支持 2～30 秒参考音视频及纯音频输入

【v1\.4\.14 \| 2026\-07\-21 】

**重要变更** ：现在图片生成需要显式提供 \-\-resolution\_type，视频生成需要显式提供 \-\-video\_resolution

**其他变更** ：

1. 新增：text2image 和 image2image 新增自定义宽高能力，可通过 \-\-width、\-\-height 精确指定目标尺寸，自定义宽高必须成对提供，并且不能与 \-\-ratio 同时使用

2. 新增：智能多帧视频生成新增分辨率选择能力，可通过 \-\-video\_resolution 指定 720P 或 1080P

3. 修复：失败的任务再次查询时不再被错误的显示为querying

4. 改进：help 信息中进一步明确各模型支持的分辨率、时长范围及默认参数；优化了配套Skill的内容

【v1\.4\.12 \| 2026\-07\-15 】

- 新增：支持Seedream 5\.0 Pro模型（如果遇到生成5\.0 Pro错误，请尝试重新安装）

【v1\.4\.10 \| 2026\-06\-26】

- 新增图片批量生成能力：text2image、image2image 支持通过 generate\_num 参数一次生成 1\-10 张图片

- 新增对 seedance2\.0\_vip 4k 分辨率的支持

- 修复图片超清任务结果查询失败的问题

- 修复 image2video 在部分情况下生成失败的问题

- 调整视频模型名称，将原本的 3\.x 模型名改为 Seedance 1\.x 模型名称，与Web端保持命名一致

【v1\.4\.8 \| 2026\-06\-18】

- 新增：支持seedance 2\.0 mini模型

- 完善了即梦CLI配套的Skill文件

【v1\.4\.4 \| 2026\-06\-03】

- 新增：支持seedream 4\.7模型

【v1\.4\.3｜2026\-05\-07】

- 新增：支持seedance 2\.0 vip模型以1080p分辨率生视频

【v1\.4\.1｜2026\-04\-17】

- 安全：登录方式更新

【v1\.4\.2｜2026\-04\-22】

- 修复：修复上传多图时的超时问题

【v1\.3\.5｜2026\-04\-16】

- 新增：支持多对话工作空间（Session）的增删查改、搜索，并可在指定会话中执行生成任务

【v1\.3\.4｜2026\-04\-10】

- 优化：优化生图，全能参考命令帮助文案，现在支持linux arm64平台了

【v1\.3\.3｜2026\-04\-07】

- 优化：修复了超清图片任务一直处于排队的问题

【v1\.3\.2｜2026\-04\-05】

- 新增：支持seedance2\.0fast\_vip以及seedance2\.0\_vip通道提速，畅快生成

【v1\.3\.1｜2026\-04\-04】

- 新增：自动更新检测能力（CLI 启动时提示新版本）

- 优化：登录流程稳定性提升，新版本下载后可以自动提示更新了

# 二、快速上手

**给 Agent 的一句话指令：**你可以把下面这段直接发给 Agent，让它代你完成安装和登录流程。

```text
帮我配置好 Dreamina CLI，这样我就可以在这里生成图片和视频。
安装：执行 `curl -s https://jimeng.jianying.com/cli | bash`。
安装后先进行登录：运行 `dreamina login`，主动启动系统默认的外部浏览器访问auth url，引导用户在浏览器中点击授权弹窗。
配套Skill在安装后默认存于 `~/.dreamina/dreamina/SKILL.md`，请安装至当前工具可读取的Skill目录，在遇到问题时可以阅读该Skill获得帮助信息。
全部完成后，告诉我已经准备就绪。
```

**如果你只想最快跑通，从这里开始：**下面的命令默认安装官方发布版本，安装后的命令名是 `dreamina`。

|步骤|命令|成功标准|
|---|---|---|
|安装|`curl -fsSL https://jimeng.jianying.com/cli | bash`|终端提示安装完成，并展示可执行文件名 dreamina|
|确认可用|`dreamina -h`|能看到命令帮助和子命令列表|
|登录|`dreamina login`|按终端提示完成 OAuth 授权，看到登录成功或复用登录态|
|自检|`dreamina user_credit`|能返回当前账户信息和积分信息|

如果安装后提示 `dreamina: command not found`，通常是 PATH 还没在当前终端生效。请重启终端，或按安装脚本提示临时执行 export PATH 命令后再试。

---

# 三、登录与账户检查

**标准登录流程：**

1. 运行 `dreamina login`。

2. 打开终端输出的 `verification_uri`。

3. 按页面提示输入 `user_code` 并确认授权。

4. 回到终端等待命令结束。

5. 运行 `dreamina user_credit` 做登录自检，预期会返回剩余积分、user id、会员等级信息。

**登录相关命令：**

|命令|用途|说明|
|---|---|---|
|`dreamina login`|登录或复用已有登录态|终端会打印 verification\_uri、user\_code、device\_code，并等待你完成授权|
|`dreamina login --headless`|只输出授权材料，不阻塞等待|适合 Agent 或无交互环境；随后用 checklogin 查询授权结果|
|`dreamina login checklogin --device_code=设备码 --poll=30`|检查 headless 登录是否完成|poll 是最多等待秒数；0 表示只检查一次|
|`dreamina relogin`|清除本地 OAuth 登录态并重新登录|切换账号时使用|
|`dreamina logout`|退出登录|只清除本地 OAuth 登录态，不删除任务记录和配置文件|

---

# 四、常用生成命令

生成命令通常会先提交任务。如果加上 `--poll=30`，CLI 会提交后最多等待 30 秒查询结果；如果超时仍未完成，会返回 querying 状态和 submit\_id，你可以稍后用 query\_result 查询。

**提交前请先确认：**生成任务可能消耗积分；命令里的本地图片、视频、音频路径必须是当前机器能访问到的文件路径；不确定参数时优先运行 `dreamina 子命令 -h`。

|任务|命令|关键参数|
|---|---|---|
|文生图|`text2image`|`--prompt`、`--ratio`、`--resolution_type`、`--model_version`、`--generate_num`|
|图生图|`image2image`|`--images`、`--prompt`、`--ratio`、`--resolution_type`、`--generate_num`|
|文生视频|`text2video`|`--prompt`、`--duration`、`--ratio`、`--video_resolution`|
|图生视频|`image2video`|`--image`、`--prompt`、`--duration`、`--video_resolution`|
|首尾帧视频|`frames2video`|`--first`、`--last`、`--prompt`、`--duration`、`--video_resolution`、`--model_version`|
|多帧视频|`multiframe2video`|`--images`、`--prompt`；3 张以上图片可使用 transition 参数|
|全能参考视频|`multimodal2video`|`--image`、`--video`、`--audio`、`--model_version`|
|图片超清|`image_upscale`|`--image`、`--resolution_type`；4k 和 8k 需要 VIP|

## 图片生成示例

```bash
dreamina text2image --prompt="一只戴墨镜的橘猫" --ratio=1:1 --resolution_type=2k --poll=30
```

```bash
dreamina image2image --images ./input.png --prompt="改成水彩风格" --resolution_type=2k --poll=30
```

## 视频生成示例

```bash
dreamina text2video --prompt="镜头推进，一只橘猫从沙发上跳下来" --duration=5 --ratio=16:9 --video_resolution=720p --poll=30
```

```bash
dreamina image2video --image ./first_frame.png --prompt="镜头慢慢推近" --duration=5 --poll=30
```

```bash
dreamina frames2video --first ./start.png --last ./end.png --prompt="从春天过渡到冬天" --duration=5 --poll=30
```

```bash
dreamina multiframe2video --images ./a.png,./b.png --prompt="角色从白天走到夜晚" --duration=3 --poll=30
```

```bash
dreamina multimodal2video --image ./input.png --audio ./music.mp3 --prompt="生成一段电影感短片" --model_version=seedance2.0fast --duration=5 --poll=30
```

## 查询和下载结果

```bash
dreamina query_result --submit_id=你的_submit_id
```

```bash
dreamina query_result --submit_id=你的_submit_id --download_dir=./downloads
```

```bash
dreamina list_task --gen_status=success
```

---

# 五、Session：把生成任务放进不同会话

Session 可以理解为生成任务所属的工作空间。默认 session 是 0；如果你要按项目隔离任务，可以创建新的 session，并在生成命令里传 `--session`。

|目标|命令|
|---|---|
|创建 session|`dreamina session create "项目名"`|
|查看最近 session|`dreamina session list`|
|搜索 session|`dreamina session search "关键词"`|
|重命名 session|`dreamina session rename session_id "新名字"`|
|删除 session|`dreamina session delete session_id`|

```bash
dreamina text2image --session=123456 --prompt="一张产品海报" --ratio=16:9 --poll=30
```

---

# 七、本地文件和日志

CLI 会在用户主目录下维护一些本地文件，用于任务记录、日志、版本检查和 Agent skill。

查看当前 CLI 版本和构建信息，可以运行 `dreamina version`；反馈问题时建议一并提供该版本信息。

|路径|说明|
|---|---|
|`~/.dreamina_cli/tasks.db`|本地任务记录数据库，用于 list\_task 和后续查询|
|`~/.dreamina_cli/logs/`|运行日志目录；排查问题时优先提供这里的相关日志|
|`~/.dreamina_cli/version.json`|安装脚本写入的版本信息，用于更新检测|
|`~/.dreamina_cli/dreamina/SKILL.md`|安装脚本下载的 Agent 使用说明|

不要把日志、配置或终端输出中的敏感信息随意发到公开渠道。排查问题时，请优先提供执行命令、错误描述、CLI 版本和相关日志片段。

---

# 八、常见问题

## 1\. 安装后找不到 dreamina 命令

- 重新打开终端后再运行 `dreamina -h`。

- 如果安装脚本提示了临时 PATH 命令，先执行那条 export PATH 命令。

- 仍失败时，重新执行安装命令。

## 2\. 登录提示“非法应用”

有用户报告在使用即梦CLI登录时提示“非法应用”，之后排查问题解决过程如下，如果有同样问题的用户请参考：

- 该用户使用claude code进行登录操作，claude code依照正常的操作顺序启动即梦CLI程序，并通过 `dreamina login` 命令打印登录url提供给用户

- 用户在浏览器中打开该登录url，页面显示错误提示“非法应用”（json格式）

- 该用户之后采用手动操作，该问题解决。以下是完整手动操作流程：

    1. 首先需要登录即梦web端

    2. 然后在即梦CLI中输入`dreamina login`命令，此时CLI会打印登录链接

    3. 在浏览器里访问这个链接进入授权页

    4. 在授权页里点击“授权”，此时CLI获得web端的登录状态，完成登录，

    5. 此时可关闭授权页，之后CLI可以通过claude code/codex等Agent正常使用

    6. 也就是说：不要通过Agent完成登录，应使用手动完成登录

⚠️ 经过分析发现：claude code所启动的即梦CLI，通过 `dreamina login` 命令打印出来的登录url是错误的，因此导致登录失败，目前还未查明原因。

## 3\. 生成命令提示未登录或无权限

- 先运行 `dreamina user_credit`。如果这个命令失败，先处理登录或账号权限问题。

- 如果返回 `AigcComplianceConfirmationRequired`，请先去即梦 Web 端完成对应授权确认，再重试 CLI 命令。

## 4\. 任务长时间没有结果

- 提交命令建议带上 `--poll=30`，先等待一段时间。

- 如果返回 querying，请保存 submit\_id，稍后运行 `dreamina query_result --submit_id=你的_submit_id`。

- 可以用 `dreamina list_task` 查看最近保存的任务。

## 5\. 如何反馈问题

请一次性提供下面信息，研发同学会更容易定位。

- 你执行的完整命令

- 终端返回的错误信息

- CLI 版本：运行 `dreamina version`

- 相关日志：位于 `~/.dreamina_cli/logs/`

- 如果是生成任务，请附上 submit\_id

---

# 九、推荐工作流

新用户建议先按最小闭环跑通，再逐步尝试复杂能力。

1. 安装并确认 `dreamina -h` 可用。

2. 执行 `dreamina login` 完成登录。

3. 执行 `dreamina user_credit` 确认账户可用。

4. 先跑一个低成本的 `text2image` 测试。

5. 保存返回的 submit\_id，并用 `query_result` 查询或下载。

6. 确认流程稳定后，再尝试视频、多帧、全能参考或批量任务。

如果你已经能完成安装、登录、user\_credit、一次 text2image 和一次 query\_result，说明即梦 CLI 的基础链路已经跑通，可以开始让 Agent 接入更复杂的创作流程了。



> (Note: Contains AI-generated content from Doubao Work.)
