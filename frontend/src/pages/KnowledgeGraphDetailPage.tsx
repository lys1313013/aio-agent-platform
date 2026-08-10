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
  InboxOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
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
  Upload,
} from 'antd';
import type { UploadFile } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import GraphCanvas from '@/components/graph/GraphCanvas';
import { graphKnowledgeApi } from '@/lib/api';
import type {
  GraphChunk,
  GraphDocument,
  GraphEntity,
  GraphExtractionJob,
  GraphKnowledgeBase,
  GraphRelationship,
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
  const [activeTab, setActiveTab] = useState('graph');

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
            { key: 'graph', label: '图谱', children: <GraphViewTab kbId={kbIdStr} /> },
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

// ---- Full graph view ----

function GraphViewTab({ kbId }: { kbId: string }) {
  const { message } = App.useApp();
  const [status, setStatus] = useState('approved');
  const [loading, setLoading] = useState(true);
  const [entities, setEntities] = useState<GraphEntity[]>([]);
  const [relationships, setRelationships] = useState<GraphRelationship[]>([]);
  const [selected, setSelected] = useState<GraphEntity | null>(null);

  const fetchGraph = useCallback(async () => {
    setLoading(true);
    try {
      const statusParam = status === 'all' ? undefined : status;
      const [ents, rels] = await Promise.all([
        graphKnowledgeApi.entities(kbId, { status: statusParam, limit: 2000 }),
        graphKnowledgeApi.relationships(kbId, { status: statusParam, limit: 5000 }),
      ]);
      setEntities(ents.items);
      setRelationships(rels.items);
    } catch (err: any) {
      message.error(`加载图谱失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, status, message]);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  const entityIds = new Set(entities.map((e) => e.id));
  const nodes = entities.map((e) => ({
    id: e.id,
    name: e.name,
    type: e.type || '其他',
    description: e.description,
  }));
  const links = relationships
    .filter((r) => entityIds.has(r.source_entity_id) && entityIds.has(r.target_entity_id))
    .map((r) => ({
      id: r.id,
      source: r.source_entity_id,
      target: r.target_entity_id,
      relationType: r.relation_type,
      description: r.description,
    }));

  const selectedRels = selected
    ? relationships.filter(
        (r) => r.source_entity_id === selected.id || r.target_entity_id === selected.id,
      )
    : [];

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <Space>
          <Segmented
            value={status}
            onChange={(v) => setStatus(String(v))}
            options={[
              { label: '已采纳', value: 'approved' },
              { label: '待确认', value: 'pending' },
              { label: '全部', value: 'all' },
            ]}
          />
          <Text type="secondary" className="text-xs">
            {entities.length} 个实体 · {links.length} 条关系
          </Text>
        </Space>
        <Space>
          <Text type="secondary" className="text-xs">拖拽平移 · 滚轮缩放 · 点击节点看详情</Text>
          <Button size="small" icon={<ReloadOutlined />} onClick={fetchGraph} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      {loading ? (
        <div className="py-24 text-center"><Spin size="large" /></div>
      ) : entities.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无实体，请先在「抽取任务」中构建图谱并采纳实体与关系"
        />
      ) : (
        <div className="border border-border rounded-lg overflow-hidden bg-white">
          <GraphCanvas
            nodes={nodes}
            links={links}
            height="calc(100vh - 340px)"
            onNodeClick={(node) => setSelected(entities.find((e) => e.id === node.id) ?? null)}
          />
        </div>
      )}

      <Drawer
        title={selected?.name}
        open={!!selected}
        onClose={() => setSelected(null)}
        width={420}
      >
        {selected && (
          <div className="space-y-4">
            <div>
              <Tag color="blue">{selected.type || '其他'}</Tag>
              {selected.status === 'approved' ? (
                <Tag color="success" icon={<CheckCircleOutlined />}>已采纳</Tag>
              ) : (
                <Tag color="warning">待确认</Tag>
              )}
            </div>
            {selected.description && (
              <div>
                <Text strong className="block mb-1">描述</Text>
                <Text type="secondary" className="text-sm whitespace-pre-wrap">
                  {selected.description}
                </Text>
              </div>
            )}
            <div>
              <Text strong className="block mb-2">关联关系（{selectedRels.length}）</Text>
              <div className="space-y-2">
                {selectedRels.map((rel) => (
                  <Card key={rel.id} size="small">
                    <div className="flex items-center gap-2 text-sm flex-wrap">
                      <Text strong>{rel.source_name}</Text>
                      <Tag color="purple">{rel.relation_type}</Tag>
                      <Text strong>{rel.target_name}</Text>
                    </div>
                    {rel.description && (
                      <Text type="secondary" className="text-xs">{rel.description}</Text>
                    )}
                  </Card>
                ))}
                {selectedRels.length === 0 && (
                  <Text type="secondary" className="text-sm">暂无关联关系</Text>
                )}
              </div>
            </div>
          </div>
        )}
      </Drawer>
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
  const [addMode, setAddMode] = useState<'upload' | 'text'>('upload');
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploadTitle, setUploadTitle] = useState('');
  const [form] = Form.useForm();

  const fetchDocs = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      setDocuments(await graphKnowledgeApi.documents(kbId));
    } catch (err: any) {
      if (!silent) message.error(`加载文档失败：${err.message}`);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [kbId, message]);

  useEffect(() => { fetchDocs(); }, [fetchDocs]);

  // Poll silently while any document is being parsed (MinerU OCR)
  useEffect(() => {
    if (!documents.some((d) => d.status === 'parsing')) return;
    const timer = setInterval(() => fetchDocs(true), 5000);
    return () => clearInterval(timer);
  }, [documents, fetchDocs]);

  const handleAdd = async () => {
    try {
      setSaving(true);
      if (addMode === 'upload') {
        const raw = fileList[0]
          ? ((fileList[0].originFileObj ?? fileList[0]) as unknown as File)
          : null;
        if (!raw) {
          message.warning('请先选择要上传的文件');
          return;
        }
        await graphKnowledgeApi.uploadDocument(kbId, raw, uploadTitle.trim() || undefined);
      } else {
        const values = await form.validateFields();
        await graphKnowledgeApi.addDocument(kbId, {
          title: values.title || undefined,
          content: values.content,
        });
      }
      message.success('文档已添加并完成分块');
      setModalOpen(false);
      form.resetFields();
      setFileList([]);
      setUploadTitle('');
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
              render: (v: string) => {
                const meta: Record<string, { color: string; label: string }> = {
                  chunked: { color: 'success', label: '已分块' },
                  parsing: { color: 'processing', label: '解析中' },
                  failed: { color: 'error', label: '失败' },
                  pending: { color: 'warning', label: '待处理' },
                };
                const m = meta[v] ?? { color: 'default', label: v };
                return <Tag color={m.color}>{m.label}</Tag>;
              },
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
        <Tabs
          activeKey={addMode}
          onChange={(k) => setAddMode(k as 'upload' | 'text')}
          className="mt-2"
          items={[
            {
              key: 'upload',
              label: '上传文件',
              children: (
                <div className="space-y-4">
                  <Upload.Dragger
                    accept=".md,.markdown,.txt,.html,.htm,.pdf,.docx"
                    maxCount={1}
                    fileList={fileList}
                    beforeUpload={() => false}
                    onChange={({ fileList: fl }) => setFileList(fl.slice(-1))}
                    onRemove={() => setFileList([])}
                  >
                    <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                    <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
                    <p className="ant-upload-hint">
                      支持 Markdown / TXT / HTML / PDF / Word(.docx),单文件不超过 20MB;扫描版
                      PDF 将自动通过 OCR 解析(需后台配置 MinerU)
                    </p>
                  </Upload.Dragger>
                  <Input
                    placeholder="标题(留空则使用文件名)"
                    value={uploadTitle}
                    onChange={(e) => setUploadTitle(e.target.value)}
                    maxLength={256}
                  />
                </div>
              ),
            },
            {
              key: 'text',
              label: '粘贴文本',
              children: (
                <Form form={form} layout="vertical">
                  <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
                    <Input placeholder="例如:产品技术手册-第3章" />
                  </Form.Item>
                  <Form.Item
                    name="content"
                    label="内容"
                    rules={[{ required: true, message: '请输入文档内容' }]}
                  >
                    <TextArea rows={12} placeholder="粘贴 Markdown / 纯文本内容,支持标题分块" />
                  </Form.Item>
                </Form>
              ),
            },
          ]}
        />
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
  const [pageSize, setPageSize] = useState(20);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<GraphEntity | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();

  const fetchEntities = useCallback(async () => {
    setLoading(true);
    try {
      const result = await graphKnowledgeApi.entities(kbId, {
        status: status === 'all' ? undefined : status,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err: any) {
      message.error(`加载实体失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, status, page, pageSize, message]);

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
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(ps !== pageSize ? 1 : p);
            setPageSize(ps);
          },
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
  const [pageSize, setPageSize] = useState(20);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<GraphRelationship | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();

  const fetchRels = useCallback(async () => {
    setLoading(true);
    try {
      const result = await graphKnowledgeApi.relationships(kbId, {
        status: status === 'all' ? undefined : status,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err: any) {
      message.error(`加载关系失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [kbId, status, page, pageSize, message]);

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
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(ps !== pageSize ? 1 : p);
            setPageSize(ps);
          },
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

  const nameToId = result
    ? new Map(result.entities.map((e) => [e.name, e.id]))
    : new Map<string, string>();

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
            <div className="border border-border rounded-lg overflow-hidden bg-white">
              <GraphCanvas
                height={560}
                nodes={result.entities.map((e) => ({
                  id: e.id,
                  name: e.name,
                  type: e.type || '其他',
                  description: e.description,
                  isSeed: e.is_seed,
                }))}
                links={result.relationships
                  .filter((r) => nameToId.has(r.source) && nameToId.has(r.target))
                  .map((r) => ({
                    id: r.id,
                    source: nameToId.get(r.source)!,
                    target: nameToId.get(r.target)!,
                    relationType: r.relation_type,
                    description: r.description,
                  }))}
              />
            </div>
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
