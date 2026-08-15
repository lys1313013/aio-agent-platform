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
  Switch,
  Table,
} from 'antd';
import { channelsApi, agentsApi, toolsApi, usersApi } from '@/lib/api';
import type { AdminUser } from '@/lib/api';
import type { Agent, Channel, ChannelBinding, ChannelMode, ChannelType, ToolInfo } from '@/lib/types';
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

const CHANNEL_TYPE_LABEL: Record<ChannelType, string> = {
  feishu: '飞书',
  wecom: '企微',
  wecom_bot: '企微机器人',
  dingtalk: '钉钉',
};

type SecretField = 'app_secret' | 'encrypt_key' | 'verification_token';

const SECRET_INPUT_PROPS = {
  autoComplete: 'new-password',
  'data-1p-ignore': 'true',
  'data-lpignore': 'true',
} as const;

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
  const [actingChannelId, setActingChannelId] = useState<string | null>(null);
  const [deletingChannelId, setDeletingChannelId] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingChannel, setEditingChannel] = useState<Channel | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const [mode, setMode] = useState<ChannelMode>('websocket');
  const [channelType, setChannelType] = useState<ChannelType>('feishu');
  const [secretsBeingEdited, setSecretsBeingEdited] = useState<Set<SecretField>>(new Set());

  const [webhookUrl, setWebhookUrl] = useState<string | null>(null);
  const [webhookChannelType, setWebhookChannelType] = useState<ChannelType>('feishu');

  const [bindingsChannel, setBindingsChannel] = useState<Channel | null>(null);
  const [bindings, setBindings] = useState<ChannelBinding[]>([]);
  const [bindingsLoading, setBindingsLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      // 主列表数据先行加载，渠道列表不依赖下拉选项数据
      const channelList = await channelsApi.list();
      setChannels(channelList);
    } catch (err: any) {
      message.error(`加载渠道失败：${err.message}`);
    } finally {
      setLoading(false);
    }

    // 次要数据（Agent/工具/用户下拉选项）并行加载，失败兜底为空数组，不阻塞主内容
    const [agentList, toolList, userList] = await Promise.all([
      agentsApi.list().catch(() => [] as Agent[]),
      toolsApi.list().catch(() => [] as ToolInfo[]),
      usersApi.list().catch(() => [] as AdminUser[]),
    ]);
    setAgents(agentList);
    setTools(toolList);
    setUsers(userList);
  }, [message]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  const agentName = (id: string) => agents.find((a) => a.id === id)?.name ?? id;

  const openModal = (channel?: Channel) => {
    setSecretsBeingEdited(new Set());
    if (channel) {
      setEditingChannel(channel);
      setChannelType(channel.channel_type);
      form.setFieldsValue({
        name: channel.name,
        channel_type: channel.channel_type,
        agent_id: channel.agent_id,
        app_id: channel.app_id,
        app_secret: '',
        encrypt_key: '',
        verification_token: '',
        agentid: (channel.extra_config?.agentid as number) ?? '',
        mode: channel.mode,
        enable_streaming: channel.enable_streaming,
        tool_blacklist: channel.tool_blacklist,
      });
      setMode(channel.mode);
    } else {
      setEditingChannel(null);
      setChannelType('feishu');
      form.resetFields();
      form.setFieldsValue({
        channel_type: 'feishu',
        mode: 'websocket',
        enable_streaming: true,
        tool_blacklist: [],
      });
      setMode('websocket');
    }
    setModalOpen(true);
  };

  const startEditingSecret = (field: SecretField) => {
    // The input is deliberately not mounted before this explicit action. This
    // prevents password managers from silently replacing a channel credential.
    form.setFieldValue(field, '');
    setSecretsBeingEdited((current) => new Set(current).add(field));
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
          mode: isWecomBot ? 'websocket' : isWecom ? 'webhook' : values.mode,
          enable_streaming: values.enable_streaming,
          tool_blacklist: values.tool_blacklist ?? [],
        };
        if (isWecom && values.agentid) {
          // 合并而非整体覆盖，避免丢弃 extra_config 中 agentid 之外的键；编辑态
          // 未填 AgentID（如 API 直建、无 agentid 的旧渠道改名）时不提交，保留原值。
          payload.extra_config = {
            ...(editingChannel.extra_config ?? {}),
            agentid: Number(values.agentid),
          };
        }
        if (secretsBeingEdited.has('app_secret') && values.app_secret) {
          payload.app_secret = values.app_secret;
        }
        if (secretsBeingEdited.has('encrypt_key') && values.encrypt_key) {
          payload.encrypt_key = values.encrypt_key;
        }
        if (secretsBeingEdited.has('verification_token') && values.verification_token) {
          payload.verification_token = values.verification_token;
        }
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
          mode: isWecomBot ? 'websocket' : isWecom ? 'webhook' : values.mode,
          enable_streaming: values.enable_streaming,
          tool_blacklist: values.tool_blacklist ?? [],
          extra_config: isWecom ? { agentid: Number(values.agentid) } : undefined,
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
    setActingChannelId(channel.id);
    try {
      const result = await channelsApi.enable(channel.id);
      message.success(`渠道「${channel.name}」已启用`);
      if (result.webhook_url) {
        setWebhookUrl(result.webhook_url);
        setWebhookChannelType(channel.channel_type);
      }
      fetchData();
    } catch (err: any) {
      message.error(err.message || '启用失败');
      fetchData();
    } finally {
      setActingChannelId(null);
    }
  };

  const handleDisable = async (channel: Channel) => {
    setActingChannelId(channel.id);
    try {
      await channelsApi.disable(channel.id);
      message.success(`渠道「${channel.name}」已停用`);
      fetchData();
    } catch (err: any) {
      message.error(err.message || '停用失败');
    } finally {
      setActingChannelId(null);
    }
  };

  const handleDelete = async (channel: Channel) => {
    setDeletingChannelId(channel.id);
    try {
      await channelsApi.delete(channel.id);
      message.success('渠道已删除');
      fetchData();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    } finally {
      setDeletingChannelId(null);
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

  const isWecom = channelType === 'wecom';
  const isWecomBot = channelType === 'wecom_bot';
  const fields = isWecomBot
    ? {
        appId: { label: 'Bot ID', placeholder: '智能机器人 Bot ID（aib 开头）' },
        appSecret: { label: 'Secret', placeholder: '智能机器人 Secret' },
        encryptKey: { label: 'Encrypt Key', placeholder: '不适用' },
        verificationToken: { label: 'Verification Token', placeholder: '不适用' },
      }
    : isWecom
    ? {
        appId: { label: '企业ID', placeholder: 'ww 开头的企业 ID' },
        appSecret: { label: '应用Secret', placeholder: '应用 Secret（corpsecret）' },
        encryptKey: { label: '回调 EncodingAESKey', placeholder: '43 位 EncodingAESKey' },
        verificationToken: { label: '回调 Token', placeholder: '接收消息的回调 Token' },
      }
    : {
        appId: { label: 'App ID', placeholder: 'cli_xxxxxxxx' },
        appSecret: { label: 'App Secret', placeholder: '输入 App Secret' },
        encryptKey: { label: 'Encrypt Key', placeholder: '事件订阅 Encrypt Key' },
        verificationToken: { label: 'Verification Token', placeholder: '事件订阅 Verification Token' },
      };

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
          <Space>
            <Button
              icon={<LinkOutlined />}
              href="https://open.feishu.cn/page/openclaw?form=multiAgent"
              target="_blank"
              rel="noopener noreferrer"
            >
              创建飞书机器人
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
              添加渠道
            </Button>
          </Space>
        </div>

        {/* Empty state */}
        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Spin size="large" />
          </div>
        ) : channels.length === 0 ? (
          <Card>
            <Empty
              description="暂无渠道。添加渠道后，用户可在 IM 中直接与 Agent 对话。"
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
                      <Tag color="cyan">{CHANNEL_TYPE_LABEL[channel.channel_type] ?? channel.channel_type}</Tag>
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
                            loading={actingChannelId === channel.id}
                          />
                        </Tooltip>
                      ) : (
                        <Tooltip title="启用">
                          <Button
                            type="text"
                            size="small"
                            icon={<PlayCircleOutlined />}
                            onClick={() => handleEnable(channel)}
                            loading={actingChannelId === channel.id}
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
                        okButtonProps={{ loading: deletingChannelId === channel.id }}
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
                    <div className="flex items-center gap-2">
                      <Text type="secondary" className="w-20 shrink-0">回复方式</Text>
                      <Tag color={channel.enable_streaming ? 'cyan' : 'default'}>
                        {channel.enable_streaming ? '流式' : '一次性'}
                      </Tag>
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
          <Form form={form} layout="vertical" className="mt-4" autoComplete="off">
            <Form.Item
              name="name"
              label="渠道名称"
              rules={[{ required: true, message: '请输入渠道名称' }]}
            >
              <Input placeholder="例如：飞书客服机器人" />
            </Form.Item>

            <div className="flex gap-4">
              <Form.Item name="channel_type" label="渠道类型" className="w-40">
                <Select
                  disabled={!!editingChannel}
                  onChange={(v) => {
                    setChannelType(v as ChannelType);
                    if (v === 'wecom') {
                      form.setFieldValue('mode', 'webhook');
                      setMode('webhook');
                    } else {
                      // 切回 feishu 等其它类型时重置为默认 websocket，避免上次选择
                      // wecom 强置的 webhook 残留，导致飞书静默以回调模式创建。
                      form.setFieldValue('mode', 'websocket');
                      setMode('websocket');
                    }
                  }}
                >
                  <Option value="feishu">飞书</Option>
                  <Option value="wecom">企微（企业内部应用）</Option>
                  <Option value="wecom_bot">企微机器人（长连接）</Option>
                  <Option value="dingtalk" disabled>钉钉（待支持）</Option>
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

            <Form.Item
              name="enable_streaming"
              label="流式回复"
              valuePropName="checked"
              tooltip="关闭后等待 Agent 完整生成，再一次性发送最终回复"
            >
              <Switch checkedChildren="开启" unCheckedChildren="关闭" />
            </Form.Item>

            <Form.Item noStyle shouldUpdate={(prev, next) => prev.enable_streaming !== next.enable_streaming}>
              {({ getFieldValue }) => getFieldValue('enable_streaming') && !isWecom && !isWecomBot ? (
                <Alert
                  type="info"
                  showIcon
                  className="mb-4"
                  message="飞书原生流式回复需要额外权限"
                  description={
                    <span>
                      请在飞书开放平台开通并发布 <Text code>cardkit:card:write</Text>
                      （创建与更新卡片）权限；未开通时将自动降级为一次性回复。
                    </span>
                  }
                />
              ) : null}
            </Form.Item>

            <div className="flex gap-4">
              <Form.Item
                name="app_id"
                label={fields.appId.label}
                rules={[{ required: true, message: `请输入${fields.appId.label}` }]}
                className="flex-1"
              >
                <Input placeholder={fields.appId.placeholder} />
              </Form.Item>

              {editingChannel && !secretsBeingEdited.has('app_secret') ? (
                <Form.Item label={fields.appSecret.label} className="flex-1">
                  <Space>
                    <Text type="secondary">已保存，保持原值</Text>
                    <Button type="link" className="px-0" onClick={() => startEditingSecret('app_secret')}>
                      修改
                    </Button>
                  </Space>
                </Form.Item>
              ) : (
                <Form.Item
                  name="app_secret"
                  label={fields.appSecret.label}
                  rules={[{ required: true, message: `请输入${fields.appSecret.label}` }]}
                  className="flex-1"
                >
                  <Input.Password {...SECRET_INPUT_PROPS} placeholder={fields.appSecret.placeholder} />
                </Form.Item>
              )}
            </div>

            {isWecom && (
              <Form.Item
                name="agentid"
                label="应用 AgentID"
                rules={[
                  {
                    // 新建必须填；编辑 API 直建、无 agentid 的渠道时豁免，允许改名/换 Agent。
                    required: !(editingChannel && !editingChannel.extra_config?.agentid),
                    message: '请输入数字应用 AgentID',
                  },
                ]}
                tooltip="企业微信自建应用的 AgentId（数字），见「应用管理 → 应用详情」"
              >
                <Input type="number" placeholder="例如 1000002" />
              </Form.Item>
            )}

            {isWecomBot ? (
              <Alert
                type="info"
                showIcon
                className="mb-4"
                message="企微机器人仅支持 WebSocket 长连接模式"
                description="智能机器人通过 wss://openws.work.weixin.qq.com 长连接收发消息，无需公网回调地址。启用后需在企微后台将 Bot 状态设为「已上线」。"
              />
            ) : isWecom ? (
              <Alert
                type="info"
                showIcon
                className="mb-4"
                message="企微（企业内部应用）仅支持 Webhook 回调连接模式"
                description="请在企微管理后台配置应用回调 URL 并开启接收消息，启用后按下方回调地址配置。"
              />
            ) : (
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
            )}

            {(mode === 'webhook' || isWecom) && !isWecomBot && (
              <div className="flex gap-4">
                {editingChannel && !secretsBeingEdited.has('encrypt_key') ? (
                  <Form.Item label={fields.encryptKey.label} className="flex-1">
                    <Space>
                      <Text type="secondary">保持原值</Text>
                      <Button type="link" className="px-0" onClick={() => startEditingSecret('encrypt_key')}>
                        修改
                      </Button>
                    </Space>
                  </Form.Item>
                ) : (
                  <Form.Item
                    name="encrypt_key"
                    label={fields.encryptKey.label}
                    className="flex-1"
                  >
                    <Input.Password {...SECRET_INPUT_PROPS} placeholder={fields.encryptKey.placeholder} />
                  </Form.Item>
                )}
                {editingChannel && !secretsBeingEdited.has('verification_token') ? (
                  <Form.Item label={fields.verificationToken.label} className="flex-1">
                    <Space>
                      <Text type="secondary">保持原值</Text>
                      <Button type="link" className="px-0" onClick={() => startEditingSecret('verification_token')}>
                        修改
                      </Button>
                    </Space>
                  </Form.Item>
                ) : (
                  <Form.Item
                    name="verification_token"
                    label={fields.verificationToken.label}
                    rules={isWecom ? [{ required: true, message: `请输入${fields.verificationToken.label}` }] : undefined}
                    className="flex-1"
                  >
                    <Input.Password {...SECRET_INPUT_PROPS} placeholder={fields.verificationToken.placeholder} />
                  </Form.Item>
                )}
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
              {webhookChannelType === 'wecom'
                ? '请将以下地址填入企业微信管理后台「应用管理 → 应用 → 接收消息 → 设置API接收」：'
                : '请将以下地址填入飞书开放平台「事件订阅 → 请求地址」中：'}
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
                title: '外部用户 ID',
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
