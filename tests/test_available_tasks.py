"""La vista available_tasks: qué se puede hacer ahora."""

from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


def test_standalone_next_is_available_other_statuses_are_not(make_task, available):
    nxt = make_task("next")
    make_task("inbox")
    make_task("someday")
    make_task("reference")
    make_task("done")
    make_task("trashed")
    make_task("converted")
    make_task("waiting", waiting_on="Ana")
    assert available() == [nxt.id]


def test_parallel_project_shows_every_next(make_task, make_project, available):
    p = make_project(sequential=False)
    a = make_task("next", project_id=p.id)
    b = make_task("next", project_id=p.id)
    assert available() == [a.id, b.id]


def test_sequential_project_shows_only_the_head(make_task, make_project, available):
    p = make_project(sequential=True)
    first = make_task("next", project_id=p.id, position=1)
    make_task("next", project_id=p.id, position=2)
    make_task("next", project_id=p.id, position=3)
    assert available() == [first.id]


def test_sequential_next_step_appears_when_head_is_done(
    session, make_task, make_project, available
):
    p = make_project(sequential=True)
    first = make_task("next", project_id=p.id, position=1)
    second = make_task("next", project_id=p.id, position=2)
    make_task("next", project_id=p.id, position=3)

    first.status = "done"
    session.flush()

    assert available() == [second.id]


def test_sequential_waiting_head_blocks_the_rest(make_task, make_project, available):
    p = make_project(sequential=True)
    make_task("waiting", title="espero respuesta", waiting_on="Ana", project_id=p.id, position=1)
    make_task("next", project_id=p.id, position=2)
    assert available() == []


def test_sequential_ignores_positions_of_finished_tasks(make_task, make_project, available):
    p = make_project(sequential=True)
    make_task("done", project_id=p.id, position=1)
    make_task("trashed", project_id=p.id, position=2)
    third = make_task("next", project_id=p.id, position=3)
    make_task("next", project_id=p.id, position=4)
    assert available() == [third.id]


def test_sequential_projects_do_not_interfere_with_each_other(
    make_task, make_project, available
):
    p1 = make_project(sequential=True)
    p2 = make_project(sequential=True)
    a1 = make_task("next", project_id=p1.id, position=1)
    make_task("next", project_id=p1.id, position=2)
    b1 = make_task("next", project_id=p2.id, position=1)
    make_task("next", project_id=p2.id, position=2)
    assert available() == [a1.id, b1.id]


def test_inactive_projects_hide_their_tasks(make_task, make_project, available):
    for status in ("someday", "done", "dropped"):
        p = make_project(status=status)
        make_task("next", project_id=p.id)
    assert available() == []


def test_deferred_task_is_hidden_until_its_date(make_task, available):
    make_task("next", defer_until=NOW + timedelta(days=1))
    visible = make_task("next", defer_until=NOW - timedelta(minutes=1))
    assert available() == [visible.id]


def test_deferred_sequential_head_leaves_project_without_available_tasks(
    make_task, make_project, available
):
    # Decisión documentada: el paso siguiente es "esperar a esa fecha", no saltarse el orden.
    p = make_project(sequential=True)
    make_task("next", project_id=p.id, position=1, defer_until=NOW + timedelta(days=1))
    make_task("next", project_id=p.id, position=2)
    assert available() == []


def test_view_is_scoped_by_user_id(session, user, make_task, available):
    from app.models import User

    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    mine = make_task("next")
    make_task("next", user_id=other.id)
    assert available() == [mine.id]
