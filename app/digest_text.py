"""Texto plano para Telegram: sin Markdown ni HTML, así no depende del parse_mode del nodo."""

from datetime import datetime
from zoneinfo import ZoneInfo

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
)


def long_date(local: datetime) -> str:
    return f"{WEEKDAYS[local.weekday()].capitalize()} {local.day} de {MONTHS[local.month - 1]} de {local.year}"


def when(moment: datetime, now: datetime, tz: ZoneInfo) -> str:
    """'hoy 15:00', 'ayer', 'hace 3 días', 'mañana', 'vie 25', '3/11'."""
    d, n = moment.astimezone(tz), now.astimezone(tz)
    diff = (d.date() - n.date()).days
    if diff == 0:
        return f"hoy {d:%H:%M}"
    if diff == 1:
        return "mañana"
    if diff == -1:
        return "ayer"
    if diff < -1:
        return f"hace {-diff} días"
    if diff < 7:
        return f"{WEEKDAYS[d.weekday()][:3]} {d.day}"
    return f"{d.day}/{d.month}"


def label(task) -> str:
    text = (task.title or task.raw_text).strip().replace("\n", " ")
    return text if len(text) <= 80 else text[:79] + "…"


def _section(title: str, count: int, lines: list[str]) -> list[str]:
    if count == 0:
        return []
    out = ["", f"{title} ({count})", *lines]
    if count > len(lines):
        out.append(f"… y {count - len(lines)} más")
    return out


def days_label(days: int | None) -> str:
    if days is None:
        return "sin fecha"
    return {0: "hoy", 1: "1 día"}.get(days, f"{days} días")


def _task_line(task, now, tz, show_due: bool = True) -> str:
    line = f"• {label(task)}"
    if task.context:
        line += f" {task.context}"
    if show_due and task.due_at is not None:
        verb = "venció" if task.due_at < now else "vence"
        line += f" — {verb} {when(task.due_at, now, tz)}"
    return line


def _wait_line(task) -> str:
    return f"• {label(task)} — {task.waiting_on}, {days_label(task.days_waiting)}"


def daily_text(digest, now: datetime, tz: ZoneInfo) -> str:
    out = [long_date(now.astimezone(tz))]
    if digest.inbox_count:
        out += ["", f"📥 Inbox: {digest.inbox_count} por clarificar"]
    out += _section(
        "⚠️ Vencidas", digest.overdue.count,
        [_task_line(t, now, tz) for t in digest.overdue.items],
    )
    out += _section(
        "📌 Para hoy", digest.due_today.count,
        [_task_line(t, now, tz) for t in digest.due_today.items],
    )
    out += _section(
        "🔓 Disponibles desde hoy", digest.reactivated.count,
        [_task_line(t, now, tz) for t in digest.reactivated.items],
    )
    out += _section(
        "▶️ Siguientes acciones", digest.next_actions.count,
        [_task_line(t, now, tz) for t in digest.next_actions.items],
    )
    out += _section(
        "⏳ Esperando respuesta", digest.waiting_followups.count,
        [_wait_line(t) for t in digest.waiting_followups.items],
    )
    out += _section(
        "🧩 Proyectos sin siguiente acción", digest.stalled_projects.count,
        [f"• {p.title}" for p in digest.stalled_projects.items],
    )
    if len(out) == 1:
        out += ["", "Sin pendientes por atender. ✅"]
    return "\n".join(out)


def review_text(review, now: datetime, tz: ZoneInfo) -> str:
    snap = review.snapshot
    if review.last_review_at is None:
        last = "Aún no has hecho ninguna revisión."
    else:
        days = review.days_since_last_review
        last = "Última revisión: hoy." if days == 0 else f"Última revisión: hace {days_label(days)}."
    out = [
        "Revisión semanal", last, "",
        f"1. Inbox: {snap.inbox}" + (" — clarifícalo primero" if snap.inbox else " ✅"),
        f"2. Vencidas: {snap.overdue}",
        f"3. Proyectos activos: {snap.active_projects}, sin siguiente acción: {snap.stalled_projects}",
        f"4. Esperando respuesta: {snap.waiting}",
        f"5. Algún día / tal vez: {snap.someday}",
        f"6. Siguientes acciones: {snap.next}",
        "",
        f"Esta semana: {snap.captured_last_7_days} capturadas, {snap.completed_last_7_days} completadas.",
    ]
    out += _section(
        "🧩 Proyectos sin siguiente acción", review.stalled_projects.count,
        [f"• {p.title}" for p in review.stalled_projects.items],
    )
    out += _section(
        "⏳ Esperando respuesta", review.waiting.count,
        [_wait_line(t) for t in review.waiting.items],
    )
    return "\n".join(out)
