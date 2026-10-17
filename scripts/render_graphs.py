"""图表渲染：知识依赖图 + 题型地图（matplotlib，输出 SVG + PNG）。

设计要求：
- 清晰的层次结构（按先修深度分层）；
- 方向明确的连线（先修 → 后续，箭头标识）；
- 薄弱程度颜色标记 + 完整图例 + 关系说明；
- 中文字体自动注册；SVG 文字转路径保证任何查看器正常显示。

诚实性原则：无复测数据时掌握程度未知，颜色仅表示「出错频次」，
并在图下注明，不伪装成测量出的掌握率。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    PROJECT_ROOT,
    RenderError,
    SkillError,
    load_config,
    read_json,
    register_matplotlib_cn_font,
    setup_logger,
)

# 配色（浅色主题，适合报告打印）
COLOR_STRONG = "#2e7d32"   # 绿：未出错/掌握良好
COLOR_WEAK = "#c62828"     # 红：多次出错
COLOR_MEDIUM = "#ef6c00"   # 橙：出错 1 次
COLOR_UNKNOWN = "#9e9e9e"  # 灰：先修知识，本批错题未涉及
COLOR_EDGE = "#546e7a"
COLOR_TEXT = "#212121"


def _edges_from_kb(kb: dict) -> list[tuple[str, str, bool]]:
    """从知识库 prerequisites 字段构建先修边（全部为已核验关系）。"""
    edges: list[tuple[str, str, bool]] = []
    ids = {kp["id"] for kp in kb.get("knowledge_points", [])}
    for kp in kb.get("knowledge_points", []):
        for pre in kp.get("prerequisites", []):
            if pre in ids:
                edges.append((pre, kp["id"], True))
    return edges


def _collect_nodes(questions: list[dict], kb: dict, priority_index: dict) -> tuple[dict, list]:
    """收集图节点与依赖边。

    节点 = 错题涉及的知识点 + 其先修链上的知识点（灰色展示）。
    """
    edges = _edges_from_kb(kb)

    wrong_counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for q in questions:
        for kp in q.get("knowledge_points", []):
            kp_id = kp["knowledge_point_id"]
            wrong_counts[kp_id] = wrong_counts.get(kp_id, 0) + 1
            names[kp_id] = kp["knowledge_point_name"]

    # 补齐先修链
    frontier = list(wrong_counts.keys())
    seen = set(frontier)
    while frontier:
        cur = frontier.pop()
        for src, tgt, _ in edges:
            if tgt == cur and src not in seen:
                seen.add(src)
                frontier.append(src)
                if src in kb_names(kb):
                    names.setdefault(src, kb_names(kb)[src])

    nodes: dict[str, dict] = {}
    for kp_id in seen:
        wc = wrong_counts.get(kp_id, 0)
        prio = priority_index.get(kp_id, {}).get("priority", 0.0)
        if wc >= 2:
            color, status = COLOR_WEAK, "多次出错"
        elif wc == 1:
            color, status = COLOR_MEDIUM, "出错 1 次"
        else:
            color, status = COLOR_UNKNOWN, "先修知识（本批未出错）"
        nodes[kp_id] = {
            "id": kp_id,
            "name": names.get(kp_id, kp_id),
            "wrong_count": wc,
            "priority": prio,
            "color": color,
            "status": status,
        }
    return nodes, edges


def kb_names(kb: dict) -> dict[str, str]:
    return {kp["id"]: kp["name"] for kp in kb.get("knowledge_points", [])}


def _levels(nodes: dict, edges: list) -> dict[str, int]:
    """按先修依赖计算分层深度。"""
    ids = set(nodes.keys())
    pres: dict[str, list[str]] = {n: [] for n in nodes}
    for src, tgt, _ in edges:
        if src in ids and tgt in ids:
            pres[tgt].append(src)
    level: dict[str, int] = {}

    def depth_of(n: str, seen: frozenset) -> int:
        if n in level:
            return level[n]
        if n in seen:
            return 0
        ps = [p for p in pres[n]]
        d = 0 if not ps else 1 + max(depth_of(p, seen | {n}) for p in ps)
        level[n] = d
        return d

    for n in nodes:
        depth_of(n, frozenset())
    return level


def render_knowledge_graph(
    questions: list[dict],
    kb: dict,
    priority: dict | None,
    config: dict,
    outdir: Path,
    logger=None,
) -> dict[str, str]:
    family = register_matplotlib_cn_font()
    if family is None:
        raise RenderError("未找到中文字体，无法渲染图表。请安装中文字体（如微软雅黑/黑体/Noto Sans CJK）。")
    try:
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
    except ImportError as e:  # pragma: no cover
        raise RenderError(f"缺少依赖 matplotlib：{e}") from e

    gconf = config["graphs"]["knowledge_graph"]
    priority_index = {r["knowledge_point_id"]: r for r in (priority or {}).get("knowledge_points", [])}
    nodes, edges = _collect_nodes(questions, kb, priority_index)
    levels = _levels(nodes, edges)

    by_level: dict[int, list[str]] = {}
    for n, lv in levels.items():
        by_level.setdefault(lv, []).append(n)
    for lv in by_level:
        by_level[lv].sort(key=lambda n: (-nodes[n]["priority"], -nodes[n]["wrong_count"]))

    max_level = max(by_level.keys(), default=0)
    fig_h = 3.2 + 2.2 * max_level
    fig_w = max(12, 3.2 * max(len(v) for v in by_level.values()))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    pos: dict[str, tuple[float, float]] = {}
    for lv, members in by_level.items():
        y = -lv * 2.2
        n = len(members)
        for i, kp_id in enumerate(members):
            x = (i - (n - 1) / 2) * 2.6
            pos[kp_id] = (x, y)

    # 画边（先修 → 后续）
    for src, tgt, verified in edges:
        if src not in pos or tgt not in pos:
            continue
        x1, y1 = pos[src]
        x2, y2 = pos[tgt]
        ax.annotate(
            "", xy=(x2, y2 + 0.32), xytext=(x1, y1 - 0.32),
            arrowprops=dict(
                arrowstyle="-|>", color=COLOR_EDGE, lw=1.6,
                linestyle="-" if verified else "--",
                shrinkA=2, shrinkB=2,
                connectionstyle="arc3,rad=0.08",
            ),
        )
        if not verified:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2, "待验证", fontsize=8,
                    color="#b71c1c", ha="center")

    # 画节点
    base_size = gconf["node_base_size"]
    for kp_id, (x, y) in pos.items():
        node = nodes[kp_id]
        size = base_size * (1 + 0.9 * min(node["wrong_count"], 4))
        ax.scatter([x], [y], s=size, c=node["color"], alpha=0.92,
                   edgecolors="#37474f", linewidths=1.4, zorder=3)
        ax.text(x, y, str(node["wrong_count"]) if node["wrong_count"] else "—",
                ha="center", va="center", fontsize=10, color="white",
                fontweight="bold", zorder=4)
        ax.annotate(node["name"], (x, y), xytext=(0, -34), textcoords="offset points",
                    ha="center", va="top", fontsize=10.5, color=COLOR_TEXT, zorder=4)
        ax.annotate(f"优先级 {node['priority']:.2f}", (x, y), xytext=(0, 26),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=8.5, color="#455a64", zorder=4)

    # 层级标注
    for lv in by_level:
        ax.text(-fig_w / 2 + 0.4, -lv * 2.2, f"第 {lv + 1} 层", fontsize=9,
                color="#78909c", ha="left", va="center")

    legend_items = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_WEAK,
               markeredgecolor="#37474f", markersize=12, label="多次出错（薄弱）"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_MEDIUM,
               markeredgecolor="#37474f", markersize=10, label="出错 1 次"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_UNKNOWN,
               markeredgecolor="#37474f", markersize=8, label="先修知识（本批未出错）"),
        Line2D([0], [0], color=COLOR_EDGE, lw=1.6, label="先修关系（已核验）"),
        Line2D([0], [0], color=COLOR_EDGE, lw=1.6, linestyle="--", label="先修关系（待验证）"),
    ]
    ax.legend(handles=legend_items, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              fontsize=9, frameon=True, title="图例", title_fontsize=10)

    ax.set_title("知识依赖图（先修关系 → 薄弱标记）", fontsize=15, color=COLOR_TEXT, pad=18)
    ax.text(0.5, -0.06 - 0.035 * (max_level + 1),
            "说明：箭头方向为先修 → 后续；节点内数字为错题数量；节点大小随错题数增大。\n"
            "本图未提供复测数据，颜色仅表示出错频次，掌握程度标记为「未知」。",
            transform=ax.transAxes, ha="center", va="top", fontsize=9, color="#546e7a")
    ax.axis("off")

    prefix = gconf["filename_prefix"]
    dpi = gconf.get("dpi", 150)
    outputs: dict[str, str] = {}
    for fmt in gconf["formats"]:
        path = outdir / f"{prefix}.{fmt}"
        fig.savefig(path, format=fmt, dpi=dpi, bbox_inches="tight",
                    facecolor="white")
        outputs[fmt] = str(path)
    plt.close(fig)
    return outputs


def render_topic_map(
    questions: list[dict],
    kb: dict,
    priority: dict | None,
    config: dict,
    outdir: Path,
    logger=None,
) -> dict[str, str]:
    """题型地图：按主要考点分组的卡片图，帮助理解「遇到某类题怎么想、怎么做」。"""
    family = register_matplotlib_cn_font()
    if family is None:
        raise RenderError("未找到中文字体，无法渲染图表。")
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError as e:  # pragma: no cover
        raise RenderError(f"缺少依赖 matplotlib：{e}") from e

    kb_index = {kp["id"]: kp for kp in kb.get("knowledge_points", [])}
    priority_index = {r["knowledge_point_id"]: r for r in (priority or {}).get("knowledge_points", [])}

    # 按主要考点聚类
    groups: dict[str, dict] = {}
    for q in questions:
        primaries = [kp for kp in q.get("knowledge_points", []) if kp.get("relevance_type") == "主要考点"]
        if not primaries:
            primaries = q.get("knowledge_points", [])[:1]
        if not primaries:
            continue
        kp = primaries[0]
        kp_id = kp["knowledge_point_id"]
        g = groups.setdefault(kp_id, {
            "name": kp["knowledge_point_name"],
            "question_ids": [],
            "error_types": [],
            "priority": priority_index.get(kp_id, {}).get("priority", 0.0),
        })
        g["question_ids"].append(q["question_id"])
        for et in kb_index.get(kp_id, {}).get("common_error_types", []):
            if et not in g["error_types"]:
                g["error_types"].append(et)

    if not groups:
        raise RenderError("没有可渲染的题型分组：题目未匹配到主要考点。")

    ordered = sorted(groups.items(), key=lambda item: -item[1]["priority"])
    n = len(ordered)
    cols = 2 if n > 1 else 1
    rows = (n + cols - 1) // cols
    card_h, card_w = 2.5, 8.6
    fig_w = cols * card_w + 1.2
    fig_h = rows * card_h + 1.6

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)

    for idx, (kp_id, g) in enumerate(ordered):
        row, col = divmod(idx, cols)
        x0 = 0.6 + col * card_w
        y0 = fig_h - 1.0 - (row + 1) * card_h + 0.25
        # 分组框
        box = FancyBboxPatch((x0, y0), card_w - 0.4, card_h - 0.35,
                             boxstyle="round,pad=0.08,rounding_size=0.18",
                             linewidth=1.4, edgecolor="#37474f",
                             facecolor="#f5f7fa", zorder=2)
        ax.add_patch(box)
        ax.text(x0 + 0.3, y0 + card_h - 0.72, f"题型 {idx + 1}：{g['name']}",
                fontsize=12, fontweight="bold", color=COLOR_TEXT, va="center")
        q_text = "、".join(g["question_ids"])
        ax.text(x0 + 0.3, y0 + card_h - 1.22,
                f"对应错题：{q_text}    优先级：{g['priority']:.2f}",
                fontsize=9.5, color="#37474f", va="center")
        errors = "、".join(g["error_types"]) if g["error_types"] else "（知识库未登记）"
        ax.text(x0 + 0.3, y0 + card_h - 1.72,
                f"易错环节：{errors}",
                fontsize=9.5, color="#b71c1c", va="center")
        ax.text(x0 + 0.3, y0 + 0.32,
                "解题步骤骨架与识别特征由分析 Agent 按实际课程内容补充核验。",
                fontsize=8.5, color="#78909c", va="center")

    ax.set_title("题型地图（按主要考点分组）", fontsize=15, color=COLOR_TEXT, pad=14)
    ax.text(fig_w / 2, 0.28,
            "说明：每个分组对应一类题型；优先级来自规则评分模型；"
            "易错环节来自知识点库登记内容。",
            ha="center", fontsize=9, color="#546e7a")
    ax.axis("off")

    prefix = config["graphs"]["topic_map"]["filename_prefix"]
    dpi = config["graphs"]["topic_map"].get("dpi", 150)
    outputs: dict[str, str] = {}
    for fmt in config["graphs"]["topic_map"]["formats"]:
        path = outdir / f"{prefix}.{fmt}"
        fig.savefig(path, format=fmt, dpi=dpi, bbox_inches="tight", facecolor="white")
        outputs[fmt] = str(path)
    plt.close(fig)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染知识依赖图与题型地图")
    parser.add_argument("--input", required=True, help="questions.json 路径")
    parser.add_argument("--output", required=True, help="输出目录（图表文件）")
    parser.add_argument("--priority", default=None, help="priority.json 路径（可选）")
    args = parser.parse_args()

    logger = setup_logger()
    try:
        data = read_json(Path(args.input))
        kb = read_json(PROJECT_ROOT / load_config()["paths"]["knowledge_base"])
        priority = read_json(Path(args.priority)) if args.priority and Path(args.priority).exists() else None
        outdir = Path(args.output)
        outdir.mkdir(parents=True, exist_ok=True)
        g1 = render_knowledge_graph(data["questions"], kb, priority, load_config(), outdir, logger)
        g2 = render_topic_map(data["questions"], kb, priority, load_config(), outdir, logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps({"ok": True, "knowledge_graph": g1, "topic_map": g2},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
