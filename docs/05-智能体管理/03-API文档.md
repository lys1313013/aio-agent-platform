# 智能体 API 文档

> 在智能体界面内嵌 API 文档面板，展示该智能体对外暴露的调用接口，方便外部系统集成。

---

## 1. 背景与目标

### 1.1 背景

当前平台的智能体只能通过 Web UI 对话界面使用。外部系统（如业务后端、自动化流水线、第三方集成）无法以 API 方式调用智能体能力。需要在智能体管理界面中提供一份**实时、可交互、与该智能体绑定**的 API 文档，让开发者快速获取调用方式并集成到外部系统。

### 1.2 目标

| 目标 | 说明 |
|------|------|
| **降低接入门槛** | 开发者无需查阅独立的后端文档，在智能体界面即可看到完整调用方式 |
| **动态绑定** | API 文档中的 URL、Agent ID、版本等参数自动填充，可直接复制使用 |
| **覆盖两种场景** | 同步一次性调用（简单场景） + SSE 流式会话调用（多轮对话场景） |
| **版本发布** | 支持发布版本，API 文档基于已发布版本展示，确保接口稳定性 |

### 1.3 用户故事

- 作为开发者，我打开智能体的 API 文档面板，就能看到完整的 curl / Python 示例代码，URL 中已自动填入了该智能体的 ID 和服务地址，直接复制就能调用。
- 作为集成方，我需要知道这个智能体是否已发布、当前版本号是什么，只有已发布的版本才能被外部 API 调用。
- 作为管理员，我可以发布一个新版本，使 API 文档中展示的版本号更新，同时记录当前智能体的配置快照。

---

## 2. 功能范围

### 2.1 功能总览

| 模块 | 功能 | 优先级 |
|------|------|--------|
| 版本发布 | 管理员可发布智能体版本（快照当前配置） | P0 |
| API 文档面板 | 侧边栏新增"API 文档"菜单项，展示完整 API 文档 | P0 |
| 同步调用 API | POST `/api/agents/{agent_id}/execute` | P0 |
| 会话式调用 API | POST 创建 Session + POST SSE 流式聊天 | P0 |
| 会话管理 API | 获取历史、清空会话、删除会话、消息反馈 | P1 |
| 代码示例 | cURL / Python / JavaScript 代码片段，一键复制 | P0 |
| 在线调试 | 提供简易输入框，可直接发起一次调用并查看结果 | P2 |

---

## 3. 版本发布

### 3.1 发布流程

管理员在智能体配置界面点击「发布版本」按钮，系统生成一个新版本：

| 步骤 | 说明 |
|------|------|
| 1 | 点击「发布版本」按钮 |
| 2 | 弹窗确认，可输入版本说明（changelog） |
| 3 | 系统创建版本记录：版本号（UUID 或 semver）、配置快照、发布时间 |
| 4 | API 文档面板展示最新的已发布版本信息 |

### 3.2 版本数据模型

```
agent_versions 表
├── id: UUID (主键)
├── agent_id: UUID (外键 → agents.id)
├── version: string (如 "1.0.0" 或 UUID)
├── changelog: text (版本说明，可选)
├── config_snapshot: JSONB (发布时的完整智能体配置快照)
│   ├── model_id
│   ├── system_prompt
│   ├── enabled_tools
│   ├── skill_ids
│   ├── knowledge_base_ids
│   ├── child_ids
│   ├── temperature
│   └── ...
├── published_by: UUID (外键 → users.id)
├── published_at: timestamp
└── is_active: boolean (是否为当前生效版本)
```

### 3.3 版本规则

- 每个智能体可有多个已发布版本，但只有一个 `is_active = true` 的生效版本
- 新版本发布时，旧版本自动变为 `is_active = false`
- API 调用基于最新的 `is_active` 版本配置执行
- 未发布过版本的智能体，API 文档面板提示"尚未发布版本，请先发布"

### 3.4 版本列表

在 API 文档面板顶部展示版本信息：

```
┌──────────────────────────────────────────────┐
│  已发布版本: e74a256e  (v1.2.0)              │
│  发布时间: 2026-06-01 15:30                  │
│  [查看历史版本]  [发布新版本]                  │
└──────────────────────────────────────────────┘
```

