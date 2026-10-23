"""Contextos (@pc, @casa, @calle...): dónde o con qué puedes hacer una acción."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Context, Task, User, available_tasks
from app.schemas import (
    BIGINT_MAX,
    ContextDeleted,
    ContextIn,
    ContextListOut,
    ContextOut,
    ContextWrite,
)

router = APIRouter(tags=["contextos"])

ContextId = Annotated[int, Path(ge=1, le=BIGINT_MAX)]


def _counted(user: User):
    """SELECT de contextos con cuántas siguientes acciones tienen, y cuántas están disponibles."""
    next_tasks = (
        select(func.count(Task.id))
        .where(Task.context_id == Context.id, Task.status == "next")
        .correlate(Context)
        .scalar_subquery()
    )
    available = (
        select(func.count(Task.id))
        .where(
            Task.context_id == Context.id,
            Task.id.in_(select(available_tasks.c.id).where(available_tasks.c.user_id == user.id)),
        )
        .correlate(Context)
        .scalar_subquery()
    )
    return select(
        Context.id, Context.name, next_tasks.label("next_tasks"), available.label("available_tasks")
    ).where(Context.user_id == user.id)


def _one(session: Session, user: User, context_id: int):
    row = session.execute(_counted(user).where(Context.id == context_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Contexto no encontrado")
    return row


@router.get("/contexts", response_model=ContextListOut)
def list_contexts(
    user: User = Depends(current_user), session: Session = Depends(get_session)
) -> ContextListOut:
    """Tus contextos por nombre, con cuántas siguientes acciones tiene cada uno."""
    rows = session.execute(_counted(user).order_by(Context.name)).all()
    return ContextListOut(count=len(rows), items=[ContextOut(**r._mapping) for r in rows])


@router.post("/contexts", response_model=ContextWrite, status_code=201)
def create_context(
    body: ContextIn,
    response: Response,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ContextWrite:
    """Crea un contexto (se normaliza: 'PC' -> '@pc'). Idempotente: si existe responde 200."""
    inserted = session.execute(
        pg_insert(Context)
        .values(user_id=user.id, name=body.name)
        .on_conflict_do_nothing(index_elements=["user_id", "name"])
        .returning(Context.id)
    ).first()
    context_id = inserted[0] if inserted else session.scalar(
        select(Context.id).where(Context.user_id == user.id, Context.name == body.name)
    )
    response.status_code = 201 if inserted else 200
    result = ContextWrite(**_one(session, user, context_id)._mapping, created=inserted is not None)
    session.commit()
    return result


@router.patch("/contexts/{context_id}", response_model=ContextOut)
def rename_context(
    context_id: ContextId,
    body: ContextIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ContextOut:
    """Renombra un contexto; sus tareas lo conservan. 409 si ya existe otro con ese nombre."""
    context = session.scalar(
        select(Context).where(Context.id == context_id, Context.user_id == user.id).with_for_update()
    )
    if context is None:
        raise HTTPException(status_code=404, detail="Contexto no encontrado")
    if context.name != body.name:
        try:
            with session.begin_nested():
                context.name = body.name
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"Ya existe el contexto {body.name}"
            ) from exc
    result = ContextOut(**_one(session, user, context.id)._mapping)
    session.commit()
    return result


@router.delete("/contexts/{context_id}", response_model=ContextDeleted)
def delete_context(
    context_id: ContextId,
    force: bool = Query(default=False, description="Borrarlo aunque haya tareas que lo usen"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ContextDeleted:
    """Borra un contexto. Si lo usan tareas responde 409, salvo con `force=true`, que se lo
    quita a esas tareas (las tareas no se tocan más)."""
    context = session.scalar(
        select(Context).where(Context.id == context_id, Context.user_id == user.id).with_for_update()
    )
    if context is None:
        raise HTTPException(status_code=404, detail="Contexto no encontrado")
    used = session.scalar(select(func.count(Task.id)).where(Task.context_id == context.id))
    if used and not force:
        raise HTTPException(
            status_code=409,
            detail=f"El contexto lo usan {used} tareas: usa force=true para quitárselo",
        )
    session.execute(update(Task).where(Task.context_id == context.id).values(context_id=None))
    session.execute(delete(Context).where(Context.id == context.id))
    session.commit()
    return ContextDeleted(id=context_id, deleted=True, tasks_updated=used)
