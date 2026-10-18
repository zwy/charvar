"""角色变体提示词生成器（charvar）。

核心只生成提示词：主体档案 + 预设（list/matrix）+ 通配符 → 结构化变体列表。
输出可直接被 ComfyUI（Text Multiline 逐行 / API 排队）消费。
"""

__version__ = "0.1.0"
