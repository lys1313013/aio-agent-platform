# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

多租户 AI 智能体平台。后端 FastAPI + SQLAlchemy，前端 React + TypeScript + Ant Design，PostgreSQL 数据库，Docker 沙箱执行代码。

## 常用命令

```bash
# 后端
cd backend
uv sync                                  # 安装依赖
uv run uvicorn aio_agent_platform.interface.api:app --host 0.0.0.0 --port 8100 --reload
uv run aio-cli                           # CLI 对话
uv run aio-manage                        # 管理命令
uv run alembic upgrade head              # 数据库迁移
uv run alembic revision --autogenerate -m "描述"  # 创建新迁移
uv run pytest                            # 运行所有测试
uv run pytest tests/test_xxx.py -k "test_name"  # 运行单个测试
uv run ruff check .                      # Lint
uv run mypy src/                         # 类型检查

# 前端
cd frontend
npm run dev                              # 开发服务器 (localhost:5273)
npm run build                            # 生产构建
npm run lint                             # ESLint

# 基础设施
docker compose up -d postgres minio redis  # 启动数据库、对象存储和任务注册缓存
docker compose --profile build up sandbox-build  # 构建沙箱镜像
```

## 架构核心

### Agent 执行循环 (ReAct)

`core/agent.py` — `AgentLoop` 是核心。流程：构建 messages → 调 LLM (streaming) → 如果有 tool_calls → 执行工具 → 注入结果 → 回到 LLM。最多 `max_iterations` 轮。通过 async generator yield `AgentStep` 和字符串事件（`reasoning:`, `tool_call:`, `tool_result:`, `text_delta:`）。

### 工具系统

三层架构：
1. `tools/registry.py` — `ToolRegistry`：工具元数据注册（名称、JSON Schema、权限级别）
2. `tools/executor.py` — `ToolExecutor`：统一执行引擎，安全校验、沙箱调度、输出截断
3. `tools/builtin.py` — 内置工具（文件读写、命令执行、网页抓取等）

扩展机制：
- `tools/mcp/` — MCP 协议集成，`MCPManager` 管理连接生命周期
- `tools/remote/` — 自定义 HTTP 工具，用户配置 REST API 映射为 Agent 工具
- `tools/executor.py` 的 `register_direct_handler(name, handler)` — 注册直接 Python 函数处理器（用于 memory、skills、delegation 等）

### 多智能体委派

`delegation/handler.py` — `delegate_task` 工具。父 Agent 调用子 Agent 时，在 `AgentLoop.run()` 中 delegation 工具与其他工具并发执行。子 Agent 拥有独立的 `AgentLoop` 实例，通过 `allowed_tools` 白名单限制子 Agent 可用工具。层级关系通过 `agent_relationships` 多对多表管理。

### 用户确认流程 (AskUserQuestion)

`core/confirmation.py` — `ConfirmationManager`。当 LLM 调用 `AskUserQuestion` 工具时，AgentLoop 在 `_run_ask_user_flow()` 中直接拦截处理（不走 tool_executor），通过 event_queue 推送确认卡片到前端 SSE，然后阻塞等待用户通过 REST API 响应。注意：此 handler 不在 tool_executor 中执行，否则会死锁。

### 上下文窗口管理

`core/context.py` — token 估算、消息截断、对话摘要压缩。`ContextBudget` 从 settings 读取窗口大小和预留空间。第三步迭代后自动压缩早期工具结果，超过 90% 阈值时告警。

### 记忆系统

`memory/service.py` — 三层记忆：L1（短期/会话内）、L2（长期/用户级）、L3（全局知识）。通过 pg_trgm 做文本相似度检索，tool_executor 的 direct handler 注册。

### 数据库

`db/models.py` — 所有表定义，无外键约束（依赖应用层维护关系）。归属分租户级（`tenant_id`，如 agents/knowledge_bases/mcp_servers/channel_configs/pet_packages）与用户级（`user_id`，如 sessions/memories/cron_jobs）。隔离靠应用层 where 过滤；`db/connection.py` 的 `get_db()` 会 `SET LOCAL app.current_user_id` 写入会话变量，但**未配置数据库 RLS policy**（无 CREATE POLICY）。后台执行（定时任务、渠道回调）由代码显式 `set_config("app.current_user_id", ...)`。详见 `docs/17-用户与租户隔离.md`。`db/sanitize.py` 处理 NUL 字节防止 PostgreSQL 写入失败。

### 应用启动流程

`interface/api.py` 的 `lifespan` 按顺序初始化：DB → 对象存储 → 沙箱管理器 → 工具注册表 → 注册各类 handler（memory/skills/knowledge/delegation/interaction/portrait/cron）→ MCP 管理器 → 远程工具管理器 → Langfuse → 定时任务调度器。所有实例挂载到 `app.state`。

### 前端架构

- React 18 + React Router 7 + Zustand 状态管理 + Ant Design 6
- 聊天通过 SSE 流式接收 Agent 事件，支持 reasoning/tool_call/tool_result/text_delta 事件类型
- `stores/chatStore.ts` 管理会话状态，`stores/authStore.ts` 管理 JWT 认证
- `/api` 请求由 Vite proxy 转发到后端 8100 端口

### 配置系统

`core/config.py` — `pydantic-settings`，`AppSettings` 组合多个子配置类（DB、JWT、LLM、Agent、Sandbox、Storage、Langfuse、Server）。LLM 模型不在 .env 配置，而是通过管理后台动态管理（`llm_providers` / `llm_models` 表）。