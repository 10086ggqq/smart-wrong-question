# 智能错题分析 Skill（Smart Wrong Question Analysis）

帮助用户分析电子错题、建立知识点依赖关系、生成题型地图、制定个性化复习计划，并导出 Markdown 与 PDF 报告的 AI Agent Skill。

## 功能特性

- **多格式输入**：PNG / JPG / JPEG / WEBP 图片、普通与扫描 PDF、TXT、Markdown。
- **结构化题目**：统一 JSON 结构保存题干、公式、选项、标准答案、学生答案与解题过程。
- **知识点识别**：基于可扩展知识点库（JSON）的关键词与规则匹配，主次考点分明，附证据与置信度。
- **错因分析**：八类错因分类，严格区分「可观察错误 / 推测原因 / 待确认」。
- **可解释优先级模型**：规则加权评分，所有指标标注来源（实际观察值 / 估算值 / 缺失）。
- **知识依赖图 & 题型地图**：matplotlib 渲染 SVG + PNG，含图例、层级与颜色含义。
- **7/14 天复习计划**：时间预算硬约束，超预算自动裁剪并说明，每天任务具体可检查。
- **双格式报告**：Markdown（Jinja2 模板）+ PDF（reportlab，中文字体）。
- **诚实性保证**：信息不足时明确标记「待确认 / 暂无法判断」，不编造学生错因或掌握率。
- **输出校验**：自动检查文件存在性、可打开性、章节完整性与时间预算。

## 环境要求

- Python >= 3.10
- 依赖：PyYAML、Jinja2、matplotlib、reportlab、pypdf（见 requirements.txt）
- 中文字体（PDF 与图表需要，至少存在一种）：
  - Windows：微软雅黑 `msyh.ttc` / 黑体 `simhei.ttf` / 宋体 `simsun.ttc`
  - Linux：Noto Sans CJK / WenQuanYi 等
  - macOS：PingFang / STHeiti
- 可选：本地 OCR（tesseract）——未安装时自动降级为 Agent 视觉识别或请求用户补充
- 可选：第三方 OCR（如 Mathpix）——需用户明确授权，不默认启用

## 安装

```bash
cd smart-wrong-question
pip install -r requirements.txt
```

作为 WorkBuddy Skill 安装（用户级）：

```bash
# 将整个 smart-wrong-question 目录复制到用户 skills 目录
cp -r smart-wrong-question ~/.workbuddy/skills/
```

## 快速开始

### 1. 完整流水线（推荐）

```bash
python scripts/run_analysis.py \
  --input examples/sample_questions.md \
  --output output/ \
  --days 7 \
  --daily-minutes 60
```

运行后在 `output/` 得到：

```
output/
├── analysis.md            # Markdown 报告
├── analysis.pdf           # PDF 报告
├── analysis.html          # HTML 预览版（浏览器直接打开）
├── analysis-data.json     # 全部结构化数据（题目/知识点/优先级/计划）
├── questions.json         # 中间产物：结构化题目
├── priority.json          # 中间产物：优先级评分
├── study_plan.json        # 中间产物：复习计划
└── assets/
    ├── knowledge-graph.svg / .png   # 知识依赖图
    └── topic-map.svg / .png         # 题型地图
```

### 2. 分步执行

```bash
# 步骤 1：提取文本（PDF/TXT/MD）
python scripts/extract_document.py --input example.pdf --output output/raw_text.txt

# 步骤 2：结构化题目
python scripts/normalize_questions.py --input output/raw_text.txt --output output/questions.json

# 步骤 3：知识点分析（Agent 语义分析结果可先合并进 questions.json）
python scripts/analyze_knowledge.py --input output/questions.json --output output/questions.json

# 步骤 4：优先级评分
python scripts/calculate_priority.py --input output/questions.json --output output/priority.json

# 步骤 5：复习计划
python scripts/build_study_plan.py --input output/questions.json --output output/study_plan.json --days 7 --daily-minutes 60

# 步骤 6：图表
python scripts/render_graphs.py --input output/questions.json --output output/assets/

# 步骤 7：Markdown 报告（加 --html 同时生成 HTML 预览版）
python scripts/generate_markdown.py --input output/questions.json --output output/analysis.md --html

# 步骤 8：PDF 报告
python scripts/generate_pdf.py --input output/analysis.md --output output/analysis.pdf

# 步骤 9：验证
python scripts/validate_output.py --output-dir output/
```

各脚本均支持 `--help` 查看参数。

## 输入格式

### 方式 A：错题 Markdown（推荐，示例见 `examples/sample_questions.md`）

```markdown
# 数学错题集（示例数据）

## Q001
题干：已知函数 f(x) = x^3 - 3x，求函数的极值。
标准答案：极小值为 -2（x = 1），极大值为 2（x = -1）。
学生答案：极小值为 -2，极大值为 2。
学生解题过程：求导得 f'(x) = 3x^2 - 3，令 f'(x) = 0 得 x = ±1，代入得极值。
```

### 方式 B：题目 JSON（Agent 视觉识别图片/扫描 PDF 后落盘）

结构见 `SKILL.md` 第 5 节。缺失字段用 `null` 或 `[]`。

### 方式 C：普通 PDF

直接传入 `.pdf`，脚本自动提取文本层；无文本层时由 Agent 视觉识别补全。

## 知识点库

`knowledge/sample_knowledge_base.json` 为高中数学示例库，包含：

- 知识点 id / 名称 / 学科 / 关键词 / 先修知识点 / 常见题型 / 常见错误。
- 新增学科：复制一份 JSON，按同样结构扩展，无需改代码。

## 优先级模型（可解释）

```
priority = w1·错题频率 + w2·近期重复 + w3·错因严重度 + w4·先修影响 + w5·复测差距
```

- 权重在 `config/default.yaml` 中可调。
- 每个输入指标标注来源：`observed`（实际记录）/ `estimated`（规则估算）/ `missing`（缺失）。
- 无复测数据时掌握程度标记为「未知」，不给精确分值。
- 先修影响区分「被依赖（前置重要性）」与「依赖他人（后续风险）」，不使用简单出度。

## 测试

```bash
python -m pytest tests/ -v
```

覆盖场景：纯文本错题、含公式题目、PDF 错题、缺少学生解题过程、复习时间不足、知识点依赖缺口、PDF 生成失败、空文件/损坏文件，共 43 项。

最近一次执行结果：**43 passed / 0 failed**（详见 `tests/TEST-REPORT.md`，含环境明细与已知限制）。

## 目录结构

```
smart-wrong-question/
├── SKILL.md                  # Skill 入口与 Agent 调用规范
├── README.md
├── requirements.txt
├── config/default.yaml       # 默认参数、权重、路径
├── scripts/                  # 9 个核心脚本 + run_analysis.py 编排入口 + common.py
├── templates/                # report.md.j2 / report.html.j2
├── knowledge/                # 知识点库
├── assets/                   # 静态资源说明
├── tests/                    # pytest 测试
├── examples/                 # 示例输入（测试数据）
└── output/                   # 交付产物
```

## 已知限制

- 本地 OCR 不可用时，扫描 PDF 依赖 Agent 视觉能力。
- PDF 公式渲染为 LaTeX 源码文本，不做排版级数学公式渲染。
- 知识点匹配基于关键词规则，冷门表述可能需要扩展知识库。
- 掌握程度估算为保守规则值，已明确标记，不可替代真实测评。

## 许可

MIT
