# {{prompt_id}}｜提示词记录

> 可选的深度记录。正式交付的主文档不依赖本目录存在。

- 项目／分支／阶段：
- 使用入口：{{模式 A 固定角色延续／模式 B 用户照片扮演}}
- 父版本：
- 日期：
- 类型：submitted_prompt（原文）／compiled_prompt（整理版）。
- 整理来源与变化（适用时）：
- 目标平台、模型与版本：未提供则明确写未提供。
- 平台字符上限：即梦 2000 字符为当前项目默认值；其他平台未提供则明确写未提供。
- 提示词语言：中文／英文（跟随用户沟通语言）。
- 参考资产ID及各自职责：
- 是否为完整提交稿：{{是（可直接提交）／否（内部组件，不可直接提交）}}
- 输出尺寸／参数／种子：未提供则明确写未提供。
- 总字符预算：
- 实际字符数与统计口径：

## 精确提示词

```text template
{{保留提交原文。不要在测试后直接润色此段，修订另建版本。}}
```

{{若本条是内部组件而非完整提交稿，改为「## 内部组件」小节，用 ```text component 标注并写明「不可直接提交」。}}

## 反馈与证据

四项分别记录，不互相推导：

| 字段 | 值 |
|---|---|
| execution_status | unknown／not_run／user_reported_run |
| user_approval | unknown／proposal／result／partial／rejected |
| assistant_image_review | not_seen／reviewed |
| batch_validation | not_tested／recorded |

- 用户反馈原话：
- 反馈范围：本轮认可的目标或局部。
- 旧标签显示摘要：proposed／user_reported／image_reviewed／batch_tested，可多选。
- 结果资产ID或实际位置：没有则写「用户侧已生成，尚未归档」。
- 图像检查内容：没有看到图则写未检查。
- 实际运行数量／可用数量：未知则写未提供。
- 已观察偏差：
- 原因假设（非结论）：
- 下轮仅修改：

## 状态变更

{{例如：本条由 compiled_prompt 升为用户报告已运行并获认可，原文未修改。}}
