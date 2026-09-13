import { describe, expect, it } from 'vitest';
import { buildTraceTree } from './traceTree';
const at = (ms: number) => new Date(1700000000000 + ms).toISOString();
describe('trace waterfall', () => {
  it('restores start times from completion logs and keeps parallel tools in the same round', () => {
    const { root, origin, duration } = buildTraceTree({ trace: { created_at: at(1000), duration_ms: 1000 }, llm_calls: [
      { model: 'a', created_at: at(200), duration_ms: 200 },
      { model: 'a', created_at: at(900), duration_ms: 200 },
    ], tool_calls: [
      { tool_name: 'one', created_at: at(600), duration_ms: 300, call_order: 1 },
      { tool_name: 'two', created_at: at(650), duration_ms: 350, call_order: 1, is_error: true },
    ] });
    expect(origin).toBe(Date.parse(at(0)));
    expect(duration).toBe(1000);
    expect(root.children![0].children!.map(n => n.name)).toEqual(['a', 'one', 'two']);
    expect(root.children![0].failed).toBe(true);
    expect(root.children![0].end).toBe(Date.parse(at(650)));
    expect(root.children![1].children).toHaveLength(1);
  });
  it('keeps tools without timing or a preceding model unassociated', () => {
    const { root } = buildTraceTree({ trace: {}, llm_calls: [], tool_calls: [{ tool_name: 'unknown' }, { tool_name: 'early', created_at: at(100), duration_ms: 100 }] });
    expect(root.children![0].kind).toBe('unknown');
    expect(root.children![0].children).toHaveLength(2);
    expect(root.children![0].children![0].start).toBeNull();
  });
  it('handles empty and zero-duration traces without invalid axis values', () => {
    const tree = buildTraceTree({ trace: { created_at: at(0), duration_ms: 0 }, llm_calls: [], tool_calls: [] });
    expect(tree.duration).toBe(1);
    expect(tree.root.children).toBeUndefined();
  });
});
