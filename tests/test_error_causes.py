"""错因多选测试（v3.1.0）：解析、校验、展示、旧版兼容、流水线集成。

覆盖：
- 用户自由文本解析（单值/多值/各种分隔符/标签直填/全部/无/非法值）；
- 存储数据严格校验（validate_error_codes 拒绝越界与非整数）；
- 展示格式化（带/不带代码前缀、空值回退）；
- 旧版单选数据无损升级（error_code -> error_codes）；
- analyze_knowledge / calculate_priority / validate_output / 报告模板集成。
"""
from __future__ import annotations

import json

import pytest

from common import InputError, SkillError
from error_causes import (
    ALL_CODES,
    ERROR_CAUSES,
    cause_menu,
    error_cause_labels,
    format_error_causes,
    normalize_error_codes,
    parse_error_causes,
    primary_error_code,
    to_severity_labels,
    upgrade_legacy_error_cause,
    validate_error_codes,
)


# ---------------------------------------------------------------------------
# 1. 解析：用户自由文本 -> 代码列表
# ---------------------------------------------------------------------------

class TestParseErrorCauses:
    def test_single_int(self):
        assert parse_error_causes(3) == [3]

    def test_single_string(self):
        assert parse_error_causes("3") == [3]

    def test_comma_variants(self):
        for sep in (",", "，", "、", "+", "＋", " ", "和", "及", "与", "/"):
            assert parse_error_causes(f"1{sep}3") == [1, 3], f"分隔符 {sep!r} 解析失败"

    def test_mixed_separators(self):
        assert parse_error_causes("1, 3、2") == [1, 2, 3]

    def test_label_direct_input(self):
        assert parse_error_causes("概念不清+计算失误") == [1, 3]
        assert parse_error_causes("方法没掌握") == [2]

    def test_all_causes_token(self):
        for token in ("全部", "全选", "所有"):
            assert parse_error_causes(token) == ALL_CODES == [1, 2, 3, 4, 5]

    def test_none_tokens(self):
        for token in (None, "", "无", "没有", "未提供"):
            assert parse_error_causes(token) == []

    def test_dedupe_and_sort(self):
        assert parse_error_causes("3,1,3,1") == [1, 3]

    def test_prefix_stripped(self):
        assert parse_error_causes("错因1") == [1]
        assert parse_error_causes("原因 3") == [3]

    def test_float_integral(self):
        assert parse_error_causes(3.0) == [3]

    def test_bool_rejected(self):
        assert parse_error_causes(True) == []
        with pytest.raises(InputError):
            parse_error_causes(True, strict=True)

    def test_out_of_range_lenient_skips(self):
        assert parse_error_causes(9) == []
        assert parse_error_causes("1,9,3") == [1, 3]

    def test_out_of_range_strict_raises(self):
        with pytest.raises(InputError, match="1-5"):
            parse_error_causes(9, strict=True)
        with pytest.raises(InputError):
            parse_error_causes("1,x", strict=True)

    def test_nested_list(self):
        assert parse_error_causes([1, "2,3", None, 5]) == [1, 2, 3, 5]

    def test_five_way_combination(self):
        """五类任意组合（含全选）均可存储。"""
        assert parse_error_causes("1,2,3,4,5") == [1, 2, 3, 4, 5]
        assert parse_error_causes("2,3") == [2, 3]          # 计算失误 + 方法没掌握
        assert parse_error_causes("1,3") == [1, 3]          # 概念不清 + 计算失误
        assert parse_error_causes("5") == [5]               # 仅其他
        assert parse_error_causes("1,5") == [1, 5]          # 概念不清 + 其他


# ---------------------------------------------------------------------------
# 2. 校验：存储数据必须干净
# ---------------------------------------------------------------------------

