import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  RobotOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  EllipsisOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import {
  Form,
  Input,
  Select,
  Button,
  Typography,
  Spin,
  App,
  Modal,
  Tag,
  Card,
  Empty,
  Dropdown,
} from 'antd';
import { agentsApi, toolsApi } from '@/lib/api';
import type { Agent, ToolInfo } from '@/lib/types';
import { getAgentIcon, AGENT_ICON_OPTIONS, DEFAULT_ICON } from '@/lib/agent-icons';

const { Text } = Typography;
const { TextArea } = Input;
const { Option } = Select;

export default function AgentsPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();

  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [allTools, setAllTools] = useState<ToolInfo[]>([]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [a, t] = await Promise.all([
        agentsApi.adminList(),
        toolsApi.list(),
      ]);
      setAgents(a);
      setAllTools(t);
    } catch (err: any) {
      message.error(`加载智能体失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ---- Agent CRUD ----
  const [modalOpen, setModalOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [form] = Form.useForm();

  const openModal = (agent?: Agent) => {
    if (agent) {
      setEditingAgent(agent);
      form.setFieldsValue({
        name: agent.name,
        description: agent.description || '',
        icon: agent.icon,
      });
    } else {
      setEditingAgent(null);
      form.resetFields();
      form.setFieldsValue({ icon: 'robot' });
    }
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const payload = {
        name: values.name,
        description: values.description || undefined,
        icon: values.icon,
      };
      if (editingAgent) {
        await agentsApi.adminUpdate(editingAgent.id, payload);
        message.success('智能体已更新');
        setModalOpen(false);
        fetchData();
      } else {
        const created = await agentsApi.adminCreate(payload);
        message.success('智能体已创建，正在跳转到配置页面...');
        setModalOpen(false);
        fetchData();
        navigate(`/agents/${created.id}/chat`);
      }
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await agentsApi.adminDelete(id);
      message.success('智能体已删除');
      fetchData();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  };

  // ---- Loading state ----
  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  const displayAgents = agents;

  // Build set of built-in tool names to exclude MCP tools from count
  const builtinToolNames = new Set(
    allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
  );
  const getBuiltinToolCount = (agent: Agent) =>
    (agent.enabled_tools || []).filter((t) => builtinToolNames.has(t)).length;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <RobotOutlined className="text-primary" />
            智能体
          </h1>
          <Text type="secondary">
            选择一个智能体开始对话，或创建新的智能体
          </Text>
        </div>

        {displayAgents.length === 0 ? (
          <div className="flex flex-col items-center gap-4">
            <Empty description="还没有智能体" />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
              添加智能体
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {displayAgents.map((agent) => (
              <Card
                key={agent.id}
                className={`transition-all hover:shadow-lg relative ${
                  !agent.is_active ? 'opacity-60' : ''
                }`}
              >
                {/* Status badge */}
                <div className="absolute top-3 right-3">
                  {agent.is_active ? (
                    <Tag color="green" className="text-xs">启用</Tag>
                  ) : (
                    <Tag color="red" className="text-xs">禁用</Tag>
                  )}
                </div>

                {/* Card content — clickable area */}
                <div
                  className="flex flex-col items-center text-center gap-3 py-2 cursor-pointer"
                  onClick={() => navigate(`/agents/${agent.id}/chat`)}
                >
                  <div className="text-4xl">
                    {getAgentIcon(agent.icon || DEFAULT_ICON)}
                  </div>
                  <div>
                    <div className="font-semibold text-base">{agent.name}</div>
                    {agent.description && (
                      <Text type="secondary" className="text-sm line-clamp-2">
                        {agent.description}
                      </Text>
                    )}
                  </div>
                  <div className="flex flex-wrap justify-center gap-1">
                    {agent.model_name && (
                      <Tag className="text-xs">{agent.model_name}</Tag>
                    )}
                    {(() => {
                      const builtinCount = getBuiltinToolCount(agent);
                      return builtinCount > 0 && (
                        <Tag color="blue" className="text-xs">
                          {builtinCount} 个工具
                        </Tag>
                      );
                    })()}
                    {agent.mcp_server_ids?.length > 0 && (
                      <Tag color="geekblue" className="text-xs">
                        {agent.mcp_server_ids.length} 个 MCP
                      </Tag>
                    )}
                    {agent.children_count > 0 && (
                      <Tag color="cyan" className="text-xs">
                        {agent.children_count} 个子智能体
                      </Tag>
                    )}
                  </div>
                </div>

                {/* Action menu (3-dot dropdown) */}
                <div className="absolute bottom-2 right-2">
                    <Dropdown
                      menu={{
                        items: [
                          {
                            key: 'edit',
                            label: '编辑',
                            icon: <EditOutlined />,
                            onClick: () => openModal(agent),
                          },
                          {
                            key: 'api',
                            label: 'API 文档',
                            icon: <ApiOutlined />,
                            onClick: () => navigate(`/agents/${agent.id}/api`),
                          },
                          { type: 'divider' },
                          {
                            key: 'delete',
                            label: '删除',
                            icon: <DeleteOutlined />,
                            danger: true,
                            onClick: () => {
                              Modal.confirm({
                                title: '确定删除该智能体？',
                                content: `即将删除智能体「${agent.name}」，此操作不可撤销。`,
                                okText: '删除',
                                okType: 'danger',
                                cancelText: '取消',
                                onOk: () => handleDelete(agent.id),
                              });
                            },
                          },
                        ],
                      }}
                      trigger={['click']}
                      placement="bottomRight"
                    >
                      <Button
                        type="text"
                        size="small"
                        icon={<EllipsisOutlined />}
                        onClick={(e) => e.stopPropagation()}
                        className="opacity-60 hover:opacity-100 transition-opacity"
                      />
                  </Dropdown>
                </div>
              </Card>
            ))}

            {/* Add agent card */}
            <Card
              hoverable
              className="cursor-pointer transition-all hover:shadow-lg border-dashed"
              onClick={() => openModal()}
            >
              <div className="flex flex-col items-center text-center gap-3 py-2">
                <div className="text-4xl text-muted-foreground">
                  <PlusOutlined />
                </div>
                <div>
                  <div className="font-semibold text-base text-muted-foreground">
                    添加智能体
                  </div>
                  <Text type="secondary" className="text-sm">
                    创建新的智能体
                  </Text>
                </div>
              </div>
            </Card>
          </div>
        )}
      </div>

      {/* Agent CRUD Modal */}
      <Modal
          title={editingAgent ? '编辑智能体' : '添加智能体'}
          open={modalOpen}
          onOk={handleSave}
          onCancel={() => setModalOpen(false)}
          width={480}
          destroyOnHidden
          okText={editingAgent ? '保存' : '创建并配置'}
        >
          <Form
            form={form}
            layout="vertical"
            initialValues={{ icon: 'robot' }}
          >
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, message: '请输入智能体名称' }]}
            >
              <Input placeholder="如：编程助手、文档专家" />
            </Form.Item>

            <Form.Item name="description" label="描述">
              <TextArea rows={3} placeholder="简短描述智能体的能力" />
            </Form.Item>

            <Form.Item name="icon" label="图标">
              <Select>
                {AGENT_ICON_OPTIONS.map(opt => (
                  <Option key={opt.value} value={opt.value}>
                    <span className="inline-flex items-center gap-2">
                      <opt.icon size={16} />
                      {opt.label}
                    </span>
                  </Option>
                ))}
              </Select>
            </Form.Item>
          </Form>
          <div className="text-xs text-muted-foreground mt-2 -mb-2">
            {editingAgent
              ? '提示词、模型、工具、技能等配置可在智能体详情页面中修改。'
              : '创建后可在智能体页面中配置提示词、模型、工具、技能等详细内容。'}
          </div>
        </Modal>
    </div>
  );
}
