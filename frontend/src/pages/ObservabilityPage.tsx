import { useCallback, useEffect, useState } from 'react';
import { Bar, Line } from '@ant-design/plots';
import {
  Card,
  Col,
  Drawer,
  Empty,
  Result,
  Row,
  Segmented,
  Skeleton,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';

import {
  observabilityApi,
  type ObsDistributionItem,
  type ObsOverview,
  type ObsQuality,
  type ObsToolRankItem,
  type ObsTraceDetail,
  type ObsTraceItem,
  type ObsWindow,
} from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { useThemeStore } from '@/stores/themeStore';

const { Title, Text } = Typography;

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function dur(ms: number | null): string {
  if (ms == null) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  completed: { color: 'success', label: '完成' },
  error: { color: 'error', label: '失败' },
  interrupted: { color: 'default', label: '中断' },
  timeout: { color: 'warning', label: '超时' },
};

function StatusTag({ status }: { status: string }) {
  const meta = STATUS_MAP[status] ?? { color: 'default', label: status };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function StatCard({
  title,
  value,
  suffix,
  danger,
  warning,
}: {
  title: string;
  value: string | number;
  suffix?: string;
  danger?: boolean;
  warning?: boolean;
}) {
  const color = danger ? '#DC2626' : warning ? '#D97706' : undefined;
  return (
    <Card size="small" style={{ borderTop: color ? `3px solid ${color}` : undefined }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {title}
      </Text>
      <div
        style={{
          fontSize: 26,
          fontWeight: 600,
          fontFamily: "'Fira Code', monospace",
          color: color ?? undefined,
          marginTop: 4,
        }}
      >
        {value}
        {suffix && <span style={{ fontSize: 14, marginLeft: 2 }}>{suffix}</span>}
      </div>
    </Card>
  );
}

function lineProps(data: { ts: string; value: number }[], color: string, threshold?: number) {
  return {
    data,
    xField: 'ts',
    yField: 'value',
    height: 200,
    smooth: true,
    color,
    lineStyle: { lineWidth: 2 },
    axis: { x: { label: { formatter: (t: string) => dayjs(t).format('HH:mm') } } },
    annotations: threshold
      ? [
          {
            type: 'line' as const,
            start: ['min', threshold],
            end: ['max', threshold],
            style: { stroke: '#DC2626', lineDash: [4, 4] },
          },
        ]
      : [],
  };
}

// ---- Overview ----

function OverviewTab({ window }: { window: ObsWindow }) {
  const [data, setData] = useState<ObsOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    observabilityApi
      .overview(window)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [window]);

  if (loading) return <Skeleton active paragraph={{ rows: 8 }} />;
  if (!data) return <Empty description="暂无数据" />;

  const c = data.cards;
  const s = data.series;
  return (
    <>
      <Row gutter={[16, 16]}>
        <Col span={4}>
          <StatCard title="LLM 调用" value={fmt(c.llm_requests)} />
        </Col>
        <Col span={4}>
          <StatCard title="工具调用" value={fmt(c.tool_requests)} />
        </Col>
        <Col span={4}>
          <StatCard title="LLM 失败率" value={c.llm_error_rate} suffix="%" danger={c.llm_error_rate > 5} />
        </Col>
        <Col span={4}>
          <StatCard title="工具错误率" value={c.tool_error_rate} suffix="%" danger={c.tool_error_rate > 20} />
        </Col>
        <Col span={4}>
          <StatCard title="平均 TTFT" value={dur(c.avg_ttft_ms)} />
        </Col>
        <Col span={4}>
          <StatCard title="Token 消耗" value={fmt(c.total_tokens)} />
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={8}>
          <Card size="small" title="LLM 调用量">
            <Line {...lineProps(s.llm_requests ?? [], '#2563EB')} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="LLM 失败率">
            <Line {...lineProps(s.llm_error_rate ?? [], '#DC2626', 5)} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="工具调用量">
            <Line {...lineProps(s.tool_requests ?? [], '#4F46E5')} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="工具失败率">
            <Line {...lineProps(s.tool_error_rate ?? [], '#EA580C', 20)} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="Token 消耗速率">
            <Line {...lineProps(s.tokens ?? [], '#0891B2')} />
          </Card>
        </Col>
      </Row>
    </>
  );
}

// ---- Traces ----

function TracesTab({ window }: { window: ObsWindow }) {
  const [items, setItems] = useState<ObsTraceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<ObsTraceDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    observabilityApi
      .traces({
        window,
        page,
        page_size: 20,
        status: status === 'all' ? undefined : status,
      })
      .then((d) => {
        setItems(d.items);
        setTotal(d.total);
      })
      .finally(() => setLoading(false));
  }, [window, page, status]);

  useEffect(() => load(), [load]);

  const openDetail = (id: string) => {
    observabilityApi.trace(id).then(setDetail);
    setDetailOpen(true);
  };

  const columns: ColumnsType<ObsTraceItem> = [
    {
      title: '执行 ID',
      dataIndex: 'trace_id',
      width: 110,
      render: (v: string) => <Text code>{v.slice(0, 8)}</Text>,
    },
    { title: '时间', dataIndex: 'created_at', width: 170, render: (v: string) => dayjs(v).format('MM-DD HH:mm:ss') },
    { title: '会话', dataIndex: 'session_title', ellipsis: true, render: (v: string | null) => v ?? '-' },
    { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <StatusTag status={v} /> },
    { title: 'LLM 轮数', dataIndex: 'iteration_count', width: 100, align: 'right' },
    { title: '工具数', dataIndex: 'tool_call_count', width: 90, align: 'right' },
    { title: '总 Token', dataIndex: 'total_tokens', width: 110, align: 'right', render: (v: number) => fmt(v) },
    { title: '耗时', dataIndex: 'duration_ms', width: 100, align: 'right', render: (v: number | null) => dur(v) },
  ];

  const llmCols: ColumnsType<Record<string, unknown>> = [
    { title: '#', dataIndex: 'call_order', width: 50 },
    { title: '模型', dataIndex: 'model', ellipsis: true },
    { title: 'Token', render: (_, r) => `${r.total_tokens ?? 0} (in ${r.prompt_tokens ?? 0})` },
    { title: 'TTFT', dataIndex: 'ttft_ms', render: (v) => dur(v as number | null) },
    { title: '耗时', dataIndex: 'duration_ms', render: (v) => dur(v as number | null) },
    {
      title: '状态',
      dataIndex: 'final_status',
      width: 90,
      render: (v: string, r) =>
        v === 'failed' ? <Tag color="error">{String(r.error_type ?? 'failed')}</Tag> : <Tag color="success">成功</Tag>,
    },
    { title: 'Stop', dataIndex: 'stop_reason', width: 90 },
  ];

  const toolCols: ColumnsType<Record<string, unknown>> = [
    { title: '#', dataIndex: 'call_order', width: 50 },
    { title: '工具', dataIndex: 'tool_name', ellipsis: true },
    { title: '类型', dataIndex: 'exec_type', width: 90 },
    { title: '耗时', dataIndex: 'duration_ms', render: (v) => dur(v as number | null) },
    { title: '注入 Token', dataIndex: 'est_injected_tokens', render: (v) => (v ? fmt(v as number) : '-') },
    {
      title: '状态',
      dataIndex: 'is_error',
      width: 90,
      render: (v: boolean, r) =>
        v ? <Tag color="error">{String(r.error_type ?? 'error')}</Tag> : <Tag color="success">成功</Tag>,
    },
  ];

  return (
    <>
      <Segmented
        value={status}
        onChange={(v) => {
          setPage(1);
          setStatus(v as string);
        }}
        options={[
          { label: '全部', value: 'all' },
          { label: '完成', value: 'completed' },
          { label: '失败', value: 'error' },
          { label: '中断', value: 'interrupted' },
          { label: '超时', value: 'timeout' },
        ]}
        style={{ marginBottom: 16 }}
      />
      <Table<ObsTraceItem>
        rowKey="trace_id"
        loading={loading}
        columns={columns}
        dataSource={items}
        size="small"
        scroll={{ x: 1000 }}
        onRow={(r) => ({
          onClick: () => openDetail(r.trace_id),
          style: { cursor: 'pointer' },
        })}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          showTotal: (t) => `共 ${t} 条`,
          onChange: setPage,
        }}
      />
      <Drawer
        title={`Trace ${detail ? detail.trace.trace_id : ''}`}
        width={760}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      >
        {detail ? (
          <>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={6}><Statistic title="状态" valueRender={() => <StatusTag status={detail.trace.status as string} />} /></Col>
              <Col span={6}><Statistic title="LLM 轮数" value={detail.trace.iteration_count as number} /></Col>
              <Col span={6}><Statistic title="工具数" value={detail.trace.tool_call_count as number} /></Col>
              <Col span={6}><Statistic title="总 Token" value={fmt(detail.trace.total_tokens as number)} /></Col>
            </Row>
            <Card size="small" title="LLM 调用" style={{ marginBottom: 16 }}>
              <Table rowKey="id" columns={llmCols} dataSource={detail.llm_calls} size="small" pagination={false} />
            </Card>
            <Card size="small" title="工具调用">
              <Table rowKey="id" columns={toolCols} dataSource={detail.tool_calls} size="small" pagination={false} />
            </Card>
          </>
        ) : (
          <Skeleton active />
        )}
      </Drawer>
    </>
  );
}

