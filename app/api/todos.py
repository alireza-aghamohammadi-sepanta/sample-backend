"""Todo endpoints for creating, listing, retrieving, updating, and deleting todo items.

All operations derive ownership from the authenticated user (`current_user`)
and scope queries strictly to the user's ID.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.security import current_user
from app.db.session import get_db
from app.models.asset import Asset
from app.models.todo import Todo
from app.schemas.todo import TodoCreate, TodoRead, TodoUpdate

router = APIRouter(prefix="/todos", tags=["todos"])

TODO_NOT_FOUND_DETAIL = "Todo not found"


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
    if payload.asset_ids:
        stmt = (
            select(Asset)
            .where(
                Asset.id.in_(payload.asset_ids),
                Asset.user_id == user_id,
            )
        )
        todo.assets = list(db.scalars(stmt).all())

    db.add(todo)
    db.commit()

    stmt = (
        select(Todo)
        .where(Todo.id == todo.id, Todo.user_id == user_id)
        .options(selectinload(Todo.assets))
    )
    return db.scalar(stmt)  # type: ignore[return-value]


@router.get("", response_model=list[TodoRead], status_code=status.HTTP_200_OK)
@router.get("/", response_model=list[TodoRead], status_code=status.HTTP_200_OK, include_in_schema=False)
def list_todos(
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Sequence[Todo]:
    """List all todo items belonging to the authenticated user."""
    stmt = (
        select(Todo)
        .where(Todo.user_id == user_id)
        .options(selectinload(Todo.assets))
        .order_by(Todo.created_at.asc())
    )
    return db.scalars(stmt).all()


@router.get("/{todo_id}", response_model=TodoRead, status_code=status.HTTP_200_OK)
def get_todo(
    todo_id: uuid.UUID,
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Todo:
    """Retrieve a single todo item belonging to the authenticated user."""
    stmt = (
        select(Todo)
        .where(Todo.id == todo_id, Todo.user_id == user_id)
        .options(selectinload(Todo.assets))
    )
    todo = db.scalar(stmt)
    if todo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=TODO_NOT_FOUND_DETAIL,
        )
    return todo


@router.patch("/{todo_id}", response_model=TodoRead, status_code=status.HTTP_200_OK)
def update_todo(
    todo_id: uuid.UUID,
    payload: TodoUpdate,
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Todo:
    """Partially update a todo item belonging to the authenticated user."""
    stmt = select(Todo).where(Todo.id == todo_id, Todo.user_id == user_id)
    todo = db.scalar(stmt)
    if todo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=TODO_NOT_FOUND_DETAIL,
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(todo, field, value)
    todo.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(todo)
    return todo


@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo(
    todo_id: uuid.UUID,
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a todo item belonging to the authenticated user."""
    stmt = select(Todo).where(Todo.id == todo_id, Todo.user_id == user_id)
    todo = db.scalar(stmt)
    if todo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=TODO_NOT_FOUND_DETAIL,
        )

    db.delete(todo)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
