import { useState, useEffect, useCallback } from 'react';
import {
  ApiOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  PoweroffOutlined,
  TeamOutlined,
  CopyOutlined,
  LinkOutlined,
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
  Empty,
  Tooltip,
  Radio,
  Alert,
  Table,
} from 'antd';
import { channelsApi, agentsApi, toolsApi, usersApi } from '@/lib/api';
import type { AdminUser } from '@/lib/api';
import type { Agent, Channel, ChannelBinding, ChannelMode, ToolInfo } from '@/lib/types';
import { useAuthStore } from '@/stores/authStore';
import { Navigate, useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

const { Text, Paragraph } = Typography;
const { Option } = Select;

const STATUS_META: Record<string, { label: string; color: string }> = {
  enabled: { label: '已启用', color: 'green' },
  disabled: { label: '已停用', color: 'default' },
  error: { label: '异常', color: 'red' },
};

const MODE_META: Record<string, { label: string; color: string }> = {
  websocket: { label: 'WebSocket', color: 'blue' },
  webhook: { label: 'Webhook', color: 'purple' },
};

export default function ChannelsPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';

  const [channels, setChannels] = useState<Channel[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingChannel, setEditingChannel] = useState<Channel | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const [mode, setMode] = useState<ChannelMode>('websocket');

  const [webhookUrl, setWebhookUrl] = useState<string | null>(null);

  const [bindingsChannel, setBindingsChannel] = useState<Channel | null>(null);
  const [bindings, setBindings] = useState<ChannelBinding[]>([]);
  const [bindingsLoading, setBindingsLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [channelList, agentList, toolList, userList] = await Promise.all([
        channelsApi.list(),
        agentsApi.list(),
        toolsApi.list().catch(() => [] as ToolInfo[]),
        usersApi.list().catch(() => [] as AdminUser[]),
      ]);
      setChannels(channelList);
      setAgents(agentList);
      setTools(toolList);
      setUsers(userList);
    } catch (err: any) {
      message.error(`加载渠道失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  const agentName = (id: string) => agents.find((a) => a.id === id)?.name ?? id;

  const openModal = (channel?: Channel) => {
    if (channel) {
      setEditingChannel(channel);
      form.setFieldsValue({
        name: channel.name,
        channel_type: channel.channel_type,
        agent_id: channel.agent_id,
        app_id: channel.app_id,
        app_secret: '',
        encrypt_key: '',
        verification_token: '',
        mode: channel.mode,
        tool_blacklist: channel.tool_blacklist,
      });
      setMode(channel.mode);
    } else {
      setEditingChannel(null);
      form.resetFields();
      form.setFieldsValue({
        channel_type: 'feishu',
        mode: 'websocket',
        tool_blacklist: [],
      });
      setMode('websocket');
    }
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      if (editingChannel) {
        const payload: Record<string, unknown> = {
          name: values.name,
          agent_id: values.agent_id,
          app_id: values.app_id,
          mode: values.mode,
          tool_blacklist: values.tool_blacklist ?? [],
        };
        if (values.app_secret) payload.app_secret = values.app_secret;
        if (values.encrypt_key) payload.encrypt_key = values.encrypt_key;
        if (values.verification_token) payload.verification_token = values.verification_token;
        await channelsApi.update(editingChannel.id, payload);
        message.success('渠道已更新；如修改了凭证或连接模式，需重新启用');
      } else {
        await channelsApi.create({
          name: values.name,
          channel_type: values.channel_type,
          agent_id: values.agent_id,
          app_id: values.app_id,
          app_secret: values.app_secret,
          encrypt_key: values.encrypt_key || null,
          verification_token: values.verification_token || null,
          mode: values.mode,
          tool_blacklist: values.tool_blacklist ?? [],
        });
        message.success('渠道已创建');
      }
      setModalOpen(false);
      fetchData();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '操作失败');
    } finally {
      setSaving(false);
    }
  };

  const handleEnable = async (channel: Channel) => {
    try {
      const result = await channelsApi.enable(channel.id);
      message.success(`渠道「${channel.name}」已启用`);
      if (result.webhook_url) {
        setWebhookUrl(result.webhook_url);
      }
      fetchData();
    } catch (err: any) {
      message.error(err.message || '启用失败');
      fetchData();
    }
  };

  const handleDisable = async (channel: Channel) => {
    try {
      await channelsApi.disable(channel.id);
      message.success(`渠道「${channel.name}」已停用`);
      fetchData();
    } catch (err: any) {
      message.error(err.message || '停用失败');
    }
  };

  const handleDelete = async (channel: Channel) => {
    try {
      await channelsApi.delete(channel.id);
      message.success('渠道已删除');
      fetchData();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    }
  };

  const openBindings = async (channel: Channel) => {
    setBindingsChannel(channel);
    setBindingsLoading(true);
    try {
      setBindings(await channelsApi.bindings(channel.id));
    } catch (err: any) {
      message.error(`加载绑定列表失败：${err.message}`);
    } finally {
      setBindingsLoading(false);
    }
  };

  const copyWebhookUrl = () => {
    if (!webhookUrl) return;
    navigator.clipboard.writeText(webhookUrl).then(
      () => message.success('回调地址已复制'),
      () => message.error('复制失败，请手动复制'),
    );
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <ApiOutlined className="text-primary" />
              渠道管理
            </h1>
            <Text type="secondary">
              接入飞书等 IM 渠道，让用户在 IM 中直接与 Agent 对话
            </Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
            添加渠道
          </Button>
        </div>

        {/* Empty state */}
        {channels.length === 0 ? (
          <Card>
            <Empty
              description="暂无渠道。添加飞书渠道后，用户可在飞书中直接与 Agent 对话。"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            >
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
                添加渠道
              </Button>
            </Empty>
          </Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {channels.map((channel) => {
              const statusInfo = STATUS_META[channel.status] ?? STATUS_META.disabled;
              const modeInfo = MODE_META[channel.mode] ?? MODE_META.websocket;
              return (
                <Card
                  key={channel.id}
                  className={cn('transition-all', channel.status === 'disabled' && 'opacity-70')}
                  title={
                    <div className="flex items-center gap-2">
                      <ApiOutlined />
                      <span className="font-semibold">{channel.name}</span>
                      <Tag color="cyan">{channel.channel_type === 'feishu' ? '飞书' : channel.channel_type}</Tag>
                      <Tag color={modeInfo.color}>{modeInfo.label}</Tag>
                      <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
                    </div>
                  }
                  extra={
                    <Space size={4}>
                      {channel.status === 'enabled' ? (
                        <Tooltip title="停用">
                          <Button
                            type="text"
                            size="small"
                            icon={<PoweroffOutlined className="text-green-500" />}
                            onClick={() => handleDisable(channel)}
                          />
                        </Tooltip>
                      ) : (
                        <Tooltip title="启用">
                          <Button
                            type="text"
                            size="small"
                            icon={<PlayCircleOutlined />}
                            onClick={() => handleEnable(channel)}
                          />
                        </Tooltip>
                      )}
                      <Tooltip title="绑定用户">
                        <Button
                          type="text"
                          size="small"
                          icon={<TeamOutlined />}
                          onClick={() => openBindings(channel)}
                        />
                      </Tooltip>
                      <Tooltip title="编辑">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          onClick={() => openModal(channel)}
                        />
                      </Tooltip>
                      <Popconfirm
                        title="删除渠道？"
                        description="将连带删除该渠道的用户绑定与会话映射，且不可恢复。"
                        onConfirm={() => handleDelete(channel)}
                        okText="删除"
                        okType="danger"
                        cancelText="取消"
                      >
                        <Tooltip title="删除">
                          <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                        </Tooltip>
                      </Popconfirm>
                    </Space>
                  }
                >
                  <div className="space-y-2 text-sm">
                    <div className="flex items-center gap-2">
                      <Text type="secondary" className="w-20 shrink-0">绑定 Agent</Text>
                      <Text>{agentName(channel.agent_id)}</Text>
                    </div>
                    <div className="flex items-center gap-2">
                      <Text type="secondary" className="w-20 shrink-0">App ID</Text>
                      <Text className="font-mono text-xs" code>{channel.app_id}</Text>
                    </div>
                    {channel.tool_blacklist.length > 0 && (
                      <div className="flex items-start gap-2">
                        <Text type="secondary" className="w-20 shrink-0">工具黑名单</Text>
                        <div className="flex flex-wrap gap-1">
                          {channel.tool_blacklist.map((t) => (
                            <Tag key={t} color="orange">{t}</Tag>
                          ))}
                        </div>
                      </div>
                    )}
                    {channel.last_error && (
                      <Alert
                        type="error"
                        showIcon
                        message={<span className="text-xs">{channel.last_error}</span>}
                        className="mt-2"
                      />
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        {/* ---- Create/Edit Modal ---- */}
        <Modal
          title={editingChannel ? '编辑渠道' : '添加渠道'}
          open={modalOpen}
          onOk={handleSave}
          onCancel={() => setModalOpen(false)}
          confirmLoading={saving}
          destroyOnHidden
          width={640}
          okText="保存"
          cancelText="取消"
        >
          <Form form={form} layout="vertical" className="mt-4">
            <Form.Item
              name="name"
              label="渠道名称"
              rules={[{ required: true, message: '请输入渠道名称' }]}
            >
              <Input placeholder="例如：飞书客服机器人" />
            </Form.Item>

            <div className="flex gap-4">
              <Form.Item name="channel_type" label="渠道类型" className="w-40">
                <Select disabled={!!editingChannel}>
                  <Option value="feishu">飞书</Option>
                  <Option value="dingtalk" disabled>钉钉（待支持）</Option>
                  <Option value="wecom" disabled>企微（待支持）</Option>
                </Select>
              </Form.Item>

              <Form.Item
                name="agent_id"
                label="绑定 Agent"
                rules={[{ required: true, message: '请选择 Agent' }]}
                className="flex-1"
                tooltip="渠道消息将由该 Agent 处理"
              >
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择 Agent"
                  options={agents.map((a) => ({ value: a.id, label: a.name }))}
                />
              </Form.Item>
            </div>

            <div className="flex gap-4">
              <Form.Item
                name="app_id"
                label="App ID"
                rules={[{ required: true, message: '请输入 App ID' }]}
                className="flex-1"
              >
                <Input placeholder="cli_xxxxxxxx" />
              </Form.Item>

              <Form.Item
                name="app_secret"
                label="App Secret"
                rules={editingChannel ? [] : [{ required: true, message: '请输入 App Secret' }]}
                className="flex-1"
              >
                <Input.Password placeholder={editingChannel ? '留空保持原值不变' : '输入 App Secret'} />
              </Form.Item>
            </div>

            <Form.Item
              name="mode"
              label="连接模式"
              rules={[{ required: true }]}
              tooltip="同一渠道同一时刻仅一种模式生效；修改后需重新启用"
            >
              <Radio.Group onChange={(e) => setMode(e.target.value)}>
                <Radio value="websocket">
                  <div className="inline-block">
                    <span className="font-medium">WebSocket 长连接</span>
                    <Text type="secondary" className="text-xs block">
                      无需公网地址，适合内网/私有化部署
                    </Text>
                  </div>
                </Radio>
                <Radio value="webhook">
                  <div className="inline-block">
                    <span className="font-medium">Webhook 回调</span>
                    <Text type="secondary" className="text-xs block">
                      需要公网可访问的回调地址
                    </Text>
                  </div>
                </Radio>
              </Radio.Group>
            </Form.Item>

            {mode === 'webhook' && (
              <div className="flex gap-4">
                <Form.Item name="encrypt_key" label="Encrypt Key" className="flex-1">
                  <Input.Password placeholder={editingChannel ? '留空保持原值不变' : '事件订阅 Encrypt Key'} />
                </Form.Item>
                <Form.Item name="verification_token" label="Verification Token" className="flex-1">
                  <Input.Password placeholder={editingChannel ? '留空保持原值不变' : '事件订阅 Verification Token'} />
                </Form.Item>
              </div>
            )}

            <Form.Item
              name="tool_blacklist"
              label="工具黑名单"
              tooltip="该渠道会话中禁用的工具（如沙箱命令执行等高危工具）"
            >
              <Select
                mode="multiple"
                placeholder="默认不限制"
                options={tools.map((t) => ({ value: t.name, label: `${t.label} (${t.name})` }))}
                optionFilterProp="label"
              />
            </Form.Item>
          </Form>
        </Modal>

        {/* ---- Webhook URL Modal ---- */}
        <Modal
          title={
            <div className="flex items-center gap-2">
              <LinkOutlined />
              <span>Webhook 回调地址</span>
            </div>
          }
          open={!!webhookUrl}
          onCancel={() => setWebhookUrl(null)}
          footer={null}
          width={560}
        >
          <div className="mt-4 space-y-3">
            <Text type="secondary">
              请将以下地址填入飞书开放平台「事件订阅 → 请求地址」中：
            </Text>
            <div className="flex items-center gap-2">
              <Paragraph
                copyable={{ text: webhookUrl ?? '', icon: <CopyOutlined /> }}
                className="flex-1 font-mono text-xs bg-muted/50 p-2 rounded mb-0 break-all"
              >
                {webhookUrl}
              </Paragraph>
            </div>
            <Button type="primary" icon={<CopyOutlined />} onClick={copyWebhookUrl} block>
              复制回调地址
            </Button>
          </div>
        </Modal>

        {/* ---- Bindings Modal ---- */}
        <Modal
          title={`绑定用户 — ${bindingsChannel?.name ?? ''}(按租户共享)`}
          open={!!bindingsChannel}
          onCancel={() => setBindingsChannel(null)}
          footer={null}
          width={720}
        >
          <Table
            rowKey="id"
            size="small"
            loading={bindingsLoading}
            dataSource={bindings}
            pagination={false}
            locale={{ emptyText: '暂无绑定用户' }}
            columns={[
              {
                title: '外部用户 ID (open_id)',
                dataIndex: 'external_id',
                render: (v: string) => <Text className="font-mono text-xs">{v}</Text>,
              },
              {
                title: '绑定类型',
                dataIndex: 'bind_type',
                width: 120,
                render: (v: string) =>
                  v === 'bound' ? <Tag color="green">已绑定账号</Tag> : <Tag>影子账号</Tag>,
              },
              {
                title: '平台用户',
                dataIndex: 'user_id',
                render: (userId: string) => {
                  const user = users.find((u) => u.id === userId);
                  if (!user) {
                    return (
                      <Tooltip title={`未找到平台用户 ${userId}`}>
                        <Text type="secondary" className="font-mono text-xs">{userId}</Text>
                      </Tooltip>
                    );
                  }
                  const label = user.display_name || user.username;
                  return (
                    <Tooltip title={`查看用户资料 (${userId})`}>
                      <Button
                        type="link"
                        size="small"
                        className="px-0"
                        onClick={() => navigate(`/users`)}
                      >
                        {label}
                      </Button>
                    </Tooltip>
                  );
                },
              },
              {
                title: '绑定时间',
                dataIndex: 'created_at',
                width: 180,
                render: (v: string) => (v ? new Date(v).toLocaleString() : '-'),
              },
            ]}
          />
        </Modal>
      </div>
    </div>
  );
}
