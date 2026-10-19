import { act, createElement, type ComponentType } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { App } from 'antd';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAuthStore } from '@/stores/authStore';
import { agentsApi, channelsApi, cronJobsApi, graphKnowledgeApi, knowledgeApi, mcpApi, ragflowSettingsApi, remoteToolsApi } from '@/lib/api';
import CronJobsPage from './CronJobsPage';
import KnowledgeGraphPage from './KnowledgeGraphPage';
import KnowledgeManagementPage from './KnowledgeManagementPage';
import McpManagementPage from './McpManagementPage';
import RemoteToolManagementPage from './RemoteToolManagementPage';

let root: Root;
let container: HTMLDivElement;
const originalRole = useAuthStore.getState().role;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  useAuthStore.setState({ role: 'user' });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(cronJobsApi, 'list').mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(agentsApi, 'adminList').mockResolvedValue([]);
  vi.spyOn(channelsApi, 'list').mockResolvedValue([]);
  vi.spyOn(graphKnowledgeApi, 'list').mockResolvedValue([]);
  vi.spyOn(knowledgeApi, 'list').mockResolvedValue([]);
  vi.spyOn(ragflowSettingsApi, 'get').mockResolvedValue({ base_url: '', has_api_key: false });
  vi.spyOn(mcpApi, 'list').mockResolvedValue([]);
  vi.spyOn(remoteToolsApi, 'list').mockResolvedValue([]);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAuthStore.setState({ role: originalRole });
  vi.restoreAllMocks();
});

const pages: [string, ComponentType, { list: (...args: never[]) => unknown }][] = [
  ['cron jobs', CronJobsPage, cronJobsApi],
  ['knowledge graph', KnowledgeGraphPage, graphKnowledgeApi],
  ['knowledge management', KnowledgeManagementPage, knowledgeApi],
  ['MCP management', McpManagementPage, mcpApi],
  ['remote tools', RemoteToolManagementPage, remoteToolsApi],
];

it.each(pages)('%s keeps hook order stable across role changes and only fetches for admins', async (_name, Page, api) => {
  await act(async () => root.render(createElement(MemoryRouter, null,
    createElement(App, null, createElement(Page)),
  )));
  expect(api.list).not.toHaveBeenCalled();
  expect(ragflowSettingsApi.get).not.toHaveBeenCalled();
  expect(agentsApi.adminList).not.toHaveBeenCalled();
  expect(channelsApi.list).not.toHaveBeenCalled();

  // Rendering the content is allowed to mount/unmount, without changing the guard's hook order.
  await act(async () => useAuthStore.setState({ role: 'admin' }));
  expect(api.list).toHaveBeenCalledTimes(1);
  await act(async () => useAuthStore.setState({ role: 'user' }));
  expect(api.list).toHaveBeenCalledTimes(1);
  await act(async () => useAuthStore.setState({ role: 'superadmin' }));
  expect(api.list).toHaveBeenCalledTimes(2);
});
