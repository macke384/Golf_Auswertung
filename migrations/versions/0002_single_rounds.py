"""Einzelrunden: tournaments.kind und note, Eindeutigkeit nur für Turniere.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tournaments",
        sa.Column("kind", sa.String(12), nullable=False, server_default="tournament"),
    )
    op.add_column("tournaments", sa.Column("note", sa.String(300), nullable=True))
    op.create_check_constraint("ck_tournaments_kind", "tournaments", "kind IN ('tournament','single')")
    op.drop_constraint("uq_tournaments_name_date", "tournaments", type_="unique")
    op.create_index(
        "uq_tournaments_name_date", "tournaments", ["name", "played_on"], unique=True,
        postgresql_where=sa.text("kind = 'tournament'"),
    )


def downgrade() -> None:
    # Einzelrunden passen nicht in das alte Schema (Namen nicht eindeutig) und werden entfernt.
    op.execute("DELETE FROM tournaments WHERE kind = 'single'")
    op.drop_index("uq_tournaments_name_date", table_name="tournaments")
    op.create_unique_constraint("uq_tournaments_name_date", "tournaments", ["name", "played_on"])
    op.drop_constraint("ck_tournaments_kind", "tournaments", type_="check")
    op.drop_column("tournaments", "note")
    op.drop_column("tournaments", "kind")
