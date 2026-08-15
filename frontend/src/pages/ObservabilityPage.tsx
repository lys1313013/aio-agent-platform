import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bar, Line } from '@ant-design/plots';
import { ReloadOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Result,
  Row,
  Segmented,
  Skeleton,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';

import {
  observabilityApi,
  type ObsDistributionItem,
  type ObsOverview,
  type ObsQuality,
  type ObsRangeQuery,
  type ObsToolRankItem,
  type ObsTraceDetail,
  type ObsTraceItem,
  type ObsWindow,
} from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { useThemeStore } from '@/stores/themeStore';
import './ObservabilityPage.css';

const { Title, Text } = Typography;

function fmt(value: number | null | undefined): string {
  const n = Number(value ?? 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function dur(ms: number | null | undefined): string {
  if (ms == null) return '-';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function localTime(value: string, window: ObsWindow): string {
  return dayjs(value).format(window === '7d' ? 'MM-DD' : 'HH:mm');
}

function tooltipTime(value: string, window: ObsWindow): string {
  return dayjs(value).format(window === '7d' ? 'YYYY-MM-DD' : 'MM-DD HH:mm');
}

function metricValue(value: unknown, suffix?: string): string {
  const n = Number(value ?? 0);
  return suffix === '%' ? `${n.toFixed(2)}%` : fmt(n);
}

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  completed: { color: 'success', label: '完成' },
  error: { color: 'error', label: '失败' },
  interrupted: { color: 'default', label: '中断' },
  timeout: { color: 'warning', label: '超时' },
};

function StatusTag({ status }: { status: string }) {
  const meta = STATUS_MAP[status] ?? { color: 'default', label: status || '未知' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function StatCard({
  title,
  value,
  suffix,
  tone = 'default',
  hint,
}: {
  title: string;
  value: string | number;
  suffix?: string;
  tone?: 'default' | 'danger' | 'warning' | 'success';
  hint?: string;
}) {
  return (
    <Card size="small" className={`obs-stat obs-stat--${tone}`}>
      <Text type="secondary" className="obs-stat__title">{title}</Text>
      <div className="obs-stat__value">
        {value}{suffix && <span>{suffix}</span>}
      </div>
      {hint && <Text type="secondary" className="obs-stat__hint">{hint}</Text>}
    </Card>
  );
}

function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return <Alert type="error" showIcon message={message} action={<Button size="small" onClick={retry}>重试</Button>} />;
}

function TrendCard({
  title,
  data,
  window,
  color,
  empty,
  suffix,
}: {
  title: string;
  data: { ts: string; value: number }[];
  window: ObsWindow;
  color: string;
  empty: boolean;
  suffix?: string;
}) {
  return (
    <Card size="small" title={title} className="obs-chart-card">
      {empty ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该时间范围内暂无调用" />
      ) : (
        <Line
          data={data}
          xField="ts"
          yField="value"
          height={220}
          color={color}
          smooth
          axis={{
            x: {
              title: false,
              labelAutoRotate: false,
              labelAutoHide: true,
              labelFormatter: (value: string) => localTime(value, window),
            },
            y: { title: false, labelFormatter: (v: number) => `${fmt(v)}${suffix ?? ''}` },
          }}
          tooltip={{
            title: (datum: { ts: string }) => tooltipTime(datum.ts, window),
            items: [{ channel: 'y', name: title, valueFormatter: (value: unknown) => metricValue(value, suffix) }],
          }}
        />
      )}
    </Card>
  );
}

// ---- Overview ----

function OverviewTab({ range }: { range: ObsRangeQuery }) {
  const { window } = range;
  const [data, setData] = useState<ObsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState<dayjs.Dayjs | null>(null);

  const load = useCallback((silent = false) => {
    if (!silent) setLoading(true);
    setError('');
    observabilityApi.overview(range)
      .then((result) => {
        setData(result);
        setUpdatedAt(dayjs());
      })
      .catch(() => setError('监控数据加载失败，请检查服务状态'))
      .finally(() => setLoading(false));
  }, [range]);

  useEffect(() => {
    load();
    const timer = window === '7d' ? undefined : globalThis.setInterval(() => load(true), 30_000);
    return () => timer && globalThis.clearInterval(timer);
  }, [load, window]);

  if (loading && !data) return <Skeleton active paragraph={{ rows: 10 }} />;
  if (error && !data) return <ErrorState message={error} retry={() => load()} />;
  if (!data) return <Empty description="暂无监控数据" />;

  const c = data.cards;
  const s = data.series;
  return (
    <Spin spinning={loading}>
      <div className="obs-tab-content">
        <div className="obs-section-heading">
          <div>
            <Text strong>核心指标</Text>
            <Text type="secondary">统计范围随右上角时间筛选同步</Text>
          </div>
          <Space size={10}>
            <Text type="secondary">{updatedAt ? `${updatedAt.format('HH:mm:ss')} 更新` : ''}</Text>
            <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>刷新</Button>
          </Space>
        </div>
        {error && <Alert type="warning" showIcon message="自动刷新失败，当前展示上次成功数据" />}
        <Row gutter={[12, 12]}>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="LLM 调用" value={fmt(c.llm_requests)} hint="模型请求总数" /></Col>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="工具调用" value={fmt(c.tool_requests)} hint="工具执行总数" /></Col>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="LLM 失败率" value={c.llm_error_rate} suffix="%" tone={c.llm_error_rate > 5 ? 'danger' : 'success'} hint="失败请求 / 全部请求" /></Col>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="工具错误率" value={c.tool_error_rate} suffix="%" tone={c.tool_error_rate > 20 ? 'danger' : 'success'} hint="失败执行 / 全部执行" /></Col>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="平均 TTFT" value={dur(c.avg_ttft_ms)} hint="首 Token 平均延迟" /></Col>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="P95 模型耗时" value={dur(c.p95_latency_ms)} hint="95% 请求低于此值" /></Col>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="Token 消耗" value={fmt(c.total_tokens)} hint={`输入 ${fmt(c.prompt_tokens)} · 输出 ${fmt(c.completion_tokens)}`} /></Col>
          <Col xs={12} sm={8} lg={6} xl={3}><StatCard title="输入缓存命中率" value={c.cache_hit_rate} suffix="%" tone={c.cache_hit_rate < 20 ? 'warning' : 'success'} hint={`命中 ${fmt(c.cache_read_tokens)} Token`} /></Col>
        </Row>
        <div className="obs-section-heading obs-section-heading--charts">
          <div><Text strong>趋势</Text><Text type="secondary">时间按本地时区展示，空档补零</Text></div>
        </div>
        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}><TrendCard title="LLM 调用量" data={s.llm_requests ?? []} window={window} color="#1677ff" empty={c.llm_requests === 0} /></Col>
          <Col xs={24} xl={12}><TrendCard title="工具调用量" data={s.tool_requests ?? []} window={window} color="#7c3aed" empty={c.tool_requests === 0} /></Col>
          <Col xs={24} xl={12}><TrendCard title="LLM 失败率" data={s.llm_error_rate ?? []} window={window} color="#dc2626" empty={c.llm_requests === 0} suffix="%" /></Col>
          <Col xs={24} xl={12}><TrendCard title="工具错误率" data={s.tool_error_rate ?? []} window={window} color="#ea580c" empty={c.tool_requests === 0} suffix="%" /></Col>
          <Col xs={24}><TrendCard title="Token 消耗" data={s.tokens ?? []} window={window} color="#0891b2" empty={c.llm_requests === 0} /></Col>
        </Row>
      </div>
    </Spin>
  );
}

// ---- Traces ----

function TracesTab({ range }: { range: ObsRangeQuery }) {
  const [items, setItems] = useState<ObsTraceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<ObsTraceDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    observabilityApi.traces({ ...range, page, page_size: 20, status: status === 'all' ? undefined : status })
      .then((result) => { setItems(result.items); setTotal(result.total); })
      .catch(() => setError('Trace 列表加载失败'))
      .finally(() => setLoading(false));
  }, [range, page, status]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [range]);

  const openDetail = (id: string) => {
    setDetail(null);
    setDetailOpen(true);
    setDetailLoading(true);
    observabilityApi.trace(id).then(setDetail).finally(() => setDetailLoading(false));
  };

  const columns: ColumnsType<ObsTraceItem> = [
    { title: 'Trace ID', dataIndex: 'trace_id', width: 120, render: (v: string) => <Text code copyable={{ text: v }}>{v.slice(0, 8)}</Text> },
    { title: '开始时间', dataIndex: 'created_at', width: 170, render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm:ss') },
    { title: '会话', dataIndex: 'session_title', ellipsis: true, render: (v: string | null) => v || <Text type="secondary">未命名会话</Text> },
    { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <StatusTag status={v} /> },
    { title: 'LLM 轮数', dataIndex: 'iteration_count', width: 100, align: 'right' },
    { title: '工具调用', dataIndex: 'tool_call_count', width: 100, align: 'right' },
    { title: 'Token', dataIndex: 'total_tokens', width: 110, align: 'right', render: (v: number) => fmt(v) },
    { title: '端到端耗时', dataIndex: 'duration_ms', width: 120, align: 'right', render: (v: number | null) => dur(v) },
  ];

  const llmCols: ColumnsType<Record<string, unknown>> = [
    { title: '#', dataIndex: 'call_order', width: 48 },
    { title: '模型', dataIndex: 'model', ellipsis: true },
    { title: '输入', dataIndex: 'prompt_tokens', align: 'right', render: (v) => fmt(v as number) },
    { title: '输出', dataIndex: 'completion_tokens', align: 'right', render: (v) => fmt(v as number) },
    { title: '缓存', dataIndex: 'cache_read_tokens', align: 'right', render: (v) => fmt(v as number) },
    { title: 'TTFT', dataIndex: 'ttft_ms', render: (v) => dur(v as number | null) },
    { title: '总耗时', dataIndex: 'duration_ms', render: (v) => dur(v as number | null) },
    { title: '结果', dataIndex: 'final_status', width: 90, render: (v: string, r) => v === 'failed' ? <Tag color="error">{String(r.error_type ?? '失败')}</Tag> : <Tag color="success">成功</Tag> },
  ];

  const toolCols: ColumnsType<Record<string, unknown>> = [
    { title: '#', dataIndex: 'call_order', width: 48 },
    { title: '工具', dataIndex: 'tool_name', ellipsis: true },
    { title: '类型', dataIndex: 'exec_type', width: 90 },
    { title: '耗时', dataIndex: 'duration_ms', render: (v) => dur(v as number | null) },
    { title: '输出', dataIndex: 'output_bytes', render: (v) => v == null ? '-' : `${fmt(v as number)} B` },
    { title: '注入 Token', dataIndex: 'est_injected_tokens', render: (v) => v == null ? '-' : fmt(v as number) },
    { title: '结果', dataIndex: 'is_error', width: 90, render: (v: boolean, r) => v ? <Tag color="error">{String(r.error_type ?? '失败')}</Tag> : <Tag color="success">成功</Tag> },
  ];

  const trace = detail?.trace ?? {};
  return (
    <div className="obs-tab-content">
      <div className="obs-toolbar">
        <Segmented value={status} onChange={(v) => { setPage(1); setStatus(String(v)); }} options={[
          { label: '全部', value: 'all' }, { label: '完成', value: 'completed' }, { label: '失败', value: 'error' },
          { label: '中断', value: 'interrupted' }, { label: '超时', value: 'timeout' },
        ]} />
        <Text type="secondary">共 {total} 条执行记录 · 点击行查看完整链路</Text>
      </div>
      {error && <ErrorState message={error} retry={load} />}
      <Card size="small" className="obs-table-card">
        <Table<ObsTraceItem> rowKey="trace_id" loading={loading} columns={columns} dataSource={items} size="small" scroll={{ x: 1050 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该筛选条件下暂无 Trace" /> }}
          onRow={(record) => ({ onClick: () => openDetail(record.trace_id), style: { cursor: 'pointer' } })}
          pagination={{ current: page, pageSize: 20, total, showSizeChanger: false, showTotal: (t) => `共 ${t} 条`, onChange: setPage }} />
      </Card>
      <Drawer title={detail ? `Trace ${String(trace.trace_id).slice(0, 8)}` : 'Trace 明细'} width="min(960px, 92vw)" open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detailLoading ? <Skeleton active paragraph={{ rows: 12 }} /> : detail ? <>
          <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 3 }} className="obs-detail-summary" items={[
            { key: 'status', label: '状态', children: <StatusTag status={String(trace.status)} /> },
            { key: 'created', label: '开始时间', children: trace.created_at ? dayjs(String(trace.created_at)).format('YYYY-MM-DD HH:mm:ss') : '-' },
            { key: 'duration', label: '端到端耗时', children: dur(trace.duration_ms as number | null) },
            { key: 'llm', label: 'LLM 轮数', children: String(trace.iteration_count ?? 0) },
            { key: 'tools', label: '工具调用', children: String(trace.tool_call_count ?? 0) },
            { key: 'tokens', label: '总 Token', children: fmt(trace.total_tokens as number) },
            { key: 'session', label: '会话 ID', span: 3, children: <Text copyable>{String(trace.session_id ?? '-')}</Text> },
          ]} />
          <Card size="small" title={`LLM 调用 (${detail.llm_calls.length})`} className="obs-detail-card"><Table rowKey="call_order" columns={llmCols} dataSource={detail.llm_calls} size="small" pagination={false} scroll={{ x: 760 }} /></Card>
          <Card size="small" title={`工具调用 (${detail.tool_calls.length})`} className="obs-detail-card"><Table rowKey="call_order" columns={toolCols} dataSource={detail.tool_calls} size="small" pagination={false} scroll={{ x: 700 }} /></Card>
        </> : <Empty description="Trace 明细加载失败" />}
      </Drawer>
    </div>
  );
}

