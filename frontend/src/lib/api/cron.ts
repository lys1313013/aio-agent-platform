import type { CronJob, CronJobListResponse, CronJobRunListResponse } from '../types';
import { request } from './transport';

// ---- Cron Jobs ----

export const cronJobsApi = {
  list(params?: { limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<CronJobListResponse>(`/cron-jobs${qs ? `?${qs}` : ''}`);
  },

  get(id: string) {
    return request<CronJob>(`/cron-jobs/${id}`);
  },

  create(data: {
    name: string;
    request_id?: string;
    allow_duplicate?: boolean;
    agent_id?: string | null;
    model_id?: string | null;
    cron_expr?: string | null;
    run_at?: string | null;
    message?: string | null;
    task_config?: Record<string, unknown>;
    channel_id?: string | null;
    is_active?: boolean;
  }) {
    return request<CronJob>('/cron-jobs', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: {
    name?: string;
    agent_id?: string | null;
    model_id?: string | null;
    cron_expr?: string | null;
    run_at?: string | null;
    message?: string | null;
    task_config?: Record<string, unknown>;
    channel_id?: string | null;
    is_active?: boolean;
  }) {
    return request<CronJob>(`/cron-jobs/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<void>(`/cron-jobs/${id}`, { method: 'DELETE' });
  },

  runs(jobId: string, params?: { limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<CronJobRunListResponse>(`/cron-jobs/${jobId}/runs${qs ? `?${qs}` : ''}`);
  },

  runsAll(params?: { job_id?: string; status?: string; limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.job_id) searchParams.set('job_id', params.job_id);
    if (params?.status) searchParams.set('status', params.status);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<CronJobRunListResponse>(`/cron-jobs/runs${qs ? `?${qs}` : ''}`);
  },

  runNow(id: string) {
    return request<{ ok: boolean }>(`/cron-jobs/${id}/run`, { method: 'POST' });
  },
};
