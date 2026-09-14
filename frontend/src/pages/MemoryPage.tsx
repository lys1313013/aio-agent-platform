import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  App,
  Button,
  Checkbox,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import {
  BulbOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  PlusOutlined,
  SearchOutlined,
  StarOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import type { TabsProps } from 'antd';
import { CalendarOutlined } from '@ant-design/icons';
import { agentsApi, memoriesApi } from '@/lib/api';
import type { Agent, Memory, MemoryLayer } from '@/lib/types';
import DailyMemoryTimeline from '@/components/memory/DailyMemoryTimeline';

type MemoryTab = MemoryLayer | 'daily';

const { Text } = Typography;
const { TextArea } = Input;

const LAYERS: MemoryLayer[] = ['L1', 'L2', 'L3'];

const LAYER_CONFIG: Record<
  MemoryLayer,
  { label: string; description: string; color: string; icon: ReactNode; bg: string; dot: string }
> = {
  L1: {
    label: '常驻上下文',
    description: '每次对话都会加载。包括用户偏好、核心事实、项目规则。',
    color: 'blue',
    icon: <StarOutlined />,
    bg: 'bg-blue-500/10 text-blue-500',
    dot: 'bg-blue-500',
  },
  L2: {
    label: '长期记忆',
    description: '按相关性检索。包括历史决策、经验教训、重要事件。',
    color: 'purple',
    icon: <DatabaseOutlined />,
    bg: 'bg-purple-500/10 text-purple-500',
    dot: 'bg-purple-500',
  },
  L3: {
    label: '情景记忆',
    description: '每日对话摘要。可按日期和关键词搜索。',
    color: 'green',
    icon: <HistoryOutlined />,
    bg: 'bg-green-500/10 text-green-500',
    dot: 'bg-green-500',
  },
};

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return '刚刚';
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`;
  return new Date(iso).toLocaleDateString('zh-CN');
}

export default function MemoryPage() {
  const { message } = App.useApp();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [scopeAgent, setScopeAgent] = useState('');
  const [formAgent, setFormAgent] = useState('');
  useEffect(() => { agentsApi.list().then(setAgents).catch(() => message.error('加载智能体失败')); }, [message]);
  const scopeOptions = [
    { value: '', label: '用户共享记忆' },
    ...agents.map((agent) => ({ value: agent.id, label: `${agent.name} · 专属记忆` })),
  ];
  const [activeLayer, setActiveLayer] = useState<MemoryTab>('L1');
  const [searchQuery, setSearchQuery] = useState('');
  const [memories, setMemories] = useState<Memory[]>([]);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState<Record<MemoryLayer, number>>({ L1: 0, L2: 0, L3: 0 });
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);

  // create / edit modal state
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editingMemory, setEditingMemory] = useState<Memory | null>(null);
  const [formLayer, setFormLayer] = useState<MemoryLayer>('L1');
  const [formContent, setFormContent] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const isDailyTab = activeLayer === 'daily';
  const config = isDailyTab
    ? {
        label: '每日记忆',
        description: '按天汇总的记忆记录。每天凌晨自动合并前一天的会话,对话结束时也会实时追加;点击"重新生成"可手动刷新。',
      }
    : LAYER_CONFIG[activeLayer];

  const fetchMemories = useCallback(async () => {
    if (activeLayer === 'daily') return;
    setLoading(true);
    try {
      if (searchQuery.trim()) {
        const results = await memoriesApi.search(searchQuery, { layer: activeLayer, agent_id: scopeAgent || undefined });
        setScores(Object.fromEntries(results.map((r) => [r.id, r.score])));
        setMemories(
          results.map((r) => ({
            id: r.id,
            agent_id: r.agent_id,
            layer: r.layer,
            content: r.content,
            metadata: {},
            created_at: r.created_at,
            updated_at: r.created_at,
          })),
        );
        setTotal(results.length);
      } else {
        const resp = await memoriesApi.list({ layer: activeLayer, agent_id: scopeAgent || undefined });
        setScores({});
        setMemories(resp.items);
        setTotal(resp.total);
      }
    } catch {
      message.error('加载记忆失败');
    } finally {
      setLoading(false);
    }
  }, [activeLayer, searchQuery, message, scopeAgent]);

  const fetchCounts = useCallback(async () => {
    try {
      const stats = await memoriesApi.stats(scopeAgent || undefined);
      setCounts({ L1: stats.L1, L2: stats.L2, L3: stats.L3 });
    } catch {
      // 统计失败不阻塞主流程
    }
  }, [scopeAgent]);

  useEffect(() => {
    fetchMemories();
  }, [fetchMemories]);

  useEffect(() => {
    fetchCounts();
  }, [fetchCounts]);

  // 列表刷新后剔除已不存在的选中项（切换层级/搜索时自动清空）
  useEffect(() => {
    const ids = new Set(memories.map((m) => m.id));
    setSelectedIds((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [memories]);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedIds((prev) => {
      const allChecked = memories.every((m) => prev.has(m.id));
      return allChecked ? new Set() : new Set(memories.map((m) => m.id));
    });
  };

  const selectedCount = selectedIds.size;
  const allSelected = memories.length > 0 && memories.every((m) => selectedIds.has(m.id));
  const someSelected = !allSelected && memories.some((m) => selectedIds.has(m.id));

  const handleCreate = async () => {
    if (!formContent.trim()) return;
    setSubmitting(true);
    try {
      await memoriesApi.create({ layer: formLayer, content: formContent.trim(), agent_id: formAgent || null });
      message.success('记忆已创建');
      setCreateModalOpen(false);
      setFormContent('');
      fetchMemories();
      fetchCounts();
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
        agent_id: formAgent || null,
      });
      message.success('记忆已更新');
      setEditingMemory(null);
      setFormContent('');
      fetchMemories();
      fetchCounts();
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
      fetchCounts();
    } catch {
      message.error('删除失败');
    }
  };

  const handleBatchDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setBatchDeleting(true);
    try {
      await memoriesApi.deleteMany(ids);
      message.success(`已删除 ${ids.length} 条记忆`);
      setSelectedIds(new Set());
      fetchMemories();
      fetchCounts();
    } catch {
      message.error('批量删除失败');
    } finally {
      setBatchDeleting(false);
    }
  };

  const openCreateModal = () => {
    setFormLayer(activeLayer === 'daily' ? 'L1' : activeLayer);
    setFormContent('');
    setFormAgent(scopeAgent);
    setCreateModalOpen(true);
  };

  const openEditModal = (memory: Memory) => {
    setFormLayer(memory.layer);
    setFormAgent(memory.agent_id || '');
    setFormContent(memory.content);
    setEditingMemory(memory);
  };

  const handleTabChange = (key: string) => {
    setActiveLayer(key as MemoryTab);
    setSearchQuery('');
  };

  const tabItems: TabsProps['items'] = [
    ...LAYERS.map((layer) => ({
      key: layer,
      label: (
        <span className="flex items-center gap-2">
          {LAYER_CONFIG[layer].icon}
          {LAYER_CONFIG[layer].label}
          <span className="rounded-md bg-muted px-1.5 text-xs tabular-nums text-muted-foreground">
            {counts[layer]}
          </span>
        </span>
      ),
    })),
    {
      key: 'daily',
      label: (
        <span className="flex items-center gap-2">
          <CalendarOutlined />
          每日记忆
        </span>
      ),
    },
  ];

  const layerOptions = LAYERS.map((layer) => ({
    value: layer,
    label: (
      <span className="flex items-center gap-2">
        <span className={`inline-block h-2 w-2 rounded-full ${LAYER_CONFIG[layer].dot}`} />
        {layer} — {LAYER_CONFIG[layer].label}
      </span>
    ),
  }));

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1440px] px-6 py-6 lg:px-8">
        {/* Page context and actions */}
        <div className="mb-7 flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold">
              <BulbOutlined className="text-primary" />
              记忆管理
            </h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              查看和管理 Agent 在不同层级的持久化记忆。
            </p>
          </div>
          <div className="flex w-full flex-wrap items-end gap-3 sm:w-auto">
            <div className="min-w-0 flex-1 sm:flex-none">
              <label htmlFor="memory-scope" className="mb-1.5 block text-xs text-muted-foreground">
                记忆范围
              </label>
              <Select
                id="memory-scope"
                aria-label="记忆范围"
                value={scopeAgent}
                onChange={(value) => { setScopeAgent(value); setSelectedIds(new Set()); }}
                options={scopeOptions}
                className="w-full sm:w-60"
                showSearch
                optionFilterProp="label"
              />
            </div>
            {!isDailyTab && (
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
                添加记忆
              </Button>
            )}
          </div>
        </div>

        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <BulbOutlined />
          <span>{scopeAgent
            ? '当前为专属记忆，仅供您与该智能体使用。'
            : '当前为用户共享记忆，供您的所有智能体使用。'}</span>
        </div>

        {/* Layer tabs */}
        <Tabs
          activeKey={activeLayer}
          onChange={handleTabChange}
          items={tabItems}
          className="mb-3"
        />

        {isDailyTab ? (
          <>
            <p className="mb-5 text-sm leading-relaxed text-muted-foreground">{config.description}</p>
            <DailyMemoryTimeline key={scopeAgent} agentId={scopeAgent || undefined} />
          </>
        ) : (
          <>
        {/* Toolbar: search + batch actions */}
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm leading-relaxed text-muted-foreground">{config.description}</p>
          <Input
            prefix={<SearchOutlined />}
            placeholder={`搜索${config.label}...`}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            allowClear
            className="w-full sm:w-72"
          />
          {selectedCount > 0 && (
            <>
              <Text type="secondary" className="text-sm">
                已选 {selectedCount} 项
              </Text>
              <Button size="small" onClick={() => setSelectedIds(new Set())}>
                取消
              </Button>
              <Popconfirm
                title={`确定删除选中的 ${selectedCount} 条记忆吗？`}
                onConfirm={handleBatchDelete}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button size="small" danger icon={<DeleteOutlined />} loading={batchDeleting}>
                  批量删除
                </Button>
              </Popconfirm>
            </>
          )}
        </div>

        {/* Memory list */}
        <Spin spinning={loading}>
          {memories.length === 0 ? (
            <div className="rounded-xl border border-border bg-card py-12">
              <Empty
                image={<BulbOutlined className="text-6xl text-muted-foreground/30" />}
                styles={{ image: { height: 60 } }}
                description={
                  <div>
                    <Text type="secondary">
                      {searchQuery ? '没有匹配的记忆。' : `暂无${config.label}记忆。`}
                    </Text>
                    <br />
                    <Text type="secondary" className="text-xs">
                      记忆会在您与 Agent 交互时自动创建。
                    </Text>
                  </div>
                }
              />
            </div>
          ) : (
            <>
              <div className="mb-3 flex items-center gap-3">
                <Checkbox
                  checked={allSelected}
                  indeterminate={someSelected}
                  onChange={toggleSelectAll}
                  disabled={memories.length === 0}
                >
                  全选
                </Checkbox>
                <Text type="secondary" className="text-xs">
                  共 {total} 条{searchQuery ? '搜索结果' : '记忆'}
                </Text>
              </div>

              <div className="space-y-2">
                {memories.map((memory) => {
                  const selected = selectedIds.has(memory.id);
                  const score = scores[memory.id];
                  const tags = memory.metadata?.tags;
                  return (
                    <div
                      key={memory.id}
                      className={`group rounded-xl border bg-card p-4 transition-all hover:shadow-sm ${
                        selected
                          ? 'border-primary/60 bg-primary/5'
                          : 'border-border hover:border-primary/40'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <Checkbox
                          checked={selected}
                          onChange={() => toggleSelect(memory.id)}
                          className="mt-0.5"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                            {memory.content}
                          </p>
                          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
                            <Tag color={LAYER_CONFIG[memory.layer].color} className="!mr-0">
                              {memory.layer}
                            </Tag>
                            <Text type="secondary" className="text-xs">
                              {formatRelativeTime(memory.created_at)}
                            </Text>
                            {score !== undefined && (
                              <Tag color="gold" className="!mr-0">
                                相关度 {(score * 100).toFixed(0)}%
                              </Tag>
                            )}
                            {Array.isArray(tags) && tags.length > 0 && (
                              <span className="flex flex-wrap items-center gap-1.5">
                                <TagsOutlined className="text-xs text-muted-foreground" />
                                {(tags as string[])
                                  .slice(0, 3)
                                  .map((tag: string) => (
                                    <span
                                      key={tag}
                                      className="rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary"
                                    >
                                      {tag}
                                    </span>
                                  ))}
                                {(tags as string[]).length > 3 && (
                                  <Text type="secondary" className="text-xs">
                                    +{(tags as string[]).length - 3}
                                  </Text>
                                )}
                              </span>
                            )}
                            <div className="ml-auto flex items-center gap-1">
                              <Button
                                size="small"
                                type="text"
                                aria-label="编辑记忆"
                                icon={<EditOutlined />}
                                onClick={() => openEditModal(memory)}
                              />
                              <Popconfirm
                                title="确定要删除这条记忆吗？"
                                onConfirm={() => handleDelete(memory.id)}
                                okText="删除"
                                cancelText="取消"
                                okButtonProps={{ danger: true }}
                              >
                                <Button size="small" type="text" danger aria-label="删除记忆" icon={<DeleteOutlined />} />
                              </Popconfirm>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </Spin>
          </>
        )}

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
              <Text className="mb-1 block text-sm font-medium">记忆范围</Text>
              <Select aria-label="保存记忆范围" value={formAgent} onChange={setFormAgent} options={scopeOptions} className="w-full" showSearch optionFilterProp="label" />
            </div>
            <div>
              <Text className="mb-1 block text-sm font-medium">层级</Text>
              <Select value={formLayer} onChange={(v) => setFormLayer(v)} options={layerOptions} className="w-full" />
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
              <Text className="mb-1 block text-sm font-medium">记忆范围</Text>
              <Select aria-label="保存记忆范围" value={formAgent} onChange={setFormAgent} options={scopeOptions} className="w-full" showSearch optionFilterProp="label" />
            </div>
            <div>
              <Text className="mb-1 block text-sm font-medium">层级</Text>
              <Select value={formLayer} onChange={(v) => setFormLayer(v)} options={layerOptions} className="w-full" />
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
