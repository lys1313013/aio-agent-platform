import { request, queryString } from './transport';

// ---- Analytics ----

export type AnalyticsScope = 'mine' | 'global';

export interface AnalyticsSummary {
  sessions: number;
  messages: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  active_users: number | null;
  prev_sessions: number;
  prev_messages: number;
  prev_total_tokens: number;
  prev_request_count: number;
}

export interface AnalyticsTrendPoint {
  date: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  sessions: number;
}

export interface AnalyticsDistributionItem {
  key: string;
  label: string;
  sessions: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface AnalyticsDetailItem {
  date: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface AnalyticsQuery {
  start?: string;
  end?: string;
  scope?: AnalyticsScope;
}

export const analyticsApi = {
  summary(q: AnalyticsQuery) {
    return request<AnalyticsSummary>(`/analytics/summary${queryString(q)}`);
  },
  trend(q: AnalyticsQuery) {
    return request<AnalyticsTrendPoint[]>(`/analytics/trend${queryString(q)}`);
  },
  distribution(q: AnalyticsQuery & { by: 'model' | 'agent' | 'user' }) {
    return request<AnalyticsDistributionItem[]>(`/analytics/distribution${queryString(q)}`);
  },
  detail(q: AnalyticsQuery & { page?: number; page_size?: number }) {
    return request<{ items: AnalyticsDetailItem[]; total: number }>(
      `/analytics/detail${queryString(q)}`,
    );
  },
};
