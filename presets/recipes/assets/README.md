# 配方素材

配方里 `refs:` / `video_refs:` 声明 `from: asset` 时，素材文件放在这里。

- **文件名单独一层**（`presets/recipes/assets/<文件名>`），**禁路径穿越** —— 配方里写
  `asset: 某图.png`，不能写 `asset: ../../secret.png`。
- 图片后缀由配方解析期校验，非法后缀直接 400。
- 配方步内按 `label` 引用本目录素材，label 全局唯一。

不放预设、不放主体档案 —— 这里只放「配方自己带的素材文件」。
