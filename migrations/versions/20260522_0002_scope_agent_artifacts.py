"""scope agent artifacts by owner

Revision ID: 20260522_0002
Revises: 20260521_0001
Create Date: 2026-05-22 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260522_0002"
down_revision = "20260521_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_processed_datasets", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_agent_processed_datasets_owner_user_id_users",
        "agent_processed_datasets",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_agent_processed_datasets_owner_user_id", "agent_processed_datasets", ["owner_user_id"], unique=False)
    op.drop_constraint("uq_agent_processed_datasets_dataset_hash", "agent_processed_datasets", type_="unique")
    op.drop_constraint("uq_agent_processed_datasets_dataset_name", "agent_processed_datasets", type_="unique")
    op.create_unique_constraint(
        "uq_agent_processed_datasets_owner_hash",
        "agent_processed_datasets",
        ["owner_user_id", "dataset_hash"],
    )
    op.create_unique_constraint(
        "uq_agent_processed_datasets_owner_name",
        "agent_processed_datasets",
        ["owner_user_id", "dataset_name"],
    )

    op.add_column("agent_processed_emails", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_agent_processed_emails_owner_user_id_users",
        "agent_processed_emails",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_agent_processed_emails_owner_user_id", "agent_processed_emails", ["owner_user_id"], unique=False)
    op.create_unique_constraint(
        "uq_agent_processed_email_owner_uid_status",
        "agent_processed_emails",
        ["owner_user_id", "email_uid", "status"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_agent_processed_email_owner_uid_status", "agent_processed_emails", type_="unique")
    op.drop_index("ix_agent_processed_emails_owner_user_id", table_name="agent_processed_emails")
    op.drop_constraint("fk_agent_processed_emails_owner_user_id_users", "agent_processed_emails", type_="foreignkey")
    op.drop_column("agent_processed_emails", "owner_user_id")

    op.drop_constraint("uq_agent_processed_datasets_owner_name", "agent_processed_datasets", type_="unique")
    op.drop_constraint("uq_agent_processed_datasets_owner_hash", "agent_processed_datasets", type_="unique")
    op.create_unique_constraint("uq_agent_processed_datasets_dataset_name", "agent_processed_datasets", ["dataset_name"])
    op.create_unique_constraint("uq_agent_processed_datasets_dataset_hash", "agent_processed_datasets", ["dataset_hash"])
    op.drop_index("ix_agent_processed_datasets_owner_user_id", table_name="agent_processed_datasets")
    op.drop_constraint("fk_agent_processed_datasets_owner_user_id_users", "agent_processed_datasets", type_="foreignkey")
    op.drop_column("agent_processed_datasets", "owner_user_id")
