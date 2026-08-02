import { useState, useEffect, useCallback } from 'react';
import {
  ApiOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  StarOutlined,
  StarFilled,
  CloudDownloadOutlined,
  CheckOutlined,
  KeyOutlined,
  DownOutlined,
  UpOutlined,
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
  Checkbox,
  Empty,
  Badge,
  Tooltip,
} from 'antd';
import { adminApi } from '@/lib/api';
import type { LLMProvider, LLMModel } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { Navigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

const { Text } = Typography;
const { Option } = Select;

// Map provider types to display info
const PROVIDER_META: Record<string, { label: string; color: string; icon: string }> = {
  openai: { label: 'OpenAI 兼容', color: 'green', icon: '🔗' },
  anthropic: { label: 'Anthropic', color: 'orange', icon: '🧠' },
};

// Number of models visible by default before collapsing
const VISIBLE_MODEL_COUNT = 5;

export default function ModelManagementPage() {
  const { message } = App.useApp();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';

  // Track which providers have their model list expanded
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(new Set());

  const toggleProviderExpanded = (providerId: string) => {
    setExpandedProviders((prev) => {
      const next = new Set(prev);
      if (next.has(providerId)) {
        next.delete(providerId);
      } else {
        next.add(providerId);
      }
      return next;
    });
  };

  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [models, setModels] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [p, m] = await Promise.all([
        adminApi.listProviders(),
        adminApi.listModels(),
      ]);
      setProviders(p);
      setModels(m);
    } catch (err: any) {
      message.error(`加载数据失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Helper: get models for a provider
  const getProviderModels = (providerId: string) =>
    models.filter((m) => m.provider_id === providerId);

  // ---- Provider CRUD ----

  const [providerModalOpen, setProviderModalOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<LLMProvider | null>(null);
  const [providerForm] = Form.useForm();

  const openProviderModal = (provider?: LLMProvider) => {
    if (provider) {
      setEditingProvider(provider);
      providerForm.setFieldsValue({
        name: provider.name,
        provider_type: provider.provider_type,
        base_url: provider.base_url || '',
        api_key: '',
        is_active: provider.is_active,
      });
    } else {
      setEditingProvider(null);
      providerForm.resetFields();
      providerForm.setFieldsValue({ provider_type: 'openai', is_active: true });
    }
    setProviderModalOpen(true);
  };

  const handleProviderSave = async () => {
    try {
      const values = await providerForm.validateFields();
      const payload = {
        name: values.name,
        provider_type: values.provider_type,
        base_url: values.base_url || undefined,
        api_key: values.api_key || undefined,
        is_active: values.is_active ?? true,
      };
      if (editingProvider) {
        await adminApi.updateProvider(editingProvider.id, payload);
        message.success('供应商已更新');
      } else {
        await adminApi.createProvider(payload);
        message.success('供应商已创建');
      }
      setProviderModalOpen(false);
      fetchData();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '操作失败');
    }
  };

  const handleDeleteProvider = async (id: string) => {
    try {
      await adminApi.deleteProvider(id);
      message.success('供应商已删除');
      fetchData();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  };

  // ---- Model CRUD ----

  const [modelModalOpen, setModelModalOpen] = useState(false);
  const [editingModel, setEditingModel] = useState<LLMModel | null>(null);
  const [modelForm] = Form.useForm();

  const openModelModal = (providerId?: string, model?: LLMModel) => {
    if (model) {
      setEditingModel(model);
      modelForm.setFieldsValue({
        provider_id: model.provider_id,
        name: model.name,
        model_name: model.model_name,
        is_multimodal: model.is_multimodal,
        is_active: model.is_active,
      });
    } else {
      setEditingModel(null);
      modelForm.resetFields();
      modelForm.setFieldsValue({ is_active: true, is_multimodal: false, provider_id: providerId });
    }
    setModelModalOpen(true);
  };

  const handleModelSave = async () => {
    try {
      const values = await modelForm.validateFields();
      const payload = {
        provider_id: values.provider_id,
        name: values.name,
        model_name: values.model_name,
        is_multimodal: values.is_multimodal ?? false,
        is_active: values.is_active ?? true,
      };
      if (editingModel) {
        await adminApi.updateModel(editingModel.id, payload);
        message.success('模型已更新');
      } else {
        await adminApi.createModel(payload);
        message.success('模型已创建');
      }
      setModelModalOpen(false);
      fetchData();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '操作失败');
    }
  };

  const handleDeleteModel = async (id: string) => {
    try {
      await adminApi.deleteModel(id);
      message.success('模型已删除');
      fetchData();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await adminApi.setDefaultModel(id);
      message.success('已设为默认模型');
      fetchData();
    } catch (err: any) {
      message.error(err.message || '操作失败');
    }
  };

  const handleToggleModelActive = async (id: string, active: boolean) => {
    try {
      await adminApi.updateModel(id, { is_active: active });
      fetchData();
    } catch (err: any) {
      message.error(err.message || '操作失败');
    }
  };

  // ---- Fetch remote models ----

  const [fetchModalProvider, setFetchModalProvider] = useState<LLMProvider | null>(null);
  const [fetchLoading, setFetchLoading] = useState(false);
  const [remoteModels, setRemoteModels] = useState<string[]>([]);
  const [selectedRemoteModels, setSelectedRemoteModels] = useState<string[]>([]);
  const [importLoading, setImportLoading] = useState(false);

  const handleFetchModels = async (provider: LLMProvider) => {
    setFetchModalProvider(provider);
    setFetchLoading(true);
    setRemoteModels([]);
    setSelectedRemoteModels([]);
    try {
      const result = await adminApi.fetchRemoteModels(provider.id);
      // Filter out already imported models
      const existingNames = new Set(
        getProviderModels(provider.id).map((m) => m.model_name),
      );
      const newModels = result.models.filter((m) => !existingNames.has(m));
      setRemoteModels(newModels);
      if (newModels.length === 0) {
        message.info('没有发现新的模型，所有模型已导入。');
      }
    } catch (err: any) {
      message.error(`拉取模型失败：${err.message}`);
    } finally {
      setFetchLoading(false);
    }
  };

  const handleImportModels = async () => {
    if (!fetchModalProvider || selectedRemoteModels.length === 0) return;
    setImportLoading(true);
    try {
      const created = await adminApi.batchCreateModels(
        fetchModalProvider.id,
        selectedRemoteModels,
      );
      message.success(`已导入 ${created.length} 个模型`);
      setFetchModalProvider(null);
      fetchData();
    } catch (err: any) {
      message.error(`导入失败：${err.message}`);
    } finally {
      setImportLoading(false);
    }
  };

  // Redirect non-admin users
  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

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
              模型管理
            </h1>
            <Text type="secondary">
              管理 LLM 供应商和模型。支持从供应商 API 自动拉取可用模型。
            </Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openProviderModal()}>
            添加供应商
          </Button>
        </div>

        {providers.length === 0 ? (
          <Card>
            <Empty
              description="暂无供应商，请先添加 LLM 供应商。"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            >
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openProviderModal()}>
                添加供应商
              </Button>
            </Empty>
          </Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {providers.map((provider) => {
              const providerModels = getProviderModels(provider.id);
              const meta = PROVIDER_META[provider.provider_type] || PROVIDER_META.openai;

              return (
                <Card
                  key={provider.id}
                  className={cn(
                    'transition-all',
                    !provider.is_active && 'opacity-60',
                  )}
                  title={
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{meta.icon}</span>
                      <span className="font-semibold">{provider.name}</span>
                      <Tag color={meta.color}>{meta.label}</Tag>
                      {!provider.is_active && <Tag color="red">已禁用</Tag>}
                    </div>
                  }
                  extra={
                    <Space size={4}>
                      <Tooltip title="编辑供应商">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          onClick={() => openProviderModal(provider)}
                        />
                      </Tooltip>
                      <Popconfirm
                        title="删除供应商？"
                        description="该供应商下的所有模型将一并删除。"
                        onConfirm={() => handleDeleteProvider(provider.id)}
                        okText="删除"
                        okType="danger"
                        cancelText="取消"
                      >
                        <Tooltip title="删除供应商">
                          <Button
                            type="text"
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                          />
                        </Tooltip>
                      </Popconfirm>
                    </Space>
                  }
                >
                  {/* Provider info */}
                  <div className="mb-3 space-y-1 text-sm">
                    {provider.base_url && (
                      <div className="flex items-center gap-2 text-muted-foreground">
                        <ApiOutlined className="text-xs" />
                        <span className="truncate">{provider.base_url}</span>
                      </div>
                    )}
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <KeyOutlined className="text-xs" />
                      {provider.has_api_key ? (
                        <Tag color="green" className="!text-xs">已配置密钥</Tag>
                      ) : (
                        <Tag color="red" className="!text-xs">未配置密钥</Tag>
                      )}
                    </div>
                  </div>

                  {/* Model list */}
                  <div className="border-t border-border pt-3">
                    <div className="flex items-center justify-between mb-2">
                      <Text strong className="text-sm">
                        模型列表
                        <Badge
                          count={providerModels.length}
                          style={{ backgroundColor: 'hsl(var(--primary))' }}
                          className="ml-2"
                        />
                      </Text>
                      <Space size={4}>
                        <Tooltip title="从供应商 API 拉取可用模型">
                          <Button
                            type="text"
                            size="small"
                            icon={<CloudDownloadOutlined />}
                            onClick={() => handleFetchModels(provider)}
                            disabled={!provider.has_api_key || !provider.base_url}
                          >
                            拉取
                          </Button>
                        </Tooltip>
                        <Tooltip title="手动添加模型">
                          <Button
                            type="text"
                            size="small"
                            icon={<PlusOutlined />}
                            onClick={() => openModelModal(provider.id)}
                          >
                            添加
                          </Button>
                        </Tooltip>
                      </Space>
                    </div>

                    {providerModels.length === 0 ? (
                      <div className="text-center py-4 text-muted-foreground text-sm">
                        暂无模型，点击「拉取」或「添加」。
                      </div>
                    ) : (
                      <div className="space-y-1.5">
                        {(() => {
                          const isExpanded = expandedProviders.has(provider.id);
                          const visibleModels = isExpanded
                            ? providerModels
                            : providerModels.slice(0, VISIBLE_MODEL_COUNT);
                          const hasMore = providerModels.length > VISIBLE_MODEL_COUNT;

                          return (
                            <>
                              {visibleModels.map((model) => (
                                <div
                                  key={model.id}
                                  className={cn(
                                    'flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition',
                                    model.is_default
                                      ? 'bg-primary/10 border border-primary/20'
                                      : 'bg-muted/50 hover:bg-muted',
                                    !model.is_active && 'opacity-50',
                                  )}
                                >
                                  {/* Default star */}
                                  <Tooltip title={model.is_default ? '默认模型' : '设为默认'}>
                                    <button
                                      onClick={() => !model.is_default && handleSetDefault(model.id)}
                                      className="flex-shrink-0"
                                    >
                                      {model.is_default ? (
                                        <StarFilled style={{ color: '#fadb14', fontSize: 16 }} />
                                      ) : (
                                        <StarOutlined style={{ color: '#999', fontSize: 16 }} />
                                      )}
                                    </button>
                                  </Tooltip>

                                  {/* Model info */}
                                  <div className="flex-1 min-w-0">
                                    <div className="font-medium truncate flex items-center gap-1.5">
                                      <span className="truncate">{model.name}</span>
                                      {model.is_multimodal && (
                                        <Tag color="purple" className="!text-xs !leading-4 !m-0">多模态</Tag>
                                      )}
                                    </div>
                                    {model.name !== model.model_name && (
                                      <div className="text-xs text-muted-foreground truncate">
                                        {model.model_name}
                                      </div>
                                    )}
                                  </div>

                                  {/* Active toggle */}
                                  <Tooltip title={model.is_active ? '禁用' : '启用'}>
                                    <Switch
                                      size="small"
                                      checked={model.is_active}
                                      onChange={(checked) =>
                                        handleToggleModelActive(model.id, checked)
                                      }
                                    />
                                  </Tooltip>

                                  {/* Actions */}
                                  <Button
                                    type="text"
                                    size="small"
                                    icon={<EditOutlined />}
                                    onClick={() => openModelModal(undefined, model)}
                                  />
                                  <Popconfirm
                                    title="删除该模型？"
                                    onConfirm={() => handleDeleteModel(model.id)}
                                    okText="删除"
                                    okType="danger"
                                    cancelText="取消"
                                  >
                                    <Button
                                      type="text"
                                      size="small"
                                      danger
                                      icon={<DeleteOutlined />}
                                    />
                                  </Popconfirm>
                                </div>
                              ))}

                              {/* Expand/Collapse button */}
                              {hasMore && (
                                <button
                                  onClick={() => toggleProviderExpanded(provider.id)}
                                  className="w-full flex items-center justify-center gap-1.5 py-2 text-sm text-primary hover:bg-primary/5 rounded-lg transition"
                                >
                                  {isExpanded ? (
                                    <>
                                      <UpOutlined className="text-xs" />
                                      收起
                                    </>
                                  ) : (
                                    <>
                                      <DownOutlined className="text-xs" />
                                      展开更多 ({providerModels.length - VISIBLE_MODEL_COUNT} 个模型)
                                    </>
                                  )}
                                </button>
                              )}
                            </>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        {/* Provider Modal */}
        <Modal
          title={editingProvider ? '编辑供应商' : '添加供应商'}
          open={providerModalOpen}
          onOk={handleProviderSave}
          onCancel={() => setProviderModalOpen(false)}
          destroyOnHidden
        >
          <Form form={providerForm} layout="vertical" initialValues={{ provider_type: 'openai', is_active: true }}>
            <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入供应商名称' }]}>
              <Input placeholder="如：OpenAI、DeepSeek、Anthropic" />
            </Form.Item>
            <Form.Item name="provider_type" label="类型" rules={[{ required: true }]}>
              <Select>
                <Option value="openai">OpenAI 兼容</Option>
                <Option value="anthropic">Anthropic</Option>
              </Select>
            </Form.Item>
            <Form.Item name="base_url" label="API 地址" extra="OpenAI 兼容接口的基础地址，如 https://api.deepseek.com/v1。拉取模型和调用都使用此地址。">
              <Input placeholder="https://api.openai.com/v1" />
            </Form.Item>
            <Form.Item name="api_key" label="API 密钥" extra={editingProvider ? '留空表示不修改当前密钥。' : '用于拉取模型列表和实际调用。'}>
              <Input.Password placeholder="sk-..." />
            </Form.Item>
            <Form.Item name="is_active" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Form>
        </Modal>

        {/* Model Modal */}
        <Modal
          title={editingModel ? '编辑模型' : '添加模型'}
          open={modelModalOpen}
          onOk={handleModelSave}
          onCancel={() => setModelModalOpen(false)}
          destroyOnHidden
        >
          <Form form={modelForm} layout="vertical" initialValues={{ is_active: true, is_multimodal: false }}>
            <Form.Item name="provider_id" label="供应商" rules={[{ required: true, message: '请选择供应商' }]}>
              <Select placeholder="选择供应商">
                {providers.filter((p) => p.is_active).map((p) => (
                  <Option key={p.id} value={p.id}>{p.name}</Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item name="name" label="显示名称" rules={[{ required: true, message: '请输入显示名称' }]}>
              <Input placeholder="如：GPT-4o、Claude Sonnet 4" />
            </Form.Item>
            <Form.Item
              name="model_name"
              label="模型标识"
              rules={[{ required: true, message: '请输入模型标识' }]}
              extra="API 调用时使用的实际模型名称，如 gpt-4o、claude-sonnet-4-20250514。"
            >
              <Input placeholder="gpt-4o" />
            </Form.Item>
            <Form.Item
              name="is_multimodal"
              label="多模态模型"
              valuePropName="checked"
              extra="开启后该模型支持图片输入；关闭时不会向其发送图片附件。"
            >
              <Switch />
            </Form.Item>
            <Form.Item name="is_active" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Form>
        </Modal>

        {/* Fetch Remote Models Modal */}
        <Modal
          title={
            <span>
              <CloudDownloadOutlined className="mr-2" />
              从 {fetchModalProvider?.name} 拉取模型
            </span>
          }
          open={!!fetchModalProvider}
          onOk={handleImportModels}
          onCancel={() => setFetchModalProvider(null)}
          okText={`导入 ${selectedRemoteModels.length} 个模型`}
          okButtonProps={{ disabled: selectedRemoteModels.length === 0, loading: importLoading }}
          cancelText="取消"
          width={520}
        >
          {fetchLoading ? (
            <div className="flex items-center justify-center py-12">
              <Spin tip="正在从供应商 API 拉取模型列表..." />
            </div>
          ) : remoteModels.length === 0 ? (
            <Empty
              description="没有发现新的可导入模型。"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            <div>
              <div className="mb-3 flex items-center justify-between">
                <Text type="secondary" className="text-sm">
                  发现 {remoteModels.length} 个新模型，请选择要导入的：
                </Text>
                <Space>
                  <Button
                    size="small"
                    onClick={() => setSelectedRemoteModels(remoteModels)}
                  >
                    全选
                  </Button>
                  <Button
                    size="small"
                    onClick={() => setSelectedRemoteModels([])}
                  >
                    清空
                  </Button>
                </Space>
              </div>
              <div className="max-h-80 overflow-y-auto border border-border rounded-lg p-2 space-y-1">
                {remoteModels.map((modelName) => {
                  const isSelected = selectedRemoteModels.includes(modelName);
                  return (
                    <div
                      key={modelName}
                      className={cn(
                        'flex items-center gap-2 rounded-md px-3 py-2 cursor-pointer transition text-sm',
                        isSelected
                          ? 'bg-primary/10 border border-primary/30'
                          : 'hover:bg-muted/50 border border-transparent',
                      )}
                      onClick={() => {
                        setSelectedRemoteModels((prev) =>
                          isSelected
                            ? prev.filter((m) => m !== modelName)
                            : [...prev, modelName],
                        );
                      }}
                    >
                      <Checkbox checked={isSelected} />
                      <span className="font-mono">{modelName}</span>
                      {isSelected && (
                        <CheckOutlined className="ml-auto text-primary text-xs" />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </Modal>
      </div>
    </div>
  );
}
