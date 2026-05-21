"""initial multi-tenant saas schema

Revision ID: 20260521_0001
Revises:
Create Date: 2026-05-21 00:00:00
"""
from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa

try:
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover - optional dependency
    Vector = None

revision = "20260521_0001"
down_revision = None
branch_labels = None
depends_on = None


def _pgvector_enabled() -> bool:
    return os.getenv("PGVECTOR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _embedding_type():
    return Vector(384) if Vector is not None and _pgvector_enabled() else sa.JSON


def upgrade() -> None:
    if Vector is not None and _pgvector_enabled():
        try:
            op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="teacher"),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("tenant_key", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"], unique=False)
    op.create_index("ix_users_tenant_key", "users", ["tenant_key"], unique=False)

    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="upload"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_dataset_owner_name"),
    )
    op.create_index("ix_datasets_owner_user_id", "datasets", ["owner_user_id"], unique=False)
    op.create_index("ix_datasets_name", "datasets", ["name"], unique=False)

    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("usn", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sgpa", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_user_id", "usn", name="uq_student_owner_usn"),
    )
    op.create_index("ix_students_owner_user_id", "students", ["owner_user_id"], unique=False)
    op.create_index("ix_students_usn", "students", ["usn"], unique=False)

    op.create_table(
        "student_semesters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sgpa", sa.Float(), nullable=False),
        sa.Column("cgpa", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_user_id", "student_id", "dataset_id", "semester", name="uq_semester_owner_student_dataset"),
    )
    op.create_index("ix_student_semesters_owner_user_id", "student_semesters", ["owner_user_id"], unique=False)
    op.create_index("ix_student_semesters_student_id", "student_semesters", ["student_id"], unique=False)
    op.create_index("ix_student_semesters_dataset_id", "student_semesters", ["dataset_id"], unique=False)

    op.create_table(
        "results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_semester_id", sa.Integer(), sa.ForeignKey("student_semesters.id", ondelete="CASCADE"), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("grade", sa.String(length=32), nullable=False),
        sa.Column("gp", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_results_owner_user_id", "results", ["owner_user_id"], unique=False)
    op.create_index("ix_results_student_id", "results", ["student_id"], unique=False)
    op.create_index("ix_results_student_semester_id", "results", ["student_semester_id"], unique=False)
    op.create_index("ix_results_subject", "results", ["subject"], unique=False)

    op.create_table(
        "semantic_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=False, server_default="student"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("embedding", _embedding_type(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_user_id", "content_hash", name="uq_semantic_document_owner_hash"),
    )
    op.create_index("ix_semantic_documents_owner_user_id", "semantic_documents", ["owner_user_id"], unique=False)
    op.create_index("ix_semantic_documents_dataset_id", "semantic_documents", ["dataset_id"], unique=False)
    op.create_index("ix_semantic_documents_student_id", "semantic_documents", ["student_id"], unique=False)
    op.create_index("ix_semantic_documents_content_hash", "semantic_documents", ["content_hash"], unique=False)
    if Vector is not None and _pgvector_enabled():
        op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_semantic_documents_embedding ON semantic_documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"))

    op.create_table(
        "agent_processed_datasets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_hash", sa.String(length=128), nullable=False),
        sa.Column("dataset_name", sa.String(length=255), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("processed_excel_path", sa.String(length=1024), nullable=False),
        sa.Column("report_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("dataset_hash", name="uq_agent_processed_datasets_dataset_hash"),
        sa.UniqueConstraint("dataset_name", name="uq_agent_processed_datasets_dataset_name"),
    )
    op.create_index("ix_agent_processed_datasets_dataset_hash", "agent_processed_datasets", ["dataset_hash"], unique=False)
    op.create_index("ix_agent_processed_datasets_dataset_name", "agent_processed_datasets", ["dataset_name"], unique=False)

    op.create_table(
        "agent_processed_emails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_uid", sa.String(length=255), nullable=False),
        sa.Column("message_id", sa.String(length=512), nullable=True),
        sa.Column("sender", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("attachment_name", sa.String(length=512), nullable=True),
        sa.Column("dataset_hash", sa.String(length=128), nullable=True),
        sa.Column("report_path", sa.String(length=1024), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_agent_processed_emails_email_uid", "agent_processed_emails", ["email_uid"], unique=False)
    op.create_index("ix_agent_processed_emails_message_id", "agent_processed_emails", ["message_id"], unique=False)
    op.create_index("ix_agent_processed_emails_sender", "agent_processed_emails", ["sender"], unique=False)
    op.create_index("ix_agent_processed_emails_status", "agent_processed_emails", ["status"], unique=False)
    op.create_index("ix_agent_processed_emails_dataset_hash", "agent_processed_emails", ["dataset_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_agent_processed_emails_dataset_hash", table_name="agent_processed_emails")
    op.drop_index("ix_agent_processed_emails_status", table_name="agent_processed_emails")
    op.drop_index("ix_agent_processed_emails_sender", table_name="agent_processed_emails")
    op.drop_index("ix_agent_processed_emails_message_id", table_name="agent_processed_emails")
    op.drop_index("ix_agent_processed_emails_email_uid", table_name="agent_processed_emails")
    op.drop_table("agent_processed_emails")

    op.drop_index("ix_agent_processed_datasets_dataset_name", table_name="agent_processed_datasets")
    op.drop_index("ix_agent_processed_datasets_dataset_hash", table_name="agent_processed_datasets")
    op.drop_table("agent_processed_datasets")

    op.drop_index("ix_semantic_documents_content_hash", table_name="semantic_documents")
    op.drop_index("ix_semantic_documents_student_id", table_name="semantic_documents")
    op.drop_index("ix_semantic_documents_dataset_id", table_name="semantic_documents")
    op.drop_index("ix_semantic_documents_owner_user_id", table_name="semantic_documents")
    op.drop_table("semantic_documents")

    op.drop_index("ix_results_subject", table_name="results")
    op.drop_index("ix_results_student_semester_id", table_name="results")
    op.drop_index("ix_results_student_id", table_name="results")
    op.drop_index("ix_results_owner_user_id", table_name="results")
    op.drop_table("results")

    op.drop_index("ix_student_semesters_dataset_id", table_name="student_semesters")
    op.drop_index("ix_student_semesters_student_id", table_name="student_semesters")
    op.drop_index("ix_student_semesters_owner_user_id", table_name="student_semesters")
    op.drop_table("student_semesters")

    op.drop_index("ix_students_usn", table_name="students")
    op.drop_index("ix_students_owner_user_id", table_name="students")
    op.drop_table("students")

    op.drop_index("ix_datasets_name", table_name="datasets")
    op.drop_index("ix_datasets_owner_user_id", table_name="datasets")
    op.drop_table("datasets")

    op.drop_index("ix_users_tenant_key", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
