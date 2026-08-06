"""Slash command text parsing.

``shlex.split`` handles quoted tokens so ``/cron create '0 9 * * *' 任务``
parses the cron expression as a single argument.
"""

from __future__ import annotations

import shlex
from uuid import UUID

from .models import Command, CommandArg


class ParseError(Exception):
    def __init__(self, message: str, usage: str) -> None:
        self.message = message
        self.usage = usage
        super().__init__(message)


def split_tokens(raw: str) -> list[str]:
    try:
        return shlex.split(raw)
    except ValueError:
        # Unbalanced quotes — fall back to plain whitespace split.
        return raw.split()


def parse_command(raw: str, cmd: Command) -> dict[str, object]:
    tokens = split_tokens(raw)
    args_tokens = tokens[1:] if tokens else []
    parsed: dict[str, object] = {}
    idx = 0

    for arg in cmd.args:
        if arg.variadic:
            if idx < len(args_tokens):
                parsed[arg.name] = " ".join(args_tokens[idx:])
                idx = len(args_tokens)
            elif arg.required:
                raise ParseError(f"缺少参数 <{arg.name}>", cmd.usage_text)
            break
        if idx >= len(args_tokens):
            if arg.required:
                raise ParseError(f"缺少参数 <{arg.name}>", cmd.usage_text)
            continue
        value = args_tokens[idx]
        idx += 1
        parsed[arg.name] = _coerce(value, arg)

    if idx < len(args_tokens) and not any(a.variadic for a in cmd.args):
        extra = " ".join(args_tokens[idx:])
        raise ParseError(f"参数过多：{extra}", cmd.usage_text)

    for arg in cmd.args:
        if arg.name in parsed and arg.choices and parsed[arg.name] not in arg.choices:
            raise ParseError(
                f"参数 <{arg.name}> 取值必须为：{' / '.join(arg.choices)}",
                cmd.usage_text,
            )

    return parsed


def _coerce(value: str, arg: CommandArg) -> object:
    if arg.kind == "int":
        try:
            return int(value)
        except ValueError:
            raise ParseError(f"参数 <{arg.name}> 必须是整数", arg_usage(arg))
    if arg.kind == "uuid":
        try:
            return str(UUID(value))
        except ValueError:
            raise ParseError(f"参数 <{arg.name}> 必须是合法的 UUID", arg_usage(arg))
    return value


def arg_usage(arg: CommandArg) -> str:
    if arg.variadic:
        return f"/<cmd> <{arg.name}...>"
    return f"/<cmd> <{arg.name}>"
