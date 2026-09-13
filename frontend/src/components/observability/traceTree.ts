import type { ObsTraceDetail } from '@/lib/api';

type Fields = Record<string, unknown>;
export interface TraceNode {
  key: string;
  name: string;
  kind: 'agent' | 'round' | 'llm' | 'tool' | 'unknown';
  start: number | null;
  end: number | null;
  failed: boolean;
  data: Fields;
  children?: TraceNode[];
}

// Recorder.created_at is the completion time, not the start time.
function interval(data: Fields) {
  const end = typeof data.created_at === 'string' ? Date.parse(data.created_at) : NaN;
  const duration = typeof data.duration_ms === 'number' && data.duration_ms >= 0 ? data.duration_ms : null;
  return { end: Number.isFinite(end) ? end : null, start: Number.isFinite(end) && duration !== null ? end - duration : null };
}

export function buildTraceTree(detail: ObsTraceDetail) {
  const leaf = (data: Fields, kind: 'llm' | 'tool', i: number): TraceNode => ({
    key: `${kind}-${i}`, kind, data, ...interval(data),
    name: String(kind === 'llm' ? data.model ?? 'LLM' : data.tool_name ?? '工具'),
    failed: data.final_status === 'failed' || data.is_error === true,
  });
  const llms = detail.llm_calls.map((data, i) => leaf(data, 'llm', i))
    .sort((a, b) => (a.start ?? Infinity) - (b.start ?? Infinity));
  const rounds: TraceNode[] = llms.map((llm, i) => ({
    key: `round-${i}`, kind: 'round', name: `第 ${i + 1} 轮`, start: llm.start, end: llm.end,
    failed: llm.failed, data: {}, children: [llm],
  }));
  const unmatched: TraceNode[] = [];
  detail.tool_calls.map((data, i) => leaf(data, 'tool', i)).forEach(tool => {
    // Only associate a tool with an already completed LLM; never invent a parent for missing timing.
    let parent = -1;
    if (tool.start !== null) llms.forEach((llm, i) => {
      if (llm.end !== null && llm.end <= tool.start!) parent = i;
    });
    if (parent < 0) unmatched.push(tool);
    else rounds[parent].children!.push(tool);
  });
  for (const round of rounds) {
    round.children!.sort((a, b) => (a.start ?? Infinity) - (b.start ?? Infinity));
    round.end = Math.max(...round.children!.map(node => node.end ?? -Infinity));
    if (!Number.isFinite(round.end)) round.end = null;
    round.failed = round.children!.some(node => node.failed);
  }
  if (unmatched.length) rounds.push({ key: 'unmatched', kind: 'unknown', name: '未关联工具', start: null, end: null, failed: unmatched.some(n => n.failed), data: {}, children: unmatched });
  const root: TraceNode = { key: 'agent', name: 'Agent 执行', kind: 'agent', ...interval(detail.trace), data: detail.trace, failed: ['error', 'timeout'].includes(String(detail.trace.status)), children: rounds.length ? rounds : undefined };
  const timed = [root, ...llms, ...rounds.flatMap(n => n.children ?? [])];
  const starts = timed.flatMap(n => n.start === null ? [] : [n.start]);
  const ends = timed.flatMap(n => n.end === null ? [] : [n.end]);
  const origin = starts.length ? Math.min(...starts) : 0;
  const duration = Math.max(1, ends.length ? Math.max(...ends) - origin : 1);
  return { root, origin, duration };
}
