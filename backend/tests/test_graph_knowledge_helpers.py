"""Unit tests for graph knowledge base chunking & extraction helpers."""

import json

from aio_agent_platform.graph_knowledge.chunking import chunk_text
from aio_agent_platform.graph_knowledge.extraction import _parse_llm_json, normalize_name


def test_chunk_markdown_sections():
    text = "# 第一章\n\n这里是第一段内容。\n\n## 子节\n\n这里是子节内容。\n\n# 第二章\n\n" + "很长" * 500 + "的结尾。"
    chunks = chunk_text(text)
    assert len(chunks) >= 2
    assert all(c and c.strip() for c in chunks)


def test_chunk_size_bounded():
    text = "没有标题的纯文本" * 100
    chunks = chunk_text(text)
    assert chunks
    assert all(len(c) <= 800 for c in chunks)


def test_chunk_empty():
    assert chunk_text("   ") == []


def test_normalize_name():
    assert normalize_name(" 阿里巴巴  集团 ") == "阿里巴巴 集团"


def test_parse_llm_json_fence():
    payload = {
        "entities": [{"name": "张三", "type": "人物", "description": "负责人"}],
        "relationships": [
            {"source": "张三", "target": "项目A", "relation_type": "负责", "confidence": 0.9}
        ],
    }
    raw = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    data = _parse_llm_json(raw)
    assert len(data["entities"]) == 1
    assert data["entities"][0]["name"] == "张三"
    assert len(data["relationships"]) == 1


def test_parse_llm_json_embedded():
    data = _parse_llm_json('前文 {"entities": []} 后文')
    assert data["entities"] == []