class TestValidateErrorCodes:
    def test_valid_list(self):
        assert validate_error_codes([3, 1, 1]) == [1, 3]

    def test_empty_and_none(self):
        assert validate_error_codes([]) == []
        assert validate_error_codes(None) == []

    def test_reject_out_of_range(self):
        with pytest.raises(InputError, match="越界"):
            validate_error_codes([1, 9])

    def test_reject_non_integer(self):
        with pytest.raises(InputError, match="非整数"):
            validate_error_codes(["1"])
        with pytest.raises(InputError):
            validate_error_codes([True])

    def test_reject_non_list(self):
        with pytest.raises(InputError, match="列表"):
            validate_error_codes({"a": 1})

    def test_normalize_strict_default(self):
        with pytest.raises(InputError):
            normalize_error_codes([7])


# ---------------------------------------------------------------------------
# 3. 展示与回显
# ---------------------------------------------------------------------------

class TestDisplay:
    def test_format_empty(self):
        assert format_error_causes([]) == "未提供"
        assert format_error_causes(None) == "未提供"

    def test_format_multi(self):
        assert format_error_causes([1, 3]) == "概念不清 + 计算失误"
        assert format_error_causes([1, 3], with_code=True) == "[1,3] 概念不清 + 计算失误"

    def test_format_all_five(self):
        text = format_error_causes(ALL_CODES)
        for label in ERROR_CAUSES.values():
            assert label in text
        assert text.count("+") == 4

    def test_labels(self):
        assert error_cause_labels([2, 3]) == ["方法没掌握", "计算失误"]

    def test_primary_code(self):
        assert primary_error_code([1, 3]) == 1
        assert primary_error_code([]) is None

    def test_cause_menu(self):
        menu = cause_menu()
        for code, label in ERROR_CAUSES.items():
            assert f"{code} {label}" in menu

    def test_jinja_undefined_safe(self):
        """模板中缺失字段不得炸渲染（过滤器兜底为「未提供」）。"""
        from generate_markdown import _error_causes_filter
        assert _error_causes_filter(None) == "未提供"
        assert _error_causes_filter("not-a-list-but-str", with_code=True) == "未提供"


# ---------------------------------------------------------------------------
# 4. 旧版单选数据兼容
# ---------------------------------------------------------------------------

class TestLegacyCompat:
    def test_upgrade_single_code(self):
        record = {"error_code": 3}
        upgraded = upgrade_legacy_error_cause(record)
        assert upgraded["error_codes"] == [3]
        assert upgraded["error_code"] == 3

    def test_upgrade_does_not_override_new_field(self):
        record = {"error_code": 3, "error_codes": [1, 2]}
        upgraded = upgrade_legacy_error_cause(record)
        assert upgraded["error_codes"] == [1, 2]
        assert upgraded["error_code"] == 1  # 主错因刷新为列表第一个

    def test_upgrade_invalid_legacy_consistent(self):
        record = {"error_code": 9}
        upgraded = upgrade_legacy_error_cause(record)
        assert upgraded["error_codes"] == []
        assert upgraded["error_code"] is None  # 不残留越界值

    def test_upgrade_no_fields_untouched(self):
        record = {"other": 1}
        upgraded = upgrade_legacy_error_cause(record)
        assert "error_codes" not in upgraded
        assert "error_code" not in upgraded


# ---------------------------------------------------------------------------
# 5. analyze_knowledge 集成
# ---------------------------------------------------------------------------

def _question(ea=None, **kwargs):
    q = {"question_id": "Q001", "question_text": "x",
         "student_answer": "错", "student_solution": "过程"}
    q.update(kwargs)
    if ea is not None:
        q["error_analysis"] = ea
    return q


