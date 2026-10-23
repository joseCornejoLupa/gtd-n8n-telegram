"""Lógica compartida por los endpoints."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Context, Project, Task

_CONTEXT_RE = re.compile(r"^@[\w-]{1,40}$")


def normalize_context_name(raw: str) -> str:
    """'PC' -> '@pc'. Minúsculas y '@' obligatorio, para que '@PC' y '@pc' no sean dos contextos."""
    name = raw.strip().lower()
    if not name.startswith("@"):
        name = "@" + name
    if not _CONTEXT_RE.fullmatch(name):
        raise ValueError(
            "Contexto inválido: usa letras, números o guiones (hasta 40 caracteres), sin espacios"
        )
    return name


def get_or_create_context(session: Session, user_id: int, name: str) -> Context:
    context = session.scalar(
        select(Context).where(Context.user_id == user_id, Context.name == name)
    )
    if context is None:
        session.execute(
            pg_insert(Context)
            .values(user_id=user_id, name=name)
            .on_conflict_do_nothing(index_elements=["user_id", "name"])
        )
        context = session.scalar(
            select(Context).where(Context.user_id == user_id, Context.name == name)
        )
    return context


def user_zone(user) -> ZoneInfo:
    """Zona horaria del usuario: en ella se leen las fechas sin zona y se escriben las respuestas."""
    return ZoneInfo(user.timezone)


def localize(value: datetime | None, timezone_name: str) -> datetime | None:
    """Una fecha sin zona horaria se interpreta en la zona del usuario; con zona, se respeta."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=ZoneInfo(timezone_name))


def append_to_project(session: Session, task: Task, project: Project) -> None:
    """Asigna la tarea al proyecto. En un proyecto secuencial va al final de la fila.

    El llamador debe tener bloqueada la fila del proyecto (SELECT ... FOR UPDATE) para que
    dos peticiones simultáneas no calculen la misma posición.
    """
    task.project_id = project.id
    if project.sequential:
        last = session.scalar(
            select(func.max(Task.position)).where(Task.project_id == project.id)
        )
        task.position = (last or 0) + 1
    else:
        # Un proyecto paralelo no usa posiciones; la de un proyecto anterior no debe arrastrarse.
        task.position = None


def is_available(session: Session, task_id: int) -> bool:
    """¿Aparece ya en 'siguientes acciones'? Consulta la vista available_tasks."""
    return (
        session.execute(
            text("SELECT 1 FROM available_tasks WHERE id = :id"), {"id": task_id}
        ).first()
        is not None
    )


_STATS_SQL = text("""
    SELECT project_id,
      count(*) FILTER (WHERE status IN ('next','waiting')
                       AND (defer_until IS NULL OR defer_until <= now())) AS live,
      count(*) FILTER (WHERE status IN ('next','waiting','someday')) AS open
    FROM tasks WHERE project_id IN :ids GROUP BY project_id""").bindparams(
    bindparam("ids", expanding=True)
)


def project_stats(session: Session, project_ids: list[int]) -> dict[int, tuple[int, int]]:
    """{proyecto: (tareas vivas, tareas abiertas)}. Los proyectos sin tareas no aparecen.

    Viva = siguiente o en espera y no diferida a futuro: es lo que mantiene el proyecto
    avanzando. Abierta = viva, diferida o de 'algún día'. Un proyecto activo sin tareas
    vivas está estancado.
    """
    if not project_ids:
        return {}
    rows = session.execute(_STATS_SQL, {"ids": list(project_ids)})
    return {r.project_id: (r.live, r.open) for r in rows}


def project_counts(session: Session, project_id: int) -> tuple[int, int]:
    """(tareas vivas, tareas abiertas) de un proyecto; ver `project_stats`."""
    return project_stats(session, [project_id]).get(project_id, (0, 0))


# --- Orden y alta de tareas en proyectos -------------------------------------------------


def lock_project(session: Session, user_id: int, project_id: int) -> Project | None:
    """Bloquea el proyecto del usuario para modificarlo (None si no existe o no es suyo).

    Primero las filas de sus tareas y luego la del proyecto: es el mismo orden que usan los
    endpoints de tareas (tarea y después proyecto), lo que evita interbloqueos.
    """
    owned = session.scalar(
        select(Project.id).where(Project.id == project_id, Project.user_id == user_id)
    )
    if owned is None:
        return None
    session.execute(
        select(Task.id).where(Task.project_id == project_id).order_by(Task.id).with_for_update()
    )
    return session.scalar(select(Project).where(Project.id == project_id).with_for_update())


def ordered_tasks(session: Session, project_id: int) -> list[Task]:
    """Las tareas con posición de un proyecto secuencial, en orden."""
    return list(
        session.scalars(
            select(Task)
            .where(Task.project_id == project_id, Task.position.is_not(None))
            .order_by(Task.position, Task.id)
        )
    )


def renumber(session: Session, ordered: list[Task]) -> None:
    """Deja las posiciones como 1..n según el orden dado.

    La restricción única (proyecto, posición) es DEFERRABLE para poder intercambiar posiciones
    dentro de una transacción; se verifica aquí mismo para que un fallo salga antes de responder.
    """
    for position, task in enumerate(ordered, start=1):
        task.position = position
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def insertion_index(ordered: list[Task], after_task_id: int | None, first: bool | None) -> int:
    """Índice donde insertar: 0 si `first`, tras `after_task_id`, o al final."""
    if first:
        return 0
    if after_task_id is None:
        return len(ordered)
    for index, task in enumerate(ordered):
        if task.id == after_task_id:
            return index + 1
    raise LookupError("after_task_id no es una tarea ordenada de este proyecto")


def step_ref(project_id: int, request_id: str, number: int) -> str:
    """source_ref de una tarea creada con `request_id`. Incluye el proyecto: la misma clave
    usada en dos proyectos distintos son dos peticiones distintas."""
    return f"{project_id}:{request_id}#{number}"


class NotSequentialError(ValueError):
    pass


def create_steps(
    session: Session,
    user,
    project: Project,
    steps: list,
    *,
    after_task_id: int | None = None,
    first: bool | None = None,
    request_id: str | None = None,
) -> list[Task]:
    """Crea tareas ya clarificadas dentro del proyecto (bloqueado por el llamador).

    En un proyecto secuencial se insertan, en el orden dado, al final o donde se indique.
    """
    ordered_placement = after_task_id is not None or bool(first)
    if ordered_placement and not project.sequential:
        raise NotSequentialError("El proyecto no es secuencial: sus tareas no tienen orden")
    ordered = ordered_tasks(session, project.id) if project.sequential else []
    index = insertion_index(ordered, after_task_id, first) if project.sequential else 0

    now = datetime.now(timezone.utc)
    created: list[Task] = []
    for number, step in enumerate(steps):
        status = step.status or "next"
        task = Task(
            user_id=user.id,
            raw_text=step.title,
            title=step.title,
            notes=step.notes,
            status=status,
            project_id=project.id,
            waiting_on=step.waiting_on,
            waiting_since=now if status == "waiting" else None,
            due_at=localize(step.due_at, user.timezone),
            defer_until=localize(step.defer_until, user.timezone),
            source="api",
            source_ref=step_ref(project.id, request_id, number) if request_id else None,
            clarified_at=now,
        )
        if step.context is not None:
            task.context_id = get_or_create_context(session, user.id, step.context).id
        session.add(task)
        created.append(task)

    if project.sequential:
        ordered[index:index] = created
        renumber(session, ordered)
    else:
        session.flush()
    return created
