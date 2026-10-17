# assets 目录说明

本目录用于存放 Skill 的静态资源（如图标、说明图）。

当前版本无需静态图片资源：
- 知识依赖图与题型地图由 `scripts/render_graphs.py` 动态生成到 `output/assets/`。
- PDF 内嵌字体由 `scripts/common.py` 自动从系统字体目录探测。

如需新增资源，请在此目录放置文件并在 README 中登记用途。
