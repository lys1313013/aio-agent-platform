"""SQLAlchemy models — all tables with RLS, no foreign key constraints."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from aio_agent_platform.db.sanitize import sanitize_pg_text

# Type alias for UUID columns
GUID = Annotated[UUID, "uuid"]
DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


# ---- User & Auth ----


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="租户名称")
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="租户唯一标识")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", comment="是否激活"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    memberships: Mapped[list["TenantMembership"]] = relationship(
        back_populates="tenant",
        primaryjoin="Tenant.id == TenantMembership.tenant_id",
        foreign_keys="[TenantMembership.tenant_id]",
    )
    users: Mapped[list["User"]] = relationship(
        secondary="tenant_memberships",
        primaryjoin="Tenant.id == TenantMembership.tenant_id",
        secondaryjoin="TenantMembership.user_id == User.id",
        foreign_keys="[TenantMembership.tenant_id, TenantMembership.user_id]",
        viewonly=True,
    )

    __table_args__ = ({"comment": "租户表"},)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="用户名")
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, comment="邮箱地址")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="密码哈希")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    role: Mapped[str] = mapped_column(
        String(16), default="user", comment="角色: user/admin/superadmin"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否激活")
    is_shadow: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false",
        comment="是否影子账号(由渠道自动创建，不可登录 Web 端)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    profile: Mapped["UserProfile | None"] = relationship(
        back_populates="user", uselist=False,
        primaryjoin="User.id == UserProfile.user_id",
        foreign_keys="[UserProfile.user_id]",
    )
    tenant: Mapped["Tenant"] = relationship(
        primaryjoin="User.tenant_id == Tenant.id",
        foreign_keys="[User.tenant_id]",
    )
    memberships: Mapped[list["TenantMembership"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == TenantMembership.user_id",
        foreign_keys="[TenantMembership.user_id]",
    )
    tenants: Mapped[list["Tenant"]] = relationship(
        secondary="tenant_memberships",
        primaryjoin="User.id == TenantMembership.user_id",
        secondaryjoin="TenantMembership.tenant_id == Tenant.id",
        foreign_keys="[TenantMembership.user_id, TenantMembership.tenant_id]",
        viewonly=True,
    )
    config: Mapped["UserConfig | None"] = relationship(
        back_populates="user", uselist=False,
        primaryjoin="User.id == UserConfig.user_id",
        foreign_keys="[UserConfig.user_id]",
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == Session.user_id",
        foreign_keys="[Session.user_id]",
    )
    workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == Workspace.user_id",
        foreign_keys="[Workspace.user_id]",
    )
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == Memory.user_id",
        foreign_keys="[Memory.user_id]",
    )
    skills: Mapped[list["Skill"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == Skill.user_id",
        foreign_keys="[Skill.user_id]",
    )
    traces: Mapped[list["Trace"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == Trace.user_id",
        foreign_keys="[Trace.user_id]",
    )
    cron_jobs: Mapped[list["CronJob"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == CronJob.user_id",
        foreign_keys="[CronJob.user_id]",
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user",
        primaryjoin="User.id == RefreshToken.user_id",
        foreign_keys="[RefreshToken.user_id]",
    )

    __table_args__ = (
        Index("idx_users_tenant", "tenant_id"),
        {"comment": "用户表"},
    )


class TenantMembership(Base):
    """Many-to-many membership between users and tenants."""

    __tablename__ = "tenant_memberships"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, comment="租户ID"
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, comment="用户ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="加入时间"
    )

    tenant: Mapped["Tenant"] = relationship(
        back_populates="memberships",
        primaryjoin="TenantMembership.tenant_id == Tenant.id",
        foreign_keys="[TenantMembership.tenant_id]",
    )
    user: Mapped["User"] = relationship(
        back_populates="memberships",
        primaryjoin="TenantMembership.user_id == User.id",
        foreign_keys="[TenantMembership.user_id]",
    )

    __table_args__ = (
        Index("idx_tenant_memberships_user", "user_id"),
        {"comment": "租户用户成员关系表"},
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, comment="令牌哈希值")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="过期时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    user: Mapped["User"] = relationship(
        back_populates="refresh_tokens",
        primaryjoin="RefreshToken.user_id == User.id",
        foreign_keys="[RefreshToken.user_id]",
    )

    __table_args__ = (
        Index("idx_refresh_tokens_user", "user_id"),
        {"comment": "刷新令牌表"},
    )


# ---- User Profile & Config ----


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="关联用户ID",
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=DEFAULT_TENANT_ID,
        comment="所属租户ID",
    )
    display_name: Mapped[str | None] = mapped_column(String(128), comment="显示名称")
    personal_portrait: Mapped[str | None] = mapped_column(Text, comment="个人画像 Markdown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    user: Mapped["User"] = relationship(
        back_populates="profile",
        primaryjoin="UserProfile.user_id == User.id",
        foreign_keys="[UserProfile.user_id]",
    )

    __table_args__ = (
        Index("idx_user_profiles_tenant", "tenant_id"),
        {"comment": "用户档案表"},
    )


class PortraitVersion(Base):
    """历史版本快照 — 每次个人画像更新时自动保存。"""
    __tablename__ = "portrait_versions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        default=DEFAULT_TENANT_ID,
        comment="所属租户ID",
    )
    content: Mapped[str | None] = mapped_column(Text, comment="画像内容快照")
    source: Mapped[str] = mapped_column(String(16), default="manual", comment="来源: manual/ai")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_portrait_versions_user", "user_id", "created_at"),
        Index("idx_portrait_versions_tenant", "tenant_id"),
        {"comment": "个人画像历史版本表"},
    )


class UserConfig(Base):
    __tablename__ = "user_configs"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="关联用户ID",
    )
    trust_level: Mapped[str] = mapped_column(String(32), default="ask_dangerous", comment="信任级别")
    memory_top_k: Mapped[int] = mapped_column(Integer, default=5, comment="记忆检索数量")
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, comment="扩展配置(JSON)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    user: Mapped["User"] = relationship(
        back_populates="config",
        primaryjoin="UserConfig.user_id == User.id",
        foreign_keys="[UserConfig.user_id]",
    )

    __table_args__ = ({"comment": "用户配置表"},)


# ---- LLM Providers & Models (admin-managed) ----


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="显示名称")
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="提供商类型: openai/anthropic")
    base_url: Mapped[str | None] = mapped_column(String(512), comment="API基础地址")
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, comment="加密后的API密钥")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    models: Mapped[list["LLMModel"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
        primaryjoin="LLMProvider.id == LLMModel.provider_id",
        foreign_keys="[LLMModel.provider_id]",
    )

    __table_args__ = (
        Index("idx_llm_providers_tenant", "tenant_id"),
        {"comment": "LLM提供商表"},
    )


class LLMModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    provider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联提供商ID",
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="显示名称")
    model_name: Mapped[str] = mapped_column(String(256), nullable=False, comment="模型标识符")
    is_multimodal: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", comment="是否为多模态模型(支持图片输入)",
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为默认模型")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    provider: Mapped["LLMProvider"] = relationship(
        back_populates="models",
        primaryjoin="LLMModel.provider_id == LLMProvider.id",
        foreign_keys="[LLMModel.provider_id]",
    )

    __table_args__ = (
        Index("idx_llm_models_tenant_default", "tenant_id", "is_default", postgresql_where=text("is_default")),
        {"comment": "LLM模型表"},
    )


# ---- Agents ----


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="智能体名称")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    icon: Mapped[str] = mapped_column(String(64), default="robot", comment="图标标识")
    system_prompt: Mapped[str | None] = mapped_column(Text, comment="系统提示词")
    model_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="关联LLM模型ID",
    )
    enabled_tools: Mapped[list] = mapped_column(JSONB, default=list, comment="启用的工具列表(JSON)")
    mcp_server_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", comment="绑定的MCP服务器ID列表(JSON)")
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True, comment="推理温度(0.0-2.0)，为空时使用全局默认值")
    max_iterations: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="ReAct 最大迭代次数(1-100)，为空时使用全局默认值",
    )
    welcome_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="欢迎语，新会话开始时展示")
    starter_prompts: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="快捷提问列表，在欢迎页展示引导用户开始对话")
    enable_memory_extraction: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="是否启用自动记忆提取",
    )
    enable_retry: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="LLM API 调用失败时是否自动重试",
    )
    enable_auto_title: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", comment="是否自动总结会话标题",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="创建者用户ID",
    )
    visibility: Mapped[str] = mapped_column(
        String(16), default="tenant", server_default="tenant", comment="可见范围: tenant/private"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    model: Mapped["LLMModel | None"] = relationship(
        primaryjoin="Agent.model_id == LLMModel.id",
        foreign_keys="[Agent.model_id]",
    )
    creator: Mapped["User | None"] = relationship(
        primaryjoin="Agent.created_by == User.id",
        foreign_keys="[Agent.created_by]",
    )
    skills: Mapped[list["Skill"]] = relationship(
        secondary="agent_skills",
        back_populates="agents",
        primaryjoin="Agent.id == AgentSkill.agent_id",
        secondaryjoin="AgentSkill.skill_id == Skill.id",
        foreign_keys="[AgentSkill.agent_id, AgentSkill.skill_id]",
    )
    # Multi-agent: many-to-many parent/child relationships via agent_relationships
    children: Mapped[list["Agent"]] = relationship(
        secondary="agent_relationships",
        primaryjoin="Agent.id == AgentRelationship.parent_id",
        secondaryjoin="AgentRelationship.child_id == Agent.id",
        back_populates="parents",
        foreign_keys="[AgentRelationship.parent_id, AgentRelationship.child_id]",
    )
    parents: Mapped[list["Agent"]] = relationship(
        secondary="agent_relationships",
        primaryjoin="Agent.id == AgentRelationship.child_id",
        secondaryjoin="AgentRelationship.parent_id == Agent.id",
        back_populates="children",
        foreign_keys="[AgentRelationship.parent_id, AgentRelationship.child_id]",
    )
    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(
        secondary="agent_knowledge_bases",
        back_populates="agents",
        primaryjoin="Agent.id == AgentKnowledgeBase.agent_id",
        secondaryjoin="AgentKnowledgeBase.knowledge_base_id == KnowledgeBase.id",
        foreign_keys="[AgentKnowledgeBase.agent_id, AgentKnowledgeBase.knowledge_base_id]",
    )
    graph_knowledge_bases: Mapped[list["GraphKnowledgeBase"]] = relationship(
        secondary="agent_graph_knowledge_bases",
        primaryjoin="Agent.id == AgentGraphKnowledgeBase.agent_id",
        secondaryjoin="AgentGraphKnowledgeBase.knowledge_base_id == GraphKnowledgeBase.id",
        foreign_keys="[AgentGraphKnowledgeBase.agent_id, AgentGraphKnowledgeBase.knowledge_base_id]",
    )

    __table_args__ = (
        Index("idx_agents_tenant_visibility", "tenant_id", "visibility"),
        Index("idx_agents_creator", "created_by"),
        {"comment": "智能体表"},
    )


class AgentRelationship(Base):
    """Many-to-many relationship between agents (parent-child DAG)."""
    __tablename__ = "agent_relationships"

    parent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="主控智能体ID",
    )
    child_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="子智能体ID",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), comment="创建时间"
    )

    __table_args__ = (
        Index("idx_agent_rel_parent", "parent_id"),
        Index("idx_agent_rel_child", "child_id"),
        {"comment": "智能体层级关系表（多对多）"},
    )


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="智能体ID",
    )
    skill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="技能ID",
    )

    __table_args__ = ({"comment": "智能体-技能关联表"},)


class AgentVersion(Base):
    """Published version snapshot of an agent, used for external API calls."""
    __tablename__ = "agent_versions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联智能体ID",
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False, comment="版本号")
    changelog: Mapped[str | None] = mapped_column(Text, comment="版本说明")
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="发布时的配置快照(JSON)")
    published_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="发布者用户ID",
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="发布时间")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否为当前生效版本")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    agent: Mapped["Agent | None"] = relationship(
        primaryjoin="AgentVersion.agent_id == Agent.id",
        foreign_keys="[AgentVersion.agent_id]",
    )

    __table_args__ = (
        Index("idx_agent_versions_agent", "agent_id"),
        Index("idx_agent_versions_active", "agent_id", postgresql_where=text("is_active = true")),
        {"comment": "智能体版本表"},
    )


# ---- Sessions & Messages ----


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="关联智能体ID",
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="关联工作区ID",
    )
    model_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="会话级模型覆盖(缺省跟随智能体模型)",
    )
    title: Mapped[str | None] = mapped_column(String(512), comment="会话标题")
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="chat", server_default="chat",
        comment="会话来源: chat(常规)/pet(宠物闲聊)",
    )
    pet_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), comment="宠物闲聊会话关联的用户宠物ID"
    )
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否置顶")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否归档")
    context_summary: Mapped[str | None] = mapped_column(Text, comment="对话历史摘要(L4 工作记忆压缩)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    user: Mapped["User"] = relationship(
        back_populates="sessions",
        primaryjoin="Session.user_id == User.id",
        foreign_keys="[Session.user_id]",
    )
    agent: Mapped["Agent | None"] = relationship(
        primaryjoin="Session.agent_id == Agent.id",
        foreign_keys="[Session.agent_id]",
    )
    workspace: Mapped["Workspace | None"] = relationship(
        back_populates="sessions",
        primaryjoin="Session.workspace_id == Workspace.id",
        foreign_keys="[Session.workspace_id]",
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
        primaryjoin="Session.id == Message.session_id",
        foreign_keys="[Message.session_id]",
    )

    __table_args__ = (
        Index("idx_sessions_user", "user_id", "updated_at"),
        {"comment": "会话表"},
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联会话ID",
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="消息角色: user/assistant/system/tool")
    content: Mapped[str | None] = mapped_column(Text, comment="消息内容")
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, comment="工具调用信息(JSON)")
    tool_call_id: Mapped[str | None] = mapped_column(String(128), comment="工具调用ID")
    token_usage: Mapped[dict | None] = mapped_column(JSONB, comment="令牌用量统计(JSON)")
    attachments: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="图片附件元数据: [{key, url, mime, size, filename}]",
    )
    file_attachments: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="文件附件元数据: [{file_id, filename, mime, size, workspace_path}]",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    session: Mapped["Session"] = relationship(
        back_populates="messages",
        primaryjoin="Message.session_id == Session.id",
        foreign_keys="[Message.session_id]",
    )

    __table_args__ = (
        Index("idx_messages_session", "session_id", "created_at"),
        {"comment": "消息表"},
    )


@event.listens_for(Message, "before_insert")
@event.listens_for(Message, "before_update")
def _scrub_message_nul_bytes(mapper, connection, target: "Message") -> None:
    """Strip NUL bytes before write — Postgres text/JSONB cannot store \\u0000.

    Tool results can carry binary file content (e.g. PDF bytes) that leaks NUL
    bytes; without this the whole INSERT fails and the turn's tool-call history
    is lost.
    """
    if target.content is not None:
        target.content = sanitize_pg_text(target.content)
    if target.tool_calls is not None:
        target.tool_calls = sanitize_pg_text(target.tool_calls)


# ---- Memory (4 layers) ----


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    layer: Mapped[str] = mapped_column(String(4), nullable=False, comment="记忆层级: L1/L2/L3")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="记忆内容")
    search_vec: Mapped[str | None] = mapped_column(Text, comment="搜索向量(用于pg_trgm检索)")
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, comment="元数据(JSON)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    user: Mapped["User"] = relationship(
        back_populates="memories",
        primaryjoin="Memory.user_id == User.id",
        foreign_keys="[Memory.user_id]",
    )

    __table_args__ = (
        Index("idx_memories_user_layer", "user_id", "layer", "created_at"),
        Index("idx_memories_tenant", "tenant_id"),
        {"comment": "记忆表"},
    )


class DailyMemory(Base):
    """Daily consolidated memory — one row per user per local day (Asia/Shanghai)."""

    __tablename__ = "daily_memories"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, comment="记忆所属日期(用户本地日,东八区)")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="当日记忆正文(Markdown)")
    highlights: Mapped[list] = mapped_column(JSONB, default=list, comment="结构化要点(JSON): [{type, text}]")
    source_session_ids: Mapped[list] = mapped_column(JSONB, default=list, comment="贡献的会话ID列表(JSON)")
    search_vec: Mapped[str | None] = mapped_column(Text, comment="搜索向量(用于pg_trgm检索)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    user: Mapped["User"] = relationship(
        primaryjoin="DailyMemory.user_id == User.id",
        foreign_keys="[DailyMemory.user_id]",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_daily_memories_user_date"),
        Index("idx_daily_memories_user_date", "user_id", "date"),
        Index("idx_daily_memories_tenant", "tenant_id"),
        {"comment": "每日记忆表(一人一天一条)"},
    )


# ---- Skills ----


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="所属用户ID",
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, comment="技能名称")
    description: Mapped[str | None] = mapped_column(Text, comment="技能描述")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="技能内容")
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, comment="标签列表(JSON)")
    category: Mapped[str] = mapped_column(String(64), default="general", comment="分类")
    trigger_condition: Mapped[str | None] = mapped_column(Text, comment="触发条件")
    use_count: Mapped[int] = mapped_column(Integer, default=0, comment="使用次数")
    success_count: Mapped[int] = mapped_column(Integer, default=0, comment="成功次数")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最后使用时间")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="版本号")
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否公开")
    execution_log: Mapped[list[dict]] = mapped_column(JSONB, default=list, comment="执行日志(JSON)")
    scripts: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default="[]",
        comment="脚本文件清单(已弃用, 使用 files): [{path, description, language}]",
    )
    files: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default="[]",
        comment="文件清单: [{path, type, description, language, size}]",
    )
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="MinIO对象存储键")
    search_vec: Mapped[str | None] = mapped_column(Text, nullable=True, comment="搜索向量(用于pg_trgm检索)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    user: Mapped["User"] = relationship(
        back_populates="skills",
        primaryjoin="Skill.user_id == User.id",
        foreign_keys="[Skill.user_id]",
    )
    versions: Mapped[list["SkillVersion"]] = relationship(
        back_populates="skill",
        primaryjoin="Skill.id == SkillVersion.skill_id",
        foreign_keys="[SkillVersion.skill_id]",
    )
    agents: Mapped[list["Agent"]] = relationship(
        secondary="agent_skills",
        back_populates="skills",
        primaryjoin="Skill.id == AgentSkill.skill_id",
        secondaryjoin="AgentSkill.agent_id == Agent.id",
        foreign_keys="[AgentSkill.agent_id, AgentSkill.skill_id]",
    )

    __table_args__ = (
        Index("idx_skills_user", "user_id", "category", "is_active"),
        {"comment": "技能表"},
    )


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    skill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联技能ID",
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="版本号")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="版本内容")
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="MinIO对象存储键")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    skill: Mapped["Skill"] = relationship(
        back_populates="versions",
        primaryjoin="SkillVersion.skill_id == Skill.id",
        foreign_keys="[SkillVersion.skill_id]",
    )

    __table_args__ = (
        Index("idx_skill_versions", "skill_id", "version"),
        {"comment": "技能版本历史表"},
    )


# ---- Traces ----


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="关联会话ID",
    )
    input: Mapped[str | None] = mapped_column(Text, comment="输入内容")
    output: Mapped[str | None] = mapped_column(Text, comment="输出内容")
    steps: Mapped[dict | None] = mapped_column(JSONB, comment="执行步骤(JSON)")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="总令牌数")
    duration_ms: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="执行耗时(毫秒)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    user: Mapped["User"] = relationship(
        back_populates="traces",
        primaryjoin="Trace.user_id == User.id",
        foreign_keys="[Trace.user_id]",
    )

    __table_args__ = (
        Index("idx_traces_user", "user_id", "created_at"),
        {"comment": "执行追踪表"},
    )


# ---- Cron Jobs ----


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        default=DEFAULT_TENANT_ID,
        comment="所属租户ID",
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="归属用户ID",
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        comment="关联智能体ID",
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, comment="任务名称")
    cron_expr: Mapped[str | None] = mapped_column(String(128), comment="Cron表达式")
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="单次执行时间")
    message: Mapped[str | None] = mapped_column(Text, comment="发送给智能体的消息")
    task_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, comment="任务配置(JSON)")
    channel_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        comment="结果推送渠道ID(关联 channel_configs)",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最后执行时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    user: Mapped["User"] = relationship(
        back_populates="cron_jobs",
        primaryjoin="CronJob.user_id == User.id",
        foreign_keys="[CronJob.user_id]",
    )

    __table_args__ = (
        Index("idx_cron_jobs_tenant", "tenant_id"),
        {"comment": "定时任务表"},
    )


class CronJobRun(Base):
    """Single execution record of a cron job (running/success/failed)."""
    __tablename__ = "cron_job_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="关联定时任务ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        default=DEFAULT_TENANT_ID,
        comment="所属租户ID",
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="任务所属用户ID")
    status: Mapped[str] = mapped_column(String(32), default="running", comment="运行状态: running/success/failed")
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="本次执行创建的会话ID")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="开始执行时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="结束时间")
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="执行耗时(毫秒)")
    output: Mapped[str | None] = mapped_column(Text, comment="执行输出")
    error: Mapped[str | None] = mapped_column(Text, comment="错误信息")

    __table_args__ = (
        Index("idx_cron_job_runs_job", "job_id", "started_at"),
        {"comment": "定时任务运行日志表"},
    )


# ---- Workspaces ----


class Workspace(Base):
    """Workspace — a named file storage entity for project isolation."""
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="所属用户ID",
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="工作区显示名称")
    slug: Mapped[str] = mapped_column(String(64), nullable=False, comment="URL友好标识(用户内唯一)")
    description: Mapped[str | None] = mapped_column(Text, comment="工作区描述")
    file_count: Mapped[int] = mapped_column(Integer, default=0, comment="文件数量(冗余)")
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, comment="总大小(字节, 冗余)")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最后同步时间")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为默认工作区")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    user: Mapped["User"] = relationship(
        back_populates="workspaces",
        primaryjoin="Workspace.user_id == User.id",
        foreign_keys="[Workspace.user_id]",
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="workspace",
        primaryjoin="Workspace.id == Session.workspace_id",
        foreign_keys="[Session.workspace_id]",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_workspace_user_slug"),
        Index("idx_workspaces_user", "user_id"),
        {"comment": "工作区表"},
    )


# ---- Sandbox Sessions ----


class SandboxSession(Base):
    __tablename__ = "sandbox_sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联用户ID",
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="关联会话ID",
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        comment="关联工作区ID",
    )
    container_id: Mapped[str | None] = mapped_column(String(128), comment="容器ID")
    status: Mapped[str] = mapped_column(String(32), default="active", comment="状态: active/destroyed")
    cpu_peak: Mapped[float] = mapped_column(Numeric(10, 4), default=0, comment="CPU峰值使用率")
    memory_peak_mb: Mapped[int] = mapped_column(Integer, default=0, comment="内存峰值(MB)")
    command_count: Mapped[int] = mapped_column(Integer, default=0, comment="命令执行次数")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="销毁时间")

    __table_args__ = (
        Index("idx_sandbox_user", "user_id", "status"),
        Index("idx_sandbox_active", "status"),
        {"comment": "沙箱会话表"},
    )


# ---- Token Usage ----


class TokenUsageDaily(Base):
    __tablename__ = "token_usage_daily"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="关联用户ID",
    )
    date: Mapped[datetime] = mapped_column(Date, primary_key=True, default=func.current_date, comment="统计日期")
    model: Mapped[str] = mapped_column(String(128), primary_key=True, comment="模型名称")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="提示令牌数")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="补全令牌数")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="总令牌数")
    request_count: Mapped[int] = mapped_column(Integer, default=0, comment="请求次数")
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0, comment="费用(美元)")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="缓存命中令牌数")
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="缓存写入令牌数")

    __table_args__ = (
        Index("idx_token_usage_user_date", "user_id", "date"),
        {"comment": "每日令牌用量统计表"},
    )


# ---- System Config (global) ----


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True, comment="配置键")
    value: Mapped[str] = mapped_column(Text, nullable=False, comment="配置值")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = ({"comment": "系统全局配置表"},)


# ---- Audit Logs ----


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键ID")
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="操作用户ID")
    action: Mapped[str] = mapped_column(String(64), nullable=False, comment="操作类型")
    resource: Mapped[str | None] = mapped_column(String(256), comment="操作资源")
    details: Mapped[dict | None] = mapped_column(JSONB, comment="操作详情(JSON)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_audit_user", "user_id", "created_at"),
        Index("idx_audit_action", "action", "created_at"),
        {"comment": "审计日志表"},
    )


# ---- Multi-Agent Delegation ----


class Delegation(Base):
    """Record of a delegation call from parent agent to child agent."""
    __tablename__ = "delegations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    parent_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="父级会话ID")
    parent_agent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="发起委派的主控智能体ID")
    child_agent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="被委派的子智能体ID")
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="关联用户ID")
    depth: Mapped[int] = mapped_column(Integer, nullable=False, comment="委派深度")
    task: Mapped[str] = mapped_column(Text, nullable=False, comment="委派任务描述")
    context: Mapped[str | None] = mapped_column(Text, comment="额外上下文")
    status: Mapped[str] = mapped_column(String(32), default="pending", comment="状态: pending/running/completed/failed/timeout")
    result: Mapped[str | None] = mapped_column(Text, comment="子智能体最终输出")
    error: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    token_usage: Mapped[dict | None] = mapped_column(JSONB, comment="令牌用量")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="执行耗时(毫秒)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="完成时间")

    __table_args__ = (
        Index("idx_delegations_user", "user_id", "created_at"),
        Index("idx_delegations_session", "parent_session_id", "depth"),
        Index("idx_delegations_status", "status"),
        {"comment": "智能体委派执行记录表"},
    )


# ---- MCP Servers ----


class MCPServer(Base):
    """MCP (Model Context Protocol) server configuration."""
    __tablename__ = "mcp_servers"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="显示名称")
    transport_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="传输类型: sse/streamable-http")

    # HTTP transport configuration (shared by SSE and streamable-http)
    url: Mapped[str] = mapped_column(String(512), nullable=False, comment="服务端点 URL")
    headers: Mapped[dict] = mapped_column(JSONB, default=dict, comment="HTTP 请求头")

    # General configuration
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    tool_prefix: Mapped[str | None] = mapped_column(String(32), comment="工具名称前缀 (避免冲突)")
    timeout: Mapped[int] = mapped_column(Integer, default=60, comment="调用超时 (秒)")
    status: Mapped[str] = mapped_column(String(32), default="disconnected", comment="连接状态: connected/disconnected/error")
    last_error: Mapped[str | None] = mapped_column(Text, comment="最后错误信息")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("idx_mcp_servers_tenant", "tenant_id"),
        {"comment": "MCP 服务器配置表"},
    )


# ---- Knowledge Base (RAGFlow Integration) ----


class KnowledgeBase(Base):
    """Knowledge base resource mapped to RAGFlow dataset."""
    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="知识库名称")
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False, comment="RAGFlow dataset ID")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建者用户ID")
    visibility: Mapped[str] = mapped_column(
        String(16), default="tenant", server_default="tenant", comment="可见范围: tenant/private"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    agents: Mapped[list["Agent"]] = relationship(
        secondary="agent_knowledge_bases",
        back_populates="knowledge_bases",
        primaryjoin="KnowledgeBase.id == AgentKnowledgeBase.knowledge_base_id",
        secondaryjoin="AgentKnowledgeBase.agent_id == Agent.id",
        foreign_keys="[AgentKnowledgeBase.knowledge_base_id, AgentKnowledgeBase.agent_id]",
    )

    __table_args__ = (
        Index("idx_knowledge_bases_dataset_id", "dataset_id"),
        Index("idx_knowledge_bases_tenant_visibility", "tenant_id", "visibility"),
        {"comment": "知识库表"},
    )


class AgentKnowledgeBase(Base):
    """Agent-KnowledgeBase many-to-many relationship."""
    __tablename__ = "agent_knowledge_bases"

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="智能体ID",
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="知识库ID",
    )

    __table_args__ = (
        Index("idx_agent_knowledge_bases_agent", "agent_id"),
        Index("idx_agent_knowledge_bases_kb", "knowledge_base_id"),
        {"comment": "智能体-知识库关联表"},
    )


# ---- Graph Knowledge Base (知识图谱) ----


class GraphKnowledgeBase(Base):
    """Graph knowledge base — entities & relationships extracted from documents."""
    __tablename__ = "graph_knowledge_bases"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="知识库名称")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", comment="是否启用"
    )
    visibility: Mapped[str] = mapped_column(
        String(16), default="tenant", server_default="tenant", comment="可见范围: tenant/private"
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建者用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("idx_graph_kbs_tenant_visibility", "tenant_id", "visibility"),
        {"comment": "图谱知识库"},
    )


class GraphDocument(Base):
    """Document inside a graph knowledge base — source text to be chunked & extracted."""
    __tablename__ = "graph_documents"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, comment="所属图谱知识库ID"
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="文档标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="文档原文")
    source_type: Mapped[str] = mapped_column(
        String(16), default="text", server_default="text", comment="来源: text/upload"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", comment="状态: pending/parsing/chunked/failed"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="分块数量")
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建者用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )

    __table_args__ = (
        Index("idx_graph_documents_kb", "knowledge_base_id"),
        {"comment": "图谱文档"},
    )


class GraphChunk(Base):
    """Chunk of a graph document — the unit fed to LLM extraction."""
    __tablename__ = "graph_chunks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="来源文档ID")
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, comment="所属图谱知识库ID"
    )
    seq: Mapped[int] = mapped_column(Integer, default=0, comment="序号")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分块内容")

    __table_args__ = (
        Index("idx_graph_chunks_document", "document_id"),
        Index("idx_graph_chunks_kb", "knowledge_base_id"),
        {"comment": "图谱分块"},
    )


class GraphEntity(Base):
    """Entity node in a graph knowledge base."""
    __tablename__ = "graph_entities"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, comment="所属图谱知识库ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="实体名称")
    name_norm: Mapped[str] = mapped_column(String(128), nullable=False, comment="归一化名称(去空白小写)")
    type: Mapped[str] = mapped_column(String(64), nullable=False, default="entity", server_default="entity", comment="实体类型")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    properties: Mapped[dict | None] = mapped_column(JSONB, comment="扩展属性")
    source_chunk_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="来源分块ID")
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", comment="状态: pending/approved"
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建者用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("idx_graph_entities_kb_name", "knowledge_base_id", "name_norm"),
        Index("idx_graph_entities_kb_type", "knowledge_base_id", "type"),
        UniqueConstraint("knowledge_base_id", "name_norm", name="uq_graph_entities_kb_name"),
        {"comment": "图谱实体"},
    )


class GraphRelationship(Base):
    """Relationship edge between two entities in a graph knowledge base."""
    __tablename__ = "graph_relationships"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, comment="所属图谱知识库ID"
    )
    source_entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, comment="来源实体ID"
    )
    target_entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, comment="目标实体ID"
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="关系类型")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    confidence: Mapped[float] = mapped_column(Float, default=0.8, comment="置信度 0-1")
    source_chunk_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="来源分块ID")
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", comment="状态: pending/approved"
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建者用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("idx_graph_rels_kb_source", "knowledge_base_id", "source_entity_id"),
        Index("idx_graph_rels_kb_type", "knowledge_base_id", "relation_type"),
        {"comment": "图谱关系"},
    )


class GraphExtractionJob(Base):
    """LLM extraction job over a graph knowledge base's documents."""
    __tablename__ = "graph_extraction_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, comment="所属图谱知识库ID"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", comment="状态: pending/running/done/failed"
    )
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, comment="分块总数")
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0, comment="已处理分块数")
    entities_found: Mapped[int] = mapped_column(Integer, default=0, comment="抽取实体数")
    relationships_found: Mapped[int] = mapped_column(Integer, default=0, comment="抽取关系数")
    error: Mapped[str | None] = mapped_column(Text, comment="失败原因")
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建者用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="完成时间")

    __table_args__ = (
        Index("idx_graph_jobs_kb", "knowledge_base_id"),
        {"comment": "图谱抽取任务"},
    )


