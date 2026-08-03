# 宠物任务条 SSE 实时推送技术方案

> 日期：2026-08-03
> 前置文档：[[01-需求文档]]（宠物系统基础）、[[02-宠物绑定智能体需求文档]]
> 目的：把宠物任务条的状态同步从「前端 5s 轮询 `GET /api/pets/active-tasks`」改为「用户级 SSE 长连接推送」，让渠道（飞书等）触发的 Agent 任务状态（开始/工具切换/完成）实时到达前端，消除轮询粒度带来的延迟。

## 1. 背景与动机

现状：

- 飞书等渠道触发的 AgentLoop 完全跑在后端，浏览器没有任何 SSE 连接，宠物 widget 只能靠 `setInterval` 每 5s 轮询一次 `GET /api/pets/active-tasks` 感知状态（`PetWidget.tsx`）。
- 任务状态由 `channels/pipeline.py` 通过 `task_started / task_tool / task_finished` 写入 Redis 哈希 `aio:pet_tasks:{user_id}`（`core/task_registry.py`），宠物路由轮询读出。

问题：

- **轮询粒度延迟**：任务出现、工具名切换、任务完成消失，最多滞后 5s（一次轮询周期）。
- 页面切后台时轮询暂停（`document.visibilityState` 守卫），切回来前状态完全盲区。
- 每次轮询都是一次完整 HTTP 往返 + Redis `hgetall`，无事件时纯浪费。

目标：

- 任务开始/工具切换/完成三类事件**即时**到达前端（亚秒级）。
- 后台标签页依然能收到事件（SSE 网络事件不受 timer throttle 影响）。
- 连接建立即同步当前全量快照，断线重连后自动恢复，不丢状态。
- Redis 故障、连接中断等异常时**优雅降级**，不拖垮对话链路（沿用宠物附属系统定位）。

## 2. 总体设计

```
渠道(飞书) webhook
      │  InboundEvent
      ▼
channels/pipeline.py  ── task_started / task_tool / task_finished ──┐
      │                                                             │
      │ 写入 Redis hash (aio:pet_tasks:{user_id})                   │ 发布到 Redis 频道
      │                                                             ▼
      │                                          aio:pet_task_events  (Redis Pub/Sub, 单频道)
      │                                                             │ 订阅 + 按 user_id 过滤
      │                                                             ▼
      │                                          core/task_events.py TaskEventBroker
      │                                                             │  内存注册表 user_id → set[Queue]
      │                                                             ▼
      │                                             SSE 端点 GET /api/pets/tasks/events
      │                                                             │  连接即快照 + 增量事件 + 心跳
      │                                                             ▼
      │                                           前端 petsApi.watchActiveTasks (fetch 流)
      │                                                             ▼
      │                                           petStore.applyRemoteTaskEvent / syncRemoteTasks
      │                                                             ▼
      │                                           宠物 widget 任务条 (tasks map)
```

要点：

- **发布端**：`task_registry` 是任务生命周期的唯一写入口，在其三个函数末尾发布事件，`pipeline.py` 零改动。
- **订阅端**：`TaskEventBroker` 每进程一个共享 Redis pubsub 订阅后台任务，按 `user_id` 分发到各 SSE 连接的内存队列。
- **单频道 + payload 带 user_id**：避免「每用户一条频道」导致的频道数量爆炸；多 worker 各自订阅同一频道即可广播。
- **先订阅后快照**：规避「快照读取与增量事件之间丢事件」的竞态。

## 3. 事件协议

SSE 传输沿用 chat 流既有约定：`data: {json}\n\n`（单行 JSON，事件类型放 `type` 字段），复用 `chat.py` 的 `_sse_event`。不写命名 `event:` 字段。心跳为 SSE 注释行 `: ping\n\n`（前端解析器对 `:` 开头的行天然跳过）。

| type | 方向 | 说明 |
|---|---|---|
| `pet_task_snapshot` | 连接建立时 | 当前在跑任务全量快照，`tasks` 为 `PetActiveTask[]` |
| `pet_task_started` | 任务开始 | `task` 为完整任务对象 |
| `pet_task_tool` | 工具切换 | `session_id` + `tool` 名 |
| `pet_task_finished` | 任务结束 | `session_id`，前端打勾停留后移除 |

payload 示例：

```json
// 连接时（先于任何增量事件）
{"type": "pet_task_snapshot", "tasks": [
  {"session_id": "…", "label": "查一下天气", "tool": null,
   "source": "feishu", "chat_key": "…", "agent_id": "…", "started_at": 1754…}
]}

// 增量
{"type": "pet_task_started", "task": { /* 同 snapshot 元素 */ }}
{"type": "pet_task_tool", "session_id": "…", "tool": "web_search"}
{"type": "pet_task_finished", "session_id": "…"}
```

`task` 对象字段与 `PetActiveTask`（`frontend/src/lib/types.ts`）完全一致，前端可复用现有合并逻辑。

## 4. 后端设计

### 4.1 `core/task_registry.py` — 发布事件

- 新增 `_publish(user_id, event: dict)`：`_redis().publish("aio:pet_task_events", json.dumps(event))`，try/except 静默降级（沿用模块既有模式：Redis 不可用不拖垮对话）。
- 在 `task_started` / `task_tool` / `task_finished` 的 **Redis 写入之后**各发布一次，保证「订阅方收到的事件」与「快照读到的状态」一致。
- 复用现有 `_redis()` 懒加载单例。

### 4.2 新模块 `core/task_events.py` — 订阅端 broker

`TaskEventBroker` 单例：

