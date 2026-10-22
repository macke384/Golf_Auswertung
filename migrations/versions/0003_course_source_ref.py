"""Herkunft importierter Plätze (courses.source_ref).

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("courses", sa.Column("source_ref", sa.String(200), nullable=True))
    op.create_unique_constraint("courses_source_ref_key", "courses", ["source_ref"])


def downgrade() -> None:
    op.drop_constraint("courses_source_ref_key", "courses", type_="unique")
    op.drop_column("courses", "source_ref")
