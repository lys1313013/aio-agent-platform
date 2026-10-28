import { createContext, useContext } from 'react';
import type { useAgentConfigData } from './useAgentConfigData';

export const AgentConfigContext = createContext<ReturnType<typeof useAgentConfigData> | null>(null);

export function useAgentConfig() {
  const config = useContext(AgentConfigContext);
  if (!config) throw new Error('AgentConfigContext is required');
  return config;
}
