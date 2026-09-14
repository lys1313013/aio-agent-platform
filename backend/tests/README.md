# 记忆与聊天室测试

以下命令在 `backend` 目录运行。

## 无数据库的业务测试

```bash
uv run pytest tests/test_memory_reconciliation.py tests/test_room_service.py tests/test_room_output_streaming.py tests/test_rooms.py -q
```

记忆测试 Mock SQLAlchemy 会话的查询结果和写入边界，执行真实的合并、更新、降级逻辑；工具与自动提取入口也有调用验证。聊天室服务测试 Mock I/O，验证重复请求、成员顺序、不可用成员、归档限制、重试确认、原上下文保留和过期任务处理。

此组当前有 30 项。不会启动或连接数据库。`test_rooms.py` 中 8 项 PostgreSQL 集成测试默认不选中，输出为 `deselected`，不是通过。本说明不表示仓库其他测试都已改为无数据库。

## PostgreSQL 集成测试（显式选跑）

原有 24 项保留，用于真实 SQL、持久化、作用域、并发锁和完整接口链路验证：

```bash
uv run pytest --run-postgres -m postgres tests/test_memory_reconciliation_postgres.py tests/test_rooms.py -q
```

运行前配置独立测试数据库：

- `DATABASE_URL`：记忆测试数据库，必须使用本机回环地址。测试会清空其中的表，禁止指向业务数据库。
- `ROOM_TEST_DATABASE_URL`：聊天室测试数据库，必须使用本机回环地址且数据库名为 `room_test`；每项测试使用独立 schema。

显式选跑时数据库不可用会报错，不再静默跳过。Mock 测试不证明 PostgreSQL 并发锁、事务或隔离有效；仍需要这组集成测试提供证据。
