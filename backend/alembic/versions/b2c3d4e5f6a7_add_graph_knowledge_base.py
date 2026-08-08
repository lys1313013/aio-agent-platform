"""add graph knowledge base tables (图谱知识库)

Revision ID: b2c3d4e5f6a7
Revises: 9a0b1c2d3e4f
Create Date: 2026-08-07

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "9a0b1c2d3e4f"
branch_labels = None
depends_on = None


def _uuid(**kwargs) -> UUID:
    return UUID(as_uuid=True, **kwargs)


def upgrade() -> None:
    op.create_table(
        "graph_knowledge_bases",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("visibility", sa.String(16), server_default="tenant", nullable=False),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="图谱知识库",
    )
    op.create_index("idx_graph_kbs_tenant_visibility", "graph_knowledge_bases", ["tenant_id", "visibility"])

    op.create_table(
        "graph_documents",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("knowledge_base_id", _uuid(), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(16), server_default="text", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="图谱文档",
    )
    op.create_index("idx_graph_documents_kb", "graph_documents", ["knowledge_base_id"])

    op.create_table(
        "graph_chunks",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("document_id", _uuid(), nullable=False),
        sa.Column("knowledge_base_id", _uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="图谱分块",
    )
    op.create_index("idx_graph_chunks_document", "graph_chunks", ["document_id"])
    op.create_index("idx_graph_chunks_kb", "graph_chunks", ["knowledge_base_id"])

    op.create_table(
        "graph_entities",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("knowledge_base_id", _uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("name_norm", sa.String(128), nullable=False),
        sa.Column("type", sa.String(64), server_default="entity", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("properties", JSONB(), nullable=True),
        sa.Column("source_chunk_id", _uuid(), nullable=True),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_base_id", "name_norm", name="uq_graph_entities_kb_name"),
        comment="图谱实体",
    )
    op.create_index("idx_graph_entities_kb_name", "graph_entities", ["knowledge_base_id", "name_norm"])
    op.create_index("idx_graph_entities_kb_type", "graph_entities", ["knowledge_base_id", "type"])

    op.create_table(
        "graph_relationships",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("knowledge_base_id", _uuid(), nullable=False),
        sa.Column("source_entity_id", _uuid(), nullable=False),
        sa.Column("target_entity_id", _uuid(), nullable=False),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default=sa.text("0.8"), nullable=False),
        sa.Column("source_chunk_id", _uuid(), nullable=True),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="图谱关系",
    )
    op.create_index("idx_graph_rels_kb_source", "graph_relationships", ["knowledge_base_id", "source_entity_id"])
    op.create_index("idx_graph_rels_kb_type", "graph_relationships", ["knowledge_base_id", "relation_type"])

    op.create_table(
        "graph_extraction_jobs",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("knowledge_base_id", _uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("total_chunks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("processed_chunks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("entities_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("relationships_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="图谱抽取任务",
    )
    op.create_index("idx_graph_jobs_kb", "graph_extraction_jobs", ["knowledge_base_id"])

    op.create_table(
        "agent_graph_knowledge_bases",
        sa.Column("agent_id", _uuid(), nullable=False),
        sa.Column("knowledge_base_id", _uuid(), nullable=False),
        sa.PrimaryKeyConstraint("agent_id", "knowledge_base_id"),
        comment="智能体-图谱知识库关联表",
    )
    op.create_index("idx_agent_graph_kbs_agent", "agent_graph_knowledge_bases", ["agent_id"])
    op.create_index("idx_agent_graph_kbs_kb", "agent_graph_knowledge_bases", ["knowledge_base_id"])


def downgrade() -> None:
    op.drop_table("agent_graph_knowledge_bases")
    op.drop_table("graph_extraction_jobs")
    op.drop_table("graph_relationships")
    op.drop_table("graph_entities")
    op.drop_table("graph_chunks")
    op.drop_table("graph_documents")
    op.drop_table("graph_knowledge_bases")