// ---- Aggregate reports ----

function StatsTab({ range }: { range: ObsRangeQuery }) {
  const [by, setBy] = useState<'model' | 'agent' | 'user' | 'tenant'>('model');
  const [metric, setMetric] = useState<'tokens' | 'count' | 'error' | 'duration'>('tokens');
  const [items, setItems] = useState<ObsDistributionItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    observabilityApi.stats({ ...range, by, metric }).then(setItems).finally(() => setLoading(false));
  }, [range, by, metric]);

  const field = { tokens: 'total_tokens', count: 'request_count', error: 'error_count', duration: 'avg_duration_ms' }[metric];
  const metricName = { tokens: 'Token', count: '调用数', error: '错误数', duration: '平均耗时' }[metric];
  return (
    <div className="obs-tab-content">
      <div className="obs-toolbar obs-toolbar--wrap">
        <Space wrap>
          <Segmented value={by} onChange={(v) => setBy(v as typeof by)} options={[
            { label: '按模型', value: 'model' }, { label: '按智能体', value: 'agent' }, { label: '按用户', value: 'user' }, { label: '按租户', value: 'tenant' },
          ]} />
          <Segmented value={metric} onChange={(v) => setMetric(v as typeof metric)} options={[
            { label: 'Token', value: 'tokens' }, { label: '调用数', value: 'count' }, { label: '错误数', value: 'error' }, { label: '平均耗时', value: 'duration' },
          ]} />
        </Space>
        <Text type="secondary">最多展示前 20 项，表格保留完整统计口径</Text>
      </div>
      {loading ? <Skeleton active paragraph={{ rows: 10 }} /> : !items.length ? <Empty description="该时间范围内暂无聚合数据" /> : <>
        <Card size="small" title={`${metricName}分布`} className="obs-chart-card">
          <Bar data={items.slice(0, 20)} xField={field} yField="label" height={Math.max(300, items.slice(0, 20).length * 38)} color="#1677ff"
            scale={{ x: { nice: true } }} axis={{ x: { title: false }, y: { title: false, labelAutoHide: false } }} label={{ text: field, position: 'right', formatter: (v: number) => metric === 'duration' ? dur(v) : fmt(v) }}
            tooltip={{ items: [{ channel: 'x', name: metricName, valueFormatter: (value: unknown) => metric === 'duration' ? dur(Number(value)) : fmt(Number(value)) }] }} />
        </Card>
        <Card size="small" className="obs-table-card obs-table-card--spaced">
          <Table<ObsDistributionItem> rowKey="key" size="small" dataSource={items} pagination={{ pageSize: 20, hideOnSinglePage: true }} columns={[
            { title: '维度', dataIndex: 'label', ellipsis: true },
            { title: '调用数', dataIndex: 'request_count', align: 'right', render: fmt },
            { title: 'Token', dataIndex: 'total_tokens', align: 'right', render: fmt },
            { title: '错误数', dataIndex: 'error_count', align: 'right', render: fmt },
            { title: '错误率', key: 'rate', align: 'right', render: (_, r) => r.request_count ? `${(r.error_count / r.request_count * 100).toFixed(2)}%` : '-' },
            { title: '平均耗时', dataIndex: 'avg_duration_ms', align: 'right', render: dur },
          ]} />
        </Card>
      </>}
    </div>
  );
}

