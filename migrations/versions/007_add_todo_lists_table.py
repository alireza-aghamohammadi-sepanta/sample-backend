"""add todo_lists table

Revision ID: 007
Revises: 006
Create Date: 2026-10-04 08:00:00.000000

"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, Sequence[str], None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create todo_lists table, seed default Inbox for existing users, and backfill todos."""
    op.create_table(
        "todo_lists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_todo_lists_user_id"),
        "todo_lists",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_todo_lists_user_id_lower_name",
        "todo_lists",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )

    with op.batch_alter_table("todos") as batch_op:
        batch_op.add_column(sa.Column("list_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_todos_list_id_todo_lists",
            "todo_lists",
            ["list_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            op.f("ix_todos_list_id"),
            ["list_id"],
            unique=False,
        )

    # Provision default Inbox for existing users and backfill todos.list_id
    connection = op.get_bind()
    now = datetime.now(timezone.utc)
    users = connection.execute(
        sa.select(sa.column("id", sa.Uuid())).select_from(sa.table("users"))
    ).fetchall()

    for (raw_uid,) in users:
        uid = uuid.UUID(str(raw_uid)) if not isinstance(raw_uid, uuid.UUID) else raw_uid
        inbox_id = uuid.uuid4()
        connection.execute(
            sa.insert(
                sa.table(
                    "todo_lists",
                    sa.column("id", sa.Uuid()),
                    sa.column("user_id", sa.Uuid()),
                    sa.column("name", sa.String()),
                    sa.column("is_default", sa.Boolean()),
                    sa.column("created_at", sa.DateTime(timezone=True)),
                    sa.column("updated_at", sa.DateTime(timezone=True)),
                )
            ).values(
                id=inbox_id,
                user_id=uid,
                name="Inbox",
                is_default=True,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            sa.update(
                sa.table(
                    "todos",
                    sa.column("user_id", sa.Uuid()),
                    sa.column("list_id", sa.Uuid()),
                )
            )
            .where(sa.column("user_id") == uid)
            .values(list_id=inbox_id)
        )

    with op.batch_alter_table("todos") as batch_op:
        batch_op.alter_column("list_id", nullable=False)


def downgrade() -> None:
    """Drop todo_lists table and remove list_id from todos."""
    with op.batch_alter_table("todos") as batch_op:
        batch_op.drop_constraint("fk_todos_list_id_todo_lists", type_="foreignkey")
        batch_op.drop_index(op.f("ix_todos_list_id"))
        batch_op.drop_column("list_id")

    op.drop_index("uq_todo_lists_user_id_lower_name", table_name="todo_lists")
    op.drop_index(op.f("ix_todo_lists_user_id"), table_name="todo_lists")
    op.drop_table("todo_lists")
