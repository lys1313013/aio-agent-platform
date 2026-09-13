import { useMemo, useState } from 'react';
import { Button, Descriptions, Space, Table, Tag, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { ObsTraceDetail } from '@/lib/api';
import { buildTraceTree, type TraceNode } from './traceTree';
import './TraceWaterfall.css';

const { Text } = Typography;
const durationText = (ms: number) => ms < 1000 ? `${Math.round(ms)}ms` : ms < 60000 ? `${(ms / 1000).toFixed(2)}s` : `${(ms / 60000).toFixed(2)}m`;
const colors = { agent: '#1677ff', round: '#8c8c8c', llm: '#722ed1', tool: '#13a8a8', unknown: '#d48806' };
const labels = { agent: 'Agent', round: '轮次', llm: 'LLM', tool: '工具', unknown: '待关联' };
const fieldLabels: Record<string, string> = { model: '模型', provider: '提供商', call_order: '记录序号', prompt_tokens: '输入 Token', completion_tokens: '输出 Token', total_tokens: '总 Token', cache_read_tokens: '缓存命中 Token', ttft_ms: '首 Token 延迟', duration_ms: '耗时', final_status: '结果', error_type: '错误类型', stop_reason: '停止原因', tool_name: '工具名称', exec_type: '执行类型', output_bytes: '输出字节', est_injected_tokens: '注入 Token', is_error: '是否失败', is_concurrent: '并发执行', is_truncated: '输出截断', agent_id: '智能体 ID', trace_id: 'Trace ID', session_id: '会话 ID', status: '状态', iteration_count: 'LLM 轮数', tool_call_count: '工具调用数' };

export default function TraceWaterfall({ detail }: { detail: ObsTraceDetail }) {
  const { token } = theme.useToken();
  const { root, origin, duration } = useMemo(() => buildTraceTree(detail), [detail]);
  const allKeys = useMemo(() => [root.key, ...(root.children ?? []).map(n => n.key)], [root]);
  const [expanded, setExpanded] = useState<React.Key[]>(allKeys);
  const [selected, setSelected] = useState<TraceNode>(root);
  const columns: ColumnsType<TraceNode> = [
    { title: '调用层级', key: 'name', width: 350, render: (_, node) => <span className="trace-node-name"><Tag color={colors[node.kind]}>{labels[node.kind]}</Tag><Text ellipsis title={node.name}>{node.name}</Text>{node.failed && <Tag color="error">异常</Tag>}</span> },
    { title: '耗时', key: 'duration', width: 100, align: 'right', render: (_, node) => node.start !== null && node.end !== null ? durationText(node.end - node.start) : '—' },
    { title: <div className="trace-axis">{[0, 1, 2, 3, 4].map(tick => <span key={tick}>{durationText(duration * tick / 4)}</span>)}</div>, key: 'timeline', render: (_, node) => <div className="trace-track">{node.start !== null && node.end !== null ? <div className="trace-bar" title={`${node.name} · +${durationText(node.start - origin)} · ${durationText(node.end - node.start)}`} style={{ left: `${(node.start - origin) / duration * 100}%`, width: `${Math.max(0, (node.end - node.start) / duration * 100)}%`, background: node.failed ? token.colorError : colors[node.kind], opacity: node.kind === 'round' ? 0.45 : 1 }} /> : <Text type="secondary">时间数据不足</Text>}</div> },
  ];
  return <section className="trace-waterfall" style={{ '--trace-border': token.colorBorderSecondary, '--trace-selected': token.controlItemBgActive } as React.CSSProperties}>
    <div className="trace-waterfall-toolbar"><Space><Text strong>执行链路</Text><Text type="secondary">{detail.llm_calls.length + detail.tool_calls.length} 次调用</Text></Space><Space><Button size="small" onClick={() => setExpanded(allKeys)}>全部展开</Button><Button size="small" onClick={() => setExpanded([])}>全部折叠</Button></Space></div>
    <Text type="secondary" className="trace-waterfall-note">轮次按完成时间推断；时间条由完成时间与耗时还原。并行调用会在时间轴上重叠。</Text>
    <Table<TraceNode> rowKey="key" size="small" columns={columns} dataSource={[root]} pagination={false} scroll={{ x: 900 }} expandable={{ expandedRowKeys: expanded, onExpandedRowsChange: keys => setExpanded([...keys]), indentSize: 20 }} onRow={node => ({ onClick: () => setSelected(node), style: { cursor: 'pointer' } })} rowClassName={node => node.key === selected.key ? 'trace-row-selected' : ''} />
    <div className="trace-node-detail"><Space><Tag color={colors[selected.kind]}>{labels[selected.kind]}</Tag><Text strong>{selected.name}</Text></Space>
      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: 'start', label: '相对开始', children: selected.start === null ? '—' : `+${durationText(selected.start - origin)}` },
        { key: 'end', label: '相对结束', children: selected.end === null ? '—' : `+${durationText(selected.end - origin)}` },
        ...Object.entries(selected.data).filter(([key, value]) => fieldLabels[key] && value != null).map(([key, value]) => ({ key, label: fieldLabels[key], children: <Text style={{ overflowWrap: 'anywhere' }}>{key.endsWith('_ms') ? durationText(Number(value)) : typeof value === 'boolean' ? value ? '是' : '否' : String(value)}</Text> })),
      ]} />
    </div>
  </section>;
}