---

## 4. API 文档面板

### 4.1 入口位置

在智能体对话页面左侧侧边栏图标导轨中新增一个菜单项「API」，使用 `<ApiOutlined />` 图标。

**菜单分组调整**：

| 分组 | 菜单项 |
|------|--------|
| 概览 | 概览 |
| 管理 | 大模型配置、系统提示词、技能 & 工具、MCP 服务、知识库、子智能体 |
| 集成 | **API 文档** (新增) |
| 用户 | 记忆、会话历史 |

### 4.2 面板布局

```
┌─────────────────────────────────────────────────────────────┐
│  🔌 API 文档                                         [✕]    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─ 版本信息 ────────────────────────────────────────────┐  │
│  │  已发布版本 e74a256e-3aa8-..., 发布于 2026-06-01      │  │
│  │  [发布新版本]                                         │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ Agent 概要 ──────────────────────────────────────────┐  │
│  │  模型: GPT-4o        流式输出: 开启                    │  │
│  │  模型思考: 关闭       工具数量: 8                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ 调用方式 Tab ────────────────────────────────────────┐  │
│  │  [一次性调用]  [会话式调用(SSE)]  [会话管理]            │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │                                                       │  │
│  │  (具体 API 文档内容，见 §5 / §6 / §7)                  │  │
│  │                                                       │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 未发布状态

如果智能体尚未发布版本：

```
┌──────────────────────────────────────────────┐
│  ⚠ 尚未发布版本                               │
│                                              │
│  请先发布一个版本，才能通过 API 调用此智能体。   │
│                                              │
│           [发布第一个版本]                      │
└──────────────────────────────────────────────┘
```

---

## 5. 一次性调用（同步执行）

### 5.1 适用场景

简单场景，发送一条消息并同步等待完整回答。无需管理 Session。

### 5.2 API 规格

```
POST /api/agents/{agent_id}/execute
```

**Request Headers**:

| Header | 必填 | 说明 |
|--------|------|------|
| `Content-Type` | ✓ | `application/json` |
| `Authorization` | ✓ | `Bearer {jwt_token}` |

**Request Body**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_input` | string | ✓ | 用户消息文本 |
| `variables` | object | ✗ | 初始变量键值对，用于传递业务上下文 |
| `session_rid` | string | ✗ | 复用已有 Session 进行多轮对话 |

**Response** (200 OK):

```json
{
  "code": 0,
  "data": {
    "markdown_response": "Agent 返回的 Markdown 文本 ...",
    "session_rid": "sess_xxx"
  }
}
```

**Error Response** (4xx / 5xx):

```json
{
  "code": 40001,
  "message": "Agent 未发布版本"
}
```

### 5.3 代码示例

面板内展示以下三种语言的代码示例，每个代码块右上角有「复制」按钮：

**cURL**:

```bash
curl -X POST '{{BASE_URL}}/api/agents/{{AGENT_ID}}/execute' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer {{JWT_TOKEN}}' \
  -d '{
    "user_input": "你好，请帮我分析一下数据"
  }'
```

**Python**:

```python
import requests

resp = requests.post(
    "{{BASE_URL}}/api/agents/{{AGENT_ID}}/execute",
    headers={"Authorization": "Bearer {{JWT_TOKEN}}"},
    json={
        "user_input": "你好，请帮我分析一下数据"
    },
)
data = resp.json()["data"]
print(data["markdown_response"])
```

**JavaScript**:

```javascript
const resp = await fetch("{{BASE_URL}}/api/agents/{{AGENT_ID}}/execute", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "Bearer {{JWT_TOKEN}}",
  },
  body: JSON.stringify({
    user_input: "你好，请帮我分析一下数据",
  }),
});
const { data } = await resp.json();
console.log(data.markdown_response);
```

### 5.4 变量模板替换

代码示例中的占位符在渲染时自动替换为实际值：

