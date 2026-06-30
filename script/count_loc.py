#!/usr/bin/env python3
"""
代码行数统计脚本
用法: python script/count_loc.py [目录路径，默认当前目录]
"""

import os
import sys
from collections import defaultdict
from pathlib import Path

# 要忽略的目录名
IGNORE_DIRS = {
    "node_modules",
    ".git",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    "target",
    ".tox",
    ".eggs",
}

# 要忽略的文件名/模式
IGNORE_FILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Pipfile.lock",
    "poetry.lock",
}

# 视为代码/配置/文档的扩展名
CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte",
             ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
             ".rb", ".php", ".swift", ".kt", ".scala", ".cs"}
STYLE_EXTS = {".css", ".scss", ".sass", ".less"}
MARKUP_EXTS = {".html", ".xml", ".svg"}
DATA_EXTS = {".json", ".yaml", ".yml", ".toml", ".sql", ".sh", ".bash"}
DOC_EXTS = {".md", ".rst", ".txt"}

ALL_EXTS = CODE_EXTS | STYLE_EXTS | MARKUP_EXTS | DATA_EXTS | DOC_EXTS


def should_skip_dir(name: str) -> bool:
    return name in IGNORE_DIRS or name.endswith(".egg-info")


def should_skip_file(name: str) -> bool:
    if name in IGNORE_FILES:
        return True
    if "lock" in name.lower() and name.endswith((".json", ".yaml", ".yml")):
        return True
    return False


def count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except (OSError, UnicodeDecodeError):
        return 0


def stat(root: Path):
    ext_stats = defaultdict(lambda: {"lines": 0, "files": 0})
    dir_stats = defaultdict(lambda: {"lines": 0, "files": 0})

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]

        rel = Path(dirpath).relative_to(root)
        if rel.parts:
            top = rel.parts[0]
            if len(rel.parts) >= 2:
                top = f"{rel.parts[0]}/{rel.parts[1]}"
        else:
            top = "."

        for fname in filenames:
            if should_skip_file(fname):
                continue
            ext = Path(fname).suffix.lower()
            if ext not in ALL_EXTS:
                continue

            fpath = Path(dirpath) / fname
            lines = count_lines(fpath)
            if lines == 0:
                continue

            ext_stats[ext]["lines"] += lines
            ext_stats[ext]["files"] += 1
            dir_stats[top]["lines"] += lines
            dir_stats[top]["files"] += 1

    return ext_stats, dir_stats


def print_table(title: str, headers: list[str], rows: list[list]):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
              for i, h in enumerate(headers)]
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(f"  {header_line}")
    print(f"  {'─' * (sum(widths) + 2 * (len(widths) - 1))}")
    for row in rows:
        line = "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers)))
        print(f"  {line}")


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    if not root.is_dir():
        print(f"错误: {root} 不是有效目录", file=sys.stderr)
        sys.exit(1)

    print(f"📂 扫描目录: {root}")

    ext_stats, dir_stats = stat(root)

    categories = [
        ("代码", sorted(e for e in CODE_EXTS if e in ext_stats)),
        ("样式", sorted(e for e in STYLE_EXTS if e in ext_stats)),
        ("标记", sorted(e for e in MARKUP_EXTS if e in ext_stats)),
        ("配置/数据", sorted(e for e in DATA_EXTS if e in ext_stats)),
        ("文档", sorted(e for e in DOC_EXTS if e in ext_stats)),
    ]

    total_lines = 0
    total_files = 0
    code_lines = 0

    for cat_name, exts in categories:
        if not exts:
            continue
        rows = []
        cat_lines = 0
        cat_files = 0
        for ext in exts:
            s = ext_stats[ext]
            rows.append([ext, f"{s['lines']:,}", f"{s['files']}"])
            cat_lines += s["lines"]
            cat_files += s["files"]
        rows.append([f"[{cat_name} 小计]", f"{cat_lines:,}", f"{cat_files}"])
        print_table(f"按语言分类 — {cat_name}", ["扩展名", "行数", "文件数"], rows)
        total_lines += cat_lines
        total_files += cat_files
        if cat_name in ("代码", "样式", "标记"):
            code_lines += cat_lines

    dir_rows = sorted(dir_stats.items(), key=lambda x: -x[1]["lines"])
    dir_table = [[d, f"{s['lines']:,}", f"{s['files']}"] for d, s in dir_rows]
    print_table("按目录统计", ["目录", "行数", "文件数"], dir_table)

    print(f"\n{'=' * 60}")
    print(f"  📊 汇总")
    print(f"{'=' * 60}")
    print(f"  纯代码 (代码+样式+标记):  {code_lines:>10,} 行")
    print(f"  全部文件 (含文档/配置):    {total_lines:>10,} 行")
    print(f"  文件总数:                 {total_files:>10} 个")
    print()


if __name__ == "__main__":
    main()