class AgentGraphKnowledgeBase(Base):
    """Agent-GraphKnowledgeBase many-to-many relationship."""
    __tablename__ = "agent_graph_knowledge_bases"

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="智能体ID",
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        comment="图谱知识库ID",
    )

    __table_args__ = (
        Index("idx_agent_graph_kbs_agent", "agent_id"),
        Index("idx_agent_graph_kbs_kb", "knowledge_base_id"),
        {"comment": "智能体-图谱知识库关联表"},
    )


@event.listens_for(GraphDocument, "before_insert")
@event.listens_for(GraphDocument, "before_update")
def _scrub_graph_document_nul(mapper, connection, target: "GraphDocument") -> None:
    """Strip NUL bytes — Postgres text columns cannot store \\u0000."""
    if target.content is not None:
        target.content = sanitize_pg_text(target.content)
    if target.title is not None:
        target.title = sanitize_pg_text(target.title)


@event.listens_for(GraphChunk, "before_insert")
@event.listens_for(GraphChunk, "before_update")
def _scrub_graph_chunk_nul(mapper, connection, target: "GraphChunk") -> None:
    if target.content is not None:
        target.content = sanitize_pg_text(target.content)


@event.listens_for(GraphEntity, "before_insert")
@event.listens_for(GraphEntity, "before_update")
def _scrub_graph_entity_nul(mapper, connection, target: "GraphEntity") -> None:
    if target.name is not None:
        target.name = sanitize_pg_text(target.name)
    if target.description is not None:
        target.description = sanitize_pg_text(target.description)


