"""Todo endpoints for creating, listing, retrieving, updating, and deleting todo items.

All operations derive ownership from the authenticated user (`current_user`)
and scope queries strictly to the user's ID.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload

from app.core.security import current_user
from app.db.session import get_db
from app.models.asset import Asset
from app.models.todo import Todo
from app.schemas.todo import TodoCreate, TodoRead, TodoUpdate

router = APIRouter(prefix="/todos", tags=["todos"])

TODO_NOT_FOUND_DETAIL = "Todo not found"
INVALID_ASSET_IDS_DETAIL = "Invalid asset IDs"


def _validate_and_get_assets(
    db: Session,
    asset_ids: list[uuid.UUID],
    user_id: uuid.UUID,
) -> list[Asset]:
    """Validate that all asset IDs exist and belong to the user, returning them in order."""
    unique_ids = list(dict.fromkeys(asset_ids))
    if not unique_ids:
        return []
    stmt = (
        select(Asset)
        .where(
            Asset.id.in_(unique_ids),
            Asset.user_id == user_id,
        )
    )
    found_assets = list(db.scalars(stmt).all())
    if len(found_assets) != len(unique_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_ASSET_IDS_DETAIL,
        )
    assets_by_id = {asset.id: asset for asset in found_assets}
    return [assets_by_id[aid] for aid in unique_ids]


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
        due_date=payload.due_date,
    )
    if payload.asset_ids:
        todo.assets = _validate_and_get_assets(db, payload.asset_ids, user_id)

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
        .order_by(
            case(
                (Todo.is_completed, 2),
                (Todo.due_date.is_(None), 1),
                else_=0,
            ).asc(),
            case(
                (~Todo.is_completed, Todo.due_date),
                else_=None,
            ).asc(),
            Todo.created_at.asc(),
        )
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

    update_data = payload.model_dump(exclude_unset=True)
    if "asset_ids" in update_data:
        asset_ids = update_data.pop("asset_ids")
        if asset_ids:
            todo.assets = _validate_and_get_assets(db, asset_ids, user_id)
        else:
            todo.assets = []

    for field, value in update_data.items():
        setattr(todo, field, value)
    todo.updated_at = datetime.now(timezone.utc)
    db.commit()

    stmt = (
        select(Todo)
        .where(Todo.id == todo.id, Todo.user_id == user_id)
        .options(selectinload(Todo.assets))
    )
    return db.scalar(stmt)  # type: ignore[return-value]


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