| 成员 | 说明 |
|---|---|
| `_subscribers: dict[str, set[asyncio.Queue]]` | user_id → 该用户的所有 SSE 连接队列 |
| `_listen_task: asyncio.Task \| None` | 共享 Redis pubsub 订阅后台任务，首次 `subscribe` 时懒启动 |
| `_listen_loop()` | 订阅 `aio:pet_task_events`，每条消息按 `user_id` 过滤，`put_nowait` 写队列（满则丢）；连接异常带 1s 退避重连 |
| `stream(user_id)` | async generator：先订阅 → `task_registry.list_tasks` 读快照 → 循环 `wait_for(q.get(), timeout=25)`（超时产出心跳哨兵）→ `finally` 退订 |

### 4.3 `interface/routes/pets.py` — SSE 端点

- `GET /api/pets/tasks/events`，依赖 `user: CurrentUser`（HTTPBearer header）。
- 返回 `StreamingResponse(media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})`。
- 遍历 `broker.stream(user.id)`：dict → `_sse_event(dict)`；心跳哨兵 → `: ping\n\n`。
- 保留 `GET /active-tasks` 轮询端点（调试/回退用），前端不再调用。

## 5. 前端设计

### 5.1 `lib/api.ts` — `petsApi.watchActiveTasks(onEvent)`

- 复用 `stream()`（L252-341）的 fetch `getReader()` + `\n\n` 切块 + `data: ` 前缀解析骨架，GET `/pets/tasks/events` + `Authorization: Bearer` header。
- 返回 `close: () => void`（内部 `AbortController.abort()`）。

### 5.2 `stores/petStore.ts` — 远端事件处理

- 从 `syncRemoteTasks` 的 incoming 分支抽出 `mergeIncoming(tasks, t: PetActiveTask)` helper，快照与增量复用。
- 新增 `applyRemoteTaskEvent(ev)`：

| 事件 | 处理 |
|---|---|
| `pet_task_snapshot` | 调 `syncRemoteTasks(ev.tasks)`（全量合并，天然兼容现有逻辑） |
| `pet_task_started` | `mergeIncoming` 单条 upsert |
| `pet_task_tool` | 按 `session_id` 更新 `tool` + `updatedAt` |
| `pet_task_finished` | `markDone` + `scheduleRemoval`（保留 60s 打勾停留） |

### 5.3 `components/pet/PetWidget.tsx` — 轮询改 SSE

- 替换 L185-204 的 `setInterval` 轮询：`useEffect` 里 `petsApi.watchActiveTasks(...)`，事件喂给 store，cleanup 调 close。
- 连接 error/意外断开 → 3s 后重连（简单退避）。
- `effectiveMood` 工作态推导（L405-409）不变，任务仍在同一 `tasks` map；本地 web 聊天任务（走 chat SSE → `reportEvent`）不冲突。

## 6. 时序图

```
浏览器               broker/SSE 端点          Redis 频道            渠道 pipeline
  │  GET /tasks/events │                       │                       │
  │──────────────────▶│  subscribe(user_id)    │                       │
  │                   │───────────────────────▶│                       │
  │                   │  list_tasks 读快照     │                       │
  │                   │◀───────────────────────│                       │
  │◀── snapshot ──────│                       │                       │
  │                   │                       │   task_started 发布    │
  │                   │                       │◀──────────────────────│
  │                   │◀─ msg(user_id) 过滤 ──│                       │
  │◀── started ───────│                       │                       │
  │                   │                       │   task_tool 发布       │
  │                   │◀──────────────────────│                       │
  │◀── tool ──────────│                       │                       │
  │                   │                       │   task_finished 发布   │
  │                   │◀──────────────────────│                       │
  │◀── finished ──────│                       │                       │
  │◀── : ping ────────│  (每 25s 心跳)        │                       │
```

## 7. 降级矩阵

| 故障场景 | 表现 | 兜底 |
|---|---|---|
| Redis 不可用（发布侧） | 事件静默丢弃 | 对话链路不受影响（沿用现有 try/except） |
| Redis 不可用（订阅侧） | pubsub 订阅失败/断连 | 监听循环 1s 退避重连；SSE 仍可建立、只发快照不发增量 |
| 浏览器断连 | 生成器抛 CancelledError | `finally` 退订队列，不泄漏 |
| 连接中途网络抖动 | 前端读到 EOF/error | 3s 后重连，重建即收快照恢复状态 |
| 快照与增量之间丢事件 | 任务状态短暂缺一条 | 快照+增量幂等合并；任务残留由 TTL(30min)/前端 60s 清扫兜底 |
| 多 worker 部署 | 各 worker 独立订阅同一频道 | 频道广播天然覆盖所有浏览器连接 |

## 8. 非功能需求

- **认证**：`CurrentUser`（HTTPBearer），SSE 长连接只在建立时校验一次 token（与现有 chat SSE 一致，长连接期间 token 过期不断连）。
- **用户隔离**：broker 按 `user_id` 过滤分发，事件只到达属主连接。
- **心跳**：每 25s 发 `: ping` 注释行，维持代理连接活性。
- **连接规模**：每浏览器 tab 一条常驻 SSE，队列长度有界（满则丢），内存可控。
- **性能**：无事件时零业务流量；事件到达到亚秒级。

## 9. 验收标准

1. 渠道（飞书等）消息发出后，宠物任务条**即时**出现任务 pill（无需等待 5s 轮询）。
2. 任务调用工具时，pill 上的工具名**即时**切换。
3. 任务结束，pill **即时**打勾，60s 后移除。
4. 页面在后台标签页时，任务状态仍能更新。
5. 断网重连后，任务条状态通过快照自动恢复。
6. 本地 web 聊天任务条行为不变（回归）。
7. Redis 停止时，Web 聊天不受影响；SSE 只退化为快照态（可接受的降级）。
