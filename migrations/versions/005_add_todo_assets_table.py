"""add todo_assets table

Revision ID: 005
Revises: 004
Create Date: 2026-09-14 08:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, Sequence[str], None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the todo_assets table."""
    op.create_table(
        "todo_assets",
        sa.Column("todo_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["todo_id"], ["todos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("todo_id", "asset_id"),
    )


def downgrade() -> None:
    """Drop the todo_assets table."""
    op.drop_table("todo_assets")
