"""Tool execution engine — security checks + sandbox dispatch."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aio_agent_platform.db.sanitize import sanitize_pg_text
from aio_agent_platform.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from aio_agent_platform.core.agent import DelegationContext
    from aio_agent_platform.sandbox import SandboxManager
    from aio_agent_platform.tools.mcp.manager import MCPManager
    from aio_agent_platform.tools.remote.executor import RemoteToolExecutor
    from aio_agent_platform.tools.remote.manager import RemoteToolManager


class SecurityError(Exception):
    """Raised when a tool call violates security policy."""


@dataclass
class ToolResult:
    """Result from tool execution."""

    tool_call_id: str
    name: str
    arguments: dict
    output: str
    success: bool
    error: str | None = None
    duration_ms: float = 0


class ToolExecutor:
    """
    Unified tool execution engine.

    Responsibilities:
    - Path safety validation (no /workspace traversal)
    - Dangerous command blacklist
    - Output truncation (>10K chars)
    - Sandbox dispatch for requires_sandbox tools
    """

    MAX_OUTPUT_SIZE = 10_000

    DANGEROUS_COMMANDS = (
        "rm -rf /",
        "mkfs",
        "dd if=/dev/zero",
        ":(){:|:&};:",
        ":(){ :|:& };:",
        "chmod -r 777 /",
        "> /dev/sda",
        "wget|sh",
        "curl|sh",
        "nc -l",
        "ncat -l",
    )

    def __init__(
        self,
        registry: ToolRegistry,
        sandbox_mgr: SandboxManager,
        direct_handlers: dict[str, Callable] | None = None,
        mcp_manager: MCPManager | None = None,
        remote_manager: RemoteToolManager | None = None,
        remote_executor: RemoteToolExecutor | None = None,
    ) -> None:
        self.registry = registry
        self.sandbox_mgr = sandbox_mgr
        self.direct_handlers: dict[str, Callable] = direct_handlers or {}
        self.mcp_manager: MCPManager | None = mcp_manager
        self.remote_manager: RemoteToolManager | None = remote_manager
        self.remote_executor: RemoteToolExecutor | None = remote_executor
        # Cache for file analysis results: key = "workspace_id:path" -> FileMeta dict
        self._file_meta_cache: dict[str, dict] = {}

    def register_direct_handler(self, name: str, handler: Callable) -> None:
        """Register a handler for a non-sandbox tool."""
        self.direct_handlers[name] = handler

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        tool_call_id: str,
        user_id: str,
        session_id: str,
        delegation: DelegationContext | None = None,
        event_queue: asyncio.Queue | None = None,
        workspace_id: str | None = None,
        allowed_tools: set[str] | None = None,
    ) -> ToolResult:
        """Execute a tool with security checks and sandbox dispatch.

        Args:
            allowed_tools: If set, only tools in this set can be executed.
                None means all tools are allowed (default for parent agents).
        """
        t_start = time.monotonic()

        # ---- Tool permission check (runtime safety net) ----
        if allowed_tools is not None and tool_name not in allowed_tools:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=tool_name,
                arguments=arguments,
                output="",
                success=False,
                error=f"Permission denied: tool '{tool_name}' is not available to this agent.",
                duration_ms=(time.monotonic() - t_start) * 1000,
            )

        # ---- MCP tool routing ----
        if self.mcp_manager and self.mcp_manager.is_mcp_tool(tool_name):
            try:
                output = await self.mcp_manager.call_tool(tool_name, arguments)
                output = self._truncate(output)
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    arguments=arguments,
                    output=output,
                    success=True,
                    duration_ms=(time.monotonic() - t_start) * 1000,
                )
            except Exception as e:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    arguments=arguments,
                    output="",
                    success=False,
                    error=f"MCP error: {e}",
                    duration_ms=(time.monotonic() - t_start) * 1000,
                )

        # ---- Remote tool routing ----
        if self.remote_manager and self.remote_manager.is_remote_tool(tool_name):
            try:
                output = await self.remote_executor.call(tool_name, arguments)
                output = self._truncate(output)
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    arguments=arguments,
                    output=output,
                    success=True,
                    duration_ms=(time.monotonic() - t_start) * 1000,
                )
            except Exception as e:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    arguments=arguments,
                    output="",
                    success=False,
                    error=f"Remote tool error: {e}",
                    duration_ms=(time.monotonic() - t_start) * 1000,
                )

        # ---- Built-in tool routing ----
        tool = self.registry.get(tool_name)

        if not tool:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=tool_name,
                arguments=arguments,
                output="",
                success=False,
                error=f"Unknown tool: {tool_name}",
                duration_ms=(time.monotonic() - t_start) * 1000,
            )

        try:
            # 1. Security checks
            if tool_name == "run_shell":
                self._check_dangerous_command(arguments.get("command", ""))

            # 2. Path safety
            if "path" in arguments:
                self._validate_path(arguments["path"])

            # 3. Execute
            if tool.requires_sandbox:
                output = await self._execute_in_sandbox(
                    tool_name, arguments, user_id, session_id, workspace_id
                )
            else:
                output = await self._execute_direct(
                    tool_name, arguments, user_id, session_id, delegation, event_queue,
                    tool_call_id=tool_call_id,
                    workspace_id=workspace_id,
                )

            # 4. Truncate
            output = self._truncate(output)

            return ToolResult(
                tool_call_id=tool_call_id,
                name=tool_name,
                arguments=arguments,
                output=output,
                success=True,
                duration_ms=(time.monotonic() - t_start) * 1000,
            )

        except SecurityError as e:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=tool_name,
                arguments=arguments,
                output="",
                success=False,
                error=f"Security error: {e}",
                duration_ms=(time.monotonic() - t_start) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=tool_name,
                arguments=arguments,
                output="",
                success=False,
                error=str(e),
                duration_ms=(time.monotonic() - t_start) * 1000,
            )

    # ---- Security Checks ----

    def _check_dangerous_command(self, command: str) -> None:
        """Block known-dangerous shell commands."""
        cmd_lower = command.lower().replace(" ", "")
        for pattern in self.DANGEROUS_COMMANDS:
            if pattern.replace(" ", "") in cmd_lower:
                raise SecurityError(f"Dangerous command blocked: {pattern}")

    def _validate_path(self, path: str) -> None:
        """Prevent path traversal outside /workspace."""
        # Normalize the path manually (PurePosixPath doesn't have resolve())
        import posixpath

        normalized = posixpath.normpath(posixpath.join("/workspace", path))
        if not normalized.startswith("/workspace"):
            raise SecurityError(f"Path traversal blocked: {path}")

    def _truncate(self, output: str) -> str:
        """Truncate output exceeding MAX_OUTPUT_SIZE.

        For JSON-like output (starting with '{' or '['), attempts to cut at
        the last newline before the limit so the LLM sees complete lines rather
        than a mid-token break.
        """
        # Strip NUL bytes — they pollute the LLM context and break the JSONB
        # write when this output is later persisted in tool_calls.
        output = sanitize_pg_text(output)

        if len(output) <= self.MAX_OUTPUT_SIZE:
            return output

        suffix = f"\n\n... [output truncated, {len(output)} chars total]"
        stripped = output.lstrip()

        # JSON-aware: try to cut at a newline boundary
        if stripped and stripped[0] in ("{", "["):
            cut_point = output.rfind("\n", 0, self.MAX_OUTPUT_SIZE)
            if cut_point > self.MAX_OUTPUT_SIZE // 2:
                return output[:cut_point] + suffix

        # Fallback: hard cut (no worse than before)
        return output[: self.MAX_OUTPUT_SIZE] + suffix

    # ---- Execution Dispatch ----

    # Max file size (bytes) for unrestricted read_file (no offset/limit)
    SAFE_READ_LIMIT = 1_000_000  # 1 MB

    async def _execute_in_sandbox(
        self,
        tool_name: str,
        args: dict,
        user_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> str:
        """Execute a tool inside the user's sandbox container."""
        # workspace_id is required for stateless sandbox; fall back to user_id for compat
        ws_id = workspace_id or user_id
        sandbox = await self.sandbox_mgr.get_or_create(user_id, session_id, ws_id)

        if tool_name == "run_shell":
            command = args.get("command", "")
            result = await self.sandbox_mgr.execute(sandbox, f"bash -c {command!r}")
            return self._format_exec_result(result)

        elif tool_name == "run_code":
            return await self._run_code(sandbox, args)

        elif tool_name == "read_file":
            path = self._sandbox_path(args["path"])
            offset = args.get("offset")
            limit = args.get("limit", 200)

            # Size guard: check file size before reading
            file_size = await self._get_file_size(sandbox, path)
            if file_size is not None and file_size > self.SAFE_READ_LIMIT:
                if offset is None:
                    preview = await self._read_file_range(sandbox, path, 0, 20)
                    return (
                        f"⚠️ 文件 {path} 大小为 {file_size:,} 字节 ({file_size / (1024*1024):.1f} MB)，"
                        f"超过安全读取上限 ({self.SAFE_READ_LIMIT:,} 字节)。\n\n"
                        f"请使用以下方式访问：\n"
                        f"- file_read 指定 offset 和 limit 参数读取特定行范围\n"
                        f"- file_grep 搜索特定内容\n"
                        f"- file_query 执行 SQL 查询（结构化文件）\n"
                        f"- file_info 获取文件结构概览\n\n"
                        f"文件前 20 行预览:\n{preview}"
                    )

            return await self._read_file_range(sandbox, path, offset or 0, limit)

        elif tool_name == "write_file":
            path = self._sandbox_path(args["path"])
            content = args["content"]
            # Use base64 to avoid shell escaping issues
            import base64

            b64_content = base64.b64encode(content.encode()).decode()
            qp = self._qpath(path)
            cmd = (
                f"mkdir -p $(dirname {qp}) && "
                f"echo '{b64_content}' | base64 -d > {qp}"
            )
            result = await self.sandbox_mgr.execute(sandbox, cmd)
            return (
                self._format_exec_result(result) if result.exit_code != 0 else f"Written to {path}"
            )

        elif tool_name == "edit_file":
            path = self._sandbox_path(args["path"])
            old_str = args["old_str"]
            new_str = args["new_str"]
            return await self._edit_file_in_sandbox(sandbox, path, old_str, new_str)

        elif tool_name == "list_directory":
            path = self._sandbox_path(args.get("path", "."))
            result = await self.sandbox_mgr.execute(sandbox, f"ls -la {self._qpath(path)}")
            return self._format_exec_result(result)

        elif tool_name == "file_info":
            path = self._sandbox_path(args["path"])
            cache_key = f"{ws_id}:{path}"
            if cache_key in self._file_meta_cache:
                return self._format_file_meta(self._file_meta_cache[cache_key])
            meta = await self._analyze_file(sandbox, path)
            self._file_meta_cache[cache_key] = meta
            return self._format_file_meta(meta)

        elif tool_name == "file_grep":
            path = self._sandbox_path(args["path"])
            pattern = args["pattern"]
            context_lines = min(args.get("context_lines", 2), 10)
            max_matches = min(args.get("max_matches", 20), 100)
            ignore_case = args.get("ignore_case", True)
            return await self._file_grep(sandbox, path, pattern, context_lines, max_matches, ignore_case)

        elif tool_name == "file_query":
            path = self._sandbox_path(args["path"])
            query = args["query"]
            self._validate_sql(query)
            return await self._file_query(sandbox, path, query)

        elif tool_name == "read_pdf":
            path = self._sandbox_path(args.get("path") or args.get("file_path") or "")
            return await self._read_pdf(
                sandbox, path, args.get("start_page"), args.get("end_page")
            )

        else:
            return f"Unknown sandbox tool: {tool_name}"

    # ---- File helpers ----

    @staticmethod
    def _sandbox_path(path: str) -> str:
        """Normalize a file path for sandbox commands.

        Accepts both relative (uploads/file.csv) and absolute
        (/workspace/uploads/file.csv) forms, always returning
        a path relative to /workspace (e.g. uploads/file.csv).
        """
        p = path
        if p.startswith("/workspace/"):
            p = p[len("/workspace/"):]
        elif p == "/workspace":
            p = ""
        return p.lstrip("/")

    @staticmethod
    def _qpath(path: str) -> str:
        """Shell-quote a workspace path for safe interpolation into commands.

        Handles spaces and special characters in filenames and prevents
        shell injection. ``path`` is relative to /workspace.
        """
        import shlex

        return shlex.quote(f"/workspace/{path}")

    async def _get_file_size(self, sandbox, path: str) -> int | None:
        """Get file size in bytes, or None if file doesn't exist."""
        result = await self.sandbox_mgr.execute(
            sandbox, f"stat -c%s {self._qpath(path)} 2>/dev/null || echo ''"
        )
        try:
            return int(result.stdout.strip())
        except (ValueError, TypeError):
            return None

    async def _read_file_range(
        self, sandbox, path: str, offset: int, limit: int
    ) -> str:
        """Read a line range from a file using sed."""
        limit = min(limit, 500)
        # sed -n 'start,endp' — line numbers are 1-based in sed
        start = offset + 1
        end = offset + limit
        result = await self.sandbox_mgr.execute(
            sandbox, f"sed -n '{start},{end}p' {self._qpath(path)}"
        )
        output = self._format_exec_result(result)
        # Get total line count for context
        wc_result = await self.sandbox_mgr.execute(
            sandbox, f"wc -l < {self._qpath(path)}"
        )
        try:
            total_lines = int(wc_result.stdout.strip())
        except (ValueError, TypeError):
            total_lines = 0
        position = f"\n[第 {offset + 1}-{offset + limit} 行 / 共 {total_lines:,} 行]"
        return output + position

    async def _file_grep(
        self, sandbox, path: str, pattern: str,
        context_lines: int, max_matches: int, ignore_case: bool,
    ) -> str:
        """Search a file using grep with context lines."""
        # Escape single quotes in pattern for shell
        escaped_pattern = pattern.replace("'", "'\"'\"'")
        flags = "-i" if ignore_case else ""
        cmd = (
            f"grep -n {flags} -A {context_lines} -B {context_lines} "
            f"-m {max_matches} '{escaped_pattern}' {self._qpath(path)}"
        )
        result = await self.sandbox_mgr.execute(sandbox, cmd)
        output = self._format_exec_result(result)
        if result.exit_code == 1 and not result.stdout:
            return f"No matches found for pattern: {pattern}"
        if not output.strip():
            return f"No matches found for pattern: {pattern}"
        return output

    async def _file_query(self, sandbox, path: str, query: str) -> str:
        """Execute a DuckDB SQL query against a file."""
        import base64

        # Write query to temp file to avoid shell escaping issues
        b64_q = base64.b64encode(query.encode()).decode()
        script = (
            f"echo '{b64_q}' | base64 -d > /tmp/_fq.sql && "
            f"duckdb -csv -c \"$(cat /tmp/_fq.sql)\" 2>&1"
        )
        result = await self.sandbox_mgr.execute(sandbox, script)
        output = self._format_exec_result(result)
        if not output.strip():
            return "(empty result)"
        return output

    @staticmethod
    def _validate_sql(query: str) -> None:
        """Only allow SELECT queries. Block DDL, DML, DCL, and PRAGMA."""
        cleaned = query.strip().upper()
        if not cleaned.startswith("SELECT"):
            raise SecurityError("只允许 SELECT 查询")
        forbidden = [
            "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
            "TRUNCATE", "GRANT", "REVOKE", "PRAGMA", "ATTACH", "DETACH",
            "EXPORT", "IMPORT", "COPY", "CALL", "EXECUTE", "VACUUM",
        ]
        # Use word-boundary check to avoid false positives (e.g. "SELECT * FROM order_updates")
        for kw in forbidden:
            if re.search(rf'\b{kw}\b', cleaned):
                raise SecurityError(f"SQL 中禁止使用 {kw}（只允许 SELECT 查询）")

    async def _analyze_file(self, sandbox, path: str) -> dict:
        """Analyze a file in the sandbox and return structured metadata."""
        meta: dict = {
            "path": path,
            "file_type": "unknown",
            "byte_size": 0,
            "line_count": None,
            "encoding": "utf-8",
            "preview": "",
        }

        # Get file size
        file_size = await self._get_file_size(sandbox, path)
        if file_size is not None:
            meta["byte_size"] = file_size

        # Detect file type via 'file' command (fall back to extension)
        file_result = await self.sandbox_mgr.execute(
            sandbox, f"file -b --mime-type {self._qpath(path)} 2>/dev/null || echo ''"
        )
        mime = file_result.stdout.strip()
        if not mime:
            import mimetypes
            mime, _ = mimetypes.guess_type(path)
            mime = mime or "application/octet-stream"
        meta["mime"] = mime

        # Detect line count for text files
        wc_result = await self.sandbox_mgr.execute(
            sandbox, f"wc -l < {self._qpath(path)} 2>/dev/null || echo '0'"
        )
        try:
            meta["line_count"] = int(wc_result.stdout.strip())
        except (ValueError, TypeError):
            pass

        # Get encoding
        enc_result = await self.sandbox_mgr.execute(
            sandbox, f"file -b --mime-encoding {self._qpath(path)}"
        )
        if enc_result.stdout.strip():
            meta["encoding"] = enc_result.stdout.strip()

        # Get preview (first 30 lines / 2000 chars)
        preview_result = await self.sandbox_mgr.execute(
            sandbox, f"head -30 {self._qpath(path)} | cut -c1-2000"
        )
        meta["preview"] = self._format_exec_result(preview_result)

        # Type-specific analysis
        if mime == "text/csv" or path.lower().endswith((".csv", ".tsv")):
            meta["file_type"] = "csv"
            meta["csv_delimiter"] = "\t" if path.lower().endswith(".tsv") else ","
            meta.update(await self._analyze_csv(sandbox, path))
        elif mime == "application/json" or path.lower().endswith(".json"):
            meta["file_type"] = "json"
            meta.update(await self._analyze_json(sandbox, path))
        elif path.lower().endswith(".jsonl"):
            meta["file_type"] = "jsonl"
            meta.update(await self._analyze_jsonl(sandbox, path))
        elif mime == "application/pdf" or path.lower().endswith(".pdf"):
            meta["file_type"] = "pdf"
            meta.update(await self._analyze_pdf(sandbox, path))
        elif mime and mime.startswith("text/"):
            meta["file_type"] = "text"
        elif path.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz")):
            meta["file_type"] = "archive"
            meta.update(await self._analyze_archive(sandbox, path))
        elif path.lower().endswith(".log"):
            meta["file_type"] = "log"

        return meta

    async def _analyze_csv(self, sandbox, path: str) -> dict:
        """Analyze CSV file structure."""
        import base64

        result: dict = {"csv_columns": []}
        try:
            py_script = f"""
import csv, io, sys
path = '/workspace/{path}'
with open(path, 'r', errors='replace') as f:
    sample = f.read(65536)
dialect = csv.Sniffer().sniff(sample, delimiters=',;\\t|')
reader = csv.reader(io.StringIO(sample), dialect)
headers = next(reader)
rows = list(reader)
print("headers:", '|'.join(headers))
print("row_count_sample:", len(rows))
for i, h in enumerate(headers):
    vals = [r[i] for r in rows[:100] if i < len(r) and r[i].strip()]
    unique_count = len(set(vals[:50]))
    print(f"col:{{h}}|unique:{{unique_count}}|sample:{{';;'.join(vals[:3])}}")
"""
            b64 = base64.b64encode(py_script.encode()).decode()
            r = await self.sandbox_mgr.execute(
                sandbox,
                f"echo '{b64}' | base64 -d | python3 2>/dev/null || echo 'CSV_PARSE_ERROR'"
            )
            output = r.stdout.strip()
            if "CSV_PARSE_ERROR" in output:
                # Fallback: just read header line
                r2 = await self.sandbox_mgr.execute(
                    sandbox, f"head -1 {self._qpath(path)}"
                )
                headers = r2.stdout.strip().split(",")
                result["csv_columns"] = [
                    {"name": h.strip(), "unique_values": None, "samples": []}
                    for h in headers
                ]
                return result

            for line in output.split("\n"):
                if line.startswith("headers:"):
                    pass  # Already parsed
                elif line.startswith("col:"):
                    parts = line[4:].split("|")
                    col_info = {}
                    for p in parts:
                        if p.startswith("unique:"):
                            col_info["unique_values"] = int(p.split(":", 1)[1])
                        elif p.startswith("sample:"):
                            col_info["samples"] = p.split(":", 1)[1].split(";;")
                    result["csv_columns"].append(col_info)
        except Exception:
            pass
        return result

    async def _analyze_json(self, sandbox, path: str) -> dict:
        """Analyze JSON file structure."""
        import base64

        result: dict = {"json_top_type": "unknown"}
        try:
            py_script = f"""
import json
with open('/workspace/{path}', 'r') as f:
    data = json.load(f)
t = type(data).__name__
print(f"top_type:{{t}}")
if isinstance(data, dict):
    print(f"keys:{{'|'.join(list(data.keys())[:20])}}")
    print(f"key_count:{{len(data)}}")
elif isinstance(data, list):
    print(f"array_length:{{len(data)}}")
    if len(data) > 0 and isinstance(data[0], dict):
        print(f"item_keys:{{'|'.join(list(data[0].keys())[:20])}}")
"""
            b64 = base64.b64encode(py_script.encode()).decode()
            r = await self.sandbox_mgr.execute(
                sandbox,
                f"echo '{b64}' | base64 -d | python3 2>/dev/null || echo 'JSON_PARSE_ERROR'"
            )
            output = r.stdout.strip()
            for line in output.split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    result[k.strip()] = v.strip()
        except Exception:
            pass
        return result

    async def _analyze_jsonl(self, sandbox, path: str) -> dict:
        """Analyze JSONL file structure."""
        result: dict = {}
        try:
            r = await self.sandbox_mgr.execute(
                sandbox, f"head -1 {self._qpath(path)} | python3 -c "
                f"\"import sys,json; d=json.loads(sys.stdin.read()); "
                f"print('keys:'+'|'.join(list(d.keys())[:20]))\" 2>/dev/null || echo ''"
            )
            if "keys:" in r.stdout:
                result["item_keys"] = r.stdout.strip().split(":", 1)[1]
        except Exception:
            pass
        return result

    async def _analyze_pdf(self, sandbox, path: str) -> dict:
        """Analyze PDF file metadata."""
        result: dict = {}
        try:
            r = await self.sandbox_mgr.execute(
                sandbox, f"pdfinfo {self._qpath(path)} 2>/dev/null || echo ''"
            )
            for line in r.stdout.strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower().replace(" ", "_")
                    result[k] = v.strip()
        except Exception:
            pass
        return result

    async def _read_pdf(
        self,
        sandbox,
        path: str,
        start_page: int | None,
        end_page: int | None,
    ) -> str:
        """Extract text from a PDF using pdftotext (poppler-utils)."""
        if not path:
            return "缺少参数 path（PDF 文件相对 /workspace 的路径）。"

        # Coerce page args to ints; ignore anything non-numeric so it can
        # never be interpolated into the shell command unsanitized.
        def _coerce(v):
            try:
                n = int(v)
                return n if n > 0 else None
            except (TypeError, ValueError):
                return None

        start = _coerce(start_page)
        end = _coerce(end_page)

        range_flags = ""
        if start is not None:
            range_flags += f" -f {start}"
        if end is not None:
            range_flags += f" -l {end}"

        # -layout preserves the visual column/table layout; write to stdout (-)
        import shlex

        quoted = shlex.quote(f"/workspace/{path}")
        result = await self.sandbox_mgr.execute(
            sandbox,
            f"pdftotext -layout{range_flags} {quoted} - 2>&1",
        )
        if result.exit_code != 0:
            return self._format_exec_result(result)

        text = result.stdout.strip()
        if not text:
            return (
                f"未从 {path} 提取到文本。该 PDF 可能是扫描件/纯图片，"
                f"需要 OCR 才能读取内容。"
            )

        header = f"PDF 文本提取: {path}"
        if start is not None or end is not None:
            header += f" (页 {start or 1}–{end or '末'})"
        return self._truncate(f"{header}\n\n{text}")

    async def _analyze_archive(self, sandbox, path: str) -> dict:
        """Analyze archive file structure."""
        result: dict = {"file_tree": []}
        try:
            ext = path.lower()
            qp = self._qpath(path)
            if ext.endswith(".zip"):
                cmd = f"unzip -l {qp} 2>/dev/null | tail -n +4 | head -100"
            elif ext.endswith((".tar.gz", ".tgz")):
                cmd = f"tar tzf {qp} 2>/dev/null | head -100"
            else:
                cmd = f"tar tf {qp} 2>/dev/null | head -100"
            r = await self.sandbox_mgr.execute(sandbox, cmd)
            tree = [line.strip() for line in r.stdout.strip().split("\n") if line.strip()]
            result["file_tree"] = tree
            result["file_count"] = len(tree)
        except Exception:
            pass
        return result

    @staticmethod
    def _format_file_meta(meta: dict) -> str:
        """Format file metadata as a readable string."""
        lines = [
            f"文件: {meta.get('path', 'unknown')}",
            f"类型: {meta.get('file_type', 'unknown')}",
            f"大小: {meta.get('byte_size', 0):,} 字节 ({meta.get('byte_size', 0) / (1024*1024):.1f} MB)",
        ]
        if meta.get("mime"):
            lines.append(f"MIME: {meta['mime']}")
        if meta.get("encoding"):
            lines.append(f"编码: {meta['encoding']}")
        if meta.get("line_count") is not None:
            lines.append(f"行数: {meta['line_count']:,}")

        # CSV-specific
        if meta.get("csv_columns"):
            lines.append(f"\n列信息 ({len(meta['csv_columns'])} 列):")
            for i, col in enumerate(meta["csv_columns"]):
                name = col.get("name", f"col_{i}")
                unique = col.get("unique_values")
                samples = col.get("samples", [])
                extra = ""
                if unique is not None:
                    extra += f" (唯一值约 {unique})"
                if samples:
                    extra += f" 示例: {', '.join(samples[:2])}"
                lines.append(f"  {i+1}. {name}{extra}")

        # JSON-specific
        if meta.get("json_top_type"):
            lines.append(f"\nJSON 顶层类型: {meta['json_top_type']}")
        if meta.get("keys"):
            lines.append(f"字段: {meta['keys']}")
        if meta.get("array_length"):
            lines.append(f"数组长度: {meta['array_length']}")
        if meta.get("item_keys"):
            lines.append(f"元素字段: {meta['item_keys']}")

        # PDF-specific
        if meta.get("pages"):
            lines.append(f"\nPDF 页数: {meta.get('pages')}")
        if meta.get("title"):
            lines.append(f"标题: {meta.get('title')}")

        # Archive-specific
        if meta.get("file_count") is not None:
            lines.append(f"\n压缩包包含 {meta.get('file_count')}+ 个文件")
        tree = meta.get("file_tree", [])
        if tree:
            lines.append("文件列表 (前100):")
            for f in tree[:20]:
                lines.append(f"  {f}")
            if len(tree) > 20:
                lines.append(f"  ... 还有 {len(tree) - 20} 个文件")

        if meta.get("preview"):
            preview = meta["preview"]
            if len(preview) > 2000:
                preview = preview[:2000] + "\n... [预览截断]"
            lines.append(f"\n预览:\n{preview}")

        return "\n".join(lines)

    async def _run_code(self, sandbox, args: dict) -> str:
        """Write code to a temp file and execute it in the sandbox."""
        import base64

        code = args.get("code", "")
        language = args.get("language", "python")

        ext = {"python": ".py", "javascript": ".js", "bash": ".sh"}.get(language, ".txt")
        filename = f"/tmp/_agent_code{ext}"

        # Use base64 to avoid shell escaping issues
        b64_code = base64.b64encode(code.encode()).decode()
        save_cmd = f"echo '{b64_code}' | base64 -d > {filename}"
        result = await self.sandbox_mgr.execute(sandbox, save_cmd)
        if result.exit_code != 0:
            return self._format_exec_result(result)

        # Execute
        run_cmd = {
            "python": f"python3 {filename}",
            "javascript": f"node {filename}",
            "bash": f"bash {filename}",
        }.get(language, f"cat {filename}")

        result = await self.sandbox_mgr.execute(sandbox, run_cmd)
        return self._format_exec_result(result)

    async def _edit_file_in_sandbox(self, sandbox, path: str, old_str: str, new_str: str) -> str:
        """
        Edit a file by reading, replacing, and writing back.

        Uses Python inside the sandbox for reliable string replacement
        (avoids fragile sed escaping).
        """
        # Use Python for reliable string replacement
        python_code = f"""
import sys
path = '/workspace/{path}'
try:
    with open(path, 'r') as f:
        content = f.read()
    old = {old_str!r}
    new = {new_str!r}
    if old not in content:
        print(f'ERROR: old_str not found in {{path}}', file=sys.stderr)
        sys.exit(1)
    content = content.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(content)
    print(f'Edited {{path}} successfully')
except Exception as e:
    print(f'ERROR: {{e}}', file=sys.stderr)
    sys.exit(1)
"""
        result = await self.sandbox_mgr.execute(sandbox, f"python3 -c {python_code!r}")
        return self._format_exec_result(result)

    async def _execute_direct(
        self,
        tool_name: str,
        args: dict,
        user_id: str,
        session_id: str,
        delegation: DelegationContext | None = None,
        event_queue: asyncio.Queue | None = None,
        tool_call_id: str | None = None,
        workspace_id: str | None = None,
    ) -> str:
        """Execute a non-sandbox tool via registered direct handlers."""
        handler = self.direct_handlers.get(tool_name)
        if handler:
            return await handler(
                args,
                user_id,
                session_id,
                delegation=delegation,
                tool_executor=self,
                event_queue=event_queue,
                tool_call_id=tool_call_id,
                workspace_id=workspace_id,
            )
        return f"Tool '{tool_name}' is not yet implemented."

    # ---- Helpers ----

    @staticmethod
    def _format_exec_result(result) -> str:
        """Format sandbox ExecResult into a human-readable string."""
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        if result.exit_code != 0:
            parts.append(f"[exit code: {result.exit_code}]")
        output = "\n".join(parts) if parts else "(no output)"
        return output
