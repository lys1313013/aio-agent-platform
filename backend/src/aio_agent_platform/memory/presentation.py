"""Stable memory references shared by prompts and memory tools."""

import json


def memory_reference(memory) -> dict:
    agent_id = getattr(memory, "agent_id", None)
    memory_id = getattr(memory, "id", None)
    return {
        "id": str(memory_id) if memory_id else None,
        "version": getattr(memory, "version", None),
        "layer": getattr(memory, "layer", None),
        "scope": "agent" if agent_id else "user",
        "agent_id": str(agent_id) if agent_id else None,
        "content": memory.content,
    }


def format_memory(memory) -> str:
    return json.dumps(memory_reference(memory), ensure_ascii=False)


def refresh_memory_prompt(prompt: str, output: str) -> str:
    """Replace only serialized memory rows after a successful tool mutation."""
    try:
        result = json.loads(output)
        action, memory = result["action"], result["memory"]
        if action not in {"updated", "merged", "deleted"} or not memory.get("id"):
            return prompt
    except (ValueError, KeyError, TypeError, AttributeError):
        return prompt
    lines = []
    for line in prompt.splitlines():
        try:
            old = json.loads(line[2:]) if line.startswith('- {"id":') else None
        except ValueError:
            old = None
        if isinstance(old, dict) and old.get("id") == memory["id"]:
            if action != "deleted":
                lines.append("- " + json.dumps(memory, ensure_ascii=False))
        else:
            lines.append(line)
    return "\n".join(lines)