// ---- Tool ranking ----

function ToolsTab({ range }: { range: ObsRangeQuery }) {
  const { window } = range;
  const [metric, setMetric] = useState<'count' | 'duration' | 'error' | 'tokens'>('count');
  const [items, setItems] = useState<ObsToolRankItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [trend, setTrend] = useState<{ ts: string; request_count: number; error_count: number }[]>([]);
  const [trendLoading, setTrendLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    observabilityApi.toolRanking({ ...range, metric, top: 20 }).then(setItems).finally(() => setLoading(false));
  }, [range, metric]);

  useEffect(() => { setSelected(null); setTrend([]); }, [range]);

  const pick = (tool: string) => {
    setSelected(tool);
    setTrendLoading(true);
    const granularity = window === '1h' ? 'minute' : window === '7d' ? 'day' : 'hour';
    observabilityApi.toolTrend(tool, granularity, range).then(setTrend).finally(() => setTrendLoading(false));
  };

  const trendData = useMemo(() => trend.flatMap((point) => [
    { ts: point.ts, value: point.request_count, metric: '调用数' },
    { ts: point.ts, value: point.error_count, metric: '错误数' },
  ]), [trend]);

  return (
    <div className="obs-tab-content">
      <div className="obs-toolbar">
        <Segmented value={metric} onChange={(v) => setMetric(v as typeof metric)} options={[
          { label: '调用次数', value: 'count' }, { label: '平均耗时', value: 'duration' }, { label: '错误率', value: 'error' }, { label: '注入 Token', value: 'tokens' },
        ]} />
        <Text type="secondary">点击工具查看当前时间范围内趋势</Text>
      </div>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={selected ? 13 : 24}>
          <Card size="small" className="obs-table-card">
            <Table<ObsToolRankItem> rowKey="tool_name" size="small" loading={loading} dataSource={items} pagination={false}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工具调用" /> }}
              onRow={(r) => ({ onClick: () => pick(r.tool_name), className: selected === r.tool_name ? 'obs-selected-row' : '', style: { cursor: 'pointer' } })}
              columns={[
                { title: '排名', key: 'rank', width: 70, render: (_, __, index) => index + 1 },
                { title: '工具', dataIndex: 'tool_name', ellipsis: true },
                { title: '调用', dataIndex: 'request_count', align: 'right', render: fmt },
                { title: '错误率', dataIndex: 'error_rate', align: 'right', render: (v: number) => <Text type={v > 20 ? 'danger' : undefined}>{v}%</Text> },
                { title: '平均 / P95 耗时', key: 'duration', align: 'right', render: (_, r) => `${dur(r.avg_duration_ms)} / ${dur(r.p95_duration_ms)}` },
                { title: '注入 Token', dataIndex: 'total_injected_tokens', align: 'right', render: fmt },
              ]} />
          </Card>
        </Col>
        {selected && <Col xs={24} xl={11}>
          <Card size="small" title={`${selected} · 调用趋势`} extra={<Button type="link" size="small" onClick={() => setSelected(null)}>收起</Button>} className="obs-chart-card">
            {trendLoading ? <Skeleton active /> : !trend.some((p) => p.request_count) ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势数据" /> :
              <Line data={trendData} xField="ts" yField="value" colorField="metric" height={300} smooth scale={{ x: { tickCount: 6 }, color: { range: ['#1677ff', '#dc2626'] } }} axis={{ x: { title: false, labelAutoRotate: false, labelFormatter: (value: string) => localTime(value, window) }, y: { title: false } }}
                tooltip={{ title: (datum: { ts: string }) => tooltipTime(datum.ts, window), items: [(datum: { metric: string; value: number }) => ({ name: datum.metric, value: fmt(datum.value) })] }} />}
          </Card>
        </Col>}
      </Row>
    </div>
  );
}

