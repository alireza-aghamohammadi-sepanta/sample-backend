"""add assets table

Revision ID: 004
Revises: 003
Create Date: 2026-09-04 04:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the assets table."""
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("todo_id", sa.Uuid(), nullable=True),
        sa.Column("gcs_path", sa.String(length=1024), nullable=False),
        sa.Column("public_url", sa.String(length=2048), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["todo_id"], ["todos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_assets_user_id"),
        "assets",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assets_todo_id"),
        "assets",
        ["todo_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the assets table."""
    op.drop_index(
        op.f("ix_assets_todo_id"),
        table_name="assets",
    )
    op.drop_index(
        op.f("ix_assets_user_id"),
        table_name="assets",
    )
    op.drop_table("assets")
