# 错题诊断与变式训练 Skill（Smart Wrong Question Analysis）

分步交互式错题诊断与变式训练 Skill：按 Step0→Step5 逐题、逐知识点引导用户定位错因、
完成费曼式输出考查、通过四级变式题闯关（连续答对 3 道或触发熔断才推进），
最终结合考试大纲加权输出 .md + PDF 诊断报告。

项目同时保留一套批处理脚本，作为**辅助工具**按需调用（见下文）。

## 主流程（由 SKILL.md 定义，v3.0.0）

| Step | 内容 |
| --- | --- |
| Step0 | 诚实性入口：无作答且无法说清错因时，A 补充作答 / B 按一般性易错点处理 / C 直接出简版报告 |
| Step1 | 错因确认（五类）+ 信心度采集（0–100%）+ 是/否门控 |
| Step2 | 费曼式文字输出考查，按五类错因判据逐条核对，缺哪条补哪条 |
| Step3 | 原题 + 四级变式题（L1 数值变 / L2 结构变 / L3 问题变 / L4 场景变），连续答对 3 道或熔断才推进 |
| Step4 | 强制输出：解题方法 + 降维解释 + 错题总结 + 元认知归因 |
| Step5 | 采集考试大纲 → 知识点权重标注（核心/常考/了解/超纲/未定）→ 完整报告 .md + PDF |

内置机制：

- **知识诅咒清除**：AI 输出前执行术语审计、“显然”检测、前置条件清点、具体化检验；每轮未解释学科术语不超过 3 个。
- **元认知修正**：事前信心度（Step1）→ 事中每题预估概率（Step3）→ 事后校准摘要（Step5 报告）。
- **变式题多样性**：不得连续两道同层级；前 3 道覆盖至少 2 种层级；序列至少 1 道 L3/L4；连对 2 道 L1 强制升级。
- **熔断机制**：累计 8 道或连错 3 道时停止刷题，先系统讲解再要求复述（最多 2 轮），该知识点标记「未通关（熔断）」。
- **大纲加权**：Step5 采集目标考试大纲，薄弱点按“权重 × 错因严重度”排序。

触发方式：用户上传错题图片/文字，或要求分析错因、生成变式题、输出错题报告。

状态落盘 `output/state.json`，支持跨会话断点恢复（继续 / 重开）。

## 辅助工具：批处理脚本（已退出主流程）

`scripts/` 下的脚本不再作为 Skill 主入口，仅作辅助：

- `generate_pdf.py`：主流程 Step5 调用的 Markdown → PDF 转换（reportlab，中文字体，A4 + 目录 + 页码）。
- 其余脚本（`extract_document` / `normalize_questions` / `analyze_knowledge` /
  `calculate_priority` / `build_study_plan` / `render_graphs` / `generate_markdown`）：
  批处理模式保留，可单独运行，用于主流程之外的知识点库交叉校验、优先级评分、
  复习计划排期、知识依赖图与题型地图等场景。

单独运行完整批处理流水线：

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

各脚本均支持 `--help` 查看参数；分步执行命令见各脚本头部注释。

## 环境要求

- Python >= 3.10
- 依赖：PyYAML、Jinja2、matplotlib、reportlab、pypdf（见 requirements.txt）
- 中文字体（PDF 与图表需要，至少存在一种）：
  - Windows：微软雅黑 `msyh.ttc` / 黑体 `simhei.ttf` / 宋体 `simsun.ttc`
  - Linux：Noto Sans CJK / WenQuanYi 等
  - macOS：PingFang / STHeiti
- 可选：本地 OCR（tesseract）——未安装时降级为 Agent 视觉识别或请求用户补充
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

仅更新 Skill 定义（主流程变更时）：

```bash
cp smart-wrong-question/SKILL.md ~/.workbuddy/skills/smart-wrong-question/SKILL.md
```

## 输入格式（批处理工具）

交互主流程直接接收图片/文字，无需预先结构化。以下格式供批处理脚本使用：

### 方式 A：错题 Markdown（示例见 `examples/sample_questions.md`）

```markdown
# 数学错题集（示例数据）

## Q001
题干：已知函数 f(x) = x^3 - 3x，求函数的极值。
标准答案：极小值为 -2（x = 1），极大值为 2（x = -1）。
学生答案：极小值为 -2，极大值为 2。
学生解题过程：求导得 f'(x) = 3x^2 - 3，令 f'(x) = 0 得 x = ±1，代入得极值。
```

### 方式 B：题目 JSON

结构见 `output/questions.json` 示例（由 `normalize_questions.py` 生成）。缺失字段用 `null` 或 `[]`。

### 方式 C：普通 PDF

直接传入 `.pdf`，脚本自动提取文本层；无文本层时由 Agent 视觉识别补全。

## 知识点库

`knowledge/sample_knowledge_base.json` 为高中数学示例库，包含：

- 知识点 id / 名称 / 学科 / 关键词 / 先修知识点 / 常见题型 / 常见错误。
- 新增学科：复制一份 JSON，按同样结构扩展，无需改代码。

交互主流程的知识点识别由 Agent 语义完成；知识点库用于批处理脚本的关键词匹配与交叉校验。

## 优先级模型（批处理侧，可解释）

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
├── SKILL.md                  # Skill 入口：交互主流程规范（v3.0.0，Step0→Step5）
├── README.md
├── requirements.txt
├── config/default.yaml       # 默认参数、权重、路径
├── scripts/                  # 辅助脚本：generate_pdf.py（主流程复用）+ 批处理流水线
├── templates/                # Jinja2 报告模板（批处理侧）
├── knowledge/                # 知识点库
├── assets/                   # 静态资源说明
├── tests/                    # pytest 测试
├── examples/                 # 示例输入（测试数据）
└── output/                   # 交付产物 + state.json（交互流程状态）
```

## 已知限制

- 交互主流程不产出复习计划与知识依赖图；需要时单独运行批处理脚本。
- 本地 OCR 不可用时，图片/扫描件依赖 Agent 视觉能力。
- PDF 公式渲染为 LaTeX 源码文本，不做排版级数学公式渲染。
- 知识点匹配（批处理侧）基于关键词规则，冷门表述可能需要扩展知识库。
- 掌握程度需真实测评，本 Skill 的输出不替代测评。

## 许可

MIT
