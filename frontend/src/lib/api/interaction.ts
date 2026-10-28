import type { ConfirmationRequest, ConfirmationResponse } from '../types';
import { API_BASE, request } from './transport';
import { tokenStorage } from '../auth';

// ---- Confirmations (AskUserQuestion) ----

export const confirmationsApi = {
  /** Submit a response to a pending confirmation */
  respond(confirmationId: string, response: ConfirmationResponse) {
    return request<{ success: boolean; message: string }>(
      `/confirmations/${confirmationId}/respond`,
      {
        method: 'POST',
        body: JSON.stringify(response),
      },
    );
  },

  /** Get all pending confirmations for a session (page refresh recovery) */
  getPending(sessionId: string) {
    return request<ConfirmationRequest[]>(
      `/confirmations/sessions/${sessionId}/pending`,
    );
  },
};

// ---- UI Actions (页内浏览器操作回包, docs/22-浏览器页面自动化) ----

export interface UiActionRespondPayload {
  status: 'ok' | 'error' | 'cancelled';
  result?: Record<string, unknown>;
  error?: string;
  warning?: string;
  snapshot_version?: number;
  dangerous_refs?: string[];
  delta_snapshot?: string;
  page_context?: import('@/lib/types').PageContext;
  image?: string;
}

export const uiActionsApi = {
  /** Report the execution result of a pending UI action */
  respond(actionId: string, payload: UiActionRespondPayload) {
    return request<{ success: boolean; message: string }>(
      `/ui-actions/${actionId}/respond`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  },

  /** Cancel all pending UI actions + orphaned confirmations for a session */
  cancelAll(sessionId: string) {
    return request<{ cancelled: number; confirmations_cancelled: number }>(
      `/ui-actions/sessions/${sessionId}/cancel_all`,
      { method: 'POST' },
    );
  },

  /** cancel_all via sendBeacon — for tab unload (beforeunload) */
  cancelAllBeacon(sessionId: string) {
    const token = tokenStorage.getAccess() || '';
    const blob = new Blob([], { type: 'application/json' });
    navigator.sendBeacon(
      `${API_BASE}/ui-actions/sessions/${sessionId}/cancel_all?token=${encodeURIComponent(token)}`,
      blob,
    );
  },
};
