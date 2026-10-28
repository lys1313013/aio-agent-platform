"""Complete the migration schema and repair databases created by legacy bootstrap.

All DDL below is a frozen snapshot of this revision. Never import live ORM
metadata here: later model changes must receive their own migration.
No existing business data or ownership is inferred or rewritten.
"""

from alembic import op

revision = "c5e6f7a8b9d0"
down_revision = "b4d5e6f7a8b9"
branch_labels = None
depends_on = None

_DDL = (
    """CREATE TABLE IF NOT EXISTS agent_trace_logs (
	id UUID NOT NULL,
	trace_id UUID NOT NULL,
	session_id UUID,
	user_id UUID,
	tenant_id UUID,
	agent_id UUID,
	status VARCHAR(16) NOT NULL,
	iteration_count INTEGER NOT NULL,
	tool_call_count INTEGER NOT NULL,
	total_tokens INTEGER NOT NULL,
	duration_ms INTEGER,
	is_compressed BOOLEAN NOT NULL,
	tokens_before_compress INTEGER,
	tokens_after_compress INTEGER,
	saved_tokens INTEGER,
	summary_tokens INTEGER,
	hit_max_iterations BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (trace_id)
)""",
    """CREATE INDEX IF NOT EXISTS idx_agent_trace_logs_session ON agent_trace_logs (session_id)""",
    """CREATE INDEX IF NOT EXISTS idx_agent_trace_logs_tenant_created ON agent_trace_logs (tenant_id, created_at)""",
    """CREATE TABLE IF NOT EXISTS llm_call_logs (
	id UUID NOT NULL,
	trace_id UUID,
	session_id UUID,
	user_id UUID,
	tenant_id UUID,
	agent_id UUID,
	model_id UUID,
	model VARCHAR(128) NOT NULL,
	provider VARCHAR(32) NOT NULL,
	call_order INTEGER NOT NULL,
	prompt_tokens INTEGER NOT NULL,
	completion_tokens INTEGER NOT NULL,
	total_tokens INTEGER NOT NULL,
	cache_read_tokens INTEGER NOT NULL,
	cache_creation_tokens INTEGER NOT NULL,
	context_window INTEGER,
	context_utilization FLOAT,
	ttft_ms INTEGER,
	duration_ms INTEGER,
	output_tokens_per_sec FLOAT,
	retry_count INTEGER NOT NULL,
	final_status VARCHAR(16) NOT NULL,
	error_type VARCHAR(32),
	stop_reason VARCHAR(16),
	temperature FLOAT,
	max_tokens INTEGER,
	tools_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)""",
    """CREATE INDEX IF NOT EXISTS idx_llm_call_logs_session ON llm_call_logs (session_id)""",
    """CREATE INDEX IF NOT EXISTS idx_llm_call_logs_tenant_created ON llm_call_logs (tenant_id, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_llm_call_logs_trace ON llm_call_logs (trace_id)""",
    """CREATE TABLE IF NOT EXISTS performance_daily (
	tenant_id UUID NOT NULL,
	date DATE NOT NULL,
	trace_count INTEGER NOT NULL,
	success_count INTEGER NOT NULL,
	error_count INTEGER NOT NULL,
	interrupted_count INTEGER NOT NULL,
	timeout_count INTEGER NOT NULL,
	total_duration_ms BIGINT NOT NULL,
	total_tokens BIGINT NOT NULL,
	llm_call_count INTEGER NOT NULL,
	tool_call_count INTEGER NOT NULL,
	llm_total_duration_ms BIGINT NOT NULL,
	ttft_total_ms BIGINT NOT NULL,
	max_llm_duration_ms INTEGER NOT NULL,
	compress_count INTEGER NOT NULL,
	saved_tokens_total BIGINT NOT NULL,
	PRIMARY KEY (tenant_id, date)
)""",
    """CREATE INDEX IF NOT EXISTS idx_performance_daily_date ON performance_daily (date)""",
    """CREATE TABLE IF NOT EXISTS tool_call_logs (
	id UUID NOT NULL,
	trace_id UUID,
	session_id UUID,
	user_id UUID,
	tenant_id UUID,
	agent_id UUID,
	tool_name VARCHAR(128) NOT NULL,
	exec_type VARCHAR(16) NOT NULL,
	call_order INTEGER NOT NULL,
	duration_ms INTEGER,
	is_error BOOLEAN NOT NULL,
	error_type VARCHAR(32),
	arg_bytes INTEGER,
	arg_chars INTEGER,
	output_bytes INTEGER,
	output_chars INTEGER,
	est_injected_tokens INTEGER,
	is_truncated BOOLEAN NOT NULL,
	is_concurrent BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)""",
    """CREATE INDEX IF NOT EXISTS idx_tool_call_logs_session ON tool_call_logs (session_id)""",
    """CREATE INDEX IF NOT EXISTS idx_tool_call_logs_tenant_tool_created ON tool_call_logs (tenant_id, tool_name, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_tool_call_logs_trace ON tool_call_logs (trace_id)""",
    """CREATE TABLE IF NOT EXISTS tool_usage_daily (
	user_id UUID NOT NULL,
	date DATE NOT NULL,
	tool_name VARCHAR(128) NOT NULL,
	request_count INTEGER NOT NULL,
	success_count INTEGER NOT NULL,
	error_count INTEGER NOT NULL,
	total_duration_ms BIGINT NOT NULL,
	max_duration_ms INTEGER NOT NULL,
	total_injected_tokens BIGINT NOT NULL,
	PRIMARY KEY (user_id, date, tool_name)
)""",
    """CREATE INDEX IF NOT EXISTS idx_tool_usage_daily_tenant ON tool_usage_daily (user_id, date)""",
    """ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL""",
    """ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_cron_jobs_tenant ON cron_jobs (tenant_id)""",
    """CREATE INDEX IF NOT EXISTS idx_mcp_servers_tenant ON mcp_servers (tenant_id)""",
)

