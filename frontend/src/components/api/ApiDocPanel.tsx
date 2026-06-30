import { useState, useEffect, useCallback } from 'react';
import {
  ApiOutlined,
  CloseOutlined,
  RocketOutlined,
  HistoryOutlined,
  ThunderboltOutlined,
  SyncOutlined,
  MessageOutlined,
  CloudServerOutlined,
} from '@ant-design/icons';
import {
  Button,
  Tabs,
  Tag,
  Typography,
  Spin,
  App,
  Input,
  Tooltip,
  Empty,
} from 'antd';
import { agentApiApi } from '@/lib/api';
import { tokenStorage } from '@/lib/auth';
import type { AgentApiDoc, AgentVersion } from '@/lib/types';
import PublishVersionModal from './PublishVersionModal';
import CodeBlock from '../chat/CodeBlock';

const { Text } = Typography;

interface Props {
  agentId: string;
  onClose: () => void;
  /** When true, hide the internal header (used when embedded in a standalone page) */
  embedded?: boolean;
}

export default function ApiDocPanel({ agentId, onClose, embedded }: Props) {
  const { message } = App.useApp();
  const [doc, setDoc] = useState<AgentApiDoc | null>(null);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [publishModalOpen, setPublishModalOpen] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [baseUrl, setBaseUrl] = useState(window.location.origin);
  const [useCurrentToken, setUseCurrentToken] = useState(false);

  const tokenPlaceholder = useCurrentToken
    ? (tokenStorage.getAccess() || 'YOUR_JWT_TOKEN')
    : 'YOUR_JWT_TOKEN';

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [d, v] = await Promise.all([
        agentApiApi.getDoc(agentId),
        agentApiApi.listVersions(agentId),
      ]);
      setDoc(d);
      setVersions(v);
    } catch (err: any) {
      message.error(`加载 API 文档失败: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [agentId, message]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handlePublished = () => {
    loadData();
  };

  if (loading) {
    return (
      <>
        {!embedded && <PanelHeader onClose={onClose} />}
        <div className="flex-1 flex items-center justify-center py-16">
          <Spin />
        </div>
      </>
    );
  }

  if (!doc) {
    return (
      <>
        {!embedded && <PanelHeader onClose={onClose} />}
        <div className="flex-1 flex items-center justify-center px-4 py-16">
          <Empty description="无法加载 API 文档" />
        </div>
      </>
    );
  }

  return (
    <>
      {!embedded && <PanelHeader onClose={onClose} />}

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {/* Base URL config */}
        <div className="mb-3">
          <Text type="secondary" className="text-xs block mb-1">API Base URL</Text>
          <Input
            size="small"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className="font-mono text-xs"
            suffix={
              <Tooltip title="使用当前登录 Token">
                <Button
                  type={useCurrentToken ? 'primary' : 'default'}
                  size="small"
                  onClick={() => setUseCurrentToken(!useCurrentToken)}
                  className="!text-xs !h-5 !px-1.5"
                >
                  {useCurrentToken ? '✓ 当前Token' : 'Token'}
                </Button>
              </Tooltip>
            }
          />
        </div>

        {/* Version info */}
        <div className="rounded-lg border border-border bg-muted/30 p-3 mb-3">
          {doc.has_published_version && doc.latest_version ? (
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 mb-1">
                  <Tag color="green" className="text-xs">已发布</Tag>
                  <Text className="text-xs font-mono">{doc.latest_version.version}</Text>
                </div>
                <Text type="secondary" className="text-xs block">
                  发布于 {new Date(doc.latest_version.published_at).toLocaleString('zh-CN')}
                </Text>
                {doc.latest_version.changelog && (
                  <Text type="secondary" className="text-xs block mt-1 line-clamp-2">
                    {doc.latest_version.changelog}
                  </Text>
                )}
              </div>
              <div className="flex flex-col gap-1 flex-shrink-0">
                <Button
                  size="small"
                  icon={<RocketOutlined />}
                  onClick={() => setPublishModalOpen(true)}
                  className="!text-xs"
                >
                  发布新版本
                </Button>
                <Button
                  size="small"
                  type="text"
                  icon={<HistoryOutlined />}
                  onClick={() => setShowHistory(!showHistory)}
                  className="!text-xs"
                >
                  历史版本
                </Button>
              </div>
            </div>
          ) : (
            <div className="text-center py-2">
              <ThunderboltOutlined className="text-2xl text-muted-foreground/30 mb-2" />
              <Text type="secondary" className="text-xs block mb-2">
                尚未发布版本，请先发布一个版本
              </Text>
              <Button
                type="primary"
                size="small"
                icon={<RocketOutlined />}
                onClick={() => setPublishModalOpen(true)}
              >
                发布第一个版本
              </Button>
            </div>
          )}
        </div>

        {/* Version history */}
        {showHistory && versions.length > 0 && (
          <div className="rounded-lg border border-border/50 mb-3 overflow-hidden">
            <div className="px-3 py-2 bg-muted/30 border-b border-border/50">
              <Text className="text-xs font-medium">版本历史 ({versions.length})</Text>
            </div>
            <div className="max-h-40 overflow-y-auto">
              {versions.map((v) => (
                <div
                  key={v.id}
                  className="flex items-center justify-between px-3 py-1.5 border-b border-border/30 last:border-0"
                >
                  <div className="flex items-center gap-2">
                    <Text className="text-xs font-mono">{v.version}</Text>
                    {v.is_active && <Tag color="green" className="text-[10px]">当前</Tag>}
                  </div>
                  <Text type="secondary" className="text-[10px]">
                    {new Date(v.published_at).toLocaleDateString('zh-CN')}
                  </Text>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Agent overview */}
        <div className="rounded-lg border border-border/50 bg-muted/20 p-3 mb-3">
          <Text className="text-xs font-semibold block mb-2">Agent 概要</Text>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
            <div className="flex items-center justify-between">
              <Text type="secondary" className="text-xs">模型</Text>
              <Tag className="text-xs">{doc.model_name || '默认'}</Tag>
            </div>
            <div className="flex items-center justify-between">
              <Text type="secondary" className="text-xs">流式输出</Text>
              <Tag color={doc.streaming_enabled ? 'green' : 'default'} className="text-xs">
                {doc.streaming_enabled ? '开启' : '关闭'}
              </Tag>
            </div>
            <div className="flex items-center justify-between">
              <Text type="secondary" className="text-xs">模型思考</Text>
              <Tag className="text-xs">{doc.thinking_enabled ? '开启' : '关闭'}</Tag>
            </div>
            <div className="flex items-center justify-between">
              <Text type="secondary" className="text-xs">工具数量</Text>
              <Tag color="blue" className="text-xs">{doc.tool_count}</Tag>
            </div>
          </div>
        </div>

        {/* API Tabs */}
        {doc.has_published_version && (
          <Tabs
            size="small"
            items={[
              {
                key: 'execute',
                label: (
                  <span className="flex items-center gap-1">
                    <SyncOutlined className="text-xs" />
                    一次性调用
                  </span>
                ),
                children: (
                  <ExecuteTab
                    agentId={agentId}
                    baseUrl={baseUrl}
                    token={tokenPlaceholder}
                  />
                ),
              },
              {
                key: 'sse',
                label: (
                  <span className="flex items-center gap-1">
                    <CloudServerOutlined className="text-xs" />
                    会话式调用
                  </span>
                ),
                children: (
                  <SseTab
                    agentId={agentId}
                    baseUrl={baseUrl}
                    token={tokenPlaceholder}
                  />
                ),
              },
              {
                key: 'sessions',
                label: (
                  <span className="flex items-center gap-1">
                    <MessageOutlined className="text-xs" />
                    会话管理
                  </span>
                ),
                children: <SessionManagementTab />,
              },
            ]}
          />
        )}
      </div>

      {/* Publish version modal */}
      <PublishVersionModal
        open={publishModalOpen}
        agentId={agentId}
        onClose={() => setPublishModalOpen(false)}
        onPublished={handlePublished}
      />
    </>
  );
}

// ---- Sub Components ----

function PanelHeader({ onClose }: { onClose: () => void }) {
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
      <div className="flex items-center gap-2">
        <ApiOutlined className="text-primary" />
        <Text strong className="text-sm">API 文档</Text>
      </div>
      <button
        onClick={onClose}
        className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
      >
        <CloseOutlined className="text-xs" />
      </button>
    </div>
  );
}

// ---- Execute Tab (Sync) ----

function ExecuteTab({
  agentId,
  baseUrl,
  token,
}: {
  agentId: string;
  baseUrl: string;
  token: string;
}) {
  const curlCode = `curl -X POST '${baseUrl}/api/agents/${agentId}/execute' \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer ${token}' \\
  -d '{
    "user_input": "你好，请帮我分析一下数据"
  }'`;

  const pythonCode = `import requests

resp = requests.post(
    "${baseUrl}/api/agents/${agentId}/execute",
    headers={"Authorization": "Bearer ${token}"},
    json={
        "user_input": "你好，请帮我分析一下数据"
    },
)
data = resp.json()["data"]
print(data["markdown_response"])`;

  const jsCode = `const resp = await fetch("${baseUrl}/api/agents/${agentId}/execute", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "Bearer ${token}",
  },
  body: JSON.stringify({
    user_input: "你好，请帮我分析一下数据",
  }),
});
const { data } = await resp.json();
console.log(data.markdown_response);`;

  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-center gap-2 mb-1.5">
          <Tag color="blue" className="text-xs">POST</Tag>
          <Text className="text-xs font-mono">/api/agents/{'{agent_id}'}/execute</Text>
        </div>
        <Text type="secondary" className="text-xs">
          适用于简单场景，发送一条消息并同步等待完整回答。无需管理 Session。
        </Text>
      </div>

      {/* Request Body */}
      <div>
        <Text className="text-xs font-medium block mb-1">Request Body</Text>
        <div className="rounded border border-border/50 overflow-hidden text-xs">
          <table className="w-full">
            <thead className="bg-muted/30">
              <tr>
                <th className="px-2 py-1 text-left text-xs font-medium">字段</th>
                <th className="px-2 py-1 text-left text-xs font-medium">类型</th>
                <th className="px-2 py-1 text-left text-xs font-medium">必填</th>
                <th className="px-2 py-1 text-left text-xs font-medium">说明</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-border/30">
                <td className="px-2 py-1 font-mono">user_input</td>
                <td className="px-2 py-1">string</td>
                <td className="px-2 py-1"><Tag color="red" className="text-[10px]">必填</Tag></td>
                <td className="px-2 py-1">用户消息文本</td>
              </tr>
              <tr className="border-t border-border/30">
                <td className="px-2 py-1 font-mono">variables</td>
                <td className="px-2 py-1">object</td>
                <td className="px-2 py-1"><Tag className="text-[10px]">可选</Tag></td>
                <td className="px-2 py-1">初始变量键值对</td>
              </tr>
              <tr className="border-t border-border/30">
                <td className="px-2 py-1 font-mono">session_rid</td>
                <td className="px-2 py-1">string</td>
                <td className="px-2 py-1"><Tag className="text-[10px]">可选</Tag></td>
                <td className="px-2 py-1">复用已有 Session</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Response */}
      <div>
        <Text className="text-xs font-medium block mb-1">Response</Text>
        <CodeBlock
          language="json"
          code={`{
  "code": 0,
  "data": {
    "markdown_response": "Agent 返回的 Markdown 文本 ...",
    "session_rid": "sess_xxx"
  }
}`}
        />
      </div>

      {/* Code examples */}
      <div>
        <Text className="text-xs font-medium block mb-1">cURL</Text>
        <CodeBlock language="bash" code={curlCode} />
      </div>
      <div>
        <Text className="text-xs font-medium block mb-1">Python</Text>
        <CodeBlock language="python" code={pythonCode} />
      </div>
      <div>
        <Text className="text-xs font-medium block mb-1">JavaScript</Text>
        <CodeBlock language="javascript" code={jsCode} />
      </div>
    </div>
  );
}

