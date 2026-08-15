import { useState, useEffect, useCallback } from 'react';
import {
  ApartmentOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ArrowRightOutlined,
  TagsOutlined,
  ShareAltOutlined,
  FileTextOutlined,
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
  Select,
} from 'antd';
import { graphKnowledgeApi } from '@/lib/api';
import type { GraphKnowledgeBase } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { Navigate, useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

const { Text } = Typography;
const { TextArea } = Input;

export default function KnowledgeGraphPage() {
  const { message } = App.useApp();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';
  const navigate = useNavigate();

  const [knowledgeBases, setKnowledgeBases] = useState<GraphKnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingKb, setEditingKb] = useState<GraphKnowledgeBase | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      setKnowledgeBases(await graphKnowledgeApi.list());
    } catch (err: any) {
      message.error(`加载数据失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const openModal = (kb?: GraphKnowledgeBase) => {
    if (kb) {
      setEditingKb(kb);
      form.setFieldsValue({
        name: kb.name,
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

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const payload = {
        name: values.name,
        description: values.description || undefined,
        is_active: values.is_active,
        visibility: values.visibility,
      };
      if (editingKb) {
        await graphKnowledgeApi.update(editingKb.id, payload);
        message.success('知识库已更新');
      } else {
        await graphKnowledgeApi.create(payload);
        message.success('知识库已创建');
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

  const handleDelete = async (id: string) => {
    try {
      await graphKnowledgeApi.delete(id);
      message.success('知识库已删除');
      fetchData();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <ApartmentOutlined className="text-primary" />
              知识图谱
            </h1>
            <Text type="secondary">
              基于 PostgreSQL 的自建结构化知识库：文档入库 → LLM 抽取实体与关系 → 图结构存储
            </Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
            新建知识库
          </Button>
        </div>

        {loading ? (
          <div className="py-16 flex items-center justify-center">
            <Spin size="large" />
          </div>
        ) : knowledgeBases.length === 0 ? (
          <Card>
            <Empty description="暂无图谱知识库，创建后上传文档并触发抽取，构建实体-关系图谱。" image={Empty.PRESENTED_IMAGE_SIMPLE}>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
                新建知识库
              </Button>
            </Empty>
          </Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {knowledgeBases.map((kb) => (
              <Card
                key={kb.id}
                className={cn('transition-all cursor-pointer hover:shadow-md', !kb.is_active && 'opacity-60')}
                onClick={() => navigate(`/knowledge-graph/${kb.id}`)}
                title={
                  <div className="flex items-center gap-2">
                    <ApartmentOutlined />
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
                  <Space size={4} onClick={(e) => e.stopPropagation()}>
                    <Tooltip title="进入管理">
                      <Button
                        type="text"
                        size="small"
                        icon={<ArrowRightOutlined />}
                        onClick={() => navigate(`/knowledge-graph/${kb.id}`)}
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
                      title="删除图谱知识库？"
                      description="将级联删除其全部文档、实体与关系。"
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
                <div className="space-y-3 text-sm">
                  {kb.description && (
                    <div className="flex items-start gap-2">
                      <Text type="secondary" className="w-16 shrink-0">描述</Text>
                      <Text className="text-xs">{kb.description}</Text>
                    </div>
                  )}
                  <div className="flex items-center gap-6 pt-1">
                    <div className="flex items-center gap-2">
                      <TagsOutlined className="text-primary" />
                      <Text strong>{kb.entity_count}</Text>
                      <Text type="secondary" className="text-xs">实体</Text>
                    </div>
                    <div className="flex items-center gap-2">
                      <ShareAltOutlined className="text-primary" />
                      <Text strong>{kb.relationship_count}</Text>
                      <Text type="secondary" className="text-xs">关系</Text>
                    </div>
                    <div className="flex items-center gap-2">
                      <FileTextOutlined className="text-primary" />
                      <Text strong>{kb.document_count}</Text>
                      <Text type="secondary" className="text-xs">文档</Text>
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}

        {/* Create/Edit Modal */}
        <Modal
          title={editingKb ? '编辑图谱知识库' : '新建图谱知识库'}
          open={modalOpen}
          onOk={handleSave}
          onCancel={() => setModalOpen(false)}
          confirmLoading={saving}
          destroyOnHidden
          width={560}
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
              <Input placeholder="例如：产品技术手册" />
            </Form.Item>

            <Form.Item name="description" label="描述">
              <TextArea rows={3} placeholder="知识库的内容描述（帮助判断何时使用）" />
            </Form.Item>

            <Form.Item name="is_active" label="启用" valuePropName="checked">
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
      </div>
    </div>
  );
}