@event.listens_for(GraphRelationship, "before_insert")
@event.listens_for(GraphRelationship, "before_update")
def _scrub_graph_relationship_nul(mapper, connection, target: "GraphRelationship") -> None:
    if target.description is not None:
        target.description = sanitize_pg_text(target.description)
    if target.relation_type is not None:
        target.relation_type = sanitize_pg_text(target.relation_type)


# ---- Remote Tools (HTTP Tools) ----


class RemoteTool(Base):
    """Custom remote tool configuration — maps a REST API endpoint to an Agent-callable tool."""
    __tablename__ = "remote_tools"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="工具名称 (function name 规范)")
    label: Mapped[str] = mapped_column(String(128), nullable=False, comment="显示名称 (中文友好)")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="工具描述 (LLM 可见)")
    method: Mapped[str] = mapped_column(String(10), nullable=False, comment="HTTP 方法: GET/POST/PUT/DELETE/PATCH")
    url_template: Mapped[str] = mapped_column(String(1024), nullable=False, comment="URL 模板")
    parameters_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="JSON Schema 参数定义")
    headers: Mapped[dict | None] = mapped_column(JSONB, comment="静态请求头 (不含认证)")
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="none", comment="认证类型: none/bearer/api_key/basic/custom_header")
    auth_config: Mapped[dict | None] = mapped_column(JSONB, comment="认证配置")
    query_params: Mapped[dict | None] = mapped_column(JSONB, comment="Query 参数映射")
    body_template: Mapped[dict | None] = mapped_column(JSONB, comment="请求体模板")
    response_extract: Mapped[str | None] = mapped_column(String(256), comment="响应 JSONPath 提取")
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=30, comment="超时时间 (秒)")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("idx_remote_tools_name", "name"),
        Index("idx_remote_tools_active", "is_active"),
        {"comment": "自定义远程工具配置表"},
    )


