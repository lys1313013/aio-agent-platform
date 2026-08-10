import { useEffect, useRef } from 'react';
import { GraphChart } from 'echarts/charts';
import { LegendComponent, TooltipComponent } from 'echarts/components';
import { init, use } from 'echarts/core';
import type { ECharts, EChartsCoreOption } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

use([GraphChart, LegendComponent, TooltipComponent, CanvasRenderer]);

export interface GraphCanvasNode {
  id: string;
  name: string;
  type: string;
  description?: string | null;
  isSeed?: boolean;
}

export interface GraphCanvasLink {
  id: string;
  /** Node id */
  source: string;
  /** Node id */
  target: string;
  relationType: string;
  description?: string | null;
}

interface GraphCanvasProps {
  nodes: GraphCanvasNode[];
  links: GraphCanvasLink[];
  height?: number | string;
  onNodeClick?: (node: GraphCanvasNode) => void;
}

const TYPE_COLORS: Record<string, string> = {
  人物: '#1677ff',
  组织: '#722ed1',
  产品: '#13c2c2',
  概念: '#52c41a',
  事件: '#fa8c16',
  地点: '#eb2f96',
  其他: '#8c8c8c',
};
const FALLBACK_COLORS = ['#f5222d', '#fa541c', '#faad14', '#a0d911', '#2f54eb', '#531dab', '#08979c', '#d4380d', '#c41d7f'];

function buildColorMap(types: string[]): Map<string, string> {
  const map = new Map<string, string>();
  let fallbackIdx = 0;
  for (const t of types) {
    const known = TYPE_COLORS[t];
    map.set(t, known ?? FALLBACK_COLORS[fallbackIdx++ % FALLBACK_COLORS.length]);
  }
  return map;
}

function escapeHtml(value: string): string {
  return value.replace(
    /[&<>'"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c] ?? c,
  );
}

/** Force-directed knowledge-graph canvas (ECharts), styled after the ontology-cowork graph page. */
export default function GraphCanvas({ nodes, links, height = 560, onNodeClick }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  const clickRef = useRef(onNodeClick);
  clickRef.current = onNodeClick;

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = init(containerRef.current);
    chartRef.current = chart;
    chart.on('click', (params) => {
      if (params.dataType === 'node' && clickRef.current) {
        clickRef.current((params.data as { raw: GraphCanvasNode }).raw);
      }
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || nodes.length === 0) return;

    const types = Array.from(new Set(nodes.map((n) => n.type || '其他')));
    const colorMap = buildColorMap(types);
    const nodeById = new Map(nodes.map((n) => [n.id, n]));
    const degree: Record<string, number> = {};
    links.forEach((l) => {
      degree[l.source] = (degree[l.source] ?? 0) + 1;
      degree[l.target] = (degree[l.target] ?? 0) + 1;
    });

    const option: EChartsCoreOption = {
      animationDurationUpdate: 350,
      color: types.map((t) => colorMap.get(t)!),
      tooltip: {
        confine: true,
        formatter: (params: unknown) => {
          const p = params as { dataType?: string; data?: Record<string, unknown> };
          const d = p.data ?? {};
          if (p.dataType === 'edge') {
            const desc = typeof d.description === 'string' && d.description ? `<br>${escapeHtml(d.description)}` : '';
            return `${escapeHtml(String(d.sourceName ?? ''))} <b>${escapeHtml(String(d.relationType ?? ''))}</b> ${escapeHtml(String(d.targetName ?? ''))}${desc}`;
          }
          let html = `<b>${escapeHtml(String(d.name ?? ''))}</b> <span style="color:#8c8c8c">${escapeHtml(String(d.type ?? ''))}</span>`;
          if (typeof d.description === 'string' && d.description) {
            html += `<br>${escapeHtml(d.description)}`;
          }
          return html;
        },
      },
      legend: {
        top: 8,
        left: 12,
        icon: 'circle',
        itemWidth: 10,
        itemGap: 14,
        textStyle: { fontSize: 12, color: '#595959' },
        data: types,
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories: types.map((t) => ({ name: t })),
          data: nodes.map((n) => ({
            id: n.id,
            name: n.name,
            category: types.indexOf(n.type || '其他'),
            symbolSize: n.isSeed ? 22 : Math.min(26, 12 + (degree[n.id] ?? 0) * 1.5),
            description: n.description ?? undefined,
            raw: n,
            label: { fontWeight: n.isSeed ? ('bold' as const) : ('normal' as const) },
            itemStyle: n.isSeed
              ? { borderColor: colorMap.get(n.type || '其他'), borderWidth: 3 }
              : undefined,
          })),
          links: links.map((l) => ({
            id: l.id,
            source: l.source,
            target: l.target,
            relationType: l.relationType,
            description: l.description ?? undefined,
            sourceName: nodeById.get(l.source)?.name ?? '',
            targetName: nodeById.get(l.target)?.name ?? '',
            label: { show: true, formatter: l.relationType, fontSize: 9, color: '#8c8c8c' },
          })),
          force: {
            repulsion: Math.min(1200, Math.max(300, nodes.length * 12)),
            edgeLength: [60, 160],
            gravity: 0.1,
            friction: 0.15,
          },
          label: { show: true, position: 'right', fontSize: 11, color: '#262626' },
          lineStyle: { color: '#d9d9d9', width: 1 },
          emphasis: {
            focus: 'adjacency',
            lineStyle: { width: 2.5 },
            label: { fontWeight: 'bold' },
          },
          scaleLimit: { min: 0.2, max: 5 },
        },
      ],
    };
    chart.setOption(option, true);
  }, [nodes, links]);

  return <div ref={containerRef} style={{ height, width: '100%' }} />;
}
