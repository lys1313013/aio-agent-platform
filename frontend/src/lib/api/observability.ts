import { request, queryString } from './transport';

// ---- Observability (大模型可观测性) ----

export interface ObsOverviewCards {
  llm_requests: number;
  tool_requests: number;
  llm_error_rate: number;
  tool_error_rate: number;
  avg_ttft_ms: number | null;
  p95_latency_ms: number | null;
  total_tokens: number;
  context_util_p95: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cache_read_tokens: number;
  cache_hit_rate: number;
}

export interface ObsOverview {
  cards: ObsOverviewCards;
  series: Record<string, { ts: string; value: number }[]>;
}

export interface ObsTraceItem {
  trace_id: string;
  session_id: string | null;
  agent_id: string | null;
  status: string;
  iteration_count: number;
  tool_call_count: number;
  total_tokens: number;
  duration_ms: number | null;
  created_at: string;
  session_title: string | null;
}

export interface ObsTracePage {
  items: ObsTraceItem[];
  total: number;
}

export interface ObsTraceDetail {
  trace: Record<string, unknown>;
  llm_calls: Record<string, unknown>[];
  tool_calls: Record<string, unknown>[];
}

export interface ObsDistributionItem {
  key: string;
  label: string;
  request_count: number;
  total_tokens: number;
  error_count: number;
  avg_duration_ms: number | null;
}

export interface ObsToolRankItem {
  tool_name: string;
  request_count: number;
  error_count: number;
  error_rate: number;
  avg_duration_ms: number | null;
  p95_duration_ms: number | null;
  total_injected_tokens: number;
}

export interface ObsToolTrendPoint {
  ts: string;
  request_count: number;
  error_count: number;
}

export interface ObsQuality {
  trace_count: number;
  success_count: number;
  error_count: number;
  interrupted_count: number;
  avg_duration_ms: number | null;
  avg_tokens_per_trace: number | null;
  avg_llm_calls: number | null;
  avg_tool_calls: number | null;
  compress_count: number;
  saved_tokens: number;
  daily: {
    ts: string;
    trace_count: number;
    success_count: number;
    total_tokens: number;
    avg_duration_ms: number | null;
    compress_count: number;
    saved_tokens: number;
  }[];
}

export type ObsWindow = '1h' | '24h' | '7d';

export interface ObsRangeQuery {
  window: ObsWindow;
  start?: string;
  end?: string;
}

export const observabilityApi = {
  overview(range: ObsRangeQuery) {
    return request<ObsOverview>(`/observability/overview${queryString(range)}`);
  },
  traces(q: {
    window?: ObsWindow;
    start?: string;
    end?: string;
    page?: number;
    page_size?: number;
    status?: string;
    agent_id?: string;
    session_id?: string;
  }) {
    return request<ObsTracePage>(`/observability/traces${queryString(q)}`);
  },
  trace(id: string) {
    return request<ObsTraceDetail>(`/observability/traces/${id}`);
  },
  stats(q: { window?: ObsWindow; start?: string; end?: string; by?: 'model' | 'agent' | 'user' | 'tenant'; metric?: string }) {
    return request<ObsDistributionItem[]>(`/observability/stats${queryString(q)}`);
  },
  toolRanking(q: { window?: ObsWindow; start?: string; end?: string; metric?: string; top?: number }) {
    return request<ObsToolRankItem[]>(`/observability/tool-ranking${queryString(q)}`);
  },
  toolTrend(tool: string, granularity: 'minute' | 'hour' | 'day', range: ObsRangeQuery) {
    return request<ObsToolTrendPoint[]>(
      `/observability/tools/${encodeURIComponent(tool)}/trend${queryString({ granularity, ...range })}`,
    );
  },
  quality(range: ObsRangeQuery) {
    return request<ObsQuality>(`/observability/quality${queryString(range)}`);
  },
};
