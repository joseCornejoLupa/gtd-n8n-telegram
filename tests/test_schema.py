"""Reglas de integridad del esquema."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Task, User


def test_next_task_requires_a_title(session, user):
    with pytest.raises(IntegrityError, match="ck_tasks_next_has_title"):
        with session.begin_nested():
            session.add(Task(user_id=user.id, raw_text="x", status="next", title=None))


def test_waiting_task_requires_waiting_on(session, user):
    with pytest.raises(IntegrityError, match="ck_tasks_waiting_has_waiting_on"):
        with session.begin_nested():
            session.add(Task(user_id=user.id, raw_text="x", status="waiting", title="t"))


def test_invalid_task_status_is_rejected(session, user):
    with pytest.raises(IntegrityError, match="ck_tasks_status"):
        with session.begin_nested():
            session.add(Task(user_id=user.id, raw_text="x", status="hecho"))


def test_invalid_project_status_is_rejected(session, make_project):
    with pytest.raises(IntegrityError, match="ck_projects_status"):
        with session.begin_nested():
            make_project(status="paused")


def test_capture_is_idempotent_per_source_ref(session, user):
    session.add(Task(user_id=user.id, raw_text="a", source="telegram", source_ref="msg-1"))
    session.flush()
    with pytest.raises(IntegrityError, match="uq_tasks_user_id"):
        with session.begin_nested():
            session.add(
                Task(user_id=user.id, raw_text="b", source="telegram", source_ref="msg-1")
            )


def test_same_source_ref_is_fine_for_another_user_or_source(session, user):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    session.add_all(
        [
            Task(user_id=user.id, raw_text="a", source="telegram", source_ref="msg-1"),
            Task(user_id=other.id, raw_text="b", source="telegram", source_ref="msg-1"),
            Task(user_id=user.id, raw_text="c", source="api", source_ref="msg-1"),
        ]
    )
    session.flush()


def test_tasks_without_source_ref_never_collide(session, user):
    session.add_all([Task(user_id=user.id, raw_text="a"), Task(user_id=user.id, raw_text="b")])
    session.flush()


def test_context_names_are_unique_per_user(session, make_context):
    make_context("@pc")
    with pytest.raises(IntegrityError, match="uq_contexts_user_id"):
        with session.begin_nested():
            make_context("@pc")


def test_positions_can_be_swapped_inside_a_transaction(
    session, make_task, make_project
):
    p = make_project(sequential=True)
    a = make_task("next", project_id=p.id, position=1)
    b = make_task("next", project_id=p.id, position=2)

    swap = text("UPDATE tasks SET position = :pos WHERE id = :id")
    session.execute(swap, {"pos": 2, "id": a.id})  # transitoriamente duplicada
    session.execute(swap, {"pos": 1, "id": b.id})
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # valida: no debe fallar

    rows = session.execute(
        text("SELECT id FROM tasks WHERE project_id = :p ORDER BY position"), {"p": p.id}
    ).all()
    assert [r[0] for r in rows] == [b.id, a.id]


def test_duplicate_positions_are_rejected_when_the_transaction_checks(
    session, make_task, make_project
):
    p = make_project(sequential=True)
    make_task("next", project_id=p.id, position=1)
    make_task("next", project_id=p.id, position=2)
    session.execute(text("UPDATE tasks SET position = 1 WHERE project_id = :p"), {"p": p.id})
    with pytest.raises(IntegrityError, match="uq_tasks_project_id"):
        with session.begin_nested():
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_project_remembers_the_task_it_came_from(session, make_task, make_project):
    origin = make_task("inbox", raw_text="arreglar el blog")
    p = make_project(title="Blog funcionando", origin_task_id=origin.id)
    origin.status = "converted"
    session.flush()
    assert p.origin_task_id == origin.id
