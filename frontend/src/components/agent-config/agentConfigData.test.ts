import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { agentsApi, adminApi, skillsApi, toolsApi, mcpApi, knowledgeApi, graphKnowledgeApi } from '@/lib/api';
import type { Agent } from '@/lib/types';
import { useAuthStore } from '@/stores/authStore';
import { invalidateAuthSession } from '@/lib/authSession';
import { useAgentConfigData } from './useAgentConfigData';
import { useAgentDetails } from './useAgentDetails';
import AgentConfigSidebar from '../AgentConfigSidebar';
import { App } from 'antd';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/lib/api', () => ({
  agentsApi: { adminList: vi.fn(), adminUpdate: vi.fn(), get: vi.fn(), stats: vi.fn() },
  adminApi: { listModels: vi.fn() }, skillsApi: { list: vi.fn() }, toolsApi: { list: vi.fn() },
  mcpApi: { list: vi.fn() }, knowledgeApi: { list: vi.fn() }, graphKnowledgeApi: { list: vi.fn() },
  authApi: {}, settingsApi: {}, sessionsApi: { list: vi.fn() }, workspacesApi: {},
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const agent = (id: string, values: Partial<Agent> = {}) => ({
  id, name: id, model_id: null,
  enabled_tools: ['read', 'remote_old'], system_prompt: `${id} prompt`, child_ids: [], ...values
}) as Agent;
const success = vi.fn();
const error = vi.fn();
const onUpdated = vi.fn();
let root: Root;
let data: ReturnType<typeof useAgentConfigData>;
let details: ReturnType<typeof useAgentDetails>;

function DataHarness({ id }: { id: string; section?: string; }) {
  data = useAgentConfigData(id, { success, error, onUpdated });
  return null;
}
function DetailsHarness({ id }: { id: string; }) {
  details = useAgentDetails(id, error);
  return null;
}
async function render(id = 'A', section = 'prompt') {
  await act(async () => root.render(createElement(DataHarness, { id, section })));
}

beforeEach(() => {
  vi.clearAllMocks();
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  useAuthStore.setState({ tenantId: 'tenant-one' });
  vi.mocked(agentsApi.adminList).mockResolvedValue([agent('A'), agent('B')]);
  vi.mocked(agentsApi.adminUpdate).mockImplementation(async (id, changes) => agent(id, changes));
  vi.mocked(agentsApi.stats).mockResolvedValue({ total_sessions: 0, total_messages: 0, last_active_at: null });
  vi.mocked(adminApi.listModels).mockResolvedValue([]);
  vi.mocked(skillsApi.list).mockResolvedValue({ items: [], total: 0, category: null });
  vi.mocked(toolsApi.list).mockResolvedValue([{ name: 'read', category: 'file' }, { name: 'write', category: 'file' }] as Awaited<ReturnType<typeof toolsApi.list>>);
  vi.mocked(mcpApi.list).mockResolvedValue([]);
  vi.mocked(knowledgeApi.list).mockResolvedValue([]);
  vi.mocked(graphKnowledgeApi.list).mockResolvedValue([]);
  root = createRoot(document.createElement('div'));
});
afterEach(async () => { await act(async () => root.unmount()); });