// ---- Stats ----

function StatsTab({ window }: { window: ObsWindow }) {
  const [by, setBy] = useState<'model' | 'agent' | 'user' | 'tenant'>('model');
  const [metric, setMetric] = useState('tokens');
  const [items, setItems] = useState<ObsDistributionItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    observabilityApi
      .stats({ window, by, metric })
      .then(setItems)
      .finally(() => setLoading(false));
  }, [window, by, metric]);

  const yField = metric === 'tokens' ? 'total_tokens' : metric === 'error' ? 'error_count' : 'request_count';

  if (loading) return <Skeleton active paragraph={{ rows: 10 }} />;
  if (!items.length) return <Empty description="暂无数据" />;

  return (
    <>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col>
          <Segmented
            value={by}
            onChange={(v) => setBy(v as typeof by)}
            options={[
              { label: '按模型', value: 'model' },
              { label: '按智能体', value: 'agent' },
              { label: '按用户', value: 'user' },
              { label: '按租户', value: 'tenant' },
            ]}
          />
        </Col>
        <Col>
          <Segmented
            value={metric}
            onChange={(v) => setMetric(v as string)}
            options={[
              { label: 'Token', value: 'tokens' },
              { label: '调用数', value: 'count' },
              { label: '错误数', value: 'error' },
            ]}
          />
        </Col>
      </Row>
      <Card size="small">
        <Bar
          data={items.slice(0, 20)}
          xField="label"
          yField={yField}
          height={Math.max(320, items.slice(0, 20).length * 32)}
          color="#2563EB"
          axis={{ label: { autoHide: true } }}
          label={{}}
        />
      </Card>
      <Table<ObsDistributionItem>
        rowKey="key"
        size="small"
        style={{ marginTop: 16 }}
        dataSource={items}
        pagination={false}
        columns={[
          { title: '维度', dataIndex: 'label' },
          { title: '调用数', dataIndex: 'request_count', align: 'right' },
          { title: 'Token', dataIndex: 'total_tokens', align: 'right', render: (v) => fmt(v) },
          { title: '错误数', dataIndex: 'error_count', align: 'right' },
          { title: '平均耗时', dataIndex: 'avg_duration_ms', align: 'right', render: (v) => dur(v) },
        ]}
      />
    </>
  );
}

