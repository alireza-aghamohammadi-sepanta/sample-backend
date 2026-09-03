"""ORM models.

Importing this package registers every model on ``Base.metadata``, which is
what Alembic uses as autogenerate target.
"""

from app.models.asset import Asset
from app.models.password_reset_token import PasswordResetToken
from app.models.todo import Todo
from app.models.user import User

__all__ = ["Asset", "PasswordResetToken", "Todo", "User"]
