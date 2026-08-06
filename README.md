# AIO Agent Platform

多租户 AI 智能体服务器，支持 LLM 调用、沙箱执行、持久记忆和多智能体委派。

## 功能特性

- 基于 PostgreSQL RLS 的多租户架构
- LLM 支持（OpenAI / Anthropic / 兼容 API）
- Docker 沙箱代码执行
- JWT 身份认证
- 持久记忆与技能系统
- 多智能体委派（父子智能体树）
- MCP 服务器集成
- CLI 和 Web UI 双界面

## 技术栈

- **后端**: Python 3.12+, FastAPI, SQLAlchemy, Alembic
- **前端**: React + TypeScript, Vite, Ant Design
- **数据库**: PostgreSQL 16
- **沙箱**: Docker
- **包管理**: uv（后端）, npm（前端）

## 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)（Python 包管理器）
- Node.js >= 18 + npm
- Docker & Docker Compose

## 项目结构

```
aio-agent-platform/
├── backend/                # 后端
│   ├── src/aio_agent_platform/ # Python 源码
│   │   ├── core/           # Agent 循环、配置、提示词
│   │   ├── delegation/     # 子智能体委派处理器
│   │   ├── interface/      # FastAPI 路由（对话、会话、智能体...）
│   │   ├── tools/          # 内置工具 + MCP 集成
│   │   ├── memory/         # 记忆服务（L1/L2/L3）
│   │   └── skills/         # 技能服务
│   ├── alembic/            # 数据库迁移
│   ├── tests/              # 后端测试
│   ├── prompts/            # Jinja2 提示模板
│   ├── sandbox/            # 沙箱 Docker 镜像
│   ├── pyproject.toml      # 后端依赖
│   ├── uv.lock
│   └── .env.example        # 环境变量模板
├── frontend/                    # 前端 React 应用
│   ├── src/
│   │   ├── components/     # UI 组件
│   │   ├── pages/          # 页面组件
│   │   ├── stores/         # Zustand 状态管理
│   │   └── lib/            # API 客户端、类型定义、工具函数
│   └── vite.config.ts
├── docs/                   # 项目文档
├── docker-compose.yml      # 基础设施服务
└── README.md
```

## 快速开始

```bash
# 1. 克隆并配置环境
git clone <repo-url> && cd aio-agent-platform
cp backend/.env.example backend/.env

# 2. 启动基础设施 + 装依赖 + 数据库迁移
docker compose up -d postgres minio
cd backend && uv sync && uv run alembic upgrade head && cd ..
cd frontend && npm install && cd ..
```

### 启动后端（终端 1）

```bash
cd backend && uv run uvicorn aio_agent_platform.interface.api:app --host 0.0.0.0 --port 8100 --reload
```

后端 API：**http://localhost:8100** ｜ API 文档：**http://localhost:8100/docs**

### 启动前端（终端 2）

```bash
cd frontend && npm run dev
```

前端：**http://localhost:1717**（`/api` 请求自动代理到后端）

### 其他命令

```bash
cd backend && uv run aio-cli          # CLI 对话
cd backend && uv run aio-manage       # 管理命令
cd backend && uv run alembic upgrade head  # 数据库迁移
cd frontend && npm run build          # 前端生产构建
```

## 配置说明

`backend/.env` 中的关键环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://agent_user:changeme@localhost:5435/aio_agent_platform` | 数据库连接字符串 |
| `JWT_SECRET` | *（必填）* | JWT 签名密钥 |
| `HOST` / `PORT` | `0.0.0.0` / `8100` | API 服务绑定地址 |
| `CORS_ORIGINS` | `http://localhost:1717,http://localhost:3000` | 允许的跨域来源 |
| `AGENT_TRUST_LEVEL` | `ask_dangerous` | 工具执行信任级别：`ask_always` / `ask_dangerous` / `auto_all` |
| `SANDBOX_IMAGE` | `aio-agent-platform/sandbox:latest` | 沙箱 Docker 镜像 |

LLM 模型通过管理后台配置，不在 `.env` 中设置。

## 数据库迁移

```bash
cd backend

# 创建新迁移
alembic revision --autogenerate -m "描述"

# 执行迁移
alembic upgrade head

# 回退一步
alembic downgrade -1
```

## 文档

详细文档入口见 [`docs/README.md`](docs/README.md)，从 `docs/00-产品需求文档.md` 与 `docs/01-技术栈.md` 开始阅读。
