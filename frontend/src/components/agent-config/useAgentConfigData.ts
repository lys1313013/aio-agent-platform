import { useEffect, useRef, useState, useSyncExternalStore, type SetStateAction } from 'react';
import { agentsApi, adminApi, skillsApi, toolsApi, mcpApi, knowledgeApi, graphKnowledgeApi } from '@/lib/api';
import type { Agent } from '@/lib/types';
import { getAuthGeneration, onAuthSessionChange } from '@/lib/authSession';
import { useAuthStore } from '@/stores/authStore';
import type { AdminSectionKey } from './types';

export function useAgentConfigScope() {
  const generation = useSyncExternalStore(onAuthSessionChange, getAuthGeneration);
  const tenantId = useAuthStore((state) => state.tenantId);
  return `${generation}:${tenantId ?? ''}`;
}

async function loadOptions() {
  const [allAgents, models, skills, allTools, mcpServers, knowledgeBases, graphKnowledgeBases] = await Promise.all([
    agentsApi.adminList(), adminApi.listModels(), skillsApi.list({ is_active: true, limit: 200 }),
    toolsApi.list(), mcpApi.list(), knowledgeApi.list(), graphKnowledgeApi.list(),
  ]);
  return { allAgents, models, skills: skills.items, allTools, mcpServers, knowledgeBases, graphKnowledgeBases };
}
type Options = Awaited<ReturnType<typeof loadOptions>>;

const emptyOptions: Options = {
  allAgents: [], models: [], skills: [], allTools: [], mcpServers: [], knowledgeBases: [], graphKnowledgeBases: [],
};

function emptyDraft() {
  return {
    selectedModelId: null as string | null,
    enabledTools: [] as string[], enabledMcpTools: [] as string[], mcpAllToolsServerIds: [] as string[],
    selectedKnowledgeBaseIds: [] as string[], selectedGraphKnowledgeBaseIds: [] as string[], selectedSkillIds: [] as string[],
    systemPrompt: '', agentTemperature: null as number | null, agentMaxIterations: null as number | null,
    welcomeMessage: '', starterPrompts: [] as Array<{ label: string; icon: string; }>,
    enableMemoryExtraction: true, enableRetry: true, enableAutoTitle: true,
    selectedChildIds: [] as string[], childMaxIterations: {} as Record<string, number | null>,
  };
}
type Draft = ReturnType<typeof emptyDraft>;

function draftFromAgent(agent: Agent, options: Options): Draft {
  const builtinNames = new Set(options.allTools.filter((tool) => tool.category !== 'mcp').map((tool) => tool.name));
  const selected = new Set(agent.enabled_tools ?? []);
  const legacyAllServers = options.mcpServers.filter((server) => {
    if (!agent.mcp_server_ids?.includes(server.id)) return false;
    const names = (server.tools ?? []).map((tool) => `${server.tool_prefix || ''}${tool.name}`);
    return names.length > 0 && names.every((name) => selected.has(name));
  }).map((server) => server.id);
  return {
    selectedModelId: agent.model_id,
    enabledTools: [...selected].filter((name) => builtinNames.has(name)),
    enabledMcpTools: [...selected].filter((name) => !builtinNames.has(name)),
    mcpAllToolsServerIds: [...new Set([...(agent.mcp_all_tools_server_ids ?? []), ...legacyAllServers])],
    selectedKnowledgeBaseIds: agent.knowledge_base_ids ?? [], selectedGraphKnowledgeBaseIds: agent.graph_knowledge_base_ids ?? [],
    selectedSkillIds: agent.skill_ids ?? [], systemPrompt: agent.system_prompt ?? '',
    agentTemperature: agent.temperature ?? null, agentMaxIterations: agent.max_iterations ?? null,
    welcomeMessage: agent.welcome_message ?? '', starterPrompts: agent.starter_prompts ?? [],
    enableMemoryExtraction: agent.enable_memory_extraction ?? true, enableRetry: agent.enable_retry ?? true,
    enableAutoTitle: agent.enable_auto_title ?? true, selectedChildIds: agent.child_ids ?? [],
    childMaxIterations: Object.fromEntries(options.allAgents.filter((child) => agent.child_ids?.includes(child.id))
      .map((child) => [child.id, child.max_iterations ?? null])),
  };
}

