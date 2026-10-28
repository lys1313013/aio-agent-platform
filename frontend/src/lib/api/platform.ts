import type { RemoteTool, RemoteToolCreate, RemoteToolUpdate, RemoteToolTestResult, Channel, ChannelCreate, ChannelUpdate, ChannelBinding } from '../types';
import { request } from './transport';

// ---- Webpage Artifacts (create_webpage 产物) ----

export const webpagesApi = {
  /** 换取一个带限时令牌的新鲜访问 URL（历史消息中的旧令牌会过期） */
  getAccess(pageId: string) {
    return request<{ url: string }>(`/webpages/${pageId}/access`, {
      method: 'POST',
    });
  },
};

// ---- Admin: System Config ----

export interface AutoTitleConfig {
  model_id: string | null;
  prompt: string;
  default_prompt: string;
}

export const systemConfigApi = {
  getAutoTitle() {
    return request<AutoTitleConfig>('/admin/system-config/auto-title');
  },

  updateAutoTitle(data: { model_id?: string | null; prompt?: string }) {
    return request<AutoTitleConfig>('/admin/system-config/auto-title', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
};

// ---- Admin: Remote Tools (HTTP Tools) ----

export const remoteToolsApi = {
  list() {
    return request<RemoteTool[]>('/admin/remote-tools');
  },

  get(id: string) {
    return request<RemoteTool>(`/admin/remote-tools/${id}`);
  },

  create(data: RemoteToolCreate) {
    return request<RemoteTool>('/admin/remote-tools', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: RemoteToolUpdate) {
    return request<RemoteTool>(`/admin/remote-tools/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<{ message: string }>(`/admin/remote-tools/${id}`, {
      method: 'DELETE',
    });
  },

  toggle(id: string) {
    return request<RemoteTool>(`/admin/remote-tools/${id}/toggle`, {
      method: 'PATCH',
    });
  },

  test(id: string, args: Record<string, unknown>) {
    return request<RemoteToolTestResult>(`/admin/remote-tools/${id}/test`, {
      method: 'POST',
      body: JSON.stringify({ arguments: args }),
    });
  },
};

// ---- Admin: Channels (IM 渠道接入) ----

export const channelsApi = {
  list() {
    return request<Channel[]>('/channels');
  },

  create(data: ChannelCreate) {
    return request<Channel>('/channels', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: ChannelUpdate) {
    return request<Channel>(`/channels/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  enable(id: string) {
    return request<Channel>(`/channels/${id}/enable`, { method: 'POST' });
  },

  disable(id: string) {
    return request<Channel>(`/channels/${id}/disable`, { method: 'POST' });
  },

  delete(id: string) {
    return request<void>(`/channels/${id}`, { method: 'DELETE' });
  },

  bindings(id: string) {
    return request<ChannelBinding[]>(`/channels/${id}/bindings`);
  },
};

// ---- User: Channel Bindings ----

export const channelBindingsApi = {
  list() {
    return request<ChannelBinding[]>('/channel-bindings');
  },

  bind(channelId: string, code: string) {
    return request<{ message: string }>('/channel-bindings/bind', {
      method: 'POST',
      body: JSON.stringify({ channel_id: channelId, code }),
    });
  },

  unbind(id: string) {
    return request<void>(`/channel-bindings/${id}`, { method: 'DELETE' });
  },
};
