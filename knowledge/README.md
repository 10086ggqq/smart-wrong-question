# 知识点库说明

本目录存放知识点定义与依赖关系，供 `scripts/analyze_knowledge.py` 匹配使用。

## 当前文件

- `sample_knowledge_base.json`：高中数学示例库（11 个知识点，含先修关系、常见题型、常见错误）。

## 数据结构

```json
{
  "meta": {"name": "...", "subject": "...", "version": "..."},
  "knowledge_points": [
    {
      "id": "唯一英文id",
      "name": "知识点名称",
      "subject": "学科",
      "keywords": ["匹配关键词"],
      "prerequisites": ["先修知识点id"],
      "common_error_types": ["易犯错因"],
      "typical_topics": ["典型题型"],
      "notes": "补充说明"
    }
  ]
}
```

## 扩展方式

1. 复制 JSON 文件，修改 `meta`。
2. 按同样结构追加知识点；`prerequisites` 引用本文件内的 id。
3. 在 `config/default.yaml` 的 `paths.knowledge_base` 指向新文件，或运行脚本时传 `--knowledge-base`。

## 依赖关系来源与验证

- 库内 `prerequisites` 依据主流教材章节顺序整理，`verified: true`。
- 模型推断出的新依赖关系必须标记 `待验证`，不得直接当作事实。
- 不能因为两个知识点同时出现在一道题中，就认定存在先修关系。