# ---- Channels (IM 渠道接入) ----


class ChannelConfig(Base):
    """IM 渠道配置 — 承载飞书/企微/钉钉等渠道的凭证与运行模式。"""
    __tablename__ = "channel_configs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    channel_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="feishu", comment="渠道类型: feishu/dingtalk/wecom"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="渠道显示名称")
    agent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="绑定的 Agent ID")
    app_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="飞书 App ID")
    app_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False, comment="App Secret(加密存储)")
    encrypt_key_encrypted: Mapped[str | None] = mapped_column(Text, comment="Event Encrypt Key(仅 webhook 模式，加密)")
    verification_token_encrypted: Mapped[str | None] = mapped_column(Text, comment="Verification Token(仅 webhook 模式，加密)")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, comment="连接模式: websocket/webhook")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="disabled",
        comment="状态: enabled/disabled/error",
    )
    channel_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True,
        comment="Webhook URL 随机串(创建时生成，避免暴露自增 ID)",
    )
    tool_blacklist: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="渠道级工具黑名单",
    )
    extra_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("{}"),
        comment="渠道类型特有配置(如企微 agentid)",
    )
    enable_streaming: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
        comment="是否启用渠道流式回复",
    )
    last_error: Mapped[str | None] = mapped_column(Text, comment="最近一次错误信息")
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建人 ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    __table_args__ = (
        Index("idx_channel_configs_tenant", "tenant_id"),
        Index("idx_channel_configs_agent", "agent_id"),
        Index("idx_channel_configs_channel_key", "channel_key"),
        {"comment": "IM 渠道配置表"},
    )


