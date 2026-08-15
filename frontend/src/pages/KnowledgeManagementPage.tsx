import { useState, useEffect, useCallback } from 'react';
import {
  DatabaseOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SaveOutlined,
  SearchOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import {
  Form,
  Input,
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
  Empty,
  Tooltip,
  Slider,
  InputNumber,
  Alert,
  Divider,
  Select,
} from 'antd';
import { knowledgeApi, ragflowSettingsApi } from '@/lib/api';
import type { KnowledgeBase, RagflowSettings, RetrievalRecord } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { Navigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

const { Text } = Typography;
const { TextArea } = Input;

export default function KnowledgeManagementPage() {
  const { message } = App.useApp();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';

  // ---- List state ----
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);

  // ---- RAGFlow settings ----
  const [settings, setSettings] = useState<RagflowSettings>({ base_url: '', has_api_key: false });
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [settingsForm] = Form.useForm();
  const [settingsSaving, setSettingsSaving] = useState(false);

  // ---- Edit/Create modal ----
  const [modalOpen, setModalOpen] = useState(false);
  const [editingKb, setEditingKb] = useState<KnowledgeBase | null>(null);
  const [form] = Form.useForm();

  // ---- Retrieval test modal ----
  const [retrievalModalOpen, setRetrievalModalOpen] = useState(false);
  const [retrievalKb, setRetrievalKb] = useState<KnowledgeBase | null>(null);
  const [retrievalForm] = Form.useForm();
  const [retrievalLoading, setRetrievalLoading] = useState(false);
  const [retrievalRecords, setRetrievalRecords] = useState<RetrievalRecord[]>([]);
  const [retrievalError, setRetrievalError] = useState<string | null>(null);
  const [retrievalTimeMs, setRetrievalTimeMs] = useState<number>(0);

  // ---- Admin guard ----
  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  // ---- Data fetching ----
  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      setKnowledgeBases(await knowledgeApi.list());
    } catch (err: any) {
      message.error(`加载数据失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  const fetchSettings = useCallback(async () => {
    setSettingsLoading(true);
    try {
      const ragSettings = await ragflowSettingsApi.get();
      setSettings(ragSettings);
      settingsForm.setFieldsValue({
        base_url: ragSettings.base_url,
        api_key: '',
      });
    } catch (err: any) {
      message.error(`加载数据失败：${err.message}`);
    } finally {
      setSettingsLoading(false);
    }
  }, [message, settingsForm]);

  useEffect(() => { fetchData(); fetchSettings(); }, [fetchData, fetchSettings]);

  // ---- Save RAGFlow settings ----
  const handleSaveSettings = async () => {
    try {
      const values = await settingsForm.validateFields();
      setSettingsSaving(true);
      const payload: { base_url?: string; api_key?: string } = {};
      if (values.base_url !== undefined) payload.base_url = values.base_url;
      if (values.api_key) payload.api_key = values.api_key;
      const updated = await ragflowSettingsApi.update(payload);
      setSettings(updated);
      settingsForm.setFieldsValue({ api_key: '' });
      message.success('RAGFlow 配置已保存');
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '保存失败');
    } finally {
      setSettingsSaving(false);
    }
  };

  // ---- Modal open/close ----
  const openModal = (kb?: KnowledgeBase) => {
    if (kb) {
      setEditingKb(kb);
      form.setFieldsValue({
        name: kb.name,
        dataset_id: kb.dataset_id,
        description: kb.description || '',
        is_active: kb.is_active,
        visibility: kb.visibility,
      });
    } else {
      setEditingKb(null);
      form.resetFields();
      form.setFieldsValue({ is_active: true, visibility: 'tenant' });
    }
    setModalOpen(true);
  };

  // ---- Save (create or update) ----
  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const payload = {
        name: values.name,
        dataset_id: values.dataset_id,
        description: values.description || undefined,
        is_active: values.is_active,
        visibility: values.visibility,
      };

      if (editingKb) {
        await knowledgeApi.update(editingKb.id, payload);
        message.success('知识库已更新');
      } else {
        await knowledgeApi.create(payload);
        message.success('知识库已创建');
      }
      setModalOpen(false);
      fetchData();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '操作失败');
    }
  };

  // ---- Delete ----
  const handleDelete = async (id: string) => {
    try {
      await knowledgeApi.delete(id);
      message.success('知识库已删除');
      fetchData();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    }
  };

  // ---- Test connection ----
  // ---- Retrieval test ----
  const openRetrievalModal = (kb: KnowledgeBase) => {
    setRetrievalKb(kb);
    setRetrievalRecords([]);
    setRetrievalError(null);
    setRetrievalTimeMs(0);
    retrievalForm.resetFields();
    retrievalForm.setFieldsValue({ top_k: 5, score_threshold: 0.0 });
    setRetrievalModalOpen(true);
  };

  const handleRetrieval = async () => {
    if (!retrievalKb) return;
    try {
      const values = await retrievalForm.validateFields();
      setRetrievalLoading(true);
      setRetrievalError(null);
      setRetrievalRecords([]);

      const result = await knowledgeApi.retrieval(retrievalKb.id, {
        query: values.query,
        top_k: values.top_k,
        score_threshold: values.score_threshold,
      });

      if (result.success) {
        setRetrievalRecords(result.records);
        setRetrievalTimeMs(result.query_time_ms || 0);
        if (result.records.length === 0) {
          setRetrievalError('未找到相关结果');
        }
      } else {
        setRetrievalError(result.message || '检索失败');
      }
    } catch (err: any) {
      if (err?.errorFields) return;
      setRetrievalError(err.message || '检索失败');
    } finally {
      setRetrievalLoading(false);
    }
  };

  // ---- Rendering ----
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <DatabaseOutlined className="text-primary" />
              知识库管理
            </h1>
            <Text type="secondary">
              管理 RAGFlow 知识库，为 Agent 提供检索增强生成（RAG）能力
            </Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
            添加知识库
          </Button>
        </div>

        {/* RAGFlow Global Settings */}
        <Card
          title={
            <div className="flex items-center gap-2">
              <ApiOutlined />
              <span>RAGFlow 全局配置</span>
              {settings.has_api_key ? (
                <Tag color="success" icon={<CheckCircleOutlined />}>已配置</Tag>
              ) : (
                <Tag color="warning" icon={<CloseCircleOutlined />}>未配置 API Key</Tag>
              )}
            </div>
          }
          className="mb-6"
        >
          {settingsLoading ? (
            <div className="py-8 flex items-center justify-center">
              <Spin size="large" />
            </div>
          ) : (
            <Form form={settingsForm} layout="vertical" className="max-w-2xl">
              <Form.Item
                name="base_url"
                label="服务地址 (Base URL)"
                tooltip="RAGFlow 服务的访问地址，例如 http://localhost:9380"
              >
                <Input placeholder="http://localhost:9380" />
              </Form.Item>

              <Form.Item
                name="api_key"
                label={`API 密钥 ${settings.has_api_key ? '(留空保持不变)' : ''}`}
                tooltip="RAGFlow 的 API Key，用于认证访问"
              >
                <Input.Password
                  placeholder={settings.has_api_key ? '输入新密钥以更新（留空保持不变）' : '输入 RAGFlow API Key'}
                />
              </Form.Item>

              <Button
                type="primary"
                icon={<SaveOutlined />}
                onClick={handleSaveSettings}
                loading={settingsSaving}
              >
                保存配置
              </Button>
            </Form>
          )}
        </Card>

        {/* Knowledge Base List */}
        {loading ? (
          <div className="py-16 flex items-center justify-center">
            <Spin size="large" />
          </div>
        ) : knowledgeBases.length === 0 ? (
          <Card>
            <Empty description="暂无知识库，请添加以扩展 Agent 的知识检索能力。" image={Empty.PRESENTED_IMAGE_SIMPLE}>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
                添加知识库
              </Button>
            </Empty>
          </Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {knowledgeBases.map((kb) => (
              <Card
                key={kb.id}
                className={cn(
                  'transition-all',
                  !kb.is_active && 'opacity-60',
                )}
                title={
                  <div className="flex items-center gap-2">
                    <DatabaseOutlined />
                    <span className="font-semibold">{kb.name}</span>
                    <Tag color={kb.is_active ? 'success' : 'default'}>
                      {kb.is_active ? '已启用' : '已禁用'}
                    </Tag>
                    <Tag color={kb.visibility === 'private' ? 'gold' : 'cyan'}>
                      {kb.visibility === 'private' ? '只有我可见' : '租户内可见'}
                    </Tag>
                  </div>
                }
                extra={
                  <Space size={4}>
                    <Tooltip title="测试连接">
                      <Button
                        type="text"
                        size="small"
                        icon={<ApiOutlined />}
                        onClick={() => openRetrievalModal(kb)}
                      />
                    </Tooltip>
                    <Tooltip title="编辑">
                      <Button
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        disabled={!kb.can_edit}
                        onClick={() => openModal(kb)}
                      />
                    </Tooltip>
                    <Popconfirm
                      title="删除知识库？"
                      description="删除后将自动解除与所有 Agent 的绑定。"
                      onConfirm={() => handleDelete(kb.id)}
                      okText="删除"
                      okType="danger"
                      cancelText="取消"
                    >
                      <Tooltip title="删除">
                        <Button type="text" size="small" danger icon={<DeleteOutlined />} disabled={!kb.can_edit} />
                      </Tooltip>
                    </Popconfirm>
                  </Space>
                }
              >
                <div className="space-y-2 text-sm">
                  <div className="flex items-center gap-2">
                    <Text type="secondary" className="w-20 shrink-0">Dataset ID</Text>
                    <Text className="font-mono text-xs truncate" code>
                      {kb.dataset_id}
                    </Text>
                  </div>

                  {kb.description && (
                    <div className="flex items-start gap-2">
                      <Text type="secondary" className="w-20 shrink-0">描述</Text>
                      <Text className="text-xs">{kb.description}</Text>
                    </div>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}

        {/* Create/Edit Modal */}
        <Modal
          title={editingKb ? '编辑知识库' : '添加知识库'}
          open={modalOpen}
          onOk={handleSave}
          onCancel={() => setModalOpen(false)}
          destroyOnHidden
          width={600}
        >
          <Form
            form={form}
            layout="vertical"
            initialValues={{ is_active: true, visibility: 'tenant' }}
            className="mt-4"
          >
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, message: '请输入知识库名称' }]}
            >
              <Input placeholder="例如：产品手册" />
            </Form.Item>

            <Form.Item
              name="dataset_id"
              label="数据集 ID (Dataset ID)"
              rules={[{ required: true, message: '请输入 RAGFlow 数据集 ID' }]}
              tooltip="RAGFlow 中的数据集标识符"
            >
              <Input placeholder="例如：dataset-abc123" />
            </Form.Item>

            <Form.Item
              name="description"
              label="描述"
            >
              <TextArea rows={3} placeholder="知识库的内容描述（可选，帮助判断何时使用该知识库）" />
            </Form.Item>

            <Form.Item
              name="is_active"
              label="启用"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>

            <Form.Item
              name="visibility"
              label="可见范围"
              tooltip="只有创建者可以调整此设置"
            >
              <Select
                options={[
                  { value: 'tenant', label: '租户内可见' },
                  { value: 'private', label: '只有我可见' },
                ]}
              />
            </Form.Item>
          </Form>
        </Modal>

        {/* Retrieval Test Modal */}
        <Modal
          title={
            <div className="flex items-center gap-2">
              <SearchOutlined />
              <span>检索测试 - {retrievalKb?.name}</span>
            </div>
          }
          open={retrievalModalOpen}
          onCancel={() => setRetrievalModalOpen(false)}
          footer={null}
          width={800}
          destroyOnHidden
        >
          <Form
            form={retrievalForm}
            layout="vertical"
            initialValues={{ top_k: 5, score_threshold: 0.0 }}
            className="mt-4"
          >
            <Form.Item
              name="query"
              label="查询语句"
              rules={[{ required: true, message: '请输入查询内容' }]}
            >
              <Input.Search
                placeholder="输入要检索的内容..."
                enterButton="检索"
                loading={retrievalLoading}
                onSearch={handleRetrieval}
              />
            </Form.Item>

            <div className="grid grid-cols-2 gap-4 mb-4">
              <Form.Item name="top_k" label="返回条数 (Top K)">
                <InputNumber min={1} max={20} className="w-full" />
              </Form.Item>

              <Form.Item name="score_threshold" label="最低相似度">
                <Slider min={0} max={1} step={0.05} marks={{ 0: '0', 0.5: '0.5', 1: '1.0' }} />
              </Form.Item>
            </div>
          </Form>

          <Divider className="my-2" />

          {/* Results */}
          <div className="mt-4">
            {retrievalError && (
              <Alert
                message={retrievalError}
                type="warning"
                showIcon
                className="mb-4"
              />
            )}

            {retrievalRecords.length > 0 && (
              <>
                <div className="flex items-center justify-between mb-3">
                  <Text strong>
                    找到 {retrievalRecords.length} 条结果
                  </Text>
                  {retrievalTimeMs > 0 && (
                    <Text type="secondary" className="text-xs">
                      耗时 {retrievalTimeMs.toFixed(0)} ms
                    </Text>
                  )}
                </div>
                <div className="space-y-3 max-h-96 overflow-y-auto">
                  {retrievalRecords.map((record, idx) => (
                    <Card
                      key={idx}
                      size="small"
                      className={cn(
                        'border-l-4',
                        record.score >= 0.8 ? 'border-l-green-500' :
                        record.score >= 0.5 ? 'border-l-yellow-500' :
                        'border-l-red-500'
                      )}
                    >
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <Tag color={
                            record.score >= 0.8 ? 'success' :
                            record.score >= 0.5 ? 'warning' :
                            'error'
                          }>
                            {(record.score * 100).toFixed(1)}%
                          </Tag>
                          {record.title && (
                            <Text strong className="text-sm">{record.title}</Text>
                          )}
                        </div>
                      </div>
                      <Text className="text-sm whitespace-pre-wrap leading-relaxed">
                        {record.content}
                      </Text>
                    </Card>
                  ))}
                </div>
              </>
            )}

            {!retrievalLoading && !retrievalError && retrievalRecords.length === 0 && (
              <Empty
                description="输入查询内容并点击检索按钮"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}

            {retrievalLoading && (
              <div className="flex items-center justify-center py-8">
                <Spin indicator={<LoadingOutlined style={{ fontSize: 24 }} spin />} />
                <Text className="ml-3">正在检索...</Text>
              </div>
            )}
          </div>
        </Modal>
      </div>
    </div>
  );
}
