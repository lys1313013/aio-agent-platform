import type { PetPackage, PetVisibility, UserPet } from '../types';
import { request, apiFetch, uploadForm, downloadBlob } from './transport';
import { getAuthSignal, scopeAuthCallback } from '../authSession';
import { pollTaskSnapshots } from '../pollTaskSnapshots';
import { readJsonEvents } from './sse';

// ---- Pets ----

export const petsApi = {
  market() {
    return request<PetPackage[]>('/pets/market');
  },
  myPackages() {
    return request<PetPackage[]>('/pets/packages/mine');
  },
  myPets() {
    return request<UserPet[]>('/pets/mine');
  },
  active() {
    return request<UserPet | null>('/pets/active');
  },
  upload(
    file: File,
    rowMapping: Record<string, number>,
    visibility: PetVisibility = 'private',
  ): Promise<PetPackage> {
    const form = new FormData();
    form.append('file', file);
    form.append('row_mapping', JSON.stringify(rowMapping));
    form.append('visibility', visibility);
    return uploadForm('/pets/packages', form);
  },
  /** Authenticated spritesheet download for a local blob URL. */
  spritesheetBlob(packageId: string): Promise<Blob> {
    return downloadBlob(`/pets/packages/${packageId}/spritesheet`);
  },

  async download(packageId: string, filename: string): Promise<void> {
    const blob = await downloadBlob(`/pets/packages/${packageId}/download`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  },

  setVisibility(packageId: string, visibility: PetVisibility) {
    return request<PetPackage>(`/pets/packages/${packageId}/visibility`, {
      method: 'PUT',
      body: JSON.stringify({ visibility }),
    });
  },
  setRowMapping(packageId: string, rowMapping: Record<string, number>) {
    return request<PetPackage>(`/pets/packages/${packageId}/row-mapping`, {
      method: 'PUT',
      body: JSON.stringify({ row_mapping: rowMapping }),
    });
  },
  deletePackage(packageId: string) {
    return request<void>(`/pets/packages/${packageId}`, { method: 'DELETE' });
  },
  adopt(packageId: string) {
    return request<UserPet>(`/pets/${packageId}/adopt`, { method: 'POST' });
  },
  activate(userPetId: string) {
    return request<UserPet>(`/pets/${userPetId}/activate`, { method: 'POST' });
  },
  deactivate() {
    return request<void>('/pets/deactivate', { method: 'POST' });
  },
  remove(userPetId: string) {
    return request<void>(`/pets/${userPetId}`, { method: 'DELETE' });
  },
  interact(userPetId: string) {
    return request<UserPet>(`/pets/${userPetId}/interact`, { method: 'POST' });
  },
  /** 实例级绑定/解绑智能体（agentId 为 null 解绑，回退包级默认） */
  bindAgent(userPetId: string, agentId: string | null) {
    return request<UserPet>(`/pets/${userPetId}/agent`, {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId }),
    });
  },
  /** 包级默认人设 Agent（仅创建人） */
  setPackageDefaultAgent(packageId: string, agentId: string | null) {
    return request<PetPackage>(`/pets/packages/${packageId}/default-agent`, {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId }),
    });
  },
  /** 上传者改包级动作名 */
  setPackageActions(packageId: string, actions: Record<string, string>) {
    return request<PetPackage>(`/pets/packages/${packageId}/actions`, {
      method: 'PUT',
      body: JSON.stringify({ actions }),
    });
  },
  /** 领养者改实例级动作名 + 状态映射（stateMapping 不传则不改映射） */
  setPetActions(
    userPetId: string,
    aliases: Record<string, string>,
    stateMapping?: Record<string, number>,
  ) {
    return request<UserPet>(`/pets/${userPetId}/actions`, {
      method: 'PUT',
      body: JSON.stringify({ aliases, state_mapping: stateMapping ?? null }),
    });
  },
  /** 开启/复用宠物闲聊会话，返回 conversation_id；forceNew 时强制新建（旧会话保留） */
  petChat(userPetId: string, opts?: { forceNew?: boolean }) {
    const qs = opts?.forceNew ? '?force_new=true' : '';
    return request<{ conversation_id: string; agent_id: string | null }>(
      `/pets/${userPetId}/chat${qs}`,
      { method: 'POST' },
    );
  },
  /**
   * 智能气泡：正常时 SSE 流式返回 pet_action / text_delta / bubble_done 事件；
   * 未绑定/失败/超限时返回 fallback JSON（type: 'fallback'）。返回 close 函数。
   */
  bubble(
    userPetId: string,
    onEvent: (ev: {
      type: string;
      text?: string;
      name?: string;
      row?: number;
      fallback?: boolean;
      quota_exceeded?: boolean;
    }) => void,
  ): () => void {
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, getAuthSignal()]);
    const receive = scopeAuthCallback((event: Parameters<typeof onEvent>[0]) => {
      if (!signal.aborted) onEvent(event);
    });
    void (async () => {
      const response = await apiFetch(`/pets/${userPetId}/bubble`, {
        method: 'POST', signal,
      }, { authRetry: 'never' });
      if ((response.headers.get('content-type') || '').includes('text/event-stream')) {
        await readJsonEvents(response, receive, signal);
        receive({ type: 'closed' });
      } else {
        const data = await response.json();
        if (data?.fallback) {
          receive({ type: 'fallback', fallback: true, text: data.text ?? undefined, quota_exceeded: !!data.quota_exceeded });
        } else {
          receive({ type: 'error' });
        }
      }
    })().catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === 'AbortError')) receive({ type: 'error' });
    });
    return () => controller.abort();
  },
  /** Refresh the pet task snapshot without reserving an HTTP streaming connection. */
  watchActiveTasks(onEvent: (event: Record<string, unknown>) => void): () => void {
    return pollTaskSnapshots(
      (signal) => request<unknown[]>('/pets/active-tasks', { signal }),
      scopeAuthCallback(onEvent),
    );
  },
};
