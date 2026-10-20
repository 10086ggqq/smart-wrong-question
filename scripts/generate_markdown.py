"""Markdown 报告生成（Jinja2 模板渲染）。

报告包含 11 个必需章节：
基本信息 / 整体概览 / 主要薄弱知识点 / 错题明细表 / 错因分析及证据 /
知识依赖图 / 题型地图 / 复习计划 / 复测记录 / 后续学习建议 / 数据来源与不确定性说明。

所有内容来自结构化数据；信息不足的章节如实标注，不用装饰性文字填充。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, SkillError, load_config, read_json, setup_logger, write_json
from error_causes import format_error_causes


def _error_causes_filter(codes, with_code: bool = False) -> str:
    """Jinja 过滤器：多选错因展示。Undefined/None -> 「未提供」。"""
    if codes is None or isinstance(codes, bool):
        return "未提供"
    if not isinstance(codes, (list, tuple, str, int, float)):
        return "未提供"
    return format_error_causes(codes, with_code=bool(with_code))


def _overview(data: dict, priority: dict) -> dict[str, Any]:
    questions = data.get("questions", [])
    with_answer = sum(1 for q in questions if q.get("student_answer") or q.get("student_solution"))
    without_answer = len(questions) - with_answer
    with_latex = sum(1 for q in questions if q.get("latex"))
    matched = sum(1 for q in questions if q.get("knowledge_points"))
    unmatched = len(questions) - matched
    issues = [f"{q['question_id']}：{'；'.join(q['recognition_issues'])}"
              for q in questions if q.get("recognition_issues")]
    return {
        "total": len(questions),
        "with_student_data": with_answer,
        "without_student_data": without_answer,
        "with_formula": with_latex,
        "knowledge_matched": matched,
        "knowledge_unmatched": unmatched,
        "recognition_issues": issues,
    }


def _render(template_path: Path, context: dict) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as e:  # pragma: no cover
        raise SkillError("缺少依赖 Jinja2，请先执行 pip install -r requirements.txt") from e
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    # v3.1.0 多选错因展示过滤器：{{ ea.error_codes|error_causes }} /
    # {{ ea.error_codes|error_causes(True) }}（带代码前缀）
    env.filters["error_causes"] = _error_causes_filter
    template = env.get_template(template_path.name)
    return template.render(**context)


def generate_markdown(
    data: dict,
    priority: dict | None,
    study_plan: dict | None,
    config: dict,
    logger=None,
) -> tuple[str, dict]:
    """渲染 Markdown 报告，返回 (markdown 文本, 模板上下文)。"""
    rconf = config["report"]
    assets_dir = rconf["assets_subdir"]
    context = {
        "title": rconf["title"],
        "meta": data.get("meta", {}),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overview": _overview(data, priority or {}),
        "questions": data.get("questions", []),
        "priority_points": (priority or {}).get("knowledge_points", []),
        "priority_meta": (priority or {}).get("meta", {}),
        "global_notes": (priority or {}).get("global", {}).get("missing_data_notes", []),
        "study_plan": study_plan,
        "graph_assets": {
            "knowledge_graph_png": f"{assets_dir}/knowledge-graph.png",
            "knowledge_graph_svg": f"{assets_dir}/knowledge-graph.svg",
            "topic_map_png": f"{assets_dir}/topic-map.png",
            "topic_map_svg": f"{assets_dir}/topic-map.svg",
        },
    }
    return _render(PROJECT_ROOT / "templates" / "report.md.j2", context), context


def generate_html(
    data: dict,
    priority: dict | None,
    study_plan: dict | None,
    config: dict,
    logger=None,
) -> str:
    """渲染 HTML 版报告（浏览器预览/备用排版）。"""
    template_path = PROJECT_ROOT / "templates" / "report.html.j2"
    if not template_path.exists():
        raise SkillError(f"HTML 模板不存在：{template_path}")
    _, context = generate_markdown(data, priority, study_plan, config, logger)
    return _render(template_path, context)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Markdown 分析报告")
    parser.add_argument("--input", required=True, help="questions.json 路径")
    parser.add_argument("--output", required=True, help="输出 analysis.md 路径")
    parser.add_argument("--priority", default=None, help="priority.json 路径（可选）")
    parser.add_argument("--study-plan", default=None, help="study_plan.json 路径（可选）")
    parser.add_argument("--html", action="store_true", help="同时生成 HTML 版报告（analysis.html）")
    args = parser.parse_args()

    logger = setup_logger()
    try:
        data = read_json(Path(args.input))
        config = load_config()
        priority = read_json(Path(args.priority)) if args.priority and Path(args.priority).exists() else None
        study_plan = read_json(Path(args.study_plan)) if args.study_plan and Path(args.study_plan).exists() else None
        markdown, _ = generate_markdown(data, priority, study_plan, config, logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    result = {"ok": True, "length": len(markdown), "output": str(out)}
    if args.html:
        try:
            html = generate_html(data, priority, study_plan, config, logger)
            html_path = out.with_suffix(".html")
            html_path.write_text(html, encoding="utf-8")
            result["html"] = str(html_path)
        except SkillError as e:
            result["html_error"] = str(e)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
