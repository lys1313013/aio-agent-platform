# AIO Agent Platform Backend

后端 API 服务，提供智能体对话、记忆管理、技能系统等功能。

## 启动

```bash
# 安装依赖
uv sync

# 执行数据库迁移
alembic upgrade head

# 启动 API 服务
uv run aio-api

# 或使用热重载模式（开发）
uv run uvicorn aio_agent_platform.interface.api:app --host 0.0.0.0 --port 8100 --reload
```

## 目录结构

```
backend/
├── src/aio_agent_platform/     # Python 源码
├── alembic/                # 数据库迁移
├── tests/                  # 测试
├── prompts/                # Jinja2 提示模板
├── sandbox/                # 沙箱 Docker 镜像
└── pyproject.toml          # 依赖配置
```