// ---- Conversation quality ----

function QualityTab({ range }: { range: ObsRangeQuery }) {
  const { window } = range;
  const [data, setData] = useState<ObsQuality | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    observabilityApi.quality(range).then(setData).finally(() => setLoading(false));
  }, [range]);

  if (loading) return <Skeleton active paragraph={{ rows: 10 }} />;
  if (!data || !data.trace_count) return <Empty description="该时间范围内暂无对话执行" />;
  const successRate = data.trace_count ? data.success_count / data.trace_count * 100 : 0;
  const executions = data.daily.map((d) => ({ ts: d.ts, value: d.trace_count }));
  const tokens = data.daily.map((d) => ({ ts: d.ts, value: d.total_tokens }));
  return (
    <div className="obs-tab-content">
      <Row gutter={[12, 12]}>
        <Col xs={12} md={8} xl={4}><StatCard title="执行次数" value={fmt(data.trace_count)} hint="Agent 完整执行" /></Col>
        <Col xs={12} md={8} xl={4}><StatCard title="成功率" value={successRate.toFixed(2)} suffix="%" tone={successRate < 95 ? 'warning' : 'success'} hint={`${data.success_count} 次成功`} /></Col>
        <Col xs={12} md={8} xl={4}><StatCard title="失败 / 中断" value={`${data.error_count} / ${data.interrupted_count}`} tone={data.error_count ? 'danger' : 'default'} hint="失败与中断次数" /></Col>
        <Col xs={12} md={8} xl={4}><StatCard title="平均端到端耗时" value={dur(data.avg_duration_ms)} hint="单次执行平均值" /></Col>
        <Col xs={12} md={8} xl={4}><StatCard title="平均 Token" value={fmt(data.avg_tokens_per_trace)} hint={`平均 ${data.avg_llm_calls ?? 0} 轮 LLM`} /></Col>
        <Col xs={12} md={8} xl={4}><StatCard title="上下文压缩" value={fmt(data.compress_count)} tone={data.compress_count ? 'warning' : 'default'} hint={`节省 ${fmt(data.saved_tokens)} Token`} /></Col>
      </Row>
      <Row gutter={[16, 16]} className="obs-quality-charts">
        <Col xs={24} xl={12}><Card size="small" title="执行量趋势" className="obs-chart-card"><Line data={executions} xField="ts" yField="value" height={240} color="#1677ff" smooth scale={{ x: { tickCount: 6 } }} axis={{ x: { title: false, labelAutoRotate: false, labelFormatter: (value: string) => localTime(value, window) }, y: { title: false } }} tooltip={{ title: (datum: { ts: string }) => tooltipTime(datum.ts, window), items: [{ channel: 'y', name: '执行次数', valueFormatter: (value: unknown) => fmt(Number(value)) }] }} /></Card></Col>
        <Col xs={24} xl={12}><Card size="small" title="Token 消耗趋势" className="obs-chart-card"><Bar data={tokens} xField="ts" yField="value" height={240} color="#0891b2" axis={{ x: { title: false, labelAutoRotate: false, labelFormatter: (value: string) => localTime(value, window) }, y: { title: false, labelFormatter: fmt } }} tooltip={{ title: (datum: { ts: string }) => tooltipTime(datum.ts, window), items: [{ channel: 'y', name: 'Token 消耗', valueFormatter: (value: unknown) => fmt(Number(value)) }] }} /></Card></Col>
      </Row>
    </div>
  );
}

