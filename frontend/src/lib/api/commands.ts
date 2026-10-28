import type { CommandMeta, ModelMeta } from '../types';
import { request } from './transport';

// ---- Slash commands ----

export const commandsApi = {
  list() {
    return request<CommandMeta[]>('/commands');
  },
};

// ---- Models ----

export const modelsApi = {
  list() {
    return request<ModelMeta[]>('/models');
  },
};