describe('agent configuration scope and section drafts', () => {
  it('renders every extracted editor without reloading its shared options', async () => {
    const container = document.createElement('div');
    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(createElement(MemoryRouter, null,
      createElement(App, null, createElement(AgentConfigSidebar, { agentId: 'A' })))));
    expect(container.textContent).toContain('智能体概览');
    for (const [index, title] of ['系统提示词', '工具配置', 'MCP 服务', '知识库', '图谱知识库', '技能配置', '子智能体'].entries()) {
      await act(async () => (container.querySelectorAll('button')[index + 1] as HTMLButtonElement).click());
      expect(container.querySelector('.agent-config-editor')).not.toBeNull();
      expect(container.textContent).toContain(title);
    }
    expect(agentsApi.adminList).toHaveBeenCalledTimes(1);
  });

  it('loads once and keeps drafts when switching sections or saving another section', async () => {
    await render();
    await act(async () => { data.setSystemPrompt('unsaved prompt'); data.setSelectedSkillIds(['skill-1']); });
    await render('A', 'skills');
    await act(async () => data.saveSection('skills', { skill_ids: data.selectedSkillIds }));
    expect(data.systemPrompt).toBe('unsaved prompt');
    expect(data.agent?.system_prompt).toBe('A prompt');
    expect(data.agent?.skill_ids).toEqual(['skill-1']);
    expect(agentsApi.adminList).toHaveBeenCalledTimes(1);
    expect(adminApi.listModels).toHaveBeenCalledTimes(1);
  });

  it('discards a slow agent load after navigating to another agent', async () => {
    const old = deferred<Agent[]>();
    vi.mocked(agentsApi.adminList).mockReturnValueOnce(old.promise);
    await render('A');
    await render('B');
    await act(async () => old.resolve([agent('A', { system_prompt: 'stale' })]));
    expect(data.agent?.id).toBe('B');
    expect(data.systemPrompt).toBe('B prompt');
    expect(error).not.toHaveBeenCalled();
  });

  it('ignores a late save and callbacks captured by the previous agent', async () => {
    const old = deferred<Agent>();
    vi.mocked(agentsApi.adminUpdate).mockReturnValueOnce(old.promise);
    await render('A');
    const oldSave = data.saveSection;
    let pending!: Promise<void>;
    await act(async () => { pending = oldSave('prompt', { system_prompt: 'saved A' }); });
    await render('B');
    await act(async () => { old.resolve(agent('A', { system_prompt: 'saved A' })); await pending; });
    await act(async () => oldSave('prompt', { system_prompt: 'wrong owner' }));
    expect(data.agent?.id).toBe('B');
    expect(data.savingSections).toEqual({});
    expect(success).not.toHaveBeenCalled();
    expect(onUpdated).not.toHaveBeenCalled();
    expect(agentsApi.adminUpdate).toHaveBeenCalledTimes(1);
  });

  it('invalidates pending saves and options when the tenant changes', async () => {
    const old = deferred<Agent>();
    vi.mocked(agentsApi.adminUpdate).mockReturnValueOnce(old.promise);
    await render();
    let pending!: Promise<void>;
    await act(async () => { pending = data.saveSection('prompt', { system_prompt: 'old tenant' }); });
    vi.mocked(agentsApi.adminList).mockResolvedValue([agent('A', { system_prompt: 'new tenant' })]);
    await act(async () => useAuthStore.setState({ tenantId: 'tenant-two' }));
    await act(async () => { old.resolve(agent('A', { system_prompt: 'old tenant' })); await pending; });
    expect(data.systemPrompt).toBe('new tenant');
    expect(success).not.toHaveBeenCalled();
    expect(agentsApi.adminList).toHaveBeenCalledTimes(2);
  });

  it('does not apply old errors or continue child writes after logout', async () => {
    const old = deferred<Agent>();
    vi.mocked(agentsApi.adminList).mockResolvedValue([agent('A', { child_ids: ['B'] }), agent('B', { max_iterations: 5 })]);
    vi.mocked(agentsApi.adminUpdate).mockReturnValueOnce(old.promise);
    await render();
    await act(async () => data.setChildMaxIterations({ B: 7 }));
    let pending!: Promise<void>;
    await act(async () => { pending = data.saveChildrenSection(); });
    await act(async () => invalidateAuthSession());
    await act(async () => { old.resolve(agent('A')); await pending; });
    expect(agentsApi.adminUpdate).toHaveBeenCalledTimes(1);
    expect(success).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
  });

  it('invalidates old saves when explicitly reloading the same agent', async () => {
    const old = deferred<Agent>();
    vi.mocked(agentsApi.adminUpdate).mockReturnValueOnce(old.promise);
    await render();
    const oldSave = data.saveSection;
    let pending!: Promise<void>;
    await act(async () => { pending = data.saveSection('prompt', { system_prompt: 'stale save' }); });
    vi.mocked(agentsApi.adminList).mockResolvedValue([agent('A', { system_prompt: 'fresh load' })]);
    await act(async () => data.retryLoad());
    await act(async () => { old.resolve(agent('A', { system_prompt: 'stale save' })); await pending; });
    await act(async () => oldSave('prompt', { system_prompt: 'stale callback' }));
    expect(data.systemPrompt).toBe('fresh load');
    expect(data.agent?.system_prompt).toBe('fresh load');
    expect(onUpdated).not.toHaveBeenCalled();
    expect(agentsApi.adminUpdate).toHaveBeenCalledTimes(1);
  });

  it('serializes tools/MCP saves while persisting only the selected section draft', async () => {
    const first = deferred<Agent>();
    vi.mocked(agentsApi.adminUpdate).mockReturnValueOnce(first.promise);
    await render();
    await act(async () => { data.setEnabledTools(['write']); data.setEnabledMcpTools(['remote_new']); });
    let toolsSave!: Promise<void>;
    let mcpSave!: Promise<void>;
    await act(async () => {
      toolsSave = data.saveSection('tools', { enabled_tools: ['write', 'remote_new'] });
      mcpSave = data.saveSection('mcp', { enabled_tools: ['write', 'remote_new'], mcp_server_ids: ['server'] });
    });
    expect(agentsApi.adminUpdate).toHaveBeenCalledTimes(1);
    expect(agentsApi.adminUpdate).toHaveBeenNthCalledWith(1, 'A', { enabled_tools: ['write', 'remote_old'] });
    await act(async () => { first.resolve(agent('A', { enabled_tools: ['write', 'remote_old'] })); await Promise.all([toolsSave, mcpSave]); });
    expect(agentsApi.adminUpdate).toHaveBeenNthCalledWith(2, 'A', { enabled_tools: ['write', 'remote_new'], mcp_server_ids: ['server'] });
    expect(data.agent?.enabled_tools).toEqual(['write', 'remote_new']);
    expect(data.enabledMcpTools).toEqual(['remote_new']);
    expect(data.savedSections.tools).toBe(true);
    expect(data.savedSections.mcp).toBe(true);
  });
});

describe('parent chat agent details', () => {
  it('keeps the latest silent refresh when responses arrive out of order', async () => {
    const old = deferred<Agent>();
    vi.mocked(agentsApi.get).mockReturnValueOnce(old.promise).mockResolvedValueOnce(agent('A', { name: 'updated' }));
    await act(async () => root.render(createElement(DetailsHarness, { id: 'A' })));
    await act(async () => details.loadAgent(true));
    await act(async () => old.resolve(agent('A', { name: 'stale' })));
    expect(details.agent?.name).toBe('updated');
    expect(details.agentLoading).toBe(false);
  });

  it('ignores old callbacks and response errors after navigation', async () => {
    const old = deferred<Agent>();
    vi.mocked(agentsApi.get).mockReturnValueOnce(old.promise).mockResolvedValueOnce(agent('B'));
    await act(async () => root.render(createElement(DetailsHarness, { id: 'A' })));
    const oldReload = details.loadAgent;
    await act(async () => root.render(createElement(DetailsHarness, { id: 'B' })));
    await act(async () => old.reject(new Error('old request')));
    await act(async () => oldReload(true));
    expect(details.agent?.id).toBe('B');
    expect(error).not.toHaveBeenCalled();
    expect(agentsApi.get).toHaveBeenCalledTimes(2);
  });
});
