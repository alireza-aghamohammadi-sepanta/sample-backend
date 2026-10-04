"""add due_date to todos

Revision ID: 006
Revises: 005
Create Date: 2026-10-04 07:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, Sequence[str], None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add due_date column to the todos table."""
    op.add_column("todos", sa.Column("due_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop due_date column from the todos table."""
    op.drop_column("todos", "due_date")
