"""Auto session-title unit tests."""

from aio_agent_platform.core.auto_title import DEFAULT_PROMPT, MAX_TITLE_LENGTH, _clean_title


def test_default_prompt_has_message_placeholder():
    assert "{message}" in DEFAULT_PROMPT


def test_clean_title_strips_quotes_and_punctuation():
    assert _clean_title('"如何配置模型"。') == "如何配置模型"
    assert _clean_title("《周报总结》！") == "周报总结"
    assert _clean_title("  普通标题  ") == "普通标题"


def test_clean_title_takes_first_line():
    assert _clean_title("第一行标题\n这是一段多余的解释") == "第一行标题"


def test_clean_title_truncates_to_max_length():
    long_title = "标" * (MAX_TITLE_LENGTH + 50)
    result = _clean_title(long_title)
    assert result is not None
    assert len(result) == MAX_TITLE_LENGTH


def test_clean_title_empty_returns_none():
    assert _clean_title("") is None
    assert _clean_title("   ") is None
    assert _clean_title('""') is None
    assert _clean_title("。") is None