// ---- Tools ----

function ToolsTab({ window }: { window: ObsWindow }) {
  const [metric, setMetric] = useState('count');
  const [items, setItems] = useState<ObsToolRankItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [trend, setTrend] = useState<{ ts: string; request_count: number; error_count: number }[]>([]);
  const [trendLoading, setTrendLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    observabilityApi
      .toolRanking({ window, metric, top: 20 })
      .then(setItems)
      .finally(() => setLoading(false));
  }, [window, metric]);

  const pick = (tool: string) => {
    setSelected(tool);
    setTrendLoading(true);
    observabilityApi
      .toolTrend(tool, 'hour')
      .then(setTrend)
      .finally(() => setTrendLoading(false));
  };

  if (loading) return <Skeleton active paragraph={{ rows: 10 }} />;

  return (
    <>
      <Segmented
        value={metric}
        onChange={(v) => setMetric(v as string)}
        style={{ marginBottom: 16 }}
        options={[
          { label: '调用次数', value: 'count' },
          { label: '平均耗时', value: 'duration' },
          { label: '错误率', value: 'error' },
          { label: '注入 Token', value: 'tokens' },
        ]}
      />
      <Row gutter={16}>
        <Col span={selected ? 12 : 24}>
          <Table<ObsToolRankItem>
            rowKey="tool_name"
            size="small"
            loading={loading}
            dataSource={items}
            onRow={(r) => ({
              onClick: () => pick(r.tool_name),
              style: { cursor: 'pointer', background: selected === r.tool_name ? '#EFF6FF' : undefined },
            })}
            pagination={false}
            columns={[
              { title: '工具', dataIndex: 'tool_name', ellipsis: true },
              { title: '调用', dataIndex: 'request_count', align: 'right', render: (v) => fmt(v) },
              {
                title: '错误率',
                dataIndex: 'error_rate',
                align: 'right',
                render: (v: number) => (
                  <span style={{ color: v > 20 ? '#DC2626' : undefined }}>{v}%</span>
                ),
              },
              { title: '平均耗时', dataIndex: 'avg_duration_ms', align: 'right', render: (v) => dur(v) },
              {
                title: '注入 Token',
                dataIndex: 'total_injected_tokens',
                align: 'right',
                render: (v) => fmt(v),
              },
            ]}
          />
        </Col>
        {selected && (
          <Col span={12}>
            <Card
              size="small"
              title={`${selected} 趋势（近24小时）`}
              extra={
                <a
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelected(null);
                  }}
                >
                  收起
                </a>
              }
            >
              {trendLoading ? (
                <Skeleton active />
              ) : (
                <>
                  <Line
                    data={trend.map((p) => ({ ts: p.ts, value: p.request_count }))}
                    xField="ts"
                    yField="value"
                    height={160}
                    smooth
                    color="#2563EB"
                    axis={{ x: { label: { formatter: (t: string) => dayjs(t).format('HH:mm') } } }}
                  />
                  <Line
                    data={trend.map((p) => ({ ts: p.ts, value: p.error_count }))}
                    xField="ts"
                    yField="value"
                    height={120}
                    smooth
                    color="#DC2626"
                    axis={{ x: { label: { formatter: (t: string) => dayjs(t).format('HH:mm') } } }}
                  />
                </>
              )}
            </Card>
          </Col>
        )}
      </Row>
    </>
  );
}

