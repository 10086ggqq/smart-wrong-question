"""主编排入口：串联完整分析流水线。

用法：
    python scripts/run_analysis.py --input <文件> --output output/ --days 7 --daily-minutes 60

流水线：
    extract_document → normalize_questions → analyze_knowledge
    → calculate_priority → build_study_plan → render_graphs
    → generate_markdown → generate_pdf → validate_output

输入可以是：
- Markdown / TXT / PDF 错题文件；
- 已结构化的 questions.json（--format json）。

中间产物全部落盘到输出目录，便于分步复查与复用。
任何一步失败都会明确报告失败步骤，不会虚假成功。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from common import PROJECT_ROOT, SkillError, load_config, read_json, setup_logger, write_json  # noqa: E402
from analyze_knowledge import analyze_knowledge, load_knowledge_base  # noqa: E402
from build_study_plan import build_study_plan  # noqa: E402
from calculate_priority import calculate_priority  # noqa: E402
from extract_document import extract_document  # noqa: E402
from generate_markdown import generate_html, generate_markdown  # noqa: E402
from generate_pdf import generate_pdf  # noqa: E402
from normalize_questions import normalize_questions  # noqa: E402
from render_graphs import render_knowledge_graph, render_topic_map  # noqa: E402
from validate_output import validate_output  # noqa: E402


def run_pipeline(
    input_path: Path,
    output_dir: Path,
    days: int | None = None,
    daily_minutes: int | None = None,
    subject: str | None = None,
    logger=None,
) -> dict[str, Any]:
    logger = logger or setup_logger()
    config = load_config()
    rconf = config["report"]
    output_dir = Path(output_dir)
    (output_dir / rconf["assets_subdir"]).mkdir(parents=True, exist_ok=True)

    steps: list[dict[str, Any]] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="swq_"))
    try:
        # 1. 提取
        if input_path.suffix.lower() == ".json":
            data = read_json(input_path)
            questions_path = output_dir / "questions.json"
            write_json(questions_path, data)
            steps.append({"step": "load-questions-json", "ok": True,
                          "detail": f"直接加载结构化题目 {input_path}"})
        else:
            extracted = extract_document(input_path, logger)
            if extracted["next_step"] != "normalize":
                raise SkillError(
                    "该输入需要视觉识别（图片/扫描 PDF）：" + "；".join(extracted["issues"])
                    + "。请先由 Agent 识别题目并保存为 questions.json，再用 --format json 重跑。"
                )
            steps.append({"step": "extract-document", "ok": True,
                          "detail": f"方式：{extracted['method']}，文本 {len(extracted['text'])} 字符"})
            raw_path = tmp_dir / "raw_text.txt"
            raw_path.write_text(extracted["text"], encoding="utf-8")
            data = normalize_questions(extracted["text"], extracted["source_file"],
                                       subject_hint=subject, logger=logger)
            questions_path = output_dir / "questions.json"
            write_json(questions_path, data)
            steps.append({"step": "normalize-questions", "ok": True,
                          "detail": f"识别 {data['meta']['question_count']} 道题"})

        # 2. 知识点分析
        kb = load_knowledge_base(logger=logger)
        data = analyze_knowledge(data, kb, logger)
        write_json(questions_path, data)
        steps.append({"step": "analyze-knowledge", "ok": True,
                      "detail": f"依赖边 {len(data.get('dependency_edges', []))} 条"})

        # 3. 优先级
        priority = calculate_priority(data, config, logger)
        priority_path = output_dir / "priority.json"
        write_json(priority_path, priority)
        steps.append({"step": "calculate-priority", "ok": True,
                      "detail": f"{len(priority['knowledge_points'])} 个薄弱知识点"})

        # 4. 复习计划
        plan_days = days or config["study"]["default_days"]
        plan_minutes = daily_minutes or config["study"]["default_daily_minutes"]
        plan = build_study_plan(data, plan_days, plan_minutes, config, logger)
        plan_path = output_dir / "study_plan.json"
        write_json(plan_path, plan)
        steps.append({"step": "build-study-plan", "ok": True,
                      "detail": f"{plan['meta']['days']} 天，每日预算 {plan['meta']['budget_per_day']} 分钟"})

        # 5. 图表
        assets_dir = output_dir / rconf["assets_subdir"]
        graphs: dict[str, str] = {}
        graphs.update({f"knowledge_graph_{k}": v
                       for k, v in render_knowledge_graph(
                           data["questions"], kb, priority, config, assets_dir, logger).items()})
        graphs.update({f"topic_map_{k}": v
                       for k, v in render_topic_map(
                           data["questions"], kb, priority, config, assets_dir, logger).items()})
        steps.append({"step": "render-graphs", "ok": True,
                      "detail": "、".join(Path(p).name for p in graphs.values())})

        # 6. Markdown（含 HTML 预览版）
        markdown, _ = generate_markdown(data, priority, plan, config, logger)
        md_path = output_dir / rconf["markdown_filename"]
        md_path.write_text(markdown, encoding="utf-8")
        steps.append({"step": "generate-markdown", "ok": True, "detail": str(md_path)})

        html_path = output_dir / "analysis.html"
        try:
            html_path.write_text(
                generate_html(data, priority, plan, config, logger), encoding="utf-8")
            steps.append({"step": "generate-html", "ok": True, "detail": str(html_path)})
        except SkillError as e:
            steps.append({"step": "generate-html", "ok": False, "detail": str(e)})

        # 7. PDF（失败不阻塞：保留 Markdown 并如实记录）
        pdf_path = output_dir / rconf["pdf_filename"]
        try:
            generate_pdf(md_path, pdf_path, rconf["title"], logger)
            steps.append({"step": "generate-pdf", "ok": True, "detail": str(pdf_path)})
        except SkillError as e:
            steps.append({"step": "generate-pdf", "ok": False, "detail": str(e)})
            logger.error("PDF 生成失败（Markdown 已保留）：%s", e)

        # 8. 汇总数据
        data_path = output_dir / rconf["data_filename"]
        write_json(data_path, {
            "meta": {**data.get("meta", {}), "generated_at": datetime.now().isoformat(timespec="seconds")},
            "questions": data.get("questions", []),
            "priority": priority,
            "study_plan": plan,
        })

        # 9. 验证
        validation = validate_output(output_dir, config, logger)
        steps.append({"step": "validate-output", "ok": validation["ok"],
                      "detail": validation["summary"]})

        return {
            "ok": all(s["ok"] for s in steps if s["step"] != "generate-pdf") and validation["ok"],
            "steps": steps,
            "validation": validation,
            "files": {
                "markdown": str(md_path),
                "html": str(html_path) if html_path.exists() else None,
                "pdf": str(pdf_path) if pdf_path.exists() else None,
                "data": str(data_path),
                "questions": str(questions_path),
                "priority": str(priority_path),
                "study_plan": str(plan_path),
                "graphs": {k: str(v) for k, v in graphs.items()},
            },
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="智能错题分析完整流水线")
    parser.add_argument("--input", required=True, help="输入文件（md/txt/pdf/json）")
    parser.add_argument("--output", default="output", help="输出目录（默认 output/）")
    parser.add_argument("--days", type=int, default=None, help="复习天数（默认 7，最大 14）")
    parser.add_argument("--daily-minutes", type=int, default=None, help="每日学习时间（分钟，默认 60）")
    parser.add_argument("--subject", default=None, help="学科提示（可选）")
    args = parser.parse_args()

    logger = setup_logger()
    input_path = Path(args.input)
    if not input_path.exists():
        print(json.dumps({"ok": False, "error": f"输入文件不存在：{input_path}"},
                         ensure_ascii=False, indent=2))
        return 2

    try:
        result = run_pipeline(
            input_path, Path(args.output),
            days=args.days, daily_minutes=args.daily_minutes,
            subject=args.subject, logger=logger,
        )
    except SkillError as e:
        print(json.dumps({"ok": False, "failed_step": "pipeline", "error": str(e)},
                         ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