// ---- SSE Tab (Streaming) ----

function SseTab({
  agentId,
  baseUrl,
  token,
}: {
  agentId: string;
  baseUrl: string;
  token: string;
}) {
  const createSessionCode = `curl -X POST '${baseUrl}/api/agents/${agentId}/sessions' \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer ${token}' \\
  -d '{
    "session_type": "production",
    "user_id": "user_123"
  }'`;

  const sseChatCode = `curl -N -X POST '${baseUrl}/api/sessions/{session_id}/chat' \\
  -H 'Content-Type: application/json' \\
  -H 'Accept: text/event-stream' \\
  -H 'Authorization: Bearer ${token}' \\
  -d '{
    "message": "你好"
  }'`;

  const pythonCode = `import requests, json

BASE = "${baseUrl}/api"
AGENT_ID = "${agentId}"
TOKEN = "${token}"

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
            break`;

  const jsCode = `const BASE = "${baseUrl}/api";
const AGENT_ID = "${agentId}";
const TOKEN = "${token}";

// 1. 创建 session
const sessionResp = await fetch(
  \`\${BASE}/agents/\${AGENT_ID}/sessions\`,
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: \`Bearer \${TOKEN}\`,
    },
    body: JSON.stringify({
      session_type: "production",
      user_id: "user_123",
    }),
  }
);
const { data: session } = await sessionResp.json();

// 2. 流式对话
const chatResp = await fetch(
  \`\${BASE}/sessions/\${session.id}/chat\`,
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: \`Bearer \${TOKEN}\`,
    },
    body: JSON.stringify({ message: "你好" }),
  }
);

const reader = chatResp.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const text = decoder.decode(value);
  for (const line of text.split("\\n")) {
    if (line.startsWith("data:")) {
      const data = JSON.parse(line.slice(5).trim());
      console.log(data);
    }
  }
}`;

  return (
    <div className="space-y-3">
      <Text type="secondary" className="text-xs">
        适用于多轮对话场景。先创建 Session，然后通过 SSE 流式接收回答。
      </Text>

      {/* Step 1 */}
      <div>
        <Text className="text-xs font-semibold block mb-1">
          Step 1: 创建 Session
        </Text>
        <div className="flex items-center gap-2 mb-1.5">
          <Tag color="blue" className="text-xs">POST</Tag>
          <Text className="text-xs font-mono">/api/agents/{'{agent_id}'}/sessions</Text>
        </div>
        <CodeBlock language="bash" code={createSessionCode} />
      </div>

      {/* Step 2 */}
      <div>
        <Text className="text-xs font-semibold block mb-1">
          Step 2: 发送消息 (SSE 流式)
        </Text>
        <div className="flex items-center gap-2 mb-1.5">
          <Tag color="blue" className="text-xs">POST</Tag>
          <Text className="text-xs font-mono">/api/sessions/{'{session_id}'}/chat</Text>
        </div>
        <CodeBlock language="bash" code={sseChatCode} />
      </div>

      {/* SSE Event Types */}
      <div>
        <Text className="text-xs font-medium block mb-1">SSE 事件类型</Text>
        <div className="rounded border border-border/50 overflow-hidden text-xs">
          <table className="w-full">
            <thead className="bg-muted/30">
              <tr>
                <th className="px-2 py-1 text-left text-xs font-medium">event</th>
                <th className="px-2 py-1 text-left text-xs font-medium">data 字段</th>
                <th className="px-2 py-1 text-left text-xs font-medium">说明</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-border/30">
                <td className="px-2 py-1 font-mono">text_delta</td>
                <td className="px-2 py-1 font-mono">{'{ text }'}</td>
                <td className="px-2 py-1">文本增量，逐字返回</td>
              </tr>
              <tr className="border-t border-border/30">
                <td className="px-2 py-1 font-mono">done</td>
                <td className="px-2 py-1 font-mono">{'{ message_id, trace }'}</td>
                <td className="px-2 py-1">回答完成</td>
              </tr>
              <tr className="border-t border-border/30">
                <td className="px-2 py-1 font-mono">error</td>
                <td className="px-2 py-1 font-mono">{'{ code, message }'}</td>
                <td className="px-2 py-1">错误</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Code examples */}
      <div>
        <Text className="text-xs font-medium block mb-1">Python (流式)</Text>
        <CodeBlock language="python" code={pythonCode} />
      </div>
      <div>
        <Text className="text-xs font-medium block mb-1">JavaScript (流式)</Text>
        <CodeBlock language="javascript" code={jsCode} />
      </div>
    </div>
  );
}

