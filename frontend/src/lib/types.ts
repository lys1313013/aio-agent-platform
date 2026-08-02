/** Shared types matching the backend API schema */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface ChatAttachment {
  key: string;
  url: string;
  mime: string;
  size: number;
  filename: string;
}

export interface FileAttachmentRef {
  file_id: string;
  filename: string;
  mime: string;
  size: number;
  workspace_path: string;
}

export interface Session {
  id: string;
  title: string | null;
  agent_id: string | null;
  workspace_id: string | null;
  is_pinned: boolean;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string | null;
  tool_calls?: Record<string, unknown>[] | null;
  tool_call_id?: string | null;
  token_usage?: Record<string, unknown> | null;
  attachments?: ChatAttachment[] | null;
  file_attachments?: FileAttachmentRef[] | null;
  created_at: string;
}

export interface SessionDetail extends Session {
  messages: Message[];
}

export interface ChatRequest {
  session_id?: string | null;
  agent_id?: string | null;
  message: string;
  attachments?: ChatAttachment[] | null;
  file_attachments?: FileAttachmentRef[] | null;
}

export interface ChatResponse {
  session_id: string;
  message_id: string;
  content: string;
  tool_calls_count: number;
  done: boolean;
}

/** Streaming state during a WebSocket conversation */
export interface StreamingState {
  thinking: string;
  toolCalls: ToolCallInfo[];
  finalText: string;
  isStreaming: boolean;
  // Delegation tracking
  delegations: DelegationInfo[];
  // Action order: tracks interleaved order of thinking, tool calls, delegations, and confirmations
  actionOrder: Array<{ type: 'thinking' | 'tool' | 'delegation' | 'confirmation'; id: string }>;
  // Thinking chunks — individual reasoning blocks keyed by id for inline rendering
  thinkingChunks: Array<{ id: string; content: string }>;
  // Confirmation tracking
  confirmations: ConfirmationRequest[];
  confirmationsResolved: Record<string, ConfirmationResolvedInfo>;
}

export interface ToolCallInfo {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: ToolResultInfo;
  confirmation?: PersistedConfirmation;
}

export interface PersistedConfirmation {
  confirmation_id: string;
  question: string;
  mode: ConfirmationMode;
  options?: ConfirmationOption[];
  table_schema?: TableSchema;
  context: ConfirmationContext;
  resolved?: ConfirmationResolvedInfo;
}

export interface ToolResultInfo {
  status: string;
  preview: string;
}

export interface WsServerMessage {
  type:
    | 'thinking'
    | 'tool_call'
    | 'tool_result'
    | 'text'
    | 'text_delta'
    | 'done'
    | 'error'
    | 'session'
    | 'delegation_start'
    | 'delegation_end'
    | 'confirmation_required'
    | 'confirmation_resolved';
  content?: string;
  id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  tool_call_id?: string;
  status?: string;
  preview?: string;
  message?: string;
  // Delegation fields
  delegation_id?: string;
  child_agent_id?: string;
  child_agent_name?: string;
  child_agent_icon?: string;
  task?: string;
  error?: string;
  duration_ms?: number;
  result_preview?: string;
  // Session field
  session_id?: string;
  // Confirmation fields
  confirmation_id?: string;
  question?: string;
  mode?: string;
  options?: import('./types').ConfirmationOption[];
  table_schema?: import('./types').TableSchema;
  context?: import('./types').ConfirmationContext;
  selected_options?: string[];
  user_input?: string;
  table_data?: Record<string, unknown>[];
  resolved_at?: string;
  created_at?: string;
}

// ---- Memory ----

export type MemoryLayer = 'L1' | 'L2' | 'L3';

