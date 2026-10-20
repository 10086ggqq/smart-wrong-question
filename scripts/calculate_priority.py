"""薄弱点优先级计算：可解释的规则加权评分模型。

模型：
    priority = Σ w_i · x_i
    x ∈ {错题频率, 近期重复, 错因严重度, 先修影响, 复测差距}

设计原则：
- 每个输入指标标注来源：observed（实际记录）/ estimated（规则估算）/ missing（缺失）。
- 无复测数据时掌握程度标记为「未知」（mastery=null），不输出看似精确的掌握率。
- 先修影响区分「被后续依赖（前置重要性）」与「依赖他人（前置缺口）」，不使用简单出度。
- 权重在 config/default.yaml 可调；归一化方式在输出中说明。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SkillError, load_config, read_json, setup_logger, write_json
from error_causes import to_severity_labels


def _severity_of(question: dict, severity_map: dict,
                 severity_labels: dict[str, str] | None = None) -> tuple[float | None, str]:
    """从错因分析提取严重度。返回 (值, 来源)。

    支持三种错因来源（v3.1.0 起前两种可为多选）：
    1. `error_analysis.error_codes`：交互主流程的多选错因代码（1-5），
       经 config 的 severity_labels 映射为严重度标签；
    2. `error_analysis.suspected_causes`：批处理侧推测原因（列表，取最高）；
    3. `error_analysis.observed_error`：可观察错误（字符串）。

    多选时取**最高**严重度（复合错因的严重度不低于其任一分项）。
    「暂无法判断」是无信息标签，不计入观察值（避免把未知当作已知）。
    """
    ea = question.get("error_analysis") or {}
    causes = list(ea.get("suspected_causes") or [])
    observed = ea.get("observed_error")
    if observed:
        causes.append(str(observed))
    if severity_labels:
        causes.extend(to_severity_labels(ea.get("error_codes"), severity_labels))
    mapped = [severity_map[c] for c in causes
              if c in severity_map and c != "暂无法判断"]
    if not mapped:
        # 没有任何可用错因信息：不能猜测，按缺失处理
        return None, "missing"
    return max(mapped), "observed"


def calculate_priority(
    data: dict[str, Any],
    config: dict,
    logger=None,
) -> dict[str, Any]:
    logger = logger or setup_logger()
    pconf = config["priority"]
    weights = pconf["weights"]
    severity_map = pconf["severity_map"]
    conservative = pconf["conservative_value"]
    severity_labels = (config.get("error_causes") or {}).get("severity_labels") or {}

    questions = data.get("questions", [])
    total = max(len(questions), 1)

    # ---- 聚合每道题的知识点 ----
    kp_questions: dict[str, list[dict]] = {}
    for q in questions:
        for kp in q.get("knowledge_points", []):
            kp_questions.setdefault(kp["knowledge_point_id"], []).append(q)

    # ---- 依赖关系（来自知识库，verified） ----
    edges = data.get("dependency_edges", [])
    required_by: dict[str, set[str]] = {}   # 被哪些后续知识点依赖
    depends_on: dict[str, set[str]] = {}    # 依赖哪些先修
    for e in edges:
        if e.get("relation") == "prerequisite":
            depends_on.setdefault(e["target"], set()).add(e["source"])
            required_by.setdefault(e["source"], set()).add(e["target"])

    max_required = max((len(v) for v in required_by.values()), default=1) or 1

    # ---- 逐知识点计算 ----
    results = []
    for kp_id, qs in kp_questions.items():
        wrong_count = len(qs)
        q_ids = [q["question_id"] for q in qs]
        kp_name = next(
            (k["knowledge_point_name"] for k in qs[0].get("knowledge_points", [])
             if k["knowledge_point_id"] == kp_id),
            kp_id,
        )

        # 1) 错题频率（observed）
        freq = wrong_count / total

        # 2) 近期重复（observed，基于错题计数；无时间字段时说明规则）
        recency = min(1.0, (wrong_count - 1) / 2.0) if wrong_count >= 2 else 0.0

        # 3) 错因严重度（observed / missing）；多选错因取最高严重度后按题平均
        severities = [_severity_of(q, severity_map, severity_labels) for q in qs]
        observed_sev = [s for s, src in severities if src == "observed"]
        if observed_sev:
            severity = sum(observed_sev) / len(observed_sev)
            sev_source = "observed"
        else:
            severity = conservative
            sev_source = "missing"  # 无错因数据：按保守值并标记

        # 4) 先修影响（observed from KB）：被后续依赖程度
        prereq_impact = len(required_by.get(kp_id, set())) / max_required
        # 前置缺口：其先修中同样薄弱（有错题）的比例
        pres = depends_on.get(kp_id, set())
        weak_pres = [p for p in pres if p in kp_questions]
        prereq_gap = len(weak_pres) / len(pres) if pres else 0.0

        # 5) 复测差距（missing → 保守估算）
        retest_gap = conservative
        retest_source = "missing"

        # 前置缺口加成后封顶为 1，保证加权和有界
        prereq_term = min(1.0, (prereq_impact + 0.5 * prereq_gap))
        priority = (
            weights["wrong_frequency"] * freq
            + weights["recency_repeat"] * recency
            + weights["error_severity"] * severity
            + weights["prerequisite_impact"] * prereq_term
            + weights["retest_gap"] * retest_gap
        )
        priority = round(min(max(priority, 0.0), 1.0), 4)

        results.append({
            "knowledge_point_id": kp_id,
            "knowledge_point_name": kp_name,
            "wrong_count": wrong_count,
            "question_ids": q_ids,
            "metrics": {
                "wrong_frequency": {"value": round(freq, 4), "source": "observed"},
                "recency_repeat": {"value": round(recency, 4), "source": "observed",
                                   "rule": "错题数≥2 时取 min(1,(n-1)/2)，无时间字段"},
                "error_severity": {"value": round(severity, 4), "source": sev_source},
                "prerequisite_impact": {"value": round(prereq_term, 4), "source": "observed",
                                        "detail": {"required_by_count": len(required_by.get(kp_id, set())),
                                                   "prerequisite_gap": round(prereq_gap, 4)}},
                "retest_gap": {"value": retest_gap, "source": retest_source,
                               "note": "无复测数据，按保守值估算"},
            },
            "mastery": {
                "value": None,
                "status": "未知",
                "note": "未提供复测/测评数据，掌握程度未知；上述分数不代表真实掌握率。",
            },
            "priority": priority,
            "status": "unknown",
            "depends_on": sorted(depends_on.get(kp_id, set())),
            "required_by": sorted(required_by.get(kp_id, set())),
        })

    results.sort(key=lambda r: r["priority"], reverse=True)
    return {
        "meta": {
            "model": "rule-based-weighted-sum",
            "weights": weights,
            "normalization": "各指标均已归一到 [0,1]：频率=错题数/总题数；重复=min(1,(n-1)/2)；"
                             "严重度=错因严重度映射值（多选错因取最高，再按题平均）；"
                             "先修影响=被依赖数/最大被依赖数（含前置缺口加成）；复测差距缺失时取保守值。",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "global": {
            "total_questions": len(questions),
            "knowledge_point_count": len(results),
            "missing_data_notes": [
                "未提供复测数据 → 掌握程度全部标记为「未知」，retest_gap 为保守估算值。",
                "未提供错因标注的题目 → error_severity 标记 missing，按保守值估算。",
            ],
        },
        "knowledge_points": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="薄弱点优先级评分")
    parser.add_argument("--input", required=True, help="questions.json 路径（含知识点标注）")
    parser.add_argument("--output", required=True, help="输出 priority.json 路径")
    args = parser.parse_args()

    logger = setup_logger()
    try:
        data = read_json(Path(args.input))
        config = load_config()
        result = calculate_priority(data, config, logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    write_json(Path(args.output), result)
    print(json.dumps({
        "ok": True,
        "knowledge_points": len(result["knowledge_points"]),
        "top5": [
            {"id": r["knowledge_point_id"], "name": r["knowledge_point_name"],
             "priority": r["priority"], "wrong_count": r["wrong_count"]}
            for r in result["knowledge_points"][:5]
        ],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
