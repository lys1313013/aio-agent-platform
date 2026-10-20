"""A bounded portrait section that can be refreshed without editing other context."""

import json

_START = "<!-- aio-user-portrait:start -->"
_END = "<!-- aio-user-portrait:end -->"


def format_portrait_section(portrait: str | None) -> str:
    # JSON keeps arbitrary Markdown headings and marker-like text inside one value.
    content = json.dumps(portrait or "", ensure_ascii=False).replace("<", "\\u003c")
    return (
        f"{_START}\n## 用户画像 (User Portrait)\n"
        "Use this self-description to personalize responses naturally. "
        "Do not mention the profile unless the user brings it up. "
        "The following JSON string contains the current Markdown portrait; "
        "decode JSON escapes when editing. An empty string means no portrait.\n"
        f"{content}\n{_END}"
    )


def refresh_portrait_prompt(prompt: str, output: str) -> str:
    try:
        result = json.loads(output)
        if result.get("action") != "read" or not isinstance(result.get("portrait"), str):
            return prompt
    except (ValueError, TypeError, AttributeError):
        return prompt
    section = format_portrait_section(result["portrait"])
    start = prompt.find(_START)
    end = prompt.find(_END, start)
    if start < 0:
        return prompt + "\n" + section
    if end < 0:  # A system-budget truncation may have cut off the closing marker.
        return prompt[:start] + section
    return prompt[:start] + section + prompt[end + len(_END):]