class ChannelBinding(Base):
    """渠道外部用户 ↔ 平台用户绑定关系(按租户生效，租户内所有渠道共享)。"""
    __tablename__ = "channel_bindings"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="外部用户 ID(飞书 open_id)")
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="绑定的平台 user ID")
    bind_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="shadow",
        comment="绑定类型: shadow(影子账号) / bound(已关联真实账号)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_channel_binding_external"),
        Index("idx_channel_bindings_user", "user_id"),
        Index("idx_channel_bindings_tenant", "tenant_id"),
        {"comment": "渠道用户绑定表"},
    )


class ChannelBindCode(Base):
    """绑定码 — 飞书用户发送 /bind 后生成，Web 端输入完成账号合并。"""
    __tablename__ = "channel_bind_codes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    code: Mapped[str] = mapped_column(String(8), nullable=False, comment="6 位数字绑定码")
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=DEFAULT_TENANT_ID, comment="所属租户ID"
    )
    channel_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="渠道 ID")
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="外部用户 ID")
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间(默认 10 分钟)"
    )
    used_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="消费该码的平台 user ID")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="消费时间")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )

    __table_args__ = (
        Index("idx_bind_codes_code", "code"),
        Index("idx_bind_codes_channel_external", "channel_id", "external_id"),
        {"comment": "渠道绑定码表"},
    )


