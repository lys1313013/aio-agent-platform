# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

多租户 AI 智能体平台。后端 Python 3.13 + FastAPI + SQLAlchemy，前端 React + TypeScript + Ant Design，PostgreSQL 数据库，Docker 沙箱执行代码。

## 常用命令

后端、沙箱和 CI 统一使用 Python 3.13，`backend/.python-version` 固定本地 uv 版本。修改运行时版本时同步更新 Dockerfile、CI、pyproject 工具配置和 uv.lock，并验证完整测试与镜像构建。

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
npm run dev                              # 开发服务器 (localhost:1717)
npm run build                            # 生产构建
npm run lint                             # ESLint

# 基础设施
docker compose up -d postgres minio      # 启动数据库和对象存储
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

`db/models.py` — 所有表定义，无外键约束（依赖应用层维护关系）。租户与资源权限必须由应用层显式校验；部分表的 RLS 策略使用 `app.current_user_id` 做用户隔离，不能代替租户权限检查。`db/sanitize.py` 处理 NUL 字节防止 PostgreSQL 写入失败。

### 应用启动流程

`interface/api.py` 的 `lifespan` 按顺序初始化：DB → 对象存储 → 沙箱管理器 → 工具注册表 → 注册各类 handler（memory/skills/knowledge/delegation/interaction/portrait/cron）→ MCP 管理器 → 远程工具管理器 → Langfuse → 定时任务调度器。所有实例挂载到 `app.state`。

### 前端架构

- React 18 + React Router 7 + Zustand 状态管理 + Ant Design 6
- 聊天通过 SSE 流式接收 Agent 事件，支持 reasoning/tool_call/tool_result/text_delta 事件类型
- `stores/chatStore.ts` 管理会话状态，`stores/authStore.ts` 管理 JWT 认证
- `/api` 请求由 Vite proxy 转发到后端 8100 端口

### 配置系统

`core/config.py` — `pydantic-settings`，`AppSettings` 组合多个子配置类（DB、JWT、LLM、Agent、Sandbox、Storage、Langfuse、Server）。LLM 模型不在 .env 配置，而是通过管理后台动态管理（`llm_providers` / `llm_models` 表）。

### 执行租户与权限（新增执行入口必须遵守）

- `users.tenant_id` 是用户在界面选中的租户，只能用于管理界面筛选或没有绑定对象的新交互入口；禁止在后台执行、工具、委派、记忆提取中重新读取它决定归属。
- 现有对象按自身租户运行：定时任务 `job.tenant_id`、渠道 `channel.tenant_id`、协作房间 `room.tenant_id`、智能体/会话绑定的 `agent.tenant_id`；动态子智能体继承父执行租户。
- 使用 `core/execution_scope.py`：入口先 `require_execution_user` 校验用户、租户启用状态和成员资格；再按执行租户及 `visibility/created_by` 校验资源，最后绑定上下文。绑定 ContextVar 本身不代表授权。
- `AgentLoop` 必须显式传入 `execution_tenant_id`，或继承已校验的执行上下文；`build_agent_loop` 会设置它。工具通过 `resolve_execution_tenant` 获取上下文；不能缓存用户当前租户，不能修改 ORM `User.tenant_id` 来模拟切换。
- 流式执行的 ContextVar 必须在创建它的 Context 中清理，不能依赖调用方始终在同一 Task 中推进生成器；跨 Task 的 `anext`、关闭、取消和异常都要验证，尤其是 Python 3.11 的 `asyncio.wait_for`。
- 新协程、流式生成器、并发工具和延迟任务都要传播执行上下文；任务结束、异常、取消时必须清理。记忆后台任务重新校验来源对象和成员资格；缺少可靠租户来源的历史无智能体任务跳过并记录错误，禁止回退公共租户。
- 回归至少覆盖：运行中切换界面租户仍使用原对象租户；成员被移除/租户停用/私有资源不可见时拒绝执行；并发、异常、取消不串上下文；用户当前租户不被写回；模型、工具、画像、记忆与观测归属一致。
- 历史归属修复是独立的数据治理操作，必须单独获得用户授权；代码修复不附带历史数据迁移。
- 详细契约见 `docs/17-用户与租户隔离.md` 第七节。

### 执行入口与定时任务补充约束

- 已有对象执行入口使用 `ExecutionUser` 校验 JWT 和活跃用户，再按对象租户校验成员资格；不要先用 `CurrentUser` 校验界面租户，否则切换到停用租户会误拦截其他租户任务。管理、新建无对象入口仍使用 `CurrentUser`。
- 房间详情、SSE、停止、重试、确认操作都必须通过 `owned_room` 检查对象归属与实际租户；SSE 持续校验实际成员资格。
- 斜杠命令使用 `ctx.tenant_id`；调用 `resolve_model` 明确写出 `tenant_id=` 和 `model_id=`，避免位置参数误传。
- 定时任务统一通过 `CronJobService` 创建/更新，校验任务所有者、智能体、通知渠道属于同一有效租户。创建缺少执行上下文和智能体时拒绝，不能从界面租户兜底。
- 新 Cron 输入遵循标准星期编号 `0/7=周日、1=周一`，入库前转为 `mon`–`sun`；存量数字保留 APScheduler 语义 `0=周一`，编辑时先转换为名称，禁止全局重解释存量数字。
- 创建请求重试复用 `request_id`；同名不同配置默认冲突，明确需要并存时才传 `allow_duplicate=true`。禁止通过改名或改请求标识绕过重复提示。
- 自动启动迁移遇到缺失租户的历史记录应停止并报错，不按用户当前租户或公共默认租户补齐。手工执行旧 Alembic 版本前也必须审查归属回填 SQL。
