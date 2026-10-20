"""知识点识别：基于知识库的关键词规则匹配 + Agent 语义标注合并。

程序职责（确定性）：
- 加载 knowledge/ 知识点库；
- 用关键词匹配题目文本（题干权重高于解题过程）；
- 判定主要/辅助考点，生成 evidence 与置信度；
- 合并 Agent 已写入的语义标注（优先保留）；
- 输出依赖关系子图（仅知识库内 verified 关系）。

诚实性原则：
- 关键词匹配是启发式，结果一律标记「待确认」，除非 Agent 显式标记已核验；
- 匹配不到知识点时明确记录，禁止编造归属；
- 不根据「两个知识点同现于一道题」推断先修关系。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, InputError, SkillError, load_config, read_json, setup_logger, write_json
from error_causes import format_error_causes, normalize_error_codes, primary_error_code

VALID_ERROR_TYPES = {
    "概念理解不清", "公式或定理使用错误", "解题方法选择不当", "计算或推导错误",
    "审题不充分", "解题步骤不完整", "知识点之间联系不清", "暂无法判断",
}


def load_knowledge_base(path: Path | None = None, logger=None) -> dict[str, Any]:
    path = path or PROJECT_ROOT / load_config()["paths"]["knowledge_base"]
    if not path.exists():
        raise SkillError(f"知识点库不存在：{path}（可在 knowledge/ 目录创建或修改 config/default.yaml）")
    data = read_json(path)
    points = data.get("knowledge_points")
    if not isinstance(points, list) or not points:
        raise SkillError(f"知识点库为空或格式错误：{path}")
    return data


def _score_question(kp: dict, question: dict) -> tuple[int, list[str]]:
    """关键词匹配打分：题干命中权重 2，公式/选项/解题过程命中权重 1。"""
    keywords = [str(k).lower() for k in kp.get("keywords", []) if str(k).strip()]
    if not keywords:
        return 0, []
    question_text = (question.get("question_text") or "").lower()
    secondary = " ".join([
        question.get("student_solution") or "",
        question.get("standard_answer") or "",
        " ".join(o.get("content", "") for o in question.get("options", [])),
        " ".join(question.get("latex", [])),
    ]).lower()
    score, evidence = 0, []
    for kw in keywords:
        if kw and kw in question_text:
            score += 2
            evidence.append(f"题干含「{kw}」")
        elif kw and kw in secondary:
            score += 1
            evidence.append(f"答案/过程含「{kw}」")
    return score, evidence


def analyze_question(question: dict, kb: dict) -> list[dict[str, Any]]:
    """对单题生成知识点标注；保留 Agent 已有标注。"""
    existing = question.get("knowledge_points") or []
    if existing:
        return existing  # Agent 语义标注优先

    scores: list[tuple[int, dict, list[str]]] = []
    for kp in kb["knowledge_points"]:
        score, evidence = _score_question(kp, question)
        if score > 0:
            scores.append((score, kp, evidence))
    if not scores:
        return []

    scores.sort(key=lambda item: item[0], reverse=True)
    max_score = scores[0][0]
    result: list[dict[str, Any]] = []
    for score, kp, evidence in scores:
        is_primary = score >= max(3, 0.5 * max_score)
        if score < 2 and not is_primary:
            continue  # 极弱匹配不纳入
        confidence = "中" if score >= 4 else "低"
        result.append({
            "knowledge_point_id": kp["id"],
            "knowledge_point_name": kp["name"],
            "relevance_type": "主要考点" if is_primary else "辅助考点",
            "evidence": evidence,
            "confidence": confidence,
            "verification_status": "待确认",
            "source": "program-keyword",
        })
    return result


def validate_error_analysis(question: dict, logger=None) -> dict[str, Any]:
    """校验/补全错因分析结构，确保字段完整且不越界编造。

    v3.1.0 起错因为多选：`error_codes`（list[int]，1-5 任意组合）。
    兼容旧版单选 `error_code`：仅含 error_code 时升级为 error_codes。
    无法识别的代码不静默吞掉——记入 note 告知用户，并按可识别部分处理。
    """
    ea = question.get("error_analysis") or {}
    observed = ea.get("observed_error")
    suspected = ea.get("suspected_causes") or []
    evidence = ea.get("evidence") or []
    has_student_data = bool(question.get("student_answer") or question.get("student_solution"))

    # ---- v3.1.0 多选错因：解析/校验（含旧版 error_code 兼容升级） ----
    raw_codes = ea.get("error_codes")
    if raw_codes is None and ea.get("error_code") is not None:
        raw_codes = ea.get("error_code")  # 旧版单选数据
    codes: list[int] = []
    code_note = ""
    if raw_codes is not None:
        try:
            codes = normalize_error_codes(raw_codes, strict=True)
        except InputError as e:
            codes = normalize_error_codes(raw_codes, strict=False)
            code_note = f"错因代码存在无法识别的值（{e}），已按可识别部分处理：{format_error_causes(codes)}"
            if logger:
                logger.warning("%s：%s", question.get("question_id", "?"), code_note)

    normalized: dict[str, Any] = {
        "observed_error": observed if observed else None,
        "suspected_causes": [c for c in suspected if c],
        "error_codes": codes,
        "error_code": primary_error_code(codes),  # 主错因（兼容旧版单选字段）
        "evidence": [e for e in evidence if e],
        "confidence": ea.get("confidence"),
        "needs_confirmation": bool(ea.get("needs_confirmation", True)),
    }

    if not has_student_data:
        # 没有学生作答数据：不得判断该学生错在哪里
        normalized["note"] = (
            "未提供学生答案/解题过程，本模块不判断该学生的具体错误；"
            "以下内容仅为该题的一般性易错点，不是已发生的错误。"
        )
        normalized["needs_confirmation"] = True
    if not normalized["suspected_causes"] and not normalized["observed_error"]:
        normalized["suspected_causes"] = ["暂无法判断"]
        if normalized["confidence"] is None:
            normalized["confidence"] = "低"
    if code_note:
        normalized["note"] = (normalized.get("note", "") + " " + code_note).strip()
    return normalized


def build_dependency_edges(kb: dict) -> list[dict[str, Any]]:
    """从知识库提取先修依赖边（verified=true）。"""
    edges = []
    ids = {kp["id"] for kp in kb["knowledge_points"]}
    for kp in kb["knowledge_points"]:
        for pre in kp.get("prerequisites", []):
            if pre in ids:
                edges.append({
                    "source": pre,
                    "target": kp["id"],
                    "relation": "prerequisite",
                    "verified": True,
                    "source_of_truth": "knowledge-base",
                })
    return edges


def analyze_knowledge(
    data: dict[str, Any],
    kb: dict[str, Any],
    logger=None,
) -> dict[str, Any]:
    """主流程：标注知识点 + 校验错因 + 输出依赖边。"""
    logger = logger or setup_logger()
    matched_ids: set[str] = set()
    unmatched: list[str] = []

    for q in data["questions"]:
        kps = analyze_question(q, kb)
        if not kps:
            unmatched.append(q["question_id"])
            q["recognition_issues"] = list(q.get("recognition_issues") or []) + [
                "未能匹配到知识点库条目（可能超出当前知识库范围）"
            ]
        q["knowledge_points"] = kps
        q["error_analysis"] = validate_error_analysis(q, logger)
        for kp in kps:
            matched_ids.add(kp["knowledge_point_id"])

    edges = build_dependency_edges(kb)
    data.setdefault("meta", {})["knowledge_base"] = kb.get("meta", {}).get("name", "")
    data["dependency_edges"] = edges
    if unmatched:
        logger.warning("以下题目未匹配到知识点：%s", unmatched)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="知识点识别与错因校验")
    parser.add_argument("--input", required=True, help="questions.json 路径")
    parser.add_argument("--output", required=True, help="输出 JSON 路径（默认覆盖输入）")
    parser.add_argument("--knowledge-base", default=None, help="知识点库路径（可选）")
    args = parser.parse_args()

    logger = setup_logger()
    try:
        data = read_json(Path(args.input))
        kb = load_knowledge_base(Path(args.knowledge_base) if args.knowledge_base else None, logger)
        result = analyze_knowledge(data, kb, logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    out = Path(args.output)
    write_json(out, result)
    summary = [
        {"question_id": q["question_id"],
         "knowledge_points": [f"{kp['knowledge_point_name']}({kp['relevance_type']})"
                              for kp in q["knowledge_points"]]}
        for q in result["questions"]
    ]
    print(json.dumps({
        "ok": True,
        "annotated": len(summary),
        "dependency_edges": len(result.get("dependency_edges", [])),
        "summary": summary,
        "output": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
