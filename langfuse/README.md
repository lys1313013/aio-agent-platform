# 独立 Langfuse 部署

此目录可以单独复制到服务器，使用 Docker Compose 启动 Langfuse Web、Worker 和 ClickHouse。PostgreSQL、Redis 和 MinIO 使用外部服务，连接参数在 `.env` 和 `.env.example` 中配置。采用 Langfuse 4 镜像，独立项目名为 `langfuse`，网络和 ClickHouse 持久化卷与主应用分开。

| 外部组件 | 连接配置 | 数据隔离 |
| --- | --- | --- |
| PostgreSQL | `DATABASE_URL` | 数据库 `langfuse` |
| Redis | `REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD` | 要求 Redis 7+，`maxmemory-policy=noeviction` |
| MinIO | `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY` | 桶 `langfuse`，前缀 `events/` 和 `media/` |

首次启动前确保外部数据库和桶已创建，且容器可连接这些服务。服务器地址和凭据统一在环境文件中配置。

## 启动

需要 Docker Engine 和 Docker Compose v2+。在本目录准备 `.env`（没有该文件时从 `.env.example` 复制），配置外部服务连接信息、登录账号和密钥。`ENCRYPTION_KEY` 必须为 64 位十六进制字符串，可用 `openssl rand -hex 32` 生成。配置完成后执行：

```bash
docker compose -f docker-compose-langfuse.yml config --quiet
docker compose -f docker-compose-langfuse.yml up -d
```

默认访问 <http://localhost:13000>，宿主机端口为 `13000`，容器内部端口仍为 `3000`。登录邮箱默认 `admin@example.com`，密码保存在 `.env` 的 `LANGFUSE_INIT_USER_PASSWORD`，可用以下命令查看：

```bash
grep '^LANGFUSE_INIT_USER_' .env
```

建议执行 `chmod 600 .env`，仅允许文件所有者读写。该文件已被仓库根目录的 `.gitignore` 忽略。手动填写包含 `$`、`#` 或空格的密码时，在 `.env` 中使用单引号包裹；`DATABASE_URL` 中的密码必须进行 URL 编码，例如 `$` 编码为 `%24`、`&` 编码为 `%26`。

首次启动自动创建组织、项目、项目 API 密钥及组织所有者账号，并关闭公开注册。`LANGFUSE_INIT_*` 仅用于初始化，修改 `.env` 不会重置已存在用户的密码。数据库密码、`SALT` 和 `ENCRYPTION_KEY` 也应随持久化数据保留，不能随意更换。

## 服务器访问

Web 默认监听所有 IPv4 网卡，不绑定某个具体 IP：

```dotenv
LANGFUSE_BIND_HOST=0.0.0.0
```

可通过 `http://任意可达的服务器IP:13000` 访问 Web。修改本机 IP 不需要修改监听配置。ClickHouse 和 Worker 不映射宿主机端口；PostgreSQL、Redis 和 MinIO 使用环境文件中的外部地址。

`NEXTAUTH_URL` 和 `MINIO_PUBLIC_URL` 是生成登录链接、文件签名链接的地址，不限制服务监听哪些 IP；不要将它们设为 `0.0.0.0`。`NEXTAUTH_URL` 默认使用 `localhost`，如需让其他机器使用生成的链接，可填写统一可达的域名或地址。`MINIO_PUBLIC_URL` 已指向外部 MinIO，必须能被浏览器和 SDK 访问；`MINIO_ENDPOINT` 用于 Web、Worker 的内部访问。调整端口时同步调整对应 URL。

其他机器能否连接还取决于网络路由、防火墙和服务器安全组是否允许对应端口。

## 接入 aio-agent-platform

在主应用的环境配置中填写：

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_BASE_URL=http://你的服务器IP:13000
LANGFUSE_PUBLIC_KEY=填写本目录.env中的LANGFUSE_INIT_PROJECT_PUBLIC_KEY
LANGFUSE_SECRET_KEY=填写本目录.env中的LANGFUSE_INIT_PROJECT_SECRET_KEY
```

后端在 Docker 容器中运行时，`LANGFUSE_BASE_URL` 必须是该容器可达的地址；`localhost` 指向后端容器自身。该独立 Compose 不会自动加入主应用网络，也不会自动修改或重启主应用。

## 查看状态与停止

```bash
docker compose -f docker-compose-langfuse.yml ps
docker compose -f docker-compose-langfuse.yml logs --tail=100 -f langfuse-web langfuse-worker
docker compose -f docker-compose-langfuse.yml down
```

`down` 保留数据卷；添加 `-v` 会删除本 Compose 的 ClickHouse 数据卷，不会删除外部服务中的数据。首次启动会自动初始化数据库表结构，完成后即可登录。

配置参考：[官方 Docker Compose](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml)、[账号初始化](https://langfuse.com/self-hosting/administration/headless-initialization)、[登录认证](https://langfuse.com/self-hosting/security/authentication-and-sso)。
