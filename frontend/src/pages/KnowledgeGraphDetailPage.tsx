import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeftOutlined,
  ApartmentOutlined,
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  ReloadOutlined,
  EyeOutlined,
  TagsOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { graphKnowledgeApi } from '@/lib/api';
import type {
  GraphChunk,
  GraphDocument,
  GraphEntity,
  GraphExtractionJob,
  GraphKnowledgeBase,
  GraphRelationship,
  GraphRetrieveEntity,
  GraphRetrieveRelationship,
  GraphRetrieveResult,
} from '@/lib/api';

const { Text } = Typography;
const { TextArea } = Input;

const JOB_STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  done: 'success',
  failed: 'error',
};

export default function KnowledgeGraphDetailPage() {
  const { kbId = '' } = useParams<{ kbId: string }>();
  const { message } = App.useApp();
  const navigate = useNavigate();

  const [kb, setKb] = useState<GraphKnowledgeBase | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('documents');

  useEffect(() => {
    graphKnowledgeApi
      .list()
      .then((kbs) => {
        setKb(kbs.find((k) => k.id === kbId) ?? null);
      })
      .catch((err: any) => message.error(`加载知识库失败：${err.message}`))
      .finally(() => setLoading(false));
  }, [kbId, message]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  if (!kb) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Empty description="图谱知识库不存在或已被删除">
          <Button onClick={() => navigate('/knowledge-graph')}>返回列表</Button>
        </Empty>
      </div>
    );
  }

  const kbIdStr = kb.id;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-6">
        <div className="mb-4 flex items-center gap-3">
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/knowledge-graph')}>
            返回
          </Button>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <ApartmentOutlined className="text-primary" />
            {kb.name}
          </h1>
          {!kb.is_active && <Tag>已禁用</Tag>}
          {kb.description && (
            <Text type="secondary" className="truncate max-w-md">{kb.description}</Text>
          )}
        </div>

        <div className="mb-4 grid grid-cols-4 gap-4 max-w-2xl">
          <Card size="small">
            <Statistic title="实体" value={kb.entity_count} prefix={<TagsOutlined />} />
          </Card>
          <Card size="small">
            <Statistic title="关系" value={kb.relationship_count} prefix={<ApartmentOutlined />} />
          </Card>
          <Card size="small">
            <Statistic title="文档" value={kb.document_count} prefix={<FileTextOutlined />} />
          </Card>
          <Card size="small">
            <Statistic title="图谱状态" value={kb.entity_count > 0 ? '已构建' : '待构建'} />
          </Card>
        </div>

        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            { key: 'documents', label: '文档', children: <DocumentsTab kbId={kbIdStr} /> },
            { key: 'entities', label: '实体', children: <EntitiesTab kbId={kbIdStr} /> },
            { key: 'relationships', label: '关系', children: <RelationshipsTab kbId={kbIdStr} /> },
            { key: 'retrieve', label: '检索测试', children: <RetrieveTab kbId={kbIdStr} /> },
            { key: 'jobs', label: '抽取任务', children: <JobsTab kbId={kbIdStr} /> },
          ]}
        />
      </div>
    </div>
  );
}

// ---- Documents ----