_NOT_NULL = (
    ("chat_room_messages", "created_at"),
    ("chat_room_runs", "heartbeat_at"),
    ("chat_room_runs", "created_at"),
    ("chat_rooms", "created_at"),
    ("chat_rooms", "updated_at"),
    ("memory_changes", "created_at"),
    ("memory_organize_plans", "created_at"),
    ("memory_versions", "created_at"),
    ("skill_mutations", "created_at"),
)

_DROP_DEFAULTS = (
    ("channel_configs", "channel_type"),
    ("channel_configs", "status"),
    ("channel_configs", "tool_blacklist"),
    ("channel_session_mappings", "is_active"),
    ("chat_runs", "stop_requested"),
    ("chat_runs", "snapshot"),
    ("chat_runs", "last_sequence"),
    ("cron_job_runs", "tenant_id"),
    ("cron_job_runs", "status"),
    ("cron_job_runs", "started_at"),
    ("daily_memories", "highlights"),
    ("daily_memories", "source_session_ids"),
    ("daily_memories", "created_at"),
    ("daily_memories", "updated_at"),
    ("graph_chunks", "seq"),
    ("graph_documents", "chunk_count"),
    ("graph_extraction_jobs", "total_chunks"),
    ("graph_extraction_jobs", "processed_chunks"),
    ("graph_extraction_jobs", "entities_found"),
    ("graph_extraction_jobs", "relationships_found"),
    ("graph_relationships", "confidence"),
    ("hook_executions", "status"),
    ("hook_executions", "duration_ms"),
    ("hooks", "scope"),
    ("hooks", "action_type"),
    ("hooks", "timeout_ms"),
    ("hooks", "retry_count"),
    ("hooks", "is_enabled"),
    ("hooks", "visibility"),
    ("mcp_servers", "tenant_id"),
    ("pet_packages", "visibility"),
    ("pet_packages", "status"),
    ("pet_packages", "row_mapping"),
    ("skill_mutations", "created_at"),
    ("token_usage_daily", "cache_read_tokens"),
    ("token_usage_daily", "cache_creation_tokens"),
    ("user_pets", "is_active"),
)

_RLS_TABLES = (
    "chat_runs",
    "chat_run_events",
    "memory_changes",
    "memory_versions",
    "memory_organize_plans",
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for statement in _DDL:
        op.execute(statement)
    # Fail on historical NULLs; fabricated timestamps/owners are not a schema repair.
    for table, column in _NOT_NULL:
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET NOT NULL')
    # Match ORM/bootstrap defaults, including removal of public-tenant fallbacks.
    # Existing rows are untouched; application inserts already supply these values.
    for table, column in _DROP_DEFAULTS:
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP DEFAULT')
    for table in _RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS user_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY user_isolation ON "{table}" USING '
            "(user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)"
        )


def downgrade() -> None:
    # A pre-repair database may already have these tables/policies from bootstrap.
    # Removing them would lose data and cannot restore its unknown original shape.
    raise RuntimeError("Schema reconciliation cannot be downgraded without an audited restore")
