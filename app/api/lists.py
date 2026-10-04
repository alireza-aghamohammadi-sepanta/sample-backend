"""TodoList endpoints for creating, listing, renaming, and deleting lists.

All operations derive ownership from the authenticated user (`current_user`)
and scope queries strictly to the user's ID.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.db.session import get_db
from app.models.todo import Todo
from app.models.todo_list import TodoList
from app.schemas.todo_list import (
    TodoListCreate,
    TodoListRead,
    TodoListUpdate,
)

router = APIRouter(prefix="/lists", tags=["lists"])
lists_router = router

LIST_NOT_FOUND_DETAIL = "List not found"
DUPLICATE_LIST_NAME_DETAIL = "A list with this name already exists"
CANNOT_DELETE_DEFAULT_LIST_DETAIL = "Cannot delete default list"


@router.post("", response_model=TodoListRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=TodoListRead, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_list(
    payload: TodoListCreate,
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> TodoList:
    """Create a new custom todo list for the authenticated user."""
    existing = db.scalar(
        select(TodoList).where(
            TodoList.user_id == user_id,
            func.lower(TodoList.name) == payload.name.lower(),
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_LIST_NAME_DETAIL,
        )

    todo_list = TodoList(
        user_id=user_id,
        name=payload.name,
        is_default=False,
    )
    db.add(todo_list)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_LIST_NAME_DETAIL,
        )

    db.refresh(todo_list)
    return todo_list


@router.get("", response_model=list[TodoListRead], status_code=status.HTTP_200_OK)
@router.get("/", response_model=list[TodoListRead], status_code=status.HTTP_200_OK, include_in_schema=False)
def list_lists(
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Sequence[TodoList]:
    """List all todo lists belonging to the authenticated user.

    The default list is always pinned first, followed by custom lists in alphabetical order.
    """
    stmt = (
        select(TodoList)
        .where(TodoList.user_id == user_id)
        .order_by(
            TodoList.is_default.desc(),
            func.lower(TodoList.name).asc(),
        )
    )
    return db.scalars(stmt).all()


@router.patch("/{id}", response_model=TodoListRead, status_code=status.HTTP_200_OK)
def update_list(
    id: uuid.UUID,
    payload: TodoListUpdate,
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> TodoList:
    """Rename a custom or default todo list for the authenticated user."""
    stmt = select(TodoList).where(TodoList.id == id, TodoList.user_id == user_id)
    todo_list = db.scalar(stmt)
    if todo_list is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=LIST_NOT_FOUND_DETAIL,
        )

    if payload.name.lower() != todo_list.name.lower():
        existing = db.scalar(
            select(TodoList).where(
                TodoList.user_id == user_id,
                TodoList.id != id,
                func.lower(TodoList.name) == payload.name.lower(),
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=DUPLICATE_LIST_NAME_DETAIL,
            )

    todo_list.name = payload.name
    todo_list.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_LIST_NAME_DETAIL,
        )

    db.refresh(todo_list)
    return todo_list


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_list(
    id: uuid.UUID,
    user_id: uuid.UUID = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a custom todo list and atomically reassign its tasks to the default list."""
    stmt = select(TodoList).where(TodoList.id == id, TodoList.user_id == user_id)
    todo_list = db.scalar(stmt)
    if todo_list is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=LIST_NOT_FOUND_DETAIL,
        )

    if todo_list.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=CANNOT_DELETE_DEFAULT_LIST_DETAIL,
        )

    stmt_default = select(TodoList).where(
        TodoList.user_id == user_id,
        TodoList.is_default == True,  # noqa: E712
    )
    default_list = db.scalar(stmt_default)
    if default_list is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Default list not found",
        )

    db.execute(
        update(Todo)
        .where(Todo.list_id == id, Todo.user_id == user_id)
        .values(list_id=default_list.id)
    )
    db.delete(todo_list)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