function DocumentsTab({ kbId }: { kbId: string }) {
  const { message } = App.useApp();
  const [documents, setDocuments] = useState<GraphDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [chunks, setChunks] = useState<GraphChunk[] | null>(null);
  const [chunkDoc, setChunkDoc] = useState<GraphDocument | null>(null);
  const [form] = Form.useForm();

  const fetchDocs = useCallback(async () => {
    setLoading(true);
    try {
      setDocuments(await graphKnowledgeApi.documents(kbId));
    } catch (err: any) {
      message.error(`加载文档失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, message]);

  useEffect(() => { fetchDocs(); }, [fetchDocs]);

  const handleAdd = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await graphKnowledgeApi.addDocument(kbId, {
        title: values.title || undefined,
        content: values.content,
      });
      message.success('文档已添加并完成分块');
      setModalOpen(false);
      form.resetFields();
      fetchDocs();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '添加失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (docId: string) => {
    try {
      await graphKnowledgeApi.deleteDocument(kbId, docId);
      message.success('文档已删除');
      fetchDocs();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    }
  };

  const viewChunks = async (doc: GraphDocument) => {
    setChunkDoc(doc);
    setChunks(null);
    try {
      setChunks(await graphKnowledgeApi.chunks(kbId, doc.id));
    } catch (err: any) {
      message.error(`加载分块失败：${err.message}`);
    }
  };

  return (
    <div>
      <div className="mb-4">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          添加文档
        </Button>
      </div>

      {documents.length === 0 && !loading ? (
        <Empty description="暂无文档，添加后自动分块，供 LLM 抽取实体与关系。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Table<GraphDocument>
          rowKey="id"
          loading={loading}
          dataSource={documents}
          pagination={false}
          columns={[
            { title: '标题', dataIndex: 'title', ellipsis: true },
            {
              title: '分块数',
              dataIndex: 'chunk_count',
              width: 90,
              render: (v: number) => <Tag>{v} 块</Tag>,
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 110,
              render: (v: string) => (
                <Tag color={v === 'chunked' ? 'success' : 'warning'}>
                  {v === 'chunked' ? '已分块' : v}
                </Tag>
              ),
            },
            {
              title: '操作',
              width: 160,
              render: (_, doc) => (
                <Space size={4}>
                  <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => viewChunks(doc)}>
                    分块
                  </Button>
                  <Popconfirm
                    title="删除该文档？"
                    description="将删除其分块；相关实体与关系保留。"
                    onConfirm={() => handleDelete(doc.id)}
                    okText="删除"
                    okType="danger"
                    cancelText="取消"
                  >
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      )}

      <Modal
        title="添加文档"
        open={modalOpen}
        onOk={handleAdd}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        destroyOnHidden
        width={640}
      >
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="例如：产品技术手册-第3章" />
          </Form.Item>
          <Form.Item
            name="content"
            label="内容"
            rules={[{ required: true, message: '请输入文档内容' }]}
          >
            <TextArea rows={12} placeholder="粘贴 Markdown / 纯文本内容，支持标题分块" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`分块列表 - ${chunkDoc?.title ?? ''}`}
        open={!!chunkDoc}
        onCancel={() => setChunkDoc(null)}
        footer={null}
        width={760}
        destroyOnHidden
      >
        {chunks === null ? (
          <div className="py-8 text-center"><Spin /></div>
        ) : (
          <div className="max-h-[60vh] overflow-y-auto space-y-3">
            {chunks.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无分块" />
            ) : (
              chunks.map((chunk) => (
                <Card key={chunk.id} size="small" title={`#${chunk.seq + 1}`}>
                  <Text className="text-sm whitespace-pre-wrap">{chunk.content}</Text>
                </Card>
              ))
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

// ---- Entities ----

function EntitiesTab({ kbId }: { kbId: string }) {
  const { message } = App.useApp();
  const [items, setItems] = useState<GraphEntity[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<string>('all');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<GraphEntity | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();

  const fetchEntities = useCallback(async () => {
    setLoading(true);
    try {
      const result = await graphKnowledgeApi.entities(kbId, {
        status: status === 'all' ? undefined : status,
        limit: 20,
        offset: (page - 1) * 20,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err: any) {
      message.error(`加载实体失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, status, page, message]);

  useEffect(() => { fetchEntities(); }, [fetchEntities]);

  const openEdit = (entity: GraphEntity) => {
    setEditing(entity);
    editForm.setFieldsValue({
      name: entity.name,
      type: entity.type,
      description: entity.description || '',
    });
    setEditOpen(true);
  };

  const handleEditSave = async () => {
    if (!editing) return;
    try {
      const values = await editForm.validateFields();
      await graphKnowledgeApi.updateEntity(kbId, editing.id, {
        name: values.name,
        type: values.type,
        description: values.description || undefined,
      });
      message.success('实体已更新');
      setEditOpen(false);
      fetchEntities();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '更新失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await graphKnowledgeApi.deleteEntity(kbId, id);
      message.success('实体已删除');
      fetchEntities();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    }
  };

  const handleApprove = async () => {
    if (selected.length === 0) return;
    try {
      const { approved } = await graphKnowledgeApi.approveEntities(kbId, selected);
      message.success(`已采纳 ${approved} 个实体`);
      setSelected([]);
      fetchEntities();
    } catch (err: any) {
      message.error(err.message || '采纳失败');
    }
  };

  const columns: ColumnsType<GraphEntity> = [
    { title: '实体', dataIndex: 'name', render: (v: string) => <Text strong>{v}</Text> },
    { title: '类型', dataIndex: 'type', width: 110, render: (v: string) => <Tag color="blue">{v}</Tag> },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) =>
        v === 'approved' ? <Tag color="success" icon={<CheckCircleOutlined />}>已采纳</Tag> : <Tag color="warning">待确认</Tag>,
    },
    {
      title: '操作',
      width: 130,
      render: (_, entity) => (
        <Space size={4}>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(entity)} />
          <Popconfirm title="删除该实体？" description="其关联的关系也会被删除。" onConfirm={() => handleDelete(entity.id)} okText="删除" okType="danger" cancelText="取消">
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Space>
          <Segmented
            value={status}
            onChange={(v) => { setStatus(String(v)); setPage(1); setSelected([]); }}
            options={[
              { label: '全部', value: 'all' },
              { label: '待确认', value: 'pending' },
              { label: '已采纳', value: 'approved' },
            ]}
          />
          <Button
            icon={<CheckCircleOutlined />}
            disabled={selected.length === 0}
            onClick={handleApprove}
          >
            采纳选中（{selected.length}）
          </Button>
        </Space>
      </div>
      <Table<GraphEntity>
        rowKey="id"
        loading={loading}
        dataSource={items}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }}
        columns={columns}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          showTotal: (t) => `共 ${t} 条`,
          onChange: setPage,
        }}
      />
      <Modal
        title="编辑实体"
        open={editOpen}
        onOk={handleEditSave}
        onCancel={() => setEditOpen(false)}
        destroyOnHidden
        width={520}
      >
        <Form form={editForm} layout="vertical" className="mt-4">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="type" label="类型">
            <Input placeholder="人物/组织/产品/概念/事件/其他" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ---- Relationships ----

function RelationshipsTab({ kbId }: { kbId: string }) {
  const { message } = App.useApp();
  const [items, setItems] = useState<GraphRelationship[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<string>('all');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<GraphRelationship | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();

  const fetchRels = useCallback(async () => {
    setLoading(true);
    try {
      const result = await graphKnowledgeApi.relationships(kbId, {
        status: status === 'all' ? undefined : status,
        limit: 20,
        offset: (page - 1) * 20,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err: any) {
      message.error(`加载关系失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, status, page, message]);

  useEffect(() => { fetchRels(); }, [fetchRels]);

  const openEdit = (rel: GraphRelationship) => {
    setEditing(rel);
    editForm.setFieldsValue({
      relation_type: rel.relation_type,
      confidence: rel.confidence,
      description: rel.description || '',
    });
    setEditOpen(true);
  };

  const handleEditSave = async () => {
    if (!editing) return;
    try {
      const values = await editForm.validateFields();
      await graphKnowledgeApi.updateRelationship(kbId, editing.id, {
        relation_type: values.relation_type,
        confidence: values.confidence,
        description: values.description || undefined,
      });
      message.success('关系已更新');
      setEditOpen(false);
      fetchRels();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '更新失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await graphKnowledgeApi.deleteRelationship(kbId, id);
      message.success('关系已删除');
      fetchRels();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    }
  };

  const handleApprove = async () => {
    if (selected.length === 0) return;
    try {
      const { approved } = await graphKnowledgeApi.approveRelationships(kbId, selected);
      message.success(`已采纳 ${approved} 条关系`);
      setSelected([]);
      fetchRels();
    } catch (err: any) {
      message.error(err.message || '采纳失败');
    }
  };

  const columns: ColumnsType<GraphRelationship> = [
    { title: '来源', dataIndex: 'source_name', render: (v: string) => <Text strong>{v ?? '-'}</Text> },
    {
      title: '关系',
      dataIndex: 'relation_type',
      width: 130,
      render: (v: string) => <Tag color="purple">{v}</Tag>,
    },
    { title: '目标', dataIndex: 'target_name', render: (v: string) => <Text strong>{v ?? '-'}</Text> },
    {
      title: '置信度',
      dataIndex: 'confidence',
      width: 90,
      render: (v: number) => `${Math.round((v ?? 0) * 100)}%`,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) =>
        v === 'approved' ? <Tag color="success" icon={<CheckCircleOutlined />}>已采纳</Tag> : <Tag color="warning">待确认</Tag>,
    },
    {
      title: '操作',
      width: 130,
      render: (_, rel) => (
        <Space size={4}>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(rel)} />
          <Popconfirm title="删除该关系？" onConfirm={() => handleDelete(rel.id)} okText="删除" okType="danger" cancelText="取消">
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Space>
          <Segmented
            value={status}
            onChange={(v) => { setStatus(String(v)); setPage(1); setSelected([]); }}
            options={[
              { label: '全部', value: 'all' },
              { label: '待确认', value: 'pending' },
              { label: '已采纳', value: 'approved' },
            ]}
          />
          <Button
            icon={<CheckCircleOutlined />}
            disabled={selected.length === 0}
            onClick={handleApprove}
          >
            采纳选中（{selected.length}）
          </Button>
        </Space>
      </div>
      <Table<GraphRelationship>
        rowKey="id"
        loading={loading}
        dataSource={items}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }}
        columns={columns}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          showTotal: (t) => `共 ${t} 条`,
          onChange: setPage,
        }}
      />
      <Modal
        title="编辑关系"
        open={editOpen}
        onOk={handleEditSave}
        onCancel={() => setEditOpen(false)}
        destroyOnHidden
        width={520}
      >
        <Form form={editForm} layout="vertical" className="mt-4">
          <Form.Item
            name="relation_type"
            label="关系类型"
            rules={[{ required: true, message: '请输入关系类型' }]}
          >
            <Input placeholder="例如：负责 / 属于 / 依赖" />
          </Form.Item>
          <Form.Item name="confidence" label="置信度">
            <InputNumber min={0} max={1} step={0.05} className="w-full" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ---- Subgraph retrieval ----

function RetrieveTab({ kbId }: { kbId: string }) {
  const { message } = App.useApp();
  const [query, setQuery] = useState('');
  const [maxDepth, setMaxDepth] = useState(2);
  const [topK, setTopK] = useState(5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GraphRetrieveResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'list' | 'graph'>('graph');

  const handleSearch = async () => {
    if (!query.trim()) {
      message.warning('请输入查询内容');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setResult(
        await graphKnowledgeApi.retrieve(kbId, {
          query,
          max_depth: maxDepth,
          top_k_entities: topK,
        }),
      );
    } catch (err: any) {
      setError(err.message || '检索失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end gap-4">
        <div className="min-w-[320px] flex-1">
          <div className="text-sm text-muted-foreground mb-1">查询</div>
          <Input.Search
            placeholder="输入实体或问题，例如：张三"
            enterButton="检索"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSearch={handleSearch}
            loading={loading}
          />
        </div>
        <div>
          <div className="text-sm text-muted-foreground mb-1">展开跳数</div>
          <InputNumber min={1} max={5} value={maxDepth} onChange={(v) => setMaxDepth(v ?? 2)} />
        </div>
        <div>
          <div className="text-sm text-muted-foreground mb-1">种子实体数</div>
          <InputNumber min={1} max={20} value={topK} onChange={(v) => setTopK(v ?? 5)} />
        </div>
      </div>

      {error && <Alert message={error} type="warning" showIcon className="mb-4" />}

      {result && (
        <div>
          <div className="mb-3 flex items-center justify-between">
            <Text strong>
              {result.entities.length} 个实体 · {result.relationships.length} 条关系
            </Text>
            <div className="flex items-center gap-3">
              <Segmented
                size="small"
                value={viewMode}
                onChange={(v) => setViewMode(String(v) as 'list' | 'graph')}
                options={[
                  { label: '图视图', value: 'graph' },
                  { label: '列表', value: 'list' },
                ]}
              />
              <Text type="secondary" className="text-xs">
                耗时 {(result.query_time_ms ?? 0).toFixed(0)} ms
              </Text>
            </div>
          </div>

          {result.seed_entities.length > 0 && (
            <div className="mb-4">
              <Text type="secondary" className="text-xs mr-2">命中实体：</Text>
              {result.seed_entities.map((s) => (
                <Tag key={s.id} color="blue">{s.name}（{s.type}）</Tag>
              ))}
            </div>
          )}

          {viewMode === 'graph' && result.entities.length > 0 ? (
            <SubgraphView entities={result.entities} relationships={result.relationships} />
          ) : (
            <>
          {result.relationships.length > 0 && (
            <div className="mb-4">
              <Text strong className="mb-2 block">关系三元组</Text>
              <div className="space-y-2">
                {result.relationships.map((rel) => (
                  <Card
                    key={rel.id}
                    size="small"
                    className="border-l-4 border-l-primary"
                    title={
                      <div className="flex items-center gap-2 text-sm flex-wrap">
                        <Text strong>{rel.source}</Text>
                        <Tag color="purple">{rel.relation_type}</Tag>
                        <Text strong>{rel.target}</Text>
                        <Text type="secondary" className="text-xs">深度{rel.depth}</Text>
                      </div>
                    }
                  >
                    {rel.description && (
                      <Text type="secondary" className="text-xs">{rel.description}</Text>
                    )}
                  </Card>
                ))}
              </div>
            </div>
          )}

          {result.entities.length > 0 && (
            <div>
              <Text strong className="mb-2 block">涉及实体</Text>
              <div className="flex flex-wrap gap-2">
                {result.entities.map((e) => (
                  <Tag key={e.id} color={e.is_seed ? 'blue' : 'default'}>{e.name}</Tag>
                ))}
              </div>
            </div>
          )}
            </>
          )}

          {result.entities.length === 0 && result.relationships.length === 0 && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未检索到相关实体，请先抽取并采纳实体与关系" />
          )}
        </div>
      )}

      {!result && !error && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="输入查询开始图谱检索（仅检索已采纳的实体与关系）"
        />
      )}
    </div>
  );
}

// ---- Subgraph SVG visualization ----

const TYPE_COLORS: Record<string, string> = {
  人物: '#1677ff',
  组织: '#722ed1',
  产品: '#13c2c2',
  概念: '#52c41a',
  事件: '#fa8c16',
  地点: '#eb2f96',
  其他: '#8c8c8c',
};

interface SubgraphViewProps {
  entities: GraphRetrieveEntity[];
  relationships: GraphRetrieveRelationship[];
}

function SubgraphView({ entities, relationships }: SubgraphViewProps) {
  const W = 820;
  const H = 560;
  const cx = W / 2;
  const cy = H / 2;

  // BFS depth from seed entities (radial ring layout).
  const seeds = entities.filter((e) => e.is_seed).map((e) => e.name);
  const depth: Record<string, number> = {};
  seeds.forEach((s) => { depth[s] = 0; });
  const adj: Record<string, string[]> = {};
  entities.forEach((e) => { adj[e.name] = []; });
  relationships.forEach((r) => {
    (adj[r.source] = adj[r.source] || []).push(r.target);
    (adj[r.target] = adj[r.target] || []).push(r.source);
  });
  const queue = [...seeds];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const nb of adj[cur] || []) {
      if (depth[nb] === undefined) {
        depth[nb] = (depth[cur] ?? 0) + 1;
        queue.push(nb);
      }
    }
  }

  const rings: Record<number, string[]> = {};
  entities.forEach((e) => {
    const d = depth[e.name] ?? 0;
    (rings[d] = rings[d] || []).push(e.name);
  });

  const pos: Record<string, { x: number; y: number }> = {};
  Object.entries(rings).forEach(([dStr, names]) => {
    const d = Number(dStr);
    const radius = d === 0 ? 0 : 74 * d;
    names.forEach((name, i) => {
      if (d === 0) { pos[name] = { x: cx, y: cy }; return; }
      const angle = (i / names.length) * Math.PI * 2 - Math.PI / 2;
      pos[name] = { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
    });
  });

  const colorFor = (type: string) => TYPE_COLORS[type] ?? '#8c8c8c';

  return (
    <div className="border border-border rounded-lg overflow-auto">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block">
        {relationships.map((r) => {
          const s = pos[r.source];
          const t = pos[r.target];
          if (!s || !t) return null;
          const mx = (s.x + t.x) / 2;
          const my = (s.y + t.y) / 2;
          return (
            <g key={r.id}>
              <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="#d9d9d9" strokeWidth={1.5} />
              <text x={mx} y={my - 4} textAnchor="middle" fontSize={10} fill="#8c8c8c">
                {r.relation_type}
              </text>
            </g>
          );
        })}
        {entities.map((e) => {
          const p = pos[e.name] ?? { x: cx, y: cy };
          const color = colorFor(e.type);
          return (
            <g key={e.id}>
              <circle cx={p.x} cy={p.y} r={24} fill={color} opacity={0.15} stroke={color} strokeWidth={2} />
              {e.is_seed && (
                <circle cx={p.x} cy={p.y} r={29} fill="none" stroke={color} strokeWidth={1} strokeDasharray="4 3" />
              )}
              <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize={11} fontWeight={600} fill={color}>
                {e.name.length > 6 ? `${e.name.slice(0, 6)}…` : e.name}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ---- Extraction jobs ----

function JobsTab({ kbId }: { kbId: string }) {
  const { message } = App.useApp();
  const [jobs, setJobs] = useState<GraphExtractionJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    try {
      setJobs(await graphKnowledgeApi.jobs(kbId));
    } catch (err: any) {
      message.error(`加载任务失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, message]);

  useEffect(() => { fetchJobs(); }, [fetchJobs]);

  // Poll while any job is pending/running.
  useEffect(() => {
    const hasActive = jobs.some((j) => j.status === 'pending' || j.status === 'running');
    if (!hasActive) return;
    const timer = setInterval(fetchJobs, 5000);
    return () => clearInterval(timer);
  }, [jobs, fetchJobs]);

  const handleStart = async () => {
    setStarting(true);
    try {
      await graphKnowledgeApi.extract(kbId);
      message.success('抽取任务已启动');
      fetchJobs();
    } catch (err: any) {
      message.error(err.message || '启动失败');
    } finally {
      setStarting(false);
    }
  };

  const handleRetry = async (jobId: string) => {
    try {
      await graphKnowledgeApi.retryJob(kbId, jobId);
      message.success('已重新启动抽取');
      fetchJobs();
    } catch (err: any) {
      message.error(err.message || '重试失败');
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-center gap-4">
        <Button type="primary" icon={<ThunderboltOutlined />} loading={starting} onClick={handleStart}>
          开始抽取
        </Button>
        <Text type="secondary">使用默认模型对全部文档分块执行实体与关系抽取</Text>
      </div>

      {jobs.length === 0 && !loading ? (
        <Empty description="暂无抽取任务，点击「开始抽取」构建图谱。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Table<GraphExtractionJob>
          rowKey="id"
          loading={loading}
          dataSource={jobs}
          pagination={false}
          columns={[
            {
              title: '状态',
              dataIndex: 'status',
              width: 110,
              render: (v: string) => (
                <Tag color={JOB_STATUS_COLOR[v] ?? 'default'}>{v}</Tag>
              ),
            },
            {
              title: '进度',
              width: 200,
              render: (_, job) => (
                <Space>
                  <Text>{job.processed_chunks} / {job.total_chunks} 块</Text>
                  {job.status === 'running' && <Spin size="small" />}
                </Space>
              ),
            },
            { title: '实体', dataIndex: 'entities_found', width: 80 },
            { title: '关系', dataIndex: 'relationships_found', width: 80 },
            {
              title: '结果',
              dataIndex: 'error',
              ellipsis: true,
              render: (v: string | null) => (v ? <Text type="danger" className="text-xs">{v}</Text> : <Text type="secondary">-</Text>),
            },
            {
              title: '操作',
              width: 90,
              render: (_, job) =>
                job.status === 'failed' ? (
                  <Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => handleRetry(job.id)}>
                    重试
                  </Button>
                ) : null,
            },
          ]}
        />
      )}
    </div>
  );
}
