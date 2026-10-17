"""图表渲染测试：文件生成、SVG 有效性、中文字体依赖处理。"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from common import PROJECT_ROOT, RenderError, read_json
from render_graphs import render_knowledge_graph, render_topic_map


@pytest.fixture()
def graph_inputs():
    """从示例文件现算图表输入（不依赖 output/ 已有产物，保证测试独立）。"""
    from analyze_knowledge import analyze_knowledge, load_knowledge_base
    from calculate_priority import calculate_priority
    from common import load_config, read_text
    from normalize_questions import normalize_questions

    text = read_text(PROJECT_ROOT / "examples" / "sample_questions.md")
    data = normalize_questions(text, "sample_questions.md")
    kb = load_knowledge_base()
    data = analyze_knowledge(data, kb)
    priority = calculate_priority(data, load_config())
    return data["questions"], kb, priority


class TestKnowledgeGraph:
    def test_files_generated(self, graph_inputs, tmp_path, chinese_font_available):
        if not chinese_font_available:
            pytest.skip("环境中无中文字体")
        from common import load_config
        questions, kb, priority = graph_inputs
        out = render_knowledge_graph(questions, kb, priority, load_config(), tmp_path)
        assert (tmp_path / "knowledge-graph.svg").exists()
        assert (tmp_path / "knowledge-graph.png").exists()
        assert (tmp_path / "knowledge-graph.png").stat().st_size > 10_000

    def test_svg_is_valid_xml(self, graph_inputs, tmp_path, chinese_font_available):
        if not chinese_font_available:
            pytest.skip("环境中无中文字体")
        from common import load_config
        questions, kb, priority = graph_inputs
        render_knowledge_graph(questions, kb, priority, load_config(), tmp_path)
        tree = ET.parse(tmp_path / "knowledge-graph.svg")  # 不抛异常即为有效 XML
        assert tree.getroot() is not None

    def test_no_font_raises_clear_error(self, graph_inputs, tmp_path, monkeypatch):
        """无中文字体时必须给出清晰错误，而不是静默失败。"""
        from common import load_config
        import render_graphs
        monkeypatch.setattr(render_graphs, "register_matplotlib_cn_font", lambda: None)
        questions, kb, priority = graph_inputs
        with pytest.raises(RenderError, match="中文字体"):
            render_knowledge_graph(questions, kb, priority, load_config(), tmp_path)


class TestTopicMap:
    def test_files_generated(self, graph_inputs, tmp_path, chinese_font_available):
        if not chinese_font_available:
            pytest.skip("环境中无中文字体")
        from common import load_config
        questions, kb, priority = graph_inputs
        out = render_topic_map(questions, kb, priority, load_config(), tmp_path)
        assert (tmp_path / "topic-map.svg").exists()
        assert (tmp_path / "topic-map.png").exists()

    def test_no_knowledge_points_raises(self, tmp_path):
        """题目无知识点时不得渲染空图假装成功。"""
        from common import load_config
        kb = read_json(PROJECT_ROOT / "knowledge" / "sample_knowledge_base.json")
        with pytest.raises(RenderError, match="没有可渲染"):
            render_topic_map([], kb, None, load_config(), tmp_path)