| 占位符 | 数据来源 | 说明 |
|--------|---------|------|
| `{{BASE_URL}}` | `window.location.origin` | 当前平台的服务地址 |
| `{{AGENT_ID}}` | 当前智能体 ID（URL 参数） | 智能体的唯一标识 |
| `{{JWT_TOKEN}}` | 当前登录用户的 JWT Token | 展示为占位提示，用户需自行替换 |

---

## 6. 会话式调用（SSE 流式输出）

### 6.1 适用场景

多轮对话场景。先创建 Session，然后通过 SSE 流式接收回答。

### 6.2 Step 1: 创建 Session

```
POST /api/agents/{agent_id}/sessions
```

**Request Body**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_type` | string | ✗ | 会话类型，默认 `"production"` |
| `user_id` | string | ✗ | 外部系统用户标识，用于追踪 |

**Response**:

```json
{
  "code": 0,
  "data": {
    "id": "sess_xxxxxxxxxxxx",
    "agent_id": "...",
    "created_at": "2026-06-01T15:30:00Z"
  }
}
```

### 6.3 Step 2: 发送消息（SSE 流式）

```
POST /api/sessions/{session_id}/chat
```

**Request Headers**:

| Header | 必填 | 说明 |
|--------|------|------|
| `Content-Type` | ✓ | `application/json` |
| `Accept` | ✓ | `text/event-stream` |
| `Authorization` | ✓ | `Bearer {jwt_token}` |

**Request Body**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | ✓ | 用户消息文本 |

**SSE 事件类型**:

| event | data 字段 | 说明 |
|-------|----------|------|
| `text_delta` | `{ "text": "..." }` | 文本增量，逐字返回 |
| `thinking` | `{ "content": "..." }` | 模型推理过程 |
| `tool_call` | `{ "id", "name", "arguments" }` | 工具调用 |
| `tool_result` | `{ "tool_call_id", "status", "preview" }` | 工具结果 |
| `done` | `{ "message_id", "trace" }` | 回答完成 |
| `error` | `{ "code", "message" }` | 错误 |

### 6.4 代码示例

**Python (流式)**:

```python
import requests, json

BASE = "{{BASE_URL}}/api"
AGENT_ID = "{{AGENT_ID}}"
TOKEN = "{{JWT_TOKEN}}"

# 1. 创建 session
session = requests.post(
    f"{BASE}/agents/{AGENT_ID}/sessions",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"session_type": "production", "user_id": "user_123"},
).json()["data"]
sid = session["id"]

# 2. 流式对话
resp = requests.post(
    f"{BASE}/sessions/{sid}/chat",
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream",
    },
    json={"message": "你好"},
    stream=True,
)

event_type = ""
for line in resp.iter_lines(decode_unicode=True):
    if line.startswith("event:"):
        event_type = line[6:].strip()
    elif line.startswith("data:"):
        data = json.loads(line[5:].strip())
        if event_type == "text_delta":
            print(data["text"], end="", flush=True)
        elif event_type == "done":
            print()
            break
```

**JavaScript (流式)**:

```javascript
const BASE = "{{BASE_URL}}/api";
const AGENT_ID = "{{AGENT_ID}}";
const TOKEN = "{{JWT_TOKEN}}";

