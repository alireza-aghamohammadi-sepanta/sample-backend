"""ORM models.

Importing this package registers every model on ``Base.metadata``, which is
what Alembic uses as autogenerate target.
"""

from app.models.user import User

__all__ = ["User"]
