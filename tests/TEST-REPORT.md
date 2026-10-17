# 测试报告（Test Report）

> 本报告记录真实执行的测试及结果，未使用虚构数据。
> 执行命令：`python -m pytest tests/ -v`（项目根目录）

## 测试环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | Windows 11（win32） |
| Python | 3.13.14（venv：`~/.workbuddy/binaries/python/envs/default`） |
| pytest | 9.1.1 |
| matplotlib | 已安装（图表渲染） |
| reportlab | 5.0.1（PDF 生成） |
| pypdf | 6.21.1（PDF 文本提取/校验） |
| 中文字体 | 微软雅黑 / 黑体 / 宋体（系统字体，可用） |
| Graphviz | **未安装**（已用 matplotlib 替代，功能不受影响） |
| tesseract OCR | **未安装**（扫描 PDF 走 Agent 视觉识别降级路径） |
| Pandoc / Chromium | **未安装**（PDF 单一方案为 reportlab） |

## 总体结果

```
43 passed, 0 failed, 0 skipped, 0 xfail （耗时约 44 秒）
```

## 分类明细

### 场景 A：纯文本错题（4 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_extract_questions（识别题目数量与学科） | ✅ |
| test_answers_and_solutions_preserved（答案/解题过程保留） | ✅ |
| test_full_pipeline_generates_report（完整报告生成） | ✅ |

### 场景 B：带公式题目（3 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_latex_preserved（LaTeX 公式保留） | ✅ |
| test_formula_in_markdown_report（公式出现在 Markdown 报告） | ✅ |
| test_formula_error_marked（公式错误可对比，未分析时不伪装定位） | ✅ |

### 场景 C：PDF 错题（3 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_pdf_text_layer（文本层提取） | ✅ |
| test_scanned_pdf_needs_vision（无文本层 → 标记需视觉识别） | ✅ |
| test_question_numbers_preserved（题号保留） | ✅ |

### 场景 D：缺少学生解题过程（4 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_no_fabricated_error_cause（不编造学生错因） | ✅ |
| test_general_risk_noted_as_general（一般性易错点明确标注） | ✅ |
| test_markdown_marks_insufficient_info（报告标注信息不足） | ✅ |
| test_mastery_unknown_not_faked（掌握程度不伪装精确值） | ✅ |

### 场景 E：复习时间不足（5 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_daily_budget_never_exceeded（每日不超时） | ✅ |
| test_insufficient_time_documented（未排入任务被记录并说明） | ✅ |
| test_high_priority_scheduled_first（高优先级先安排） | ✅ |
| test_tasks_are_actionable（任务含完成标准与复盘方式） | ✅ |
| test_every_day_has_checkable_result（每天有可检查目标） | ✅ |

### 场景 F：知识点依赖缺口（3 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_prerequisite_scheduled_before_dependent（先修先于后续） | ✅ |
| test_unverified_dependency_marked（依赖关系可标记核验状态） | ✅ |
| test_missing_prerequisite_gap_raises_priority（前置缺口提升优先级） | ✅ |

### 场景 G：PDF 生成失败（4 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_missing_font_clear_error（字体缺失 → 清晰错误） | ✅ |
| test_markdown_preserved_on_pdf_failure（Markdown 保留，失败步骤如实记录） | ✅ |
| test_pipeline_ok_without_pdf_when_font_missing（不虚假报告成功） | ✅ |
| test_pdf_generation_success_when_font_present（字体可用时 PDF 成功） | ✅ |

### 场景 H：空文件与损坏文件（5 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_empty_file_rejected（空文本文件） | ✅ |
| test_zero_byte_file_rejected（0 字节文件） | ✅ |
| test_corrupt_pdf_rejected（损坏 PDF） | ✅ |
| test_unsupported_format_rejected（不支持的格式） | ✅ |
| test_no_question_content_raises（无题目内容） | ✅ |

### 图表渲染（5 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_files_generated × 2（SVG+PNG 生成） | ✅ |
| test_svg_is_valid_xml（SVG 为有效 XML） | ✅ |
| test_no_font_raises_clear_error（无字体 → 清晰错误） | ✅ |
| test_no_knowledge_points_raises（无知识点不渲染空图） | ✅ |

### 优先级模型（7 项，全部通过）

| 测试 | 结果 |
| --- | --- |
| test_scores_bounded_and_sorted（分数有界且降序） | ✅ |
| test_metric_sources_marked（指标来源标注） | ✅ |
| test_no_retest_data_marks_mastery_unknown（无复测 → 掌握度未知） | ✅ |
| test_missing_error_cause_not_counted_as_observed（"暂无法判断"不计入观察值） | ✅ |
| test_observed_error_cause_counted（实质错因计入观察值） | ✅ |
| test_prerequisite_impact_uses_dependency_not_cooccurrence（先修影响来自依赖而非同现） | ✅ |
| test_wrong_frequency_observed（频率指标正确） | ✅ |
| test_empty_questions_handled（空输入不崩溃） | ✅ |

## 已知限制（未测试/无法测试的部分）

1. **本地 OCR 未安装**：扫描 PDF 的 OCR 路径未做真实端到端测试，仅验证了「标记需视觉识别」的降级分支。安装 tesseract 后需补充测试。
2. **Agent 语义识别未自动化**：图片题目的视觉识别由 Agent 完成，属于模型能力，无法用确定性测试覆盖；测试仅覆盖识别结果落盘后的下游流程。
3. **无外部 API 测试**：未配置任何第三方 OCR/LLM 凭证，相关代码路径未执行。
4. **PDF 成功路径依赖中文字体**：在无中文字体的环境会自动 skip（pytest.skip），不会误报失败。
5. **跨平台未验证**：本次仅在 Windows 11 执行；Linux/macOS 的字体路径已在代码中做候选探测，但未经实机测试。

## 复现方式

```bash
cd smart-wrong-question
pip install -r requirements.txt
python -m pytest tests/ -v
```