class ChannelSessionMapping(Base):
    """渠道会话映射 — (channel_id, chat_id, external_id) → session_id。"""
    __tablename__ = "channel_session_mappings"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    channel_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="渠道 ID")
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="飞书 chat_id(群聊/单聊会话)")
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="外部用户 ID")
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="平台 session ID")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="是否活跃(/new 后置 false)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    __table_args__ = (
        Index(
            "uq_channel_session_active",
            "channel_id", "chat_id", "external_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index("idx_channel_session_session", "session_id"),
        {"comment": "渠道会话映射表"},
    )


# ---- Pets (Codex-compatible pet packages) ----

PET_VISIBILITIES = ("private", "tenant", "public", "official")
PET_STATES = ("idle", "think", "work", "wait", "celebrate", "sad", "sleep", "happy")


class PetPackage(Base):
    """宠物包：兼容 Codex ~/.codex/pets 格式（pet.json + spritesheet.webp）。"""
    __tablename__ = "pet_packages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="宠物标识(pet.json 的 id)")
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, comment="展示名称")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    kind: Mapped[str | None] = mapped_column(String(32), comment="pet.json 的 kind(可选)")
    default_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), comment="包级默认人设智能体ID(领养者未绑定时生效)"
    )
    owner_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="创建人ID")
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="归属租户ID")
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="private",
        comment="可见性: private/tenant/public/official",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active",
        comment="状态: active/taken_down(管理员下架)",
    )
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="pet.json 原文(JSON)")
    row_mapping: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="平台状态→精灵图行号 映射 {idle:0, work:1, ...}",
    )
    actions: Mapped[dict | None] = mapped_column(
        JSONB, comment="动作目录 {row: {name, state}}，上传时自动生成，创建人可改",
    )
    frame_width: Mapped[int] = mapped_column(Integer, nullable=False, comment="帧宽(px)")
    frame_height: Mapped[int] = mapped_column(Integer, nullable=False, comment="帧高(px)")
    col_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="列数(每行帧数)")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="行数(动画数)")
    spritesheet_key: Mapped[str] = mapped_column(String(512), nullable=False, comment="精灵图对象存储key")
    package_key: Mapped[str] = mapped_column(String(512), nullable=False, comment="原始zip对象存储key(导出用)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    __table_args__ = (
        Index("idx_pet_packages_tenant_visibility", "tenant_id", "visibility"),
        Index("idx_pet_packages_owner", "owner_id"),
        {"comment": "宠物包表(Codex格式兼容)"},
    )