// ---- Session Management Tab ----

function SessionManagementTab() {
  const endpoints = [
    { method: 'GET', path: '/api/sessions/{session_id}/messages', desc: '获取会话历史消息' },
    { method: 'POST', path: '/api/sessions/{session_id}/clear', desc: '清空会话（保留会话）' },
    { method: 'DELETE', path: '/api/sessions/{session_id}', desc: '删除会话' },
    { method: 'POST', path: '/api/sessions/{session_id}/messages/{message_id}/feedback', desc: '提交消息反馈 (up/down)' },
  ];

  return (
    <div className="space-y-3">
      <Text type="secondary" className="text-xs">
        管理通过 API 创建的会话。
      </Text>

      <div className="space-y-2">
        {endpoints.map((ep, idx) => (
          <div
            key={idx}
            className="rounded-lg border border-border/50 p-2.5"
          >
            <div className="flex items-center gap-2 mb-1">
              <Tag
                color={ep.method === 'GET' ? 'green' : ep.method === 'DELETE' ? 'red' : 'blue'}
                className="text-xs"
              >
                {ep.method}
              </Tag>
              <Text className="text-xs font-mono">{ep.path}</Text>
            </div>
            <Text type="secondary" className="text-xs">{ep.desc}</Text>
          </div>
        ))}
      </div>

      {/* Example: get messages */}
      <div>
        <Text className="text-xs font-medium block mb-1">示例：获取会话历史</Text>
        <CodeBlock
          language="json"
          code={`// GET /api/sessions/{session_id}/messages
// Response:
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
}`}
        />
      </div>

      {/* Example: feedback */}
      <div>
        <Text className="text-xs font-medium block mb-1">示例：提交消息反馈</Text>
        <CodeBlock
          language="json"
          code={`// POST /api/sessions/{session_id}/messages/{message_id}/feedback
// Request Body:
{
  "feedback": "up"
}`}
        />
      </div>
    </div>
  );
}
