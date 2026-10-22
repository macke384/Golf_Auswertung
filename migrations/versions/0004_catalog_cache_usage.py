"""Zwischenspeicher und Kontingentzähler für externe Platzkataloge.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_cache",
        sa.Column("source", sa.String(30), primary_key=True),
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "api_usage",
        sa.Column("source", sa.String(30), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint("count >= 0", name="ck_api_usage_count"),
    )


def downgrade() -> None:
    op.drop_table("api_usage")
    op.drop_table("catalog_cache")