export interface Memory {
  id: string;
  layer: MemoryLayer;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MemoryListResponse {
  items: Memory[];
  total: number;
  layer: MemoryLayer | null;
}

export interface MemorySearchResult {
  id: string;
  layer: MemoryLayer;
  content: string;
  score: number;
  created_at: string;
}

// ---- Skills ----

export interface SkillFile {
  path: string;
  type: 'script' | 'reference' | 'asset';
  description: string;
  language: string;
  size: number;
}

export interface Skill {
  id: string;
  name: string;
  description: string | null;
  content: string | null;
  tags: string[];
  category: string;
  trigger_condition: string | null;
  use_count: number;
  success_count: number;
  is_public: boolean;
  is_active: boolean;
  version: number;
  files: SkillFile[];
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillVersion {
  id: string;
  skill_id: string;
  version: number;
  content: string;
  created_at: string;
}

export interface SkillListResponse {
  items: Skill[];
  total: number;
  category: string | null;
}

export interface SkillSearchResult {
  id: string;
  name: string;
  description: string | null;
  category: string;
  tags: string[];
  use_count: number;
  success_count: number;
  version: number;
  files: SkillFile[];
  score: number;
}

// ---- Tools ----

export interface ToolInfo {
  name: string;
  description: string;
  label: string;
  category: string;
  permission_level: string;
  requires_sandbox: boolean;
  timeout: number;
  method?: string;
  parameters?: Record<string, unknown>;
}

// ---- Remote Tools ----

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
export type AuthType = 'none' | 'bearer' | 'api_key' | 'basic' | 'custom_header';

export interface RemoteTool {
  id: string;
  name: string;
  label: string;
  description: string;
  method: HttpMethod;
  url_template: string;
  parameters_schema: Record<string, unknown>;
  headers: Record<string, string> | null;
  auth_type: AuthType;
  auth_config_masked: Record<string, unknown> | null;
  query_params: Record<string, string> | null;
  body_template: Record<string, unknown> | null;
  response_extract: string | null;
  timeout: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface RemoteToolCreate {
  name: string;
  label: string;
  description: string;
  method: HttpMethod;
  url_template: string;
  parameters_schema: Record<string, unknown>;
  headers?: Record<string, string> | null;
  auth_type?: AuthType;
  auth_config?: Record<string, unknown> | null;
  query_params?: Record<string, string> | null;
  body_template?: Record<string, unknown> | null;
  response_extract?: string | null;
  timeout?: number;
  is_active?: boolean;
}

export interface RemoteToolUpdate {
  name?: string;
  label?: string;
  description?: string;
  method?: HttpMethod;
  url_template?: string;
  parameters_schema?: Record<string, unknown>;
  headers?: Record<string, string> | null;
  auth_type?: AuthType;
  auth_config?: Record<string, unknown> | null;
  query_params?: Record<string, string> | null;
  body_template?: Record<string, unknown> | null;
  response_extract?: string | null;
  timeout?: number;
  is_active?: boolean;
}

export interface RemoteToolTestResult {
  success: boolean;
  status_code: number | null;
  response_body: string | null;
  duration_ms: number;
  error: string | null;
}

// ---- Channels ----

export type ChannelType = 'feishu' | 'dingtalk' | 'wecom';
export type ChannelMode = 'websocket' | 'webhook';
export type ChannelStatus = 'enabled' | 'disabled' | 'error';

export interface Channel {
  id: string;
  channel_type: ChannelType;
  name: string;
  agent_id: string;
  app_id: string;
  mode: ChannelMode;
  status: ChannelStatus;
  channel_key: string;
  tool_blacklist: string[];
  last_error: string | null;
  created_at: string;
  updated_at: string;
  webhook_url?: string;
}

export interface ChannelCreate {
  name: string;
  channel_type?: ChannelType;
  agent_id: string;
  app_id: string;
  app_secret: string;
  encrypt_key?: string | null;
  verification_token?: string | null;
  mode: ChannelMode;
  tool_blacklist?: string[];
}

export interface ChannelUpdate {
  name?: string;
  agent_id?: string;
  app_id?: string;
  app_secret?: string;
  encrypt_key?: string | null;
  verification_token?: string | null;
  mode?: ChannelMode;
  tool_blacklist?: string[];
}

export interface ChannelBinding {
  id: string;
  channel_id: string;
  external_id: string;
  user_id: string;
  bind_type: 'shadow' | 'bound';
  created_at: string;
  updated_at: string;
}

// ---- Agents ----

export interface Agent {
  id: string;
  name: string;
  description: string | null;
  icon: string;
  system_prompt?: string | null;
  model_id: string | null;
  model_name: string | null;
  enabled_tools: string[];
  mcp_server_ids: string[];
  temperature?: number | null;
  max_iterations?: number | null;
  welcome_message?: string | null;
  starter_prompts?: Array<{ label: string; icon: string }> | null;
  enable_memory_extraction?: boolean;
  enable_retry?: boolean;
  is_active: boolean;
  skill_ids: string[];
  knowledge_base_ids: string[];
  // Multi-agent relationships
  parent_ids: string[];
  child_ids: string[];
  children_count: number;
  tenant_id: string;
  created_by: string;
  visibility: 'tenant' | 'private';
  can_edit: boolean;
}

export interface AgentStats {
  total_sessions: number;
  total_messages: number;
  last_active_at: string | null;
}

// ---- Delegation (Multi-Agent) ----

export type DelegationStatus = 'running' | 'completed' | 'failed' | 'timeout';

export interface DelegationInfo {
  delegation_id: string;
  child_agent_id: string;
  child_agent_name: string;
  child_agent_icon?: string;
  task: string;
  status: DelegationStatus;
  thinking?: string;
  toolCalls?: ToolCallInfo[];
  result?: string;
  error?: string;
  duration_ms?: number;
  /** The parent's delegate_task tool_call_id — used for correct actionOrder positioning */
  tool_call_id?: string;
}

// ---- User Confirmation (AskUserQuestion) ----

export type ConfirmationMode = 'single_select' | 'multi_select' | 'free_input' | 'approve' | 'table_input';
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type ConfirmationCategory = 'plan' | 'tool_confirm' | 'clarify' | 'skill_save' | 'batch_action';
export type ConfirmationStatus = 'pending' | 'approved' | 'rejected' | 'modified' | 'timeout';

export interface ConfirmationOption {
  id: string;
  label: string;
  description?: string;
  preview?: string;
}

export type TableColumnType = 'text' | 'number' | 'date' | 'select' | 'boolean';

export interface TableColumnDef {
  key: string;
  title: string;
  type?: TableColumnType;
  required?: boolean;
  default?: unknown;
  options?: string[];
  placeholder?: string;
  width?: number;
}

export interface TableSchema {
  columns: TableColumnDef[];
  rows?: Record<string, unknown>[];
  min_rows?: number;
  max_rows?: number;
  allow_add_row?: boolean;
  allow_delete_row?: boolean;
}

export interface ConfirmationContext {
  risk_level?: RiskLevel;
  category?: ConfirmationCategory;
  agent_id?: string;
  agent_name?: string;
  delegation_id?: string;
  timeout_seconds: number;
}

export interface ConfirmationRequest {
  confirmation_id: string;
  question: string;
  mode: ConfirmationMode;
  options?: ConfirmationOption[];
  table_schema?: TableSchema;
  context: ConfirmationContext;
  created_at: string;
}

export interface ConfirmationResponse {
  status: 'approved' | 'rejected' | 'modified';
  selected_options?: string[];
  user_input?: string;
  table_data?: Record<string, unknown>[];
}

export interface ConfirmationResolvedInfo {
  confirmation_id: string;
  status: ConfirmationStatus;
  selected_options?: string[];
  user_input?: string;
  table_data?: Record<string, unknown>[];
  resolved_at: string;
}

// ---- Agent API (external API / version management) ----

export interface AgentVersion {
  id: string;
  agent_id: string;
  version: string;
  changelog?: string | null;
  published_at: string;
  is_active: boolean;
}

export interface AgentApiDoc {
  agent_id: string;
  agent_name: string;
  agent_description: string | null;
  agent_icon: string;
  model_name: string | null;
  streaming_enabled: boolean;
  thinking_enabled: boolean;
  tool_count: number;
  skill_count: number;
  has_published_version: boolean;
  latest_version: AgentVersion | null;
  base_url?: string;
}

// ---- Cron Jobs ----

export interface CronJob {
  id: string;
  agent_id: string | null;
  name: string;
  cron_expr: string | null;
  run_at: string | null;
  message: string | null;
  task_config: Record<string, unknown>;
  is_active: boolean;
  last_run_at: string | null;
}

export interface CronJobListResponse {
  items: CronJob[];
  total: number;
}

// ---- Pets (Codex-compatible) ----

export type PetVisibility = 'private' | 'tenant' | 'public' | 'official';

/** 宠物状态 → 精灵图行号映射；_row_frames 为每行有效帧数 */
export interface PetRowMapping {
  idle: number;
  think?: number;
  work?: number;
  wait?: number;
  celebrate?: number;
  sad?: number;
  sleep?: number;
  happy?: number;
  _row_frames?: number[];
}

export interface PetPackage {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  kind: string | null;
  owner_id: string;
  tenant_id: string;
  visibility: PetVisibility;
  status: string;
  manifest: Record<string, unknown>;
  row_mapping: PetRowMapping;
  frame_width: number;
  frame_height: number;
  col_count: number;
  row_count: number;
  created_at: string;
  spritesheet_url: string;
}

export interface UserPet {
  id: string;
  package_id: string;
  level: number;
  exp: number;
  is_active: boolean;
  adopted_at: string;
  package: PetPackage;
}

export type PetMood =
  | 'idle'
  | 'think'
  | 'work'
  | 'wait'
  | 'celebrate'
  | 'sad'
  | 'sleep'
  | 'happy';
