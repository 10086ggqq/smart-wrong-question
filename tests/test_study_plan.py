"""复习计划测试：场景 E（时间不足）与场景 F（依赖缺口）。

- 场景 E：每日任务不超时、按优先级安排、明确说明无法完成的目标；
- 场景 F：先修知识优先安排、不确定依赖关系被标记。
"""
from __future__ import annotations

from build_study_plan import build_study_plan
from common import load_config


def _questions_with_priority(priority_points, edges):
    questions = []
    for kp in priority_points:
        questions.append({
            "question_id": f"Q{len(questions)+1:03d}",
            "question_text": f"{kp['knowledge_point_name']} 题目",
            "knowledge_points": [{
                "knowledge_point_id": kp["knowledge_point_id"],
                "knowledge_point_name": kp["knowledge_point_name"],
                "relevance_type": "主要考点", "evidence": [], "confidence": "中",
                "verification_status": "待确认",
            }],
            "error_analysis": {},
        })
    return {"questions": questions, "dependency_edges": edges, "_priority": {
        "knowledge_points": priority_points}}


PRIORITY_POINTS = [
    {"knowledge_point_id": "kp-high", "knowledge_point_name": "高频薄弱点",
     "wrong_count": 3, "question_ids": [], "depends_on": [], "required_by": [],
     "priority": 0.9},
    {"knowledge_point_id": "kp-mid", "knowledge_point_name": "中频薄弱点",
     "wrong_count": 1, "question_ids": [], "depends_on": [], "required_by": [],
     "priority": 0.5},
    {"knowledge_point_id": "kp-low", "knowledge_point_name": "低频薄弱点",
     "wrong_count": 1, "question_ids": [], "depends_on": [], "required_by": [],
     "priority": 0.3},
]


class TestScenarioE:
    """场景 E：复习时间不足。"""

    def test_daily_budget_never_exceeded(self):
        config = load_config()
        data = _questions_with_priority(PRIORITY_POINTS, [])
        plan = build_study_plan(data, days=7, daily_minutes=20, config=config)
        for day in plan["days"]:
            assert day["total_minutes"] <= day["budget_minutes"], \
                f"第 {day['day']} 天超时：{day['total_minutes']} > {day['budget_minutes']}"

    def test_insufficient_time_documented(self):
        """时间明显不足时必须记录未排入任务并给出说明。"""
        config = load_config()
        data = _questions_with_priority(PRIORITY_POINTS, [])
        plan = build_study_plan(data, days=2, daily_minutes=15, config=config)
        assert plan["dropped_tasks"], "时间不足时应记录未排入任务"
        assert any("预算不足" in w for w in plan["warnings"]), "应说明目标与时间冲突"

    def test_high_priority_scheduled_first(self):
        config = load_config()
        data = _questions_with_priority(PRIORITY_POINTS, [])
        plan = build_study_plan(data, days=3, daily_minutes=60, config=config)
        day1 = plan["days"][0]
        titles = " ".join(t["title"] for t in day1["tasks"])
        assert "高频薄弱点" in titles, "第一天应安排最高优先级知识点"

    def test_tasks_are_actionable(self):
        config = load_config()
        data = _questions_with_priority(PRIORITY_POINTS, [])
        plan = build_study_plan(data, days=7, daily_minutes=60, config=config)
        for day in plan["days"]:
            for t in day["tasks"]:
                assert t["completion_criteria"], "任务缺少完成标准"
                assert t["review_method"], "任务缺少复盘方式"
                assert t["minutes"] > 0

    def test_every_day_has_checkable_result(self):
        config = load_config()
        data = _questions_with_priority(PRIORITY_POINTS, [])
        plan = build_study_plan(data, days=7, daily_minutes=60, config=config)
        for day in plan["days"]:
            assert day["goals"], f"第 {day['day']} 天缺少学习目标"


class TestScenarioF:
    """场景 F：知识点依赖存在缺口。"""

    def test_prerequisite_scheduled_before_dependent(self):
        """先修知识应先于后续知识安排。"""
        config = load_config()
        points = [
            {"knowledge_point_id": "advanced", "knowledge_point_name": "高阶知识",
             "wrong_count": 2, "question_ids": [], "depends_on": ["basic"],
             "required_by": [], "priority": 0.95},
            {"knowledge_point_id": "basic", "knowledge_point_name": "先修基础",
             "wrong_count": 1, "question_ids": [], "depends_on": [],
             "required_by": ["advanced"], "priority": 0.4},
        ]
        edges = [{"source": "basic", "target": "advanced",
                  "relation": "prerequisite", "verified": True}]
        data = _questions_with_priority(points, edges)
        plan = build_study_plan(data, days=7, daily_minutes=90, config=config)
        day1_titles = " ".join(t["title"] for t in plan["days"][0]["tasks"])
        assert "先修基础" in day1_titles, "先修知识应在第一天安排"

    def test_unverified_dependency_marked(self):
        """不确定的依赖关系必须可标记（verified=False）。"""
        from analyze_knowledge import build_dependency_edges
        from common import read_json, PROJECT_ROOT

        kb = read_json(PROJECT_ROOT / "knowledge" / "sample_knowledge_base.json")
        edges = build_dependency_edges(kb)
        assert all(e["verified"] for e in edges), "知识库关系应全部已核验"
        # 模型推断关系不在库内，不会自动生成（由 Agent 写入并标待验证）
        ids = {kp["id"] for kp in kb["knowledge_points"]}
        for e in edges:
            assert e["source"] in ids and e["target"] in ids

    def test_missing_prerequisite_gap_raises_priority(self):
        """先修缺口（先修本身薄弱）应提升优先级。"""
        from calculate_priority import calculate_priority

        data = {
            "questions": [
                {"question_id": "Q1", "question_text": "x",
                 "knowledge_points": [{"knowledge_point_id": "advanced",
                                       "knowledge_point_name": "高阶",
                                       "relevance_type": "主要考点"}],
                 "error_analysis": {"suspected_causes": ["概念理解不清"]}},
                {"question_id": "Q2", "question_text": "y",
                 "knowledge_points": [{"knowledge_point_id": "basic",
                                       "knowledge_point_name": "基础",
                                       "relevance_type": "主要考点"}],
                 "error_analysis": {"suspected_causes": ["概念理解不清"]}},
            ],
            "dependency_edges": [
                {"source": "basic", "target": "advanced",
                 "relation": "prerequisite", "verified": True},
            ],
        }
        config = load_config()
        result = calculate_priority(data, config)
        by_id = {r["knowledge_point_id"]: r for r in result["knowledge_points"]}
        assert by_id["advanced"]["metrics"]["prerequisite_impact"]["detail"]["prerequisite_gap"] == 1.0, \
            "先修 basic 薄弱时，advanced 的前置缺口应为 1.0"