class UserPet(Base):
    """用户领养/上传的宠物实例。"""
    __tablename__ = "user_pets"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="用户ID")
    package_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="宠物包ID")
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), comment="实例级绑定智能体ID(优先级高于包级默认)"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否当前激活")
    adopted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="领养时间"
    )
    action_aliases: Mapped[dict | None] = mapped_column(
        JSONB, comment="实例级动作名覆盖 {row: name}，优先级高于包级动作目录"
    )
    state_mapping: Mapped[dict | None] = mapped_column(
        JSONB, comment="实例级状态→行 覆盖 {state: row}，优先级高于包级 row_mapping"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "package_id", name="uq_user_pets_user_package"),
        Index("idx_user_pets_user", "user_id"),
        Index(
            "uq_user_pets_active",
            "user_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        {"comment": "用户宠物表"},
    )


class PetExpLog(Base):
    """宠物行为计数（当前用于智能气泡每日配额计数）。"""
    __tablename__ = "pet_exp_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="用户ID")
    pet_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, comment="用户宠物ID")
    delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="计数变动值(配额流水恒为0)")
    reason: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="来源: bubble_llm(智能气泡配额)",
    )
    ref_id: Mapped[str | None] = mapped_column(String(64), comment="关联ID(如session_id)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )

    __table_args__ = (
        Index("idx_pet_exp_logs_user_date", "user_id", "created_at"),
        {"comment": "宠物行为计数表(气泡配额)"},
    )


# ---- Observability (大模型可观测性) ----

# 三张明细表为观测事实源，异步采集写入（observation/recorder.py）。
# 预聚合表 tool_usage_daily / performance_daily 由采集侧累加，供报表秒查。


class LLMCallLog(Base):
    """每次 LLM 调用一行。"""

    __tablename__ = "llm_call_logs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    trace_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="Agent 执行实例ID")
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="会话ID")
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="用户ID")
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="租户ID")
    agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="智能体ID")
    model_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="模型ID")
    model: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型标识")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, comment="提供商类型")
    call_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="一次执行中的第几轮")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="输入令牌数")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="输出令牌数")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="总令牌数")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="缓存命中令牌数")
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="缓存写入令牌数")
    context_window: Mapped[int | None] = mapped_column(Integer, comment="模型上下文窗口")
    context_utilization: Mapped[float | None] = mapped_column(Float, comment="上下文利用率(输入/窗口)")
    ttft_ms: Mapped[int | None] = mapped_column(Integer, comment="首token延迟(毫秒)")
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="总耗时(毫秒)")
    output_tokens_per_sec: Mapped[float | None] = mapped_column(Float, comment="输出吞吐(tokens/s)")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, comment="重试次数")
    final_status: Mapped[str] = mapped_column(String(16), nullable=False, default="success", comment="最终状态: success/failed")
    error_type: Mapped[str | None] = mapped_column(String(32), comment="错误分类")
    stop_reason: Mapped[str | None] = mapped_column(String(16), comment="停止原因: stop/length/tool_calls/max_tokens")
    temperature: Mapped[float | None] = mapped_column(Float, comment="温度")
    max_tokens: Mapped[int | None] = mapped_column(Integer, comment="最大输出令牌")
    tools_count: Mapped[int] = mapped_column(Integer, default=0, comment="携带工具数")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="调用时间"
    )

    __table_args__ = (
        Index("idx_llm_call_logs_tenant_created", "tenant_id", "created_at"),
        Index("idx_llm_call_logs_session", "session_id"),
        Index("idx_llm_call_logs_trace", "trace_id"),
        {"comment": "LLM调用明细表"},
    )


class ToolCallLog(Base):
    """每次工具调用一行。"""

    __tablename__ = "tool_call_logs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    trace_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="Agent 执行实例ID")
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="会话ID")
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="用户ID")
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="租户ID")
    agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="智能体ID")
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="工具名")
    exec_type: Mapped[str] = mapped_column(String(16), nullable=False, default="direct", comment="执行类型: sandbox/direct/mcp/remote/builtin")
    call_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="执行链路序号")
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="耗时(毫秒)")
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否失败")
    error_type: Mapped[str | None] = mapped_column(String(32), comment="错误分类")
    arg_bytes: Mapped[int | None] = mapped_column(Integer, comment="参数大小(字节)")
    arg_chars: Mapped[int | None] = mapped_column(Integer, comment="参数长度(字符)")
    output_bytes: Mapped[int | None] = mapped_column(Integer, comment="输出大小(字节)")
    output_chars: Mapped[int | None] = mapped_column(Integer, comment="输出长度(字符)")
    est_injected_tokens: Mapped[int | None] = mapped_column(Integer, comment="输出估算令牌")
    is_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="输出是否截断")
    is_concurrent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否并发执行(委派)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="调用时间"
    )

    __table_args__ = (
        Index("idx_tool_call_logs_tenant_tool_created", "tenant_id", "tool_name", "created_at"),
        Index("idx_tool_call_logs_session", "session_id"),
        Index("idx_tool_call_logs_trace", "trace_id"),
        {"comment": "工具调用明细表"},
    )