export default function ObservabilityPage() {
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';
  const [rangeMode, setRangeMode] = useState<ObsWindow | 'custom'>('24h');
  const [customRange, setCustomRange] = useState<[Dayjs, Dayjs]>(() => [
    dayjs().subtract(24, 'hour'),
    dayjs(),
  ]);
  const [tab, setTab] = useState('overview');
  const isDark = useThemeStore((s) => s.resolvedTheme()) === 'dark';
  const range = useMemo<ObsRangeQuery>(() => {
    if (rangeMode !== 'custom') return { window: rangeMode };
    const hours = customRange[1].diff(customRange[0], 'hour', true);
    const bucketWindow: ObsWindow = hours <= 2 ? '1h' : hours <= 36 ? '24h' : '7d';
    return {
      window: bucketWindow,
      start: customRange[0].toISOString(),
      end: customRange[1].toISOString(),
    };
  }, [customRange, rangeMode]);

  if (!isAdmin) return <Result status="403" title="403" subTitle="需要管理员权限访问可观测页" />;
  return (
    <div className={`obs-page${isDark ? ' obs-page--dark' : ''}`}>
      <div className="obs-page-header">
        <div><Title level={3}>大模型可观测性</Title><Text type="secondary">从模型请求到工具执行，定位性能、错误与上下文消耗</Text></div>
        <div className="obs-time-control">
          <Segmented value={rangeMode} onChange={(v) => setRangeMode(v as ObsWindow | 'custom')} options={[
            { label: '近 1 小时', value: '1h' },
            { label: '近 24 小时', value: '24h' },
            { label: '近 7 天', value: '7d' },
            { label: '自定义', value: 'custom' },
          ]} />
          {rangeMode === 'custom' && (
            <DatePicker.RangePicker
              value={customRange}
              showTime={{ format: 'HH:mm' }}
              format="YYYY-MM-DD HH:mm"
              allowClear={false}
              presets={[
                { label: '今天', value: [dayjs().startOf('day'), dayjs()] },
                { label: '过去 3 天', value: [dayjs().subtract(3, 'day'), dayjs()] },
                { label: '过去 30 天', value: [dayjs().subtract(30, 'day'), dayjs()] },
              ]}
              onChange={(values) => {
                if (values?.[0] && values[1]) setCustomRange([values[0], values[1]]);
              }}
            />
          )}
        </div>
      </div>
      <Tabs activeKey={tab} onChange={setTab} destroyOnHidden items={[
        { key: 'overview', label: '实时监控', children: <OverviewTab range={range} /> },
        { key: 'traces', label: 'Trace 明细', children: <TracesTab range={range} /> },
        { key: 'stats', label: '聚合报表', children: <StatsTab range={range} /> },
        { key: 'tools', label: '工具排行', children: <ToolsTab range={range} /> },
        { key: 'quality', label: '对话质量', children: <QualityTab range={range} /> },
      ]} />
    </div>
  );
}
