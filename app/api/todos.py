"""Todo endpoints for creating and listing todo items.

All operations derive ownership from the authenticated user (`current_user`)
and scope queries strictly to the user's ID.
"""

import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.db.session import get_db
from app.models.todo import Todo
from app.schemas.todo import TodoCreate, TodoRead

router = APIRouter(prefix="/todos", tags=["todos"])


@router.post("", response_model=TodoRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=TodoRead, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_todo(
    payload: TodoCreate,
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Todo:
    """Create a new todo item for the authenticated user."""
    todo = Todo(
        user_id=user_id,
        title=payload.title,
        description=payload.description,
    )
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return todo


@router.get("", response_model=list[TodoRead], status_code=status.HTTP_200_OK)
@router.get("/", response_model=list[TodoRead], status_code=status.HTTP_200_OK, include_in_schema=False)
def list_todos(
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Sequence[Todo]:
    """List all todo items belonging to the authenticated user."""
    stmt = select(Todo).where(Todo.user_id == user_id).order_by(Todo.created_at.asc())
    return db.scalars(stmt).all()