interface Feedback {
  success: (text: string) => void;
  error: (text: string) => void;
  onUpdated?: () => void;
}

/** One mounted agent owns its options and drafts; changing tabs never reloads them. */
export function useAgentConfigData(agentId: string, feedback: Feedback) {
  const scope = useAgentConfigScope();
  const identity = `${scope}:${agentId}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const activeRef = useRef(false);
  const epochRef = useRef(0);
  const renderEpoch = epochRef.current;
  const savedAgentRef = useRef<Agent | null>(null);
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const feedbackRef = useRef(feedback);
  feedbackRef.current = feedback;
  const [data, setData] = useState<Options>(emptyOptions);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [draft, setDraft] = useState(emptyDraft);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const [savingSections, setSavingSections] = useState<Partial<Record<AdminSectionKey, boolean>>>({});
  const [savedSections, setSavedSections] = useState<Partial<Record<AdminSectionKey, boolean>>>({});
  const savingRef = useRef(new Set<AdminSectionKey>());
  const savedTimers = useRef(new Map<AdminSectionKey, ReturnType<typeof setTimeout>>());

  function current(owner: string, epoch = epochRef.current) {
    const liveScope = `${getAuthGeneration()}:${useAuthStore.getState().tenantId ?? ''}`;
    return activeRef.current && epoch === epochRef.current && identityRef.current === owner && owner === `${liveScope}:${agentId}`;
  }

  useEffect(() => {
    activeRef.current = true;
    const epoch = ++epochRef.current;
    savedAgentRef.current = null;
    saveQueueRef.current = Promise.resolve();
    let cancelled = false;
    setLoading(true);
    setError(null);
    setAgent(null);
    setData(emptyOptions);
    setDraft(emptyDraft());
    setSavingSections({});
    setSavedSections({});
    savingRef.current.clear();
    void loadOptions().then((options) => {
      if (cancelled || !current(identity, epoch)) return;
      const found = options.allAgents.find((item) => item.id === agentId);
      setData(options);
      setAgent(found ?? null);
      savedAgentRef.current = found ?? null;
      if (found) setDraft(draftFromAgent(found, options));
    }).catch((cause: unknown) => {
      if (cancelled || !current(identity, epoch)) return;
      const text = cause instanceof Error ? cause.message : '加载配置失败';
      setError(text);
      feedbackRef.current.error(`加载配置失败：${text}`);
    }).finally(() => {
      if (!cancelled && current(identity, epoch)) setLoading(false);
    });
    const timers = savedTimers.current;
    return () => {
      cancelled = true;
      activeRef.current = false;
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
    // current checks the live identity without restarting when a callback changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, agentId, loadVersion]);

  function field<K extends keyof Draft>(key: K) {
    const owner = identity;
    const epoch = renderEpoch;
    return (update: SetStateAction<Draft[K]>) => {
      if (!current(owner, epoch)) return;
      setDraft((previous) => ({
        ...previous, [key]: typeof update === 'function'
          ? (update as (value: Draft[K]) => Draft[K])(previous[key]) : update
      }));
    };
  }

  function mergeSaved(saved: Agent, payload: Record<string, unknown>) {
    // Concurrent section saves return complete snapshots. Merge only their fields
    // so an older response cannot overwrite another section's newer saved values.
    const keys = [...Object.keys(payload)];
    if ('model_id' in payload) keys.push('model_name');
    if ('child_ids' in payload) keys.push('children_count');
    const changes = Object.fromEntries(keys.filter((key) => key in saved).map((key) => [key, saved[key as keyof Agent]]));
    savedAgentRef.current = savedAgentRef.current ? { ...savedAgentRef.current, ...changes } : saved;
    setAgent((previous) => previous ? { ...previous, ...changes } : saved);
    setData((previous) => ({ ...previous, allAgents: previous.allAgents.map((item) => item.id === saved.id ? { ...item, ...changes } : item) }));
  }

  async function performSave(section: AdminSectionKey, payload: Record<string, unknown>, children?: Draft['childMaxIterations']) {
    const owner = identity;
    const epoch = renderEpoch;
    if (!current(owner, epoch) || !agent || savingRef.current.has(section)) return;
    savingRef.current.add(section);
    setSavingSections((previous) => ({ ...previous, [section]: true }));
    setSavedSections((previous) => ({ ...previous, [section]: false }));
    const oldTimer = savedTimers.current.get(section);
    if (oldTimer) clearTimeout(oldTimer);
    const queued = saveQueueRef.current.then(async () => {
      if (!current(owner, epoch)) return;
      // The tools and MCP sections share one backend field. Serialize writes
      // and compose with the latest saved counterpart, never its unsaved draft.
      let changes = payload;
      if ((section === 'tools' || section === 'mcp') && Array.isArray(payload.enabled_tools)) {
        const builtins = new Set(data.allTools.filter((tool) => tool.category !== 'mcp').map((tool) => tool.name));
        const selected = payload.enabled_tools as string[];
        const saved = savedAgentRef.current?.enabled_tools ?? [];
        changes = {
          ...payload, enabled_tools: section === 'tools'
            ? [...selected.filter((name) => builtins.has(name)), ...saved.filter((name) => !builtins.has(name))]
            : [...saved.filter((name) => builtins.has(name)), ...selected.filter((name) => !builtins.has(name))]
        };
      }
      const saved = await agentsApi.adminUpdate(agentId, changes);
      if (!current(owner, epoch)) return;
      mergeSaved(saved, changes);
      if (children) {
        for (const [childId, maxIterations] of Object.entries(children)) {
          if (!current(owner, epoch)) return;
          const updated = await agentsApi.adminUpdate(childId, { max_iterations: maxIterations });
          if (!current(owner, epoch)) return;
          setData((previous) => ({ ...previous, allAgents: previous.allAgents.map((item) => item.id === updated.id ? updated : item) }));
        }
      }
      if (!current(owner, epoch)) return;
      feedbackRef.current.success('保存成功');
      feedbackRef.current.onUpdated?.();
      setSavedSections((previous) => ({ ...previous, [section]: true }));
      savedTimers.current.set(section, setTimeout(() => {
        if (current(owner, epoch)) setSavedSections((previous) => ({ ...previous, [section]: false }));
      }, 1500));
    }).catch((cause: unknown) => {
      if (current(owner, epoch)) feedbackRef.current.error(cause instanceof Error ? cause.message : '保存失败');
    }).finally(() => {
      if (current(owner, epoch)) {
        savingRef.current.delete(section);
        setSavingSections((previous) => ({ ...previous, [section]: false }));
      }
    });
    saveQueueRef.current = queued;
    await queued;
  }

  return {
    agentId, agent, ...data, ...draft, loading, error, savingSections, savedSections,
    retryLoad: () => { epochRef.current += 1; setLoadVersion((version) => version + 1); },
    saveSection: (section: AdminSectionKey, payload: Record<string, unknown>) => performSave(section, payload),
    saveChildrenSection: () => performSave('children', { child_ids: draft.selectedChildIds }, Object.fromEntries(
      Object.entries(draft.childMaxIterations).filter(([id, value]) => draft.selectedChildIds.includes(id)
        && value !== data.allAgents.find((item) => item.id === id)?.max_iterations),
    )),
    setSelectedModelId: field('selectedModelId'), setEnabledTools: field('enabledTools'),
    setEnabledMcpTools: field('enabledMcpTools'), setMcpAllToolsServerIds: field('mcpAllToolsServerIds'),
    setSelectedKnowledgeBaseIds: field('selectedKnowledgeBaseIds'), setSelectedGraphKnowledgeBaseIds: field('selectedGraphKnowledgeBaseIds'),
    setSelectedSkillIds: field('selectedSkillIds'), setSystemPrompt: field('systemPrompt'),
    setAgentTemperature: field('agentTemperature'), setAgentMaxIterations: field('agentMaxIterations'),
    setWelcomeMessage: field('welcomeMessage'), setStarterPrompts: field('starterPrompts'),
    setEnableMemoryExtraction: field('enableMemoryExtraction'), setEnableRetry: field('enableRetry'), setEnableAutoTitle: field('enableAutoTitle'),
    setSelectedChildIds: field('selectedChildIds'), setChildMaxIterations: field('childMaxIterations'),
  };
}