// ---- Quality ----

function QualityTab({ window }: { window: ObsWindow }) {
  const [data, setData] = useState<ObsQuality | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    observabilityApi
      .quality(window)
      .then(setData)
      .finally(() => setLoading(false));
  }, [window]);

  if (loading) return <Skeleton active paragraph={{ rows: 8 }} />;
  if (!data) return <Empty description="暂无数据" />;

  return (
    <>
      <Row gutter={[16, 16]}>
        <Col span={4}><StatCard title="执行次数" value={fmt(data.trace_count)} /></Col>
        <Col span={4}><StatCard title="成功次数" value={fmt(data.success_count)} /></Col>
        <Col span={4}><StatCard title="对话平均耗时" value={dur(data.avg_duration_ms)} /></Col>
        <Col span={4}><StatCard title="平均 Token/执行" value={fmt(data.avg_tokens_per_trace ?? 0)} /></Col>
        <Col span={4}><StatCard title="平均 LLM 轮数" value={data.avg_llm_calls ?? 0} /></Col>
        <Col span={4}>
          <StatCard
            title="压缩触发"
            value={`${data.compress_count} 次`}
            warning={data.compress_count > 0}
          />
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Card size="small" title="执行量与成功率（按日）">
            <Line
              data={data.daily.map((d) => ({ ts: d.ts, value: d.trace_count }))}
              xField="ts"
              yField="value"
              height={220}
              smooth
              color="#2563EB"
              axis={{ x: { label: { formatter: (t: string) => dayjs(t).format('MM-DD') } } }}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="Token 消耗（按日）">
            <Bar
              data={data.daily.map((d) => ({ ts: d.ts, value: d.total_tokens }))}
              xField="ts"
              yField="value"
              height={220}
              color="#0891B2"
              axis={{ x: { label: { formatter: (t: string) => dayjs(t).format('MM-DD') } } }}
            />
          </Card>
        </Col>
      </Row>
    </>
  );
}

// ---- Main ----

export default function ObservabilityPage() {
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';
  const [window, setWindow] = useState<ObsWindow>('24h');
  const [tab, setTab] = useState('overview');
  const isDark = useThemeStore((s) => s.resolvedTheme()) === 'dark';

  if (!isAdmin) {
    return <Result status="403" title="403" subTitle="需要管理员权限访问观测页" />;
  }

  return (
    <div style={{ padding: 24, background: isDark ? undefined : '#F8FAFC', minHeight: '100vh' }}>
      <Row justify="space-between" align="middle" style={{ marginBottom: 8 }}>
        <Title level={4} style={{ margin: 0 }}>
          大模型可观测性
        </Title>
        <Segmented
          value={window}
          onChange={(v) => setWindow(v as ObsWindow)}
          options={[
            { label: '近1小时', value: '1h' },
            { label: '近24小时', value: '24h' },
            { label: '近7天', value: '7d' },
          ]}
        />
      </Row>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: 'overview', label: '实时监控', children: <OverviewTab window={window} /> },
          { key: 'traces', label: 'Trace 明细', children: <TracesTab window={window} /> },
          { key: 'stats', label: '聚合报表', children: <StatsTab window={window} /> },
          { key: 'tools', label: '工具排行', children: <ToolsTab window={window} /> },
          { key: 'quality', label: '对话质量', children: <QualityTab window={window} /> },
        ]}
      />
    </div>
  );
}
