"""个性化复习计划生成：7/14 天，时间预算硬约束。

原则：
- 先修优先：按先修深度分层（基础薄弱点先复习），同层按优先级降序；
- 每日总时长不得超过 每日可用时间 × 安全系数；
- 超预算时缩减低优先级任务并显式记录，不得虚构过短用时；
- 间隔复习按保守间隔（每 3 天一次复测），无复测反馈时不假装已掌握；
- 每天的任务必须具体、可检查，附完成标准与复盘方式。
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

TASK_MINUTES = {
    "theory": 15,      # 理论回顾
    "practice": 30,    # 重做错题 + 变式练习
    "retest": 20,      # 复测
    "review": 15,      # 阶段复盘
}
RETEST_INTERVAL = 3    # 保守间隔：每 3 天安排一次复测（无反馈数据时不上调间隔）


def _kp_lookup(data: dict) -> dict[str, dict]:
    """知识点 id -> {name, question_ids, wrong_count, depends_on, required_by}"""
    lookup: dict[str, dict] = {}
    for q in data.get("questions", []):
        for kp in q.get("knowledge_points", []):
            kp_id = kp["knowledge_point_id"]
            entry = lookup.setdefault(kp_id, {
                "knowledge_point_id": kp_id,
                "knowledge_point_name": kp["knowledge_point_name"],
                "question_ids": [],
                "wrong_count": 0,
                "depends_on": [],
                "required_by": [],
            })
            entry["question_ids"].append(q["question_id"])
            entry["wrong_count"] += 1
    priority_data = data.get("_priority") or {}
    for r in priority_data.get("knowledge_points", []):
        if r["knowledge_point_id"] in lookup:
            lookup[r["knowledge_point_id"]]["depends_on"] = r.get("depends_on", [])
            lookup[r["knowledge_point_id"]]["required_by"] = r.get("required_by", [])
            lookup[r["knowledge_point_id"]]["priority"] = r.get("priority", 0)
    return lookup


def _topo_order(kp_list: list[dict]) -> list[dict]:
    """先修深度分层 + 层内优先级降序（保证基础薄弱点先复习）。"""
    ids = {kp["knowledge_point_id"] for kp in kp_list}
    depth: dict[str, int] = {}

    def depth_of(kp_id: str, seen: frozenset = frozenset()) -> int:
        if kp_id in depth:
            return depth[kp_id]
        if kp_id in seen:  # 防御环
            return 0
        pres = [p for p in next((k for k in kp_list if k["knowledge_point_id"] == kp_id),
                                {}).get("depends_on", []) if p in ids]
        d = 0 if not pres else 1 + max(depth_of(p, seen | {kp_id}) for p in pres)
        depth[kp_id] = d
        return d

    for kp in kp_list:
        depth_of(kp["knowledge_point_id"])
    return sorted(kp_list, key=lambda k: (depth[k["knowledge_point_id"]],
                                          -k.get("priority", 0)))


def _make_task(kp: dict, ttype: str, minutes: int) -> dict[str, Any]:
    name = kp["knowledge_point_name"]
    q_ids = "、".join(kp["question_ids"])
    if ttype == "theory":
        return {
            "type": "theory",
            "title": f"理论回顾：{name}",
            "description": (
                f"复习「{name}」的定义、核心公式与常见易错点"
                f"（关联错题：{q_ids}）。先合上书回忆，再对照笔记查漏。"
            ),
            "minutes": minutes,
            "completion_criteria": "能不看资料写出该知识点的定义、公式，并说出至少 2 个易错点。",
            "review_method": "在白纸上默写知识点框架，卡住的步骤做标记，第二天复测时优先回顾。",
        }
    if ttype == "practice":
        return {
            "type": "practice",
            "title": f"练习巩固：{name}",
            "description": (
                f"重做错题 {q_ids}，再做 1-2 道同类变式题；"
                f"每道题完整写出解题步骤，不跳步。"
            ),
            "minutes": minutes,
            "completion_criteria": f"独立完成错题重做且步骤完整；变式题错误能定位到具体步骤。",
            "review_method": "对比标准答案，逐步标记差异点；仍不会的步骤写入「待解决问题清单」。",
        }
    if ttype == "retest":
        return {
            "type": "retest",
            "title": f"复测：{name}",
            "description": f"在不看资料的情况下重做此前「{name}」相关错题，检验是否真正掌握。",
            "minutes": minutes,
            "completion_criteria": "错题重做正确且能说出当时错误的原因；未掌握的回到该知识点的先修内容。",
            "review_method": "记录复测结果（正确/部分正确/错误），作为下次调整复习间隔的依据。",
        }
    return {
        "type": "review",
        "title": f"阶段复盘：{name}",
        "description": f"回顾「{name}」的错题清单、复测记录与仍存在的问题，整理成一句话结论。",
        "minutes": minutes,
        "completion_criteria": "能说出该知识点「错在哪、为什么错、下次怎么检查」三句话。",
        "review_method": "更新错题标记（已掌握/仍需巩固），把仍未解决的问题列入下一轮计划。",
    }


def build_study_plan(
    data: dict[str, Any],
    days: int,
    daily_minutes: int,
    config: dict,
    logger=None,
) -> dict[str, Any]:
    logger = logger or setup_logger()
    sconf = config["study"]
    days = max(1, min(days, sconf["max_days"]))
    granularity = sconf["task_granularity"]
    min_task = sconf["min_task_minutes"]
    budget = int(daily_minutes * sconf["reserve_ratio"])

    lookup = _kp_lookup(data)
    kp_list = _topo_order(list(lookup.values()))
    if not kp_list:
        raise SkillError("没有可安排的知识点：题目未匹配到任何知识点，请先检查知识点库或手动标注。")

    warnings: list[str] = []
    dropped: list[dict] = []

    # ---- 构建任务池：每个知识点 = 理论 + 练习 ----
    task_pool: list[dict] = []
    for kp in kp_list:
        task_pool.append({"kp_id": kp["knowledge_point_id"],
                          "task": _make_task(kp, "theory", TASK_MINUTES["theory"])})
        task_pool.append({"kp_id": kp["knowledge_point_id"],
                          "task": _make_task(kp, "practice", TASK_MINUTES["practice"])})

    # ---- 逐日贪心装入 ----
    day_plans: list[dict[str, Any]] = []
    pool_idx = 0
    learned: list[dict] = []  # 已学习知识点（用于复测）
    retest_due: list[dict] = []  # 到达复测间隔的知识点

    for day in range(1, days + 1):
        remaining = budget
        tasks: list[dict] = []
        day_kps: set[str] = set()

        # 复测优先占用部分预算（保守间隔；无反馈数据时不上调间隔）
        if day > 1 and (day - 1) % RETEST_INTERVAL == 0 and retest_due:
            for kp in list(retest_due):
                if remaining < TASK_MINUTES["retest"]:
                    break
                tasks.append(_make_task(kp, "retest", TASK_MINUTES["retest"]))
                day_kps.add(kp["knowledge_point_name"])
                remaining -= TASK_MINUTES["retest"]
                retest_due.remove(kp)

        # 新任务装入
        while pool_idx < len(task_pool) and remaining >= min_task:
            entry = task_pool[pool_idx]
            task = entry["task"]
            kp = lookup.get(entry["kp_id"])
            minutes = task["minutes"]
            if minutes > remaining:
                # 尝试缩减（仅练习类允许缩减，理论任务保持完整）
                if task["type"] == "practice" and remaining >= min_task and kp is not None:
                    shrink = max(min_task, (remaining // granularity) * granularity)
                    if shrink < minutes:
                        task = _make_task(kp, "practice", shrink)
                        task["note"] = f"原计划 {minutes} 分钟，因当日预算缩减为 {shrink} 分钟"
                        minutes = shrink
                    else:
                        break
                else:
                    break
            tasks.append(task)
            remaining -= minutes
            if kp:
                day_kps.add(kp["knowledge_point_name"])
                if task["type"] in ("theory", "practice") and kp not in learned:
                    learned.append(kp)
                    retest_due.append(kp)
            pool_idx += 1

        # 当天若完全空（极端情况），放入最短理论任务
        if not tasks and pool_idx < len(task_pool):
            kp = lookup.get(task_pool[pool_idx]["kp_id"])
            if kp:
                tasks.append(_make_task(kp, "theory", min(min_task, budget)))
                day_kps.add(kp["knowledge_point_name"])
                learned.append(kp)
                retest_due.append(kp)
                pool_idx += 1

        # 最后一天：复盘
        if day == days and remaining >= TASK_MINUTES["review"] and learned:
            for kp in learned[-3:]:
                if remaining < TASK_MINUTES["review"]:
                    break
                tasks.append(_make_task(kp, "review", TASK_MINUTES["review"]))
                day_kps.add(kp["knowledge_point_name"])
                remaining -= TASK_MINUTES["review"]

        total_minutes = sum(t["minutes"] for t in tasks)
        assert total_minutes <= budget, "时间预算约束被违反"

        has_retest = any(t["type"] == "retest" for t in tasks)
        day_plans.append({
            "day": day,
            "theme": _day_theme(day, days, has_retest),
            "goals": _day_goals(day, days, day_kps, bool(tasks), has_retest),
            "knowledge_points": sorted(day_kps),
            "tasks": tasks,
            "total_minutes": total_minutes,
            "budget_minutes": budget,
        })

    # 未排入的任务
    if pool_idx < len(task_pool):
        for entry in task_pool[pool_idx:]:
            dropped.append({
                "title": entry["task"]["title"],
                "minutes": entry["task"]["minutes"],
                "reason": "每日时间预算不足，未能排入计划",
            })
        warnings.append(
            f"时间预算不足：{len(dropped)} 个任务未能排入 {days} 天计划。"
            "建议增加每日学习时间、延长复习周期，或减少练习量。"
        )

    # 未覆盖的知识点说明
    covered = {t for d in day_plans for t in d["knowledge_points"]}
    uncovered = [kp["knowledge_point_name"] for kp in kp_list
                 if kp["knowledge_point_name"] not in covered]
    if uncovered:
        warnings.append(f"以下知识点本次计划未覆盖：{'、'.join(uncovered)}（时间预算限制）。")

    return {
        "meta": {
            "days": days,
            "daily_minutes": daily_minutes,
            "budget_per_day": budget,
            "reserve_ratio": sconf["reserve_ratio"],
            "retest_interval_days": RETEST_INTERVAL,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "note": "无复测反馈数据，间隔按保守值安排；掌握良好后可适当延长间隔。",
        },
        "days": day_plans,
        "dropped_tasks": dropped,
        "warnings": warnings,
    }


def _day_theme(day: int, total_days: int, has_retest: bool) -> str:
    """主题由当天实际任务类型决定，而非日期公式。"""
    if day == total_days:
        return "总复盘与查漏补缺"
    if has_retest:
        return "复测检验"
    if day == 1:
        return "基础薄弱点突破"
    return "巩固提升与变式练习"


def _day_goals(day: int, total_days: int, kps: set[str], has_tasks: bool,
               has_retest: bool) -> list[str]:
    if not has_tasks:
        return ["本日无任务（时间预算不足或任务已全部完成）"]
    names = "、".join(sorted(kps)) if kps else "相关知识点"
    if day == total_days:
        return [f"复盘全部薄弱知识点（{names}）", "整理仍未解决的问题清单"]
    if has_retest:
        return [f"复测前期内容（{names}）", "标记真正掌握与仍需巩固的知识点"]
    return [f"突破知识点：{names}", "完成理论回顾与错题重做"]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成个性化复习计划")
    parser.add_argument("--input", required=True, help="questions.json 路径")
    parser.add_argument("--output", required=True, help="输出 study_plan.json 路径")
    parser.add_argument("--priority", default=None, help="priority.json 路径（可选）")
    parser.add_argument("--days", type=int, default=None, help="复习天数（默认取配置）")
    parser.add_argument("--daily-minutes", type=int, default=None, help="每日学习时间（分钟）")
    args = parser.parse_args()

    logger = setup_logger()
    try:
        data = read_json(Path(args.input))
        config = load_config()
        if args.priority and Path(args.priority).exists():
            data["_priority"] = read_json(Path(args.priority))
        else:
            # 无 priority.json 时内部现算，保证计划可独立生成
            from calculate_priority import calculate_priority
            data["_priority"] = calculate_priority(data, config, logger)
        days = args.days or config["study"]["default_days"]
        daily_minutes = args.daily_minutes or config["study"]["default_daily_minutes"]
        result = build_study_plan(data, days, daily_minutes, config, logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    write_json(Path(args.output), result)
    print(json.dumps({
        "ok": True,
        "days": result["meta"]["days"],
        "budget_per_day": result["meta"]["budget_per_day"],
        "daily_totals": [d["total_minutes"] for d in result["days"]],
        "dropped_tasks": len(result["dropped_tasks"]),
        "warnings": result["warnings"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
