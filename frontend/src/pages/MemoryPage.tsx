import { useCallback, useEffect, useState } from 'react';
import {
  BulbOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  SearchOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { Button, Input, Tabs, Empty, Card, Typography, Modal, Select, message, Spin, Popconfirm } from 'antd';
import type { TabsProps } from 'antd';
import { memoriesApi } from '@/lib/api';
import type { Memory, MemoryLayer } from '@/lib/types';

const { Text } = Typography;
const { TextArea } = Input;

const LAYER_CONFIG: Record<MemoryLayer, { label: string; description: string; color: string }> = {
  L1: {
    label: '常驻上下文',
    description: '每次对话都会加载。包括用户偏好、核心事实、项目规则。',
    color: 'blue',
  },
  L2: {
    label: '长期记忆',
    description: '按相关性检索。包括历史决策、经验教训、重要事件。',
    color: 'purple',
  },
  L3: {
    label: '情景记忆',
    description: '每日对话摘要。可按日期和关键词搜索。',
    color: 'green',
  },
};

export default function MemoryPage() {
  const [activeLayer, setActiveLayer] = useState<MemoryLayer>('L1');
  const [searchQuery, setSearchQuery] = useState('');
  const [memories, setMemories] = useState<Memory[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editingMemory, setEditingMemory] = useState<Memory | null>(null);

  // Form state
  const [formLayer, setFormLayer] = useState<MemoryLayer>('L1');
  const [formContent, setFormContent] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      if (searchQuery.trim()) {
        const results = await memoriesApi.search(searchQuery, { layer: activeLayer });
        setMemories(
          results.map((r) => ({
            id: r.id,
            layer: r.layer,
            content: r.content,
            metadata: {},
            created_at: r.created_at,
            updated_at: r.created_at,
          })),
        );
        setTotal(results.length);
      } else {
        const resp = await memoriesApi.list({ layer: activeLayer });
        setMemories(resp.items);
        setTotal(resp.total);
      }
    } catch {
      message.error('加载记忆失败');
    } finally {
      setLoading(false);
    }
  }, [activeLayer, searchQuery]);

  useEffect(() => {
    fetchMemories();
  }, [fetchMemories]);

  const config = LAYER_CONFIG[activeLayer];

  const handleCreate = async () => {
    if (!formContent.trim()) return;
    setSubmitting(true);
    try {
      await memoriesApi.create({ layer: formLayer, content: formContent.trim() });
      message.success('记忆已创建');
      setCreateModalOpen(false);
      setFormContent('');
      fetchMemories();
    } catch {
      message.error('创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleUpdate = async () => {
    if (!editingMemory || !formContent.trim()) return;
    setSubmitting(true);
    try {
      await memoriesApi.update(editingMemory.id, {
        content: formContent.trim(),
        layer: formLayer,
      });
      message.success('记忆已更新');
      setEditingMemory(null);
      setFormContent('');
      fetchMemories();
    } catch {
      message.error('更新失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await memoriesApi.delete(id);
      message.success('记忆已删除');
      fetchMemories();
    } catch {
      message.error('删除失败');
    }
  };

  const openCreateModal = () => {
    setFormLayer(activeLayer);
    setFormContent('');
    setCreateModalOpen(true);
  };

  const openEditModal = (memory: Memory) => {
    setFormLayer(memory.layer);
    setFormContent(memory.content);
    setEditingMemory(memory);
  };

  const tabItems: TabsProps['items'] = (['L1', 'L2', 'L3'] as MemoryLayer[]).map((layer) => ({
    key: layer,
    label: (
      <span className="flex items-center gap-2">
        <AppstoreOutlined />
        {LAYER_CONFIG[layer].label}
      </span>
    ),
  }));

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <BulbOutlined className="text-primary" />
              记忆管理
            </h1>
            <Text type="secondary">
              查看和管理 Agent 在不同层级的持久化记忆。
            </Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
            添加记忆
          </Button>
        </div>

        {/* Layer tabs */}
        <Tabs
          activeKey={activeLayer}
          onChange={(key) => {
            setActiveLayer(key as MemoryLayer);
            setSearchQuery('');
          }}
          items={tabItems}
          className="mb-4"
        />

        {/* Layer description */}
        <div className="mb-6 rounded-lg bg-primary/5 border border-primary/20 px-4 py-3">
          <Text>{config.description}</Text>
        </div>

        {/* Search */}
        <Input
          prefix={<SearchOutlined />}
          placeholder={`搜索${config.label}...`}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          allowClear
          className="mb-4"
        />

        {/* Memory list */}
        <Spin spinning={loading}>
          <div className="space-y-2">
            {memories.length === 0 ? (
              <Card>
                <Empty
                  image={<BulbOutlined className="text-6xl text-muted-foreground/30" />}
                  styles={{ image: { height: 60 } }}
                  description={
                    <div>
                      <Text type="secondary">
                        {searchQuery
                          ? '没有匹配的记忆。'
                          : `暂无${config.label}记忆。`}
                      </Text>
                      <br />
                      <Text type="secondary" className="text-xs">
                        记忆会在您与 Agent 交互时自动创建。
                      </Text>
                    </div>
                  }
                />
              </Card>
            ) : (
              <>
                <div className="mb-2">
                  <Text type="secondary" className="text-xs">
                    共 {total} 条记忆
                  </Text>
                </div>
                {memories.map((memory) => (
                  <Card
                    key={memory.id}
                    size="small"
                    className="group hover:border-primary/50 transition"
                    actions={[
                      <Button
                        key="edit"
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        onClick={() => openEditModal(memory)}
                      />,
                      <Popconfirm
                        key="delete"
                        title="确定要删除这条记忆吗？"
                        onConfirm={() => handleDelete(memory.id)}
                        okText="删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                        />
                      </Popconfirm>,
                    ]}
                  >
                    <p className="text-sm leading-relaxed mb-2 whitespace-pre-wrap">
                      {memory.content}
                    </p>
                    <div className="flex items-center gap-3">
                      <Text type="secondary" className="text-xs">
                        {new Date(memory.created_at).toLocaleDateString('zh-CN')}
                      </Text>
                      {Array.isArray(memory.metadata?.tags) && (
                        <div className="flex gap-1">
                          {(memory.metadata.tags as string[]).map((tag: string) => (
                            <span
                              key={tag}
                              className="inline-block rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </Card>
                ))}
              </>
            )}
          </div>
        </Spin>

        {/* Create Modal */}
        <Modal
          title="添加记忆"
          open={createModalOpen}
          onOk={handleCreate}
          onCancel={() => setCreateModalOpen(false)}
          confirmLoading={submitting}
          okText="创建"
          cancelText="取消"
        >
          <div className="space-y-4 pt-2">
            <div>
              <Text className="mb-1 block text-sm font-medium">层级</Text>
              <Select
                value={formLayer}
                onChange={(v) => setFormLayer(v)}
                options={Object.entries(LAYER_CONFIG).map(([key, cfg]) => ({
                  value: key,
                  label: `${key} — ${cfg.label}`,
                }))}
                className="w-full"
              />
            </div>
            <div>
              <Text className="mb-1 block text-sm font-medium">内容</Text>
              <TextArea
                value={formContent}
                onChange={(e) => setFormContent(e.target.value)}
                placeholder="输入要记住的信息..."
                autoSize={{ minRows: 3, maxRows: 8 }}
                maxLength={5000}
                showCount
              />
            </div>
          </div>
        </Modal>

        {/* Edit Modal */}
        <Modal
          title="编辑记忆"
          open={!!editingMemory}
          onOk={handleUpdate}
          onCancel={() => setEditingMemory(null)}
          confirmLoading={submitting}
          okText="保存"
          cancelText="取消"
        >
          <div className="space-y-4 pt-2">
            <div>
              <Text className="mb-1 block text-sm font-medium">层级</Text>
              <Select
                value={formLayer}
                onChange={(v) => setFormLayer(v)}
                options={Object.entries(LAYER_CONFIG).map(([key, cfg]) => ({
                  value: key,
                  label: `${key} — ${cfg.label}`,
                }))}
                className="w-full"
              />
            </div>
            <div>
              <Text className="mb-1 block text-sm font-medium">内容</Text>
              <TextArea
                value={formContent}
                onChange={(e) => setFormContent(e.target.value)}
                placeholder="输入要记住的信息..."
                autoSize={{ minRows: 3, maxRows: 8 }}
                maxLength={5000}
                showCount
              />
            </div>
          </div>
        </Modal>
      </div>
    </div>
  );
}
