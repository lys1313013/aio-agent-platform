import { useCallback, useEffect, useRef, useState } from 'react';
import { agentsApi } from '@/lib/api';
import { getAuthGeneration } from '@/lib/authSession';
import { useAuthStore } from '@/stores/authStore';
import type { Agent } from '@/lib/types';
import { useAgentConfigScope } from './useAgentConfigData';

/** Latest request wins, including silent refreshes after a configuration save. */
export function useAgentDetails(agentId: string | undefined, onError: (message: string) => void) {
  const scope = useAgentConfigScope();
  const identity = `${scope}:${agentId ?? ''}`;
  const ownerRef = useRef(identity);
  ownerRef.current = identity;
  const sequence = useRef(0);
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const [state, setState] = useState<{ identity: string; agent: Agent | null; loading: boolean; }>({ identity, agent: null, loading: true });

  const loadAgent = useCallback((silent = false) => {
    const owner = identity;
    const liveIdentity = () => `${getAuthGeneration()}:${useAuthStore.getState().tenantId ?? ''}:${agentId ?? ''}`;
    if (!agentId || owner !== ownerRef.current || owner !== liveIdentity()) return;
    const request = ++sequence.current;
    const current = () => owner === ownerRef.current && owner === liveIdentity() && request === sequence.current;
    if (!silent) setState({ identity: owner, agent: null, loading: true });
    void agentsApi.get(agentId).then((agent) => {
      if (current()) setState({ identity: owner, agent, loading: false });
    }).catch((cause: unknown) => {
      if (current()) errorRef.current(`加载智能体失败：${cause instanceof Error ? cause.message : '未知错误'}`);
    }).finally(() => {
      if (current()) setState((previous) => ({ ...previous, identity: owner, loading: false }));
    });
  }, [identity, agentId]);

  useEffect(() => {
    loadAgent();
    return () => { sequence.current += 1; };
  }, [loadAgent]);

  return {
    agent: state.identity === identity ? state.agent : null,
    agentLoading: state.identity !== identity || state.loading, loadAgent
  };
}
