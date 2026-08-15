import { useState, useEffect, useCallback } from 'react';
import {
  DisconnectOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ReloadOutlined,
  ToolOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import {
  Form,
  Input,
  Select,
  Button,
  Card,
  Typography,
  Spin,
  App,
  Modal,
  Tag,
  Popconfirm,
  Space,
  Switch,
  InputNumber,
  Empty,
  Tooltip,
} from 'antd';
import { mcpApi } from '@/lib/api';
import type { McpServer } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { Navigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

const { Text } = Typography;
const { TextArea } = Input;
const { Option } = Select;

// Transport type display metadata
const TRANSPORT_META: Record<string, { label: string; color: string; description: string }> = {
  'sse': { label: 'SSE', color: 'cyan', description: 'Server-Sent Events 长连接' },
  'streamable-http': { label: 'Streamable HTTP', color: 'purple', description: 'HTTP POST + 可选 SSE 流式响应' },
};

// Status tag colors
const STATUS_META: Record<string, { color: string; label: string }> = {
  connected: { color: 'success', label: '已连接' },
  disconnected: { color: 'default', label: '未连接' },
  error: { color: 'error', label: '连接错误' },
};

type TransportType = 'sse' | 'streamable-http';

export default function McpManagementPage() {
  const { message } = App.useApp();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';

  // ---- List state ----
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);

  // ---- Edit/Create modal ----
  const [modalOpen, setModalOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServer | null>(null);
  const [form] = Form.useForm();

  // ---- Tools detail modal ----
  const [toolsModalOpen, setToolsModalOpen] = useState(false);
  const [toolsModalServer, setToolsModalServer] = useState<McpServer | null>(null);

  // ---- Admin guard ----
  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  // ---- Data fetching ----
  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const data = await mcpApi.list();
      setServers(data);
    } catch (err: any) {
      message.error(`加载 MCP Server 失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ---- Parse key=value text to object ----
  const parseKeyValue = (text: string): Record<string, string> => {
    const result: Record<string, string> = {};
    if (!text?.trim()) return result;
    for (const line of text.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.includes('=')) continue;
      const eqIdx = trimmed.indexOf('=');
      const key = trimmed.slice(0, eqIdx).trim();
      const value = trimmed.slice(eqIdx + 1).trim();
      if (key) result[key] = value;
    }
    return result;
  };

  // ---- Modal open/close ----
  const openModal = (server?: McpServer) => {
    if (server) {
      setEditingServer(server);
      form.setFieldsValue({
        name: server.name,
        transport_type: server.transport_type,
        url: server.url,
        headers: Object.entries(server.headers || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
        tool_prefix: server.tool_prefix || '',
        timeout: server.timeout,
        is_active: server.is_active,
      });
    } else {
      setEditingServer(null);
      form.resetFields();
      form.setFieldsValue({
        transport_type: 'streamable-http',
        timeout: 60,
        is_active: true,
      });
    }
    setModalOpen(true);
  };

  // ---- Save (create or update) ----
  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const payload = {
        name: values.name,
        transport_type: values.transport_type as TransportType,
        url: values.url,
        headers: parseKeyValue(values.headers || ''),
        tool_prefix: values.tool_prefix || undefined,
        timeout: values.timeout,
        is_active: values.is_active,
      };

      if (editingServer) {
        await mcpApi.update(editingServer.id, payload);
        message.success('MCP Server 已更新');
      } else {
        await mcpApi.create(payload);
        message.success('MCP Server 已创建');
      }
      setModalOpen(false);
      fetchData();
    } catch (err: any) {
      if (err?.errorFields) return; // form validation error
      message.error(err.message || '操作失败');
    } finally {
      setSaving(false);
    }
  };

  // ---- Delete ----
  const handleDelete = async (id: string) => {
    setDeletingId(id);
    try {
      await mcpApi.delete(id);
      message.success('MCP Server 已删除');
      fetchData();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    } finally {
      setDeletingId(null);
    }
  };

  // ---- Refresh ----
  const handleRefresh = async (id: string) => {
    setRefreshingId(id);
    try {
      await mcpApi.refresh(id);
      message.success('MCP Server 已刷新');
      fetchData();
    } catch (err: any) {
      message.error(`刷新失败：${err.message}`);
    } finally {
      setRefreshingId(null);
    }
  };

  // ---- View tools ----
  const openToolsModal = (server: McpServer) => {
    setToolsModalServer(server);
    setToolsModalOpen(true);
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <DisconnectOutlined className="text-primary" />
              MCP 服务管理
            </h1>
            <Text type="secondary">
              管理 Model Context Protocol 服务器，通过 SSE 或 Streamable HTTP 连接外部工具生态
            </Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
            添加 MCP Server
          </Button>
        </div>

        {/* Empty state */}
        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Spin size="large" />
          </div>
        ) : servers.length === 0 ? (
          <Card>
            <Empty description="暂无 MCP Server，请添加以扩展 Agent 的工具能力。" image={Empty.PRESENTED_IMAGE_SIMPLE}>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
                添加 MCP Server
              </Button>
            </Empty>
          </Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {servers.map((server) => {
              const statusInfo = STATUS_META[server.status] || STATUS_META.disconnected;
              const transportInfo = TRANSPORT_META[server.transport_type] || TRANSPORT_META['sse'];
              return (
                <Card
                  key={server.id}
                  className={cn(
                    'transition-all',
                    !server.is_active && 'opacity-60',
                  )}
                  title={
                    <div className="flex items-center gap-2">
                      <DisconnectOutlined />
                      <span className="font-semibold">{server.name}</span>
                      <Tag color={statusInfo.color} className="ml-1">
                        {statusInfo.label}
                      </Tag>
                      {server.tool_prefix && (
                        <Tag color="blue">前缀: {server.tool_prefix}</Tag>
                      )}
                    </div>
                  }
                  extra={
                    <Space size={4}>
                      <Tooltip title="刷新连接">
                        <Button
                          type="text"
                          size="small"
                          icon={<ReloadOutlined />}
                          onClick={() => handleRefresh(server.id)}
                          disabled={!server.is_active}
                          loading={refreshingId === server.id}
                        />
                      </Tooltip>
                      <Tooltip title="编辑">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          onClick={() => openModal(server)}
                        />
                      </Tooltip>
                      <Popconfirm
                        title="删除 MCP Server？"
                        description="删除后 Agent 将无法使用该 Server 提供的工具。"
                        onConfirm={() => handleDelete(server.id)}
                        okText="删除"
                        okType="danger"
                        cancelText="取消"
                        okButtonProps={{ loading: deletingId === server.id }}
                      >
                        <Tooltip title="删除">
                          <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                        </Tooltip>
                      </Popconfirm>
                    </Space>
                  }
                >
                  <div className="space-y-2 text-sm">
                    {/* Transport type */}
                    <div className="flex items-center gap-2">
                      <Text type="secondary" className="w-20 shrink-0">传输类型</Text>
                      <Tooltip title={transportInfo.description}>
                        <Tag color={transportInfo.color}>
                          {transportInfo.label}
                        </Tag>
                      </Tooltip>
                    </div>

                    {/* URL */}
                    <div className="flex items-start gap-2">
                      <Text type="secondary" className="w-20 shrink-0">URL</Text>
                      <Text className="font-mono text-xs truncate" code>
                        {server.url}
                      </Text>
                    </div>

                    {/* Timeout */}
                    <div className="flex items-center gap-2">
                      <Text type="secondary" className="w-20 shrink-0">超时</Text>
                      <Text>{server.timeout}s</Text>
                    </div>

                    {/* Tools count */}
                    <div className="flex items-center gap-2">
                      <Text type="secondary" className="w-20 shrink-0">工具数量</Text>
                      <div className="flex items-center gap-2">
                        <Text>
                          <ToolOutlined className="mr-1" />
                          {server.tools_count} 个工具
                        </Text>
                        {server.tools_count > 0 && (
                          <Button
                            type="link"
                            size="small"
                            onClick={() => openToolsModal(server)}
                            className="p-0 h-auto"
                          >
                            查看详情
                          </Button>
                        )}
                      </div>
                    </div>

                    {/* Error message */}
                    {server.status === 'error' && server.last_error && (
                      <div className="mt-2 p-2 bg-red-50 dark:bg-red-950/20 rounded-md border border-red-200 dark:border-red-900">
                        <Text type="danger" className="text-xs">
                          <CloseCircleOutlined className="mr-1" />
                          {server.last_error}
                        </Text>
                      </div>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        {/* ---- Create/Edit Modal ---- */}
        <Modal
          title={editingServer ? '编辑 MCP Server' : '添加 MCP Server'}
          open={modalOpen}
          onOk={handleSave}
          onCancel={() => setModalOpen(false)}
          confirmLoading={saving}
          destroyOnHidden
          width={600}
        >
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              transport_type: 'streamable-http',
              timeout: 60,
              is_active: true,
            }}
            className="mt-4"
          >
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, message: '请输入服务器名称' }]}
            >
              <Input placeholder="例如：GitHub MCP Server" />
            </Form.Item>

            <Form.Item
              name="transport_type"
              label="传输类型"
              rules={[{ required: true, message: '请选择传输类型' }]}
            >
              <Select>
                <Option value="streamable-http">
                  Streamable HTTP
                </Option>
                <Option value="sse">
                  SSE
                </Option>
              </Select>
            </Form.Item>

            <Form.Item
              name="url"
              label="服务端点 URL"
              rules={[
                { required: true, message: '请输入服务端点 URL' },
                { type: 'url', message: '请输入有效的 URL' },
              ]}
              tooltip="SSE 端点或 Streamable HTTP 端点的完整 URL"
            >
              <Input placeholder="http://localhost:3000/mcp" />
            </Form.Item>

            <Form.Item
              name="headers"
              label="请求头"
              tooltip="每行一个，格式 KEY=VALUE。用于认证等。"
            >
              <TextArea
                rows={3}
                placeholder={'Authorization=Bearer your-token\nX-Custom-Header=value'}
              />
            </Form.Item>

            <Form.Item
              name="tool_prefix"
              label="工具前缀"
              tooltip="为工具名称添加前缀以避免不同 Server 之间的工具名冲突（可选）"
            >
              <Input placeholder="例如：github_（可选）" />
            </Form.Item>

            <Form.Item
              name="timeout"
              label="调用超时（秒）"
            >
              <InputNumber min={5} max={600} className="w-full" />
            </Form.Item>

            <Form.Item
              name="is_active"
              label="启用"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </Form>
        </Modal>

        {/* ---- Tools Detail Modal ---- */}
        <Modal
          title={
            <div className="flex items-center gap-2">
              <ToolOutlined />
              <span>{toolsModalServer?.name} — 工具列表</span>
              <Tag color="blue">{toolsModalServer?.tools_count} 个工具</Tag>
            </div>
          }
          open={toolsModalOpen}
          onCancel={() => setToolsModalOpen(false)}
          footer={null}
          width={700}
        >
          {toolsModalServer?.tools && toolsModalServer.tools.length > 0 ? (
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {toolsModalServer.tools.map((tool) => (
                <div
                  key={tool.name}
                  className="p-3 bg-muted/50 rounded-lg border border-border"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Text strong className="font-mono">{tool.name}</Text>
                  </div>
                  <Text type="secondary" className="text-xs block">
                    {tool.description || '无描述'}
                  </Text>
                  {tool.input_schema && Object.keys(tool.input_schema).length > 0 && (
                    <details className="mt-2">
                      <summary className="text-xs cursor-pointer text-primary">
                        查看参数 Schema
                      </summary>
                      <pre className="mt-1 p-2 bg-background rounded text-xs overflow-x-auto max-h-40">
                        {JSON.stringify(tool.input_schema, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty description="该 Server 未提供任何工具" />
          )}
        </Modal>
      </div>
    </div>
  );
}
