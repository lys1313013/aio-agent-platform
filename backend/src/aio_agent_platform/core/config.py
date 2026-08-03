"""Application configuration via pydantic-settings."""

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env file early so nested BaseSettings subclasses can read env vars
load_dotenv()


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    url: str = Field(
        default="",
        description="PostgreSQL connection URL (required)",
        min_length=1,
    )


class JWTSettings(BaseSettings):
    """JWT authentication configuration."""

    model_config = SettingsConfigDict(env_prefix="JWT_")

    secret: str = Field(
        default="",
        description="JWT signing secret (required, min 32 chars)",
        min_length=32,
    )
    algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=21600, ge=1, le=525600)  # 15 days
    refresh_token_expire_days: int = Field(default=7, ge=1, le=365)

    @field_validator("secret")
    @classmethod
    def secret_must_not_be_placeholder(cls, v: str) -> str:
        if v.lower() in {"your-secret-key-here", "changeme", "change-me"}:
            raise ValueError("JWT secret must not be a placeholder value")
        return v


class LLMProvidersSettings(BaseSettings):
    """LLM provider configuration (legacy fallback, models should be configured via admin)."""

    model_config = SettingsConfigDict(env_prefix="LLM_")

    provider: str = Field(default="", description="LLM provider type (configured via admin)")
    model: str = Field(default="", description="Model name (configured via admin)")
    base_url: str = Field(default="", description="API base URL (configured via admin)")
    api_key: str = Field(default="", description="API key (configured via admin)")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout: int = Field(default=120, ge=10, le=600)


class AgentSettings(BaseSettings):
    """Agent behavior configuration."""

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    max_iterations: int = Field(default=20, ge=1, le=100)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    trust_level: str = Field(
        default="ask_dangerous",
        pattern="^(ask_always|ask_dangerous|auto_all)$",
    )
    memory_top_k: int = Field(default=5, ge=1, le=20)
    # Multi-agent delegation settings
    max_delegation_depth: int = Field(
        default=3, ge=1, le=10, description="Maximum delegation nesting depth"
    )
    child_max_iterations: int = Field(
        default=10, ge=1, le=50, description="Max ReAct iterations for child agents"
    )
    delegation_timeout: int = Field(
        default=300, ge=30, le=1800, description="Timeout per delegation in seconds"
    )
    # Context window management
    context_window: int = Field(
        default=128000, ge=4096, le=1000000, description="Model context window size in tokens"
    )
    context_reserve_output: int = Field(
        default=8192, ge=1024, le=65536, description="Tokens reserved for LLM output"
    )
    context_compress_threshold: float = Field(
        default=0.75, ge=0.3, le=0.95, description="Trigger compression when usage exceeds this ratio"
    )
    context_summary_max_chars: int = Field(
        default=1500, ge=200, le=5000, description="Max characters for conversation summary output"
    )
    context_history_soft_limit: int = Field(
        default=20, ge=5, le=100, description="Soft limit on history messages for adaptive loading"
    )


class SandboxSettings(BaseSettings):
    """Sandbox execution configuration."""

    model_config = SettingsConfigDict(env_prefix="SANDBOX_")

    image: str = Field(default="aio-agent-platform/sandbox:latest")
    cpu_limit: float = Field(default=1.0, ge=0.1, le=32)
    memory_limit: str = Field(default="512m")
    tmpfs_size: str = Field(default="512m")
    network_disabled: bool = Field(default=True, description="Disable outbound network from sandbox")
    command_timeout: int = Field(default=60, ge=5, le=600)
    session_ttl: int = Field(default=3600, ge=60, le=86400)
    workspace_quota_mb: int = Field(default=500, ge=10, le=10240)
    max_concurrent: int = Field(default=10, ge=1, le=100)


class StorageSettings(BaseSettings):
    """MinIO / S3 object storage configuration."""

    model_config = SettingsConfigDict(env_prefix="STORAGE_")

    endpoint: str = Field(default="localhost:9010", description="MinIO endpoint (host:port)")
    access_key: str = Field(default="", description="MinIO access key (required)", min_length=1)
    secret_key: str = Field(default="", description="MinIO secret key (required)", min_length=1)
    bucket: str = Field(default="skills", description="Bucket name for skill zip storage (legacy)")
    workspace_bucket: str = Field(
        default="aio-agent-platform", description="Unified bucket for all storage (workspaces, skills, exports)"
    )
    secure: bool = Field(default=False, description="Use HTTPS")
    sync_interval_seconds: int = Field(default=300, ge=60, le=3600, description="Periodic sync interval")
    max_workspace_size_mb: int = Field(default=500, ge=10, le=10240, description="Max workspace size in MB")
    presign_expire_seconds: int = Field(default=3600, ge=60, le=86400, description="Presigned URL expiry")


class LangfuseSettings(BaseSettings):
    """Langfuse observability configuration."""

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_")

    secret_key: str = Field(default="", description="Langfuse secret key")
    public_key: str = Field(default="", description="Langfuse public key")
    base_url: str = Field(default="http://localhost:3000", description="Langfuse base URL")
    enabled: bool = Field(default=True, description="Enable Langfuse tracing")


class WebSettings(BaseSettings):
    """Web search/fetch tool configuration."""

    model_config = SettingsConfigDict(env_prefix="WEB_")

    enabled: bool = Field(default=True, description="Enable web_search / web_fetch tools")
    search_provider: str = Field(
        default="auto",
        pattern="^(auto|duckduckgo|brave|tavily|searxng)$",
    )
    brave_api_key: str = Field(default="")
    tavily_api_key: str = Field(default="")
    searxng_url: str = Field(default="", description="Self-hosted SearXNG base URL")
    firecrawl_api_key: str = Field(default="", description="Optional Firecrawl fallback for web_fetch")
    fetch_max_chars: int = Field(default=8000, ge=500, le=10000)
    fetch_max_response_bytes: int = Field(default=2_000_000, ge=100_000, le=20_000_000)
    fetch_timeout_seconds: int = Field(default=30, ge=5, le=120)
    fetch_max_redirects: int = Field(default=3, ge=0, le=10)
    cache_ttl_seconds: int = Field(default=900, ge=0, le=86400, description="0 = disable cache")
    summary_enabled: bool = Field(
        default=False,
        description="Summarize oversized pages with the default LLM instead of truncating",
    )


class ServerSettings(BaseSettings):
    """Server configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8100, ge=1, le=65535)
    cors_origins: str = Field(default="http://localhost:5273")
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    reload: bool = Field(
        default=False,
        description="Auto-restart server when source code changes (dev only).",
    )
    reload_delay: float = Field(
        default=5,
        ge=0,
        description="Seconds between source-change polls when reload is on; "
                    "changes within each interval are batched into one restart.",
    )
    server_url: str = Field(
        default="",
        description="Public URL of this server (e.g. https://agent.example.com). "
                    "Used for generating publicly-accessible image URLs. "
                    "If empty, falls back to http://localhost:{port}.",
    )


class AppSettings(BaseSettings):
    """Root application settings, composes all sub-configs."""

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    jwt: JWTSettings = Field(default_factory=JWTSettings)
    llm: LLMProvidersSettings = Field(default_factory=LLMProvidersSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Global settings instance
settings = AppSettings()