class AgentTraceLog(Base):
    """每次 Agent 执行（Agent.run）一行。"""

    __tablename__ = "agent_trace_logs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    trace_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False, comment="执行实例ID")
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="会话ID")
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="用户ID")
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="租户ID")
    agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="智能体ID")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed", comment="状态: completed/interrupted/timeout/error")
    iteration_count: Mapped[int] = mapped_column(Integer, default=0, comment="LLM轮数")
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, comment="工具调用总数")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="累计令牌")
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="端到端耗时(毫秒)")
    is_compressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否触发上下文压缩")
    tokens_before_compress: Mapped[int | None] = mapped_column(Integer, comment="压缩前令牌")
    tokens_after_compress: Mapped[int | None] = mapped_column(Integer, comment="压缩后令牌")
    saved_tokens: Mapped[int | None] = mapped_column(Integer, comment="压缩节省令牌")
    summary_tokens: Mapped[int | None] = mapped_column(Integer, comment="摘要令牌")
    hit_max_iterations: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否顶到最大迭代")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="执行时间"
    )

    __table_args__ = (
        Index("idx_agent_trace_logs_tenant_created", "tenant_id", "created_at"),
        Index("idx_agent_trace_logs_session", "session_id"),
        {"comment": "Agent执行明细表"},
    )


class ToolUsageDaily(Base):
    """按 用户×日期×工具 的预聚合（采集侧累加）。"""

    __tablename__ = "tool_usage_daily"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, comment="用户ID")
    date: Mapped[datetime] = mapped_column(Date, primary_key=True, default=func.current_date, comment="统计日期")
    tool_name: Mapped[str] = mapped_column(String(128), primary_key=True, comment="工具名")
    request_count: Mapped[int] = mapped_column(Integer, default=0, comment="调用次数")
    success_count: Mapped[int] = mapped_column(Integer, default=0, comment="成功次数")
    error_count: Mapped[int] = mapped_column(Integer, default=0, comment="失败次数")
    total_duration_ms: Mapped[int] = mapped_column(BigInteger, default=0, comment="总耗时(毫秒)")
    max_duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="最大耗时(毫秒)")
    total_injected_tokens: Mapped[int] = mapped_column(BigInteger, default=0, comment="注入上下文令牌合计")

    __table_args__ = (
        Index("idx_tool_usage_daily_tenant", "user_id", "date"),
        {"comment": "工具用量每日统计表"},
    )


class PerformanceDaily(Base):
    """按 租户×日期 的平台级性能预聚合。"""

    __tablename__ = "performance_daily"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, comment="租户ID")
    date: Mapped[datetime] = mapped_column(Date, primary_key=True, default=func.current_date, comment="统计日期")
    trace_count: Mapped[int] = mapped_column(Integer, default=0, comment="执行次数")
    success_count: Mapped[int] = mapped_column(Integer, default=0, comment="成功次数")
    error_count: Mapped[int] = mapped_column(Integer, default=0, comment="失败次数")
    interrupted_count: Mapped[int] = mapped_column(Integer, default=0, comment="中断次数")
    timeout_count: Mapped[int] = mapped_column(Integer, default=0, comment="超时次数")
    total_duration_ms: Mapped[int] = mapped_column(BigInteger, default=0, comment="总耗时(毫秒)")
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, comment="总令牌")
    llm_call_count: Mapped[int] = mapped_column(Integer, default=0, comment="LLM调用总数")
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, comment="工具调用总数")
    llm_total_duration_ms: Mapped[int] = mapped_column(BigInteger, default=0, comment="LLM总耗时(毫秒)")
    ttft_total_ms: Mapped[int] = mapped_column(BigInteger, default=0, comment="首token总延迟(毫秒)")
    max_llm_duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="最大LLM耗时(毫秒)")
    compress_count: Mapped[int] = mapped_column(Integer, default=0, comment="压缩触发次数")
    saved_tokens_total: Mapped[int] = mapped_column(BigInteger, default=0, comment="压缩节省令牌合计")

    __table_args__ = (
        Index("idx_performance_daily_date", "date"),
        {"comment": "平台性能每日统计表"},
    )


class Hook(Base):
    """Hook 配置：事件 → 动作，global/tenant/agent 三级作用域。"""

    __tablename__ = "hooks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="所属租户ID（global 作用域为 NULL）")
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), comment="创建者用户ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="名称")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="tenant", comment="作用域: global/tenant/agent")
    agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="scope=agent 时目标智能体ID")
    event: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件名")
    action_type: Mapped[str] = mapped_column(String(16), nullable=False, default="webhook", comment="动作类型: webhook/sandbox_command")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, comment="动作参数")
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=5000, comment="动作超时(毫秒)")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="失败重试次数")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="tenant", comment="可见范围: tenant/private")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), server_default=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("idx_hooks_tenant_event", "tenant_id", "event"),
        Index("idx_hooks_agent_event", "agent_id", "event"),
        {"comment": "Hook配置表"},
    )


class HookExecution(Base):
    """Hook 执行日志（每次触发一行）。"""

    __tablename__ = "hook_executions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键ID")
    hook_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), comment="关联HookID")
    event: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件名")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, comment="作用域")
    trace_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="执行实例ID")
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="会话ID")
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="用户ID")
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="租户ID")
    agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), comment="智能体ID")
    action_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="动作类型")
    target: Mapped[str | None] = mapped_column(String(512), comment="URL 或命令（脱敏）")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success", comment="状态: success/failed/timeout/skipped")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="耗时(毫秒)")
    http_status: Mapped[int | None] = mapped_column(Integer, comment="HTTP状态码")
    exit_code: Mapped[int | None] = mapped_column(Integer, comment="命令退出码")
    error: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    response_preview: Mapped[str | None] = mapped_column(Text, comment="响应摘要")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), comment="触发时间"
    )

    __table_args__ = (
        Index("idx_hook_executions_tenant_created", "tenant_id", "created_at"),
        Index("idx_hook_executions_hook_created", "hook_id", "created_at"),
        {"comment": "Hook执行日志表"},
    )
