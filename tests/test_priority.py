"""优先级模型测试：指标来源标注、缺失值处理、无复测数据时的诚实标记。"""
from __future__ import annotations

import pytest

from calculate_priority import calculate_priority
from common import load_config


def _prepare_questions():
    """构造带知识点标注的题目数据（含依赖边）。"""
    return {
        "meta": {"subject": "数学"},
        "dependency_edges": [
            {"source": "function-basics", "target": "derivative", "relation": "prerequisite", "verified": True},
            {"source": "derivative", "target": "extremum", "relation": "prerequisite", "verified": True},
        ],
        "questions": [
            {
                "question_id": "Q001", "question_text": "求极值",
                "knowledge_points": [
                    {"knowledge_point_id": "function-basics", "knowledge_point_name": "函数基础",
                     "relevance_type": "辅助考点", "evidence": [], "confidence": "中",
                     "verification_status": "待确认"},
                    {"knowledge_point_id": "extremum", "knowledge_point_name": "极值",
                     "relevance_type": "主要考点", "evidence": [], "confidence": "中",
                     "verification_status": "待确认"},
                ],
                "error_analysis": {"observed_error": "遗漏定义域",
                                   "suspected_causes": ["审题不充分"], "evidence": [],
                                   "confidence": "中", "needs_confirmation": False},
            },
            {
                "question_id": "Q002", "question_text": "再求极值",
                "knowledge_points": [
                    {"knowledge_point_id": "extremum", "knowledge_point_name": "极值",
                     "relevance_type": "主要考点", "evidence": [], "confidence": "中",
                     "verification_status": "待确认"},
                ],
                "error_analysis": {},
            },
        ],
    }


class TestPriorityModel:
    def test_scores_bounded_and_sorted(self):
        config = load_config()
        result = calculate_priority(_prepare_questions(), config)
        scores = [r["priority"] for r in result["knowledge_points"]]
        assert all(0.0 <= s <= 1.0 for s in scores)
        assert scores == sorted(scores, reverse=True), "结果应按优先级降序"

    def test_metric_sources_marked(self):
        config = load_config()
        result = calculate_priority(_prepare_questions(), config)
        for r in result["knowledge_points"]:
            for name, metric in r["metrics"].items():
                assert metric["source"] in ("observed", "estimated", "missing"), \
                    f"{name} 缺少来源标注"

    def test_no_retest_data_marks_mastery_unknown(self):
        """无复测数据：掌握程度必须为未知，不得给出精确分值。"""
        config = load_config()
        result = calculate_priority(_prepare_questions(), config)
        for r in result["knowledge_points"]:
            assert r["mastery"]["value"] is None
            assert r["mastery"]["status"] == "未知"
            assert r["metrics"]["retest_gap"]["source"] == "missing"

    def test_missing_error_cause_not_counted_as_observed(self):
        """「暂无法判断」不得计入观察值：全部无实质错因时应标记 missing。"""
        config = load_config()
        data = _prepare_questions()
        for q in data["questions"]:
            q["error_analysis"] = {
                "observed_error": None, "suspected_causes": ["暂无法判断"],
                "evidence": [], "confidence": "低", "needs_confirmation": True,
            }
        result = calculate_priority(data, config)
        by_id = {r["knowledge_point_id"]: r for r in result["knowledge_points"]}
        assert by_id["extremum"]["metrics"]["error_severity"]["source"] == "missing"
        assert by_id["function-basics"]["metrics"]["error_severity"]["source"] == "missing"

    def test_observed_error_cause_counted(self):
        """有实质错因时应标记 observed。"""
        config = load_config()
        result = calculate_priority(_prepare_questions(), config)
        by_id = {r["knowledge_point_id"]: r for r in result["knowledge_points"]}
        # function-basics 仅出现在 Q001，Q001 有「审题不充分」(0.5)
        assert by_id["function-basics"]["metrics"]["error_severity"]["source"] == "observed"
        assert by_id["function-basics"]["metrics"]["error_severity"]["value"] == 0.5

    def test_prerequisite_impact_uses_dependency_not_cooccurrence(self):
        """先修影响来自依赖关系，不是同现关系。"""
        config = load_config()
        result = calculate_priority(_prepare_questions(), config)
        by_id = {r["knowledge_point_id"]: r for r in result["knowledge_points"]}
        # function-basics 被 derivative 依赖 → 先修影响 > 0
        assert by_id["function-basics"]["metrics"]["prerequisite_impact"]["value"] > 0
        assert by_id["function-basics"]["required_by"] == ["derivative"]
        # extremum 不被任何知识点依赖
        assert by_id["extremum"]["required_by"] == []

    def test_wrong_frequency_observed(self):
        config = load_config()
        result = calculate_priority(_prepare_questions(), config)
        by_id = {r["knowledge_point_id"]: r for r in result["knowledge_points"]}
        assert by_id["extremum"]["metrics"]["wrong_frequency"]["value"] == 1.0  # 2/2
        assert by_id["function-basics"]["metrics"]["wrong_frequency"]["value"] == 0.5  # 1/2

    def test_empty_questions_handled(self):
        config = load_config()
        result = calculate_priority({"questions": [], "dependency_edges": []}, config)
        assert result["knowledge_points"] == []