class TestAnalyzeKnowledgeIntegration:
    def test_multi_codes_normalized(self):
        from analyze_knowledge import validate_error_analysis
        ea = validate_error_analysis(_question({"error_codes": [3, 1]}))
        assert ea["error_codes"] == [1, 3]
        assert ea["error_code"] == 1

    def test_legacy_code_upgraded(self):
        from analyze_knowledge import validate_error_analysis
        ea = validate_error_analysis(_question({"error_code": 2}))
        assert ea["error_codes"] == [2]
        assert ea["error_code"] == 2

    def test_invalid_codes_noted(self):
        from analyze_knowledge import validate_error_analysis
        ea = validate_error_analysis(_question({"error_codes": [1, 9]}))
        assert ea["error_codes"] == [1]          # 越界值不静默吞掉
        assert "无法识别" in ea["note"]

    def test_absent_codes_empty(self):
        from analyze_knowledge import validate_error_analysis
        ea = validate_error_analysis(_question())
        assert ea["error_codes"] == []
        assert ea["error_code"] is None


# ---------------------------------------------------------------------------
# 6. calculate_priority 集成：多选严重度
# ---------------------------------------------------------------------------

def _priority_data(ea: dict) -> dict:
    return {
        "questions": [
            {"question_id": "Q001", "question_text": "x",
             "knowledge_points": [{"knowledge_point_id": "kp", "knowledge_point_name": "知识点",
                                   "relevance_type": "主要考点"}],
             "error_analysis": ea},
        ],
        "dependency_edges": [],
    }


class TestPriorityIntegration:
    def test_multi_codes_take_max_severity(self):
        """多选错因取最高严重度：计算失误(0.6) + 时间不够(0.4) -> 0.6。"""
        from calculate_priority import calculate_priority
        from common import load_config
        result = calculate_priority(_priority_data({"error_codes": [3, 4]}), load_config())
        metric = result["knowledge_points"][0]["metrics"]["error_severity"]
        assert metric["source"] == "observed"
        assert metric["value"] == 0.6

    def test_concept_clears_highest(self):
        from calculate_priority import calculate_priority
        from common import load_config
        result = calculate_priority(_priority_data({"error_codes": [1, 3]}), load_config())
        metric = result["knowledge_points"][0]["metrics"]["error_severity"]
        assert metric["value"] == 1.0  # 概念不清 1.0 > 计算失误 0.6

    def test_other_only_is_missing(self):
        """仅选「其他」= 无实质错因信息 -> missing，不按已知处理。"""
        from calculate_priority import calculate_priority
        from common import load_config
        result = calculate_priority(_priority_data({"error_codes": [5]}), load_config())
        assert result["knowledge_points"][0]["metrics"]["error_severity"]["source"] == "missing"

    def test_empty_codes_is_missing(self):
        from calculate_priority import calculate_priority
        from common import load_config
        result = calculate_priority(_priority_data({"error_codes": []}), load_config())
        assert result["knowledge_points"][0]["metrics"]["error_severity"]["source"] == "missing"

    def test_all_five_codes_observed(self):
        from calculate_priority import calculate_priority
        from common import load_config
        result = calculate_priority(_priority_data({"error_codes": [1, 2, 3, 4, 5]}), load_config())
        assert result["knowledge_points"][0]["metrics"]["error_severity"]["source"] == "observed"

    def test_severity_labels_mapping(self):
        labels = to_severity_labels([1, 3, 5], {
            "概念不清": "概念理解不清", "计算失误": "计算或推导错误", "其他": "暂无法判断"})
        assert labels == ["概念理解不清", "计算或推导错误", "暂无法判断"]


# ---------------------------------------------------------------------------
# 7. validate_output 集成
# ---------------------------------------------------------------------------