// 1. 创建 session
const sessionResp = await fetch(`${BASE}/agents/${AGENT_ID}/sessions`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${TOKEN}`,
  },
  body: JSON.stringify({ session_type: "production", user_id: "user_123" }),
});
const { data: session } = await sessionResp.json();

// 2. 流式对话
const chatResp = await fetch(`${BASE}/sessions/${session.id}/chat`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    Authorization: `Bearer ${TOKEN}`,
  },
  body: JSON.stringify({ message: "你好" }),
});

const reader = chatResp.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const text = decoder.decode(value);
  // 解析 SSE 事件...
  for (const line of text.split("\n")) {
    if (line.startsWith("data:")) {
      const data = JSON.parse(line.slice(5).trim());
      console.log(data);
    }
  }
}
```

---

## 7. 会话管理 API

### 7.1 接口列表

在文档面板的「会话管理」Tab 中以表格形式展示以下接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/sessions/{session_id}/messages` | 获取会话历史消息 |
| `POST` | `/api/sessions/{session_id}/clear` | 清空会话（创建新会话） |
| `DELETE` | `/api/sessions/{session_id}` | 删除会话 |
| `POST` | `/api/sessions/{session_id}/messages/{message_id}/feedback` | 提交消息反馈 |

### 7.2 获取会话历史消息

```
GET /api/sessions/{session_id}/messages
```

**Response**:

```json
{
  "code": 0,
  "data": [
    {
      "id": "msg_xxx",
      "role": "user",
      "content": "你好",
      "created_at": "2026-06-01T15:30:00Z"
    },
    {
      "id": "msg_yyy",
      "role": "assistant",
      "content": "你好！有什么可以帮你的吗？",
      "created_at": "2026-06-01T15:30:05Z"
    }
  ]
}
```

### 7.3 提交消息反馈

```
POST /api/sessions/{session_id}/messages/{message_id}/feedback
```

**Request Body**:

```json
{
  "feedback": "up"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `feedback` | string | ✓ | `"up"` 或 `"down"` |

---

## 8. 后端 API 实现要求

### 8.1 新增路由

在 `backend/src/aio_agent/interface/routes/` 下新增 `agent_api.py`：

| 路由 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/agents/{agent_id}/execute` | POST | 一次性同步调用 | JWT |
| `/api/agents/{agent_id}/sessions` | POST | 创建外部会话 | JWT |
| `/api/agents/{agent_id}/versions` | GET | 获取版本列表 | JWT |
| `/api/agents/{agent_id}/versions` | POST | 发布新版本 | Admin JWT |
| `/api/agents/{agent_id}/versions/{version_id}` | GET | 获取版本详情 | JWT |
| `/api/agents/{agent_id}/api-doc` | GET | 获取 API 文档元数据 | JWT |

### 8.2 新增数据表

```sql
CREATE TABLE agent_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version VARCHAR(64) NOT NULL,
    changelog TEXT,
    config_snapshot JSONB NOT NULL,
    published_by UUID REFERENCES users(id),
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agent_versions_agent ON agent_versions(agent_id);
CREATE INDEX idx_agent_versions_active ON agent_versions(agent_id) WHERE is_active = true;
```

### 8.3 `/execute` 端点实现要点

1. **版本检查**：查询 `agent_versions` 表获取 `is_active = true` 的版本，如不存在返回 400
2. **配置加载**：从 `config_snapshot` 加载智能体配置（而非当前实时配置）
3. **Session 管理**：
   - 如果请求中包含 `session_rid`，复用该 Session
   - 否则创建新 Session
4. **Agent Loop 调用**：使用加载的配置创建 Agent Loop，同步执行并等待完整结果
5. **响应组装**：将完整 Markdown 回答和 Session ID 返回

### 8.4 SSE `/sessions/{id}/chat` 实现要点

- 复用现有的 SSE 流式推送机制（参考 `chat.py` 中的实现）
- 通过 `Accept: text/event-stream` 请求头区分 SSE 和 JSON 响应
- 事件类型保持与前端 WebSocket 事件一致

### 8.5 权限控制

| 端点 | 权限 | 说明 |
|------|------|------|
| 发布版本 | Admin / Agent Owner | 仅管理员或智能体创建者可发布 |
| 查看 API 文档 | 所有已登录用户 | 方便开发者查阅 |
| 调用 API | 所有已登录用户 | 通过 JWT Token 认证 |
| 未来扩展 | API Key 认证 | 支持生成独立的 API Key，无需 JWT |

---

## 9. 前端实现要求

### 9.1 侧边栏新增菜单项

在 `AgentConfigSidebar.tsx` 的图标导轨中新增：

- 图标：`<ApiOutlined />` 或 `<CodeOutlined />`
- Tooltip：`"API 文档"`
- Section Key：`api`

### 9.2 API 文档面板组件

新增 `ApiDocPanel.tsx` 组件，包含：

1. **版本信息区域**
   - 显示最新已发布版本号、发布时间
   - 「发布新版本」按钮（仅管理员 / 所有者可见）
   - 「查看历史版本」链接

2. **Agent 概要卡片**
   - 模型名称、流式输出状态、工具数量、技能数量等

3. **Tab 切换**
   - Tab 1：一次性调用（同步）
   - Tab 2：会话式调用（SSE 流式）
   - Tab 3：会话管理

4. **代码示例展示**
   - 语言切换：cURL / Python / JavaScript
   - 代码高亮渲染（使用 `prism.js` 或 `react-syntax-highlighter`）
   - 一键复制按钮

5. **在线调试区域**（P2）
   - 输入框 + 发送按钮
   - 实时显示响应结果
   - 显示请求耗时

### 9.3 发布版本弹窗

新增 `PublishVersionModal.tsx` 组件：

| 字段 | 控件 | 必填 | 说明 |
|------|------|------|------|
| 版本号 | Input（自动生成，可修改） | ✓ | 默认 UUID 前 8 位，可手动输入 semver |
| 版本说明 | TextArea | ✗ | 本次发布的变更说明 |

### 9.4 API 类型定义

在 `frontend/src/lib/api.ts` 中新增：

```typescript
export const agentApiApi = {
  // 获取 API 文档元数据
  getDoc(agentId: string) {
    return request<AgentApiDoc>(`/agents/${agentId}/api-doc`);
  },

  // 版本管理
  listVersions(agentId: string) {
    return request<AgentVersion[]>(`/agents/${agentId}/versions`);
  },

  publishVersion(agentId: string, data: { version?: string; changelog?: string }) {
    return request<AgentVersion>(`/agents/${agentId}/versions`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },
};
```

---

## 10. 输入输出说明汇总

### 10.1 一次性调用 (`/execute`)

**输入**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_input` | string | ✓ | 用户消息文本 |
| `variables` | object | ✗ | 初始变量键值对，用于传递业务上下文 |
| `session_rid` | string | ✗ | 复用已有 Session 进行多轮对话 |

**输出**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `markdown_response` | string | Agent 回答的完整 Markdown 文本 |
| `session_rid` | string | Session ID，可用于后续多轮调用 |

### 10.2 会话式调用 (SSE)

**创建 Session 输入**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_type` | string | ✗ | `"production"` (默认) |
| `user_id` | string | ✗ | 外部用户标识 |

**SSE 事件格式**:

```
event: text_delta
data: {"text": "你"}

event: text_delta
data: {"text": "好"}

event: done
data: {"message_id": "msg_xxx", "trace": {...}}
```

---

## 11. 交互细节

### 11.1 代码复制

- 每个代码块右上角显示复制图标按钮
- 点击后图标变为 ✓ 并显示 "已复制" 提示，2 秒后恢复
- 复制内容包含完整的代码片段（含换行和缩进）

### 11.2 API Base URL

- 默认使用 `window.location.origin` 作为 Base URL
- 提供一个可编辑的输入框，允许用户手动修改 Base URL（如指向其他环境）
- 修改 Base URL 后所有代码示例实时更新

### 11.3 Token 处理

- 代码示例中的 Token 默认显示为 `YOUR_JWT_TOKEN`
- 提供「使用当前 Token」按钮，点击后自动填入当前登录用户的 JWT Token
- 填入后所有代码示例实时更新
- 安全提示文案："请勿在生产环境泄露您的 Token"

### 11.4 在线调试（P2）

```
┌─ 在线调试 ─────────────────────────────────────┐
│                                               │
│  用户消息:                                      │
│  ┌──────────────────────────────────────┐      │
│  │ 你好，请帮我分析一下数据               │      │
│  └──────────────────────────────────────┘      │
│                                               │
│  变量 (JSON):                                   │
│  ┌──────────────────────────────────────┐      │
│  │ {}                                    │      │
│  └──────────────────────────────────────┘      │
│                                               │
│  [发送请求]                                     │
│                                               │
│  响应 (耗时 2.3s):                              │
│  ┌──────────────────────────────────────┐      │
│  │ {                                     │      │
│  │   "code": 0,                          │      │
│  │   "data": {                           │      │
│  │     "markdown_response": "...",       │      │
│  │     "session_rid": "sess_xxx"         │      │
│  │   }                                   │      │
│  │ }                                     │      │
│  └──────────────────────────────────────┘      │
│                                               │
└───────────────────────────────────────────────┘
```

---

## 12. 错误码定义

| 错误码 | HTTP Status | 说明 |
|--------|-------------|------|
| 0 | 200 | 成功 |
| 40001 | 400 | Agent 未发布版本 |
| 40002 | 400 | 请求参数错误 |
| 40101 | 401 | 未认证（Token 无效或过期） |
| 40301 | 403 | 无权限调用此 Agent |
| 40401 | 404 | Agent 不存在 |
| 40402 | 404 | Session 不存在 |
| 42901 | 429 | 调用频率超限 |
| 50001 | 500 | Agent 执行超时 |
| 50002 | 500 | Agent 内部错误 |

---

## 13. 安全考量

| 维度 | 措施 |
|------|------|
| **认证** | 所有 API 调用需要有效的 JWT Token |
| **授权** | 用户只能调用自己有权限访问的智能体 |
| **频率限制** | 单用户每分钟最多 60 次调用（可配置） |
| **输入过滤** | `user_input` 长度限制 10,000 字符 |
| **输出过滤** | 响应中不暴露系统提示词、API Key 等敏感信息 |
| **CORS** | 仅允许平台域名的跨域请求 |
| **审计日志** | 记录所有外部 API 调用（调用者、时间、耗时、Token 用量） |
| **未来扩展** | 支持独立的 API Key 认证（Bearer Token 之外） |

---

## 14. 数据模型变更

### 14.1 新增表

- `agent_versions` — 智能体版本记录（见 §3.2）

### 14.2 现有表变更

无。API 调用基于版本的 `config_snapshot` 执行，不需要修改现有 `agents` 表结构。

### 14.3 数据库迁移

新增 Alembic 迁移脚本 `017_agent_versions.py`。

---

## 15. 影响范围

### 15.1 后端

| 文件 | 变更 |
|------|------|
| `interface/routes/agent_api.py` | 新增，API 调用 + 版本管理路由 |
| `interface/api.py` | 注册新路由 |
| `db/models.py` | 新增 `AgentVersion` 模型 |
| `alembic/versions/017_agent_versions.py` | 新增迁移脚本 |

### 15.2 前端

| 文件 | 变更 |
|------|------|
| `components/AgentConfigSidebar.tsx` | 新增 API 文档菜单项 |
| `components/api/ApiDocPanel.tsx` | 新增，API 文档面板主组件 |
| `components/api/PublishVersionModal.tsx` | 新增，发布版本弹窗 |
| `components/api/CodeBlock.tsx` | 新增，代码块展示组件（含复制） |
| `components/api/ApiDebugger.tsx` | 新增（P2），在线调试组件 |
| `lib/api.ts` | 新增 API 文档相关接口 |
| `lib/types.ts` | 新增 `AgentVersion`、`AgentApiDoc` 类型 |

---

## 16. 里程碑

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| M1 | 版本发布 + API 文档面板（代码示例展示） | P0 |
| M2 | `/execute` 同步调用端点 | P0 |
| M3 | SSE 会话式调用端点 | P0 |
| M4 | 会话管理 API | P1 |
| M5 | 在线调试功能 | P2 |
| M6 | API Key 认证（独立于 JWT） | P2 |

---

## 17. 附录：参考 API 文档格式

本需求文档中的 API 规格参考以下已发布的示例格式：

```
已发布版本 e74a256e-3aa8-4442-91a4-4d0724cedab3

Agent 概要:
  模型: —
  流式输出: 开启
  模型思考: 关闭
  工具数量: 0

POST 一次性调用（同步执行）
POST /api/agents/{agent_id}/execute

SSE 会话式调用（流式输出）
POST /api/agents/{agent_id}/sessions    → 创建 Session
POST /api/sessions/{session_id}/chat    → 发送消息（SSE）

会话管理:
GET    /api/sessions/{session_id}/messages            → 获取历史
POST   /api/sessions/{session_id}/clear               → 清空会话
DELETE /api/sessions/{session_id}                     → 删除会话
POST   /api/sessions/{session_id}/messages/{id}/feedback → 消息反馈
```
