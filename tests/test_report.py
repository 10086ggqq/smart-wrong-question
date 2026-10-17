"""报告生成测试：场景 D（缺少学生解题过程）与场景 G（PDF 生成失败）。

- 场景 D：没有学生作答数据时不得编造错因，须分析一般性易错点并标记信息不足；
- 场景 G：PDF 引擎/字体失败时给出清晰错误、保留 Markdown、不虚假报告成功。
"""
from __future__ import annotations

import json

import pytest

from common import PdfGenerationError, SkillError, read_json, write_json
from generate_pdf import generate_pdf
from run_analysis import run_pipeline


class TestScenarioD:
    """场景 D：缺少学生解题过程。"""

    def test_no_fabricated_error_cause(self, sample_md_no_student, tmp_path):
        from run_analysis import run_pipeline

        out = tmp_path / "out"
        run_pipeline(sample_md_no_student, out, days=7, daily_minutes=60)
        data = read_json(out / "questions.json")
        for q in data["questions"]:
            ea = q["error_analysis"]
            assert ea["observed_error"] is None, "没有学生作答数据时不得判断可观察错误"
            assert ea["needs_confirmation"] is True
            assert ea["note"], "必须明确标注信息不足"

    def test_general_risk_noted_as_general(self, sample_md_no_student, tmp_path):
        from run_analysis import run_pipeline

        out = tmp_path / "out"
        run_pipeline(sample_md_no_student, out, days=7, daily_minutes=60)
        data = read_json(out / "questions.json")
        note = data["questions"][0]["error_analysis"]["note"]
        assert "一般性" in note, "须说明仅为一般性易错点"

    def test_markdown_marks_insufficient_info(self, sample_md_no_student, tmp_path):
        from run_analysis import run_pipeline

        out = tmp_path / "out"
        run_pipeline(sample_md_no_student, out, days=7, daily_minutes=60)
        md = (out / "analysis.md").read_text(encoding="utf-8")
        assert "未提供学生作答数据" in md or "一般性" in md

    def test_mastery_unknown_not_faked(self, sample_md_no_student, tmp_path):
        from run_analysis import run_pipeline

        out = tmp_path / "out"
        run_pipeline(sample_md_no_student, out, days=7, daily_minutes=60)
        priority = read_json(out / "priority.json")
        for r in priority["knowledge_points"]:
            assert r["mastery"]["value"] is None
            assert r["mastery"]["status"] == "未知"


class TestScenarioG:
    """场景 G：PDF 生成失败。"""

    def test_missing_font_clear_error(self, sample_md_file, tmp_path, monkeypatch):
        """字体注册失败 → 清晰错误信息。"""
        import generate_pdf as gp
        monkeypatch.setattr(gp, "register_reportlab_cn_fonts", lambda: None)
        md = tmp_path / "analysis.md"
        md.write_text("# 测试报告\n\n## 一、章节\n\n正文内容。", encoding="utf-8")
        with pytest.raises(PdfGenerationError, match="中文字体"):
            gp.generate_pdf(md, tmp_path / "analysis.pdf")

    def test_markdown_preserved_on_pdf_failure(self, sample_md_file, tmp_path, monkeypatch):
        """PDF 失败时 Markdown 必须保留，且流水线如实记录失败步骤。"""
        import generate_pdf
        monkeypatch.setattr(generate_pdf, "register_reportlab_cn_fonts", lambda: None)
        out = tmp_path / "out"
        result = run_pipeline(sample_md_file, out, days=7, daily_minutes=60)
        # Markdown 必须存在
        assert (out / "analysis.md").exists()
        # PDF 步骤必须标记失败
        pdf_step = next(s for s in result["steps"] if s["step"] == "generate-pdf")
        assert pdf_step["ok"] is False
        assert "中文字体" in pdf_step["detail"]
        # 不得虚假报告整体成功
        assert result["ok"] is False

    def test_pipeline_ok_without_pdf_when_font_missing(self, sample_md_file, tmp_path, monkeypatch):
        """字体缺失时整体结果应为失败（有 error 级步骤失败）。"""
        import generate_pdf
        monkeypatch.setattr(generate_pdf, "register_reportlab_cn_fonts", lambda: None)
        out = tmp_path / "out"
        result = run_pipeline(sample_md_file, out, days=7, daily_minutes=60)
        failed = [s["step"] for s in result["steps"] if not s["ok"]]
        assert "generate-pdf" in failed

    def test_pdf_generation_success_when_font_present(self, sample_md_file, tmp_path,
                                                      chinese_font_available):
        if not chinese_font_available:
            pytest.skip("环境中无中文字体，跳过 PDF 成功路径测试")
        out = tmp_path / "out"
        result = run_pipeline(sample_md_file, out, days=7, daily_minutes=60)
        assert result["ok"]
        pdf = out / "analysis.pdf"
        assert pdf.exists() and pdf.stat().st_size > 5000
        from pypdf import PdfReader
        assert len(PdfReader(str(pdf)).pages) > 0
