# workflows 目录

放置 ComfyUI「导出 (API)」的 workflow 与同名 mapping 配置，成对使用：

```
Q21Charvar.json            ← ComfyUI 导出 (API) 的 workflow JSON
Q21Charvar.mapping.json    ← 字段映射（格式同 presets/comfy_mapping.example.json）
```

- `charvar gen --format comfyui --workflow Q21Charvar` 会自动在这两个文件间配对；
- 节点 id 以你机器上导出的 workflow 为准，不要照抄示例；
- mapping 里 `field=prompt` 必填，指向正向文本编码节点的 text 输入。

当前已内置一对：`image_qwen_image_2_1_turbo_image_edit_easy_api.json/.mapping.json`，正/负向提示词注入到 TextEncodeQwenImage21，输出文件前缀注入到 SaveImageAdvanced。


当前内置两对：

- `image_qwen_image_2_1_turbo_t2i_easy_api.json/.mapping.json` —— 纯文生图，不接参考图；mapping 注入 prompt / negative_prompt / width / height / seed / filename_prefix。
- `image_qwen_image_2_1_turbo_image_edit_easy_api.json/.mapping.json` —— 图像编辑，接 `LoadImage` 参考图；mapping 注入 prompt / negative_prompt / 宽高 / seed / filename_prefix。
