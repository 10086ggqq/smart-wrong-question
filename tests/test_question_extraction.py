"""场景 A / B / C / H 测试：题目提取与结构化。

- 场景 A：纯文本错题 → 正确提取题目、保留答案与解题过程；
- 场景 B：带 LaTeX 公式 → 公式被保留；
- 场景 C：PDF 错题 → 题目边界、编号保留、文本提取失败（扫描件）处理；
- 场景 H：空文件 / 损坏文件 → 拒绝无效输入、不崩溃。
"""
from __future__ import annotations

import json

import pytest
from conftest import make_blank_pdf, make_pdf

from common import InputError, read_json
from extract_document import extract_document
from normalize_questions import normalize_questions


class TestScenarioA:
    """场景 A：纯文本错题。"""

    def test_extract_questions(self, sample_md_file):
        result = extract_document(sample_md_file)
        assert result["method"] == "direct-read"
        assert result["next_step"] == "normalize"
        data = normalize_questions(result["text"], result["source_file"])
        assert data["meta"]["question_count"] == 2
        assert data["meta"]["subject"] == "数学"

    def test_answers_and_solutions_preserved(self, sample_md_file):
        result = extract_document(sample_md_file)
        data = normalize_questions(result["text"], result["source_file"])
        q1 = data["questions"][0]
        assert q1["question_id"] == "Q001"
        assert "极值" in q1["question_text"]
        assert q1["standard_answer"] and "极大值" in q1["standard_answer"]
        assert q1["student_answer"] and "极大值" in q1["student_answer"]
        assert q1["student_solution"] and "导数符号" in q1["student_solution"]

    def test_full_pipeline_generates_report(self, sample_md_file, tmp_path):
        from run_analysis import run_pipeline

        out = tmp_path / "out"
        result = run_pipeline(sample_md_file, out, days=7, daily_minutes=60)
        assert result["ok"], result
        assert (out / "analysis.md").exists()
        assert (out / "analysis.pdf").exists()
        assert (out / "analysis-data.json").exists()
        assert (out / "assets" / "knowledge-graph.png").exists()
        assert result["validation"]["summary"]["failed"] == 0


class TestScenarioB:
    """场景 B：带 LaTeX 公式的题目。"""

    def test_latex_preserved(self, sample_md_formula):
        result = extract_document(sample_md_formula)
        data = normalize_questions(result["text"], result["source_file"])
        q1 = data["questions"][0]
        assert q1["latex"], "公式未被提取"
        assert any("3x^2 - 3" in f for f in q1["latex"]), f"公式内容不完整：{q1['latex']}"

    def test_formula_in_markdown_report(self, sample_md_formula, tmp_path):
        from run_analysis import run_pipeline

        out = tmp_path / "out"
        run_pipeline(sample_md_formula, out, days=7, daily_minutes=30)
        md = (out / "analysis.md").read_text(encoding="utf-8")
        assert "3x^2 - 3" in md, "Markdown 报告中公式丢失"

    def test_formula_error_marked(self, sample_md_formula):
        """公式使用错误应可被标记（学生求导得到 3x^2，漏掉 -3）。"""
        result = extract_document(sample_md_formula)
        data = normalize_questions(result["text"], result["source_file"])
        q1 = data["questions"][0]
        # 学生答案与标准答案公式不同 → 结构化数据中可对比
        assert "3x^2" in q1["student_answer"]
        assert "3x^2 - 3" in q1["standard_answer"]
        # 未经 Agent 语义分析时，不得假装已定位错误
        ea = q1["error_analysis"]
        assert ea.get("observed_error") is None


class TestScenarioC:
    """场景 C：PDF 错题。"""

    def test_pdf_text_layer(self, tmp_path):
        pdf = make_pdf(tmp_path / "q.pdf", [
            "1. 已知函数 f(x) = x^2，求导数。",
            "2. 求 sin(30) 的值。",
        ])
        result = extract_document(pdf)
        assert result["method"] == "pdf-text-layer"
        assert result["page_count"] == 2
        data = normalize_questions(result["text"], result["source_file"])
        assert data["meta"]["question_count"] == 2

    def test_scanned_pdf_needs_vision(self, tmp_path):
        """无文本层 PDF → 标记需要视觉识别，不假装成功。"""
        pdf = make_blank_pdf(tmp_path / "scan.pdf")
        result = extract_document(pdf)
        assert result["method"] == "needs-vision"
        assert result["next_step"] == "vision-recognition"
        assert any("扫描" in i or "视觉" in i for i in result["issues"])

    def test_question_numbers_preserved(self, tmp_path):
        pdf = make_pdf(tmp_path / "q.pdf", ["1. 第一题内容。\n2. 第二题内容。\n3. 第三题内容。"])
        result = extract_document(pdf)
        data = normalize_questions(result["text"], result["source_file"])
        ids = [q["question_id"] for q in data["questions"]]
        assert len(ids) == 3


class TestScenarioH:
    """场景 H：空文件和损坏文件。"""

    def test_empty_file_rejected(self, tmp_path):
        p = tmp_path / "empty.md"
        p.write_text("", encoding="utf-8")
        with pytest.raises(InputError, match="空文件|没有.*内容|无效"):
            extract_document(p)

    def test_zero_byte_file_rejected(self, tmp_path):
        p = tmp_path / "zero.pdf"
        p.write_bytes(b"")
        with pytest.raises(InputError):
            extract_document(p)

    def test_corrupt_pdf_rejected(self, tmp_path):
        p = tmp_path / "corrupt.pdf"
        p.write_bytes(b"%PDF-1.4 this is not a real pdf content" * 10)
        with pytest.raises(InputError):
            extract_document(p)

    def test_unsupported_format_rejected(self, tmp_path):
        p = tmp_path / "data.xyz"
        p.write_text("some content", encoding="utf-8")
        with pytest.raises(InputError, match="不支持"):
            extract_document(p)

    def test_no_question_content_raises(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("今天天气很好，没有题目。", encoding="utf-8")
        result = extract_document(p)
        with pytest.raises(Exception, match="未能.*识别|题目"):
            normalize_questions(result["text"], result["source_file"])