def _output_dir(tmp_path, data: dict) -> "object":
    out = tmp_path / "out"
    out.mkdir()
    (out / "analysis-data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return out


class TestValidateOutputIntegration:
    def _run(self, out):
        from validate_output import validate_output
        from common import load_config
        return validate_output(out, load_config())

    def test_valid_multi_codes_pass(self, tmp_path):
        data = {"questions": [{"question_id": "Q1",
                               "error_analysis": {"error_codes": [1, 3], "error_code": 1}}]}
        result = self._run(_output_dir(tmp_path, data))
        check = next(c for c in result["checks"] if "错因多选" in c["check"])
        assert check["ok"], check["detail"]

    def test_out_of_range_fails(self, tmp_path):
        data = {"questions": [{"question_id": "Q1",
                               "error_analysis": {"error_codes": [1, 9]}}]}
        result = self._run(_output_dir(tmp_path, data))
        check = next(c for c in result["checks"] if "错因多选" in c["check"])
        assert not check["ok"]
        assert "越界" in check["detail"]

    def test_inconsistent_primary_fails(self, tmp_path):
        data = {"questions": [{"question_id": "Q1",
                               "error_analysis": {"error_codes": [1, 3], "error_code": 3}}]}
        result = self._run(_output_dir(tmp_path, data))
        check = next(c for c in result["checks"] if "错因多选" in c["check"])
        assert not check["ok"]
        assert "不一致" in check["detail"]

    def test_legacy_only_data_passes(self, tmp_path):
        """旧版数据未使用错因代码字段：合法，跳过检查。"""
        data = {"questions": [{"question_id": "Q1", "error_analysis": {}}]}
        result = self._run(_output_dir(tmp_path, data))
        check = next(c for c in result["checks"] if "错因多选" in c["check"])
        assert check["ok"], check["detail"]

    def test_legacy_single_code_consistent(self, tmp_path):
        data = {"questions": [{"question_id": "Q1",
                               "error_analysis": {"error_codes": [2], "error_code": 2}}]}
        result = self._run(_output_dir(tmp_path, data))
        check = next(c for c in result["checks"] if "错因多选" in c["check"])
        assert check["ok"], check["detail"]


# ---------------------------------------------------------------------------
# 8. 报告模板回显
# ---------------------------------------------------------------------------

def _report_data(error_codes):
    return {
        "meta": {"subject": "数学", "source_file": "test.md"},
        "questions": [
            {"question_id": "Q001", "question_text": "求极值", "latex": [],
             "options": [], "standard_answer": "A", "student_answer": "B",
             "student_solution": "s", "student_note": None,
             "recognition_issues": [], "review_status": "pending",
             "knowledge_points": [{"knowledge_point_id": "kp", "knowledge_point_name": "极值",
                                   "relevance_type": "主要考点", "evidence": [],
                                   "confidence": "中", "verification_status": "待确认"}],
             "error_analysis": {"observed_error": None, "suspected_causes": ["审题不充分"],
                                "error_codes": error_codes,
                                "evidence": [], "confidence": "中",
                                "needs_confirmation": False}},
        ],
    }


class TestReportRendering:
    def test_markdown_shows_multi_causes(self, tmp_path):
        from generate_markdown import generate_markdown
        from common import load_config
        md, _ = generate_markdown(_report_data([1, 3]), None, None, load_config())
        assert "[1,3] 概念不清 + 计算失误" in md          # 错因分析区（带代码）
        assert "概念不清 + 计算失误" in md                 # 明细表列（不带代码）

    def test_html_shows_multi_causes(self, tmp_path):
        from generate_markdown import generate_html
        from common import load_config
        html = generate_html(_report_data([1, 3]), None, None, load_config())
        assert "[1,3] 概念不清 + 计算失误" in html
        assert "错因（多选）" in html

    def test_empty_causes_show_unprovided(self):
        from generate_markdown import generate_markdown
        from common import load_config
        md, _ = generate_markdown(_report_data([]), None, None, load_config())
        assert "错因标注（多选）**：未提供" in md

    def test_all_five_causes_render(self):
        from generate_markdown import generate_markdown
        from common import load_config
        md, _ = generate_markdown(_report_data([1, 2, 3, 4, 5]), None, None, load_config())
        assert "[1,2,3,4,5]" in md
        for label in ERROR_CAUSES.values():
            assert label in md
