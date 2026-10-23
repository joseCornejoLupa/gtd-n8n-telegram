from datetime import datetime, timedelta, timezone

import pytest

from app.models import User

NOW = datetime.now(timezone.utc)


def complete(client, auth, task_id):
    return client.post(f"/tasks/{task_id}/complete", headers=auth)


def test_complete_a_next_action(client, auth, session, make_task):
    t = make_task("next", title="hacer")
    r = complete(client, auth, t.id)
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["status"] == "done"
    assert body["task"]["completed_at"] is not None
    assert body["already_completed"] is False
    assert body["project"] is None
    assert body["project_stalled"] is False and body["next_task"] is None
    session.refresh(t)
    assert t.closed_from == "next"


@pytest.mark.parametrize("status", ["waiting", "someday", "inbox"])
def test_waiting_someday_and_inbox_tasks_can_be_completed(client, auth, make_task, status):
    extra = {"waiting_on": "Ana"} if status == "waiting" else {}
    t = make_task(status, **extra)
    r = complete(client, auth, t.id)
    assert r.status_code == 200
    assert r.json()["task"]["status"] == "done"


def test_completing_from_the_inbox_marks_it_as_processed(client, auth, make_task):
    t = make_task("inbox")
    assert complete(client, auth, t.id).json()["task"]["clarified_at"] is not None


@pytest.mark.parametrize("status", ["reference", "trashed", "converted"])
def test_other_states_cannot_be_completed(client, auth, make_task, status):
    assert complete(client, auth, make_task(status).id).status_code == 409


def test_completing_twice_is_idempotent(client, auth, make_task):
    t = make_task("next", title="x")
    first = complete(client, auth, t.id).json()
    again = complete(client, auth, t.id)
    assert again.status_code == 200
    assert again.json()["already_completed"] is True
    assert again.json()["task"]["completed_at"] == first["task"]["completed_at"]


def test_completed_task_leaves_the_next_list(client, auth, make_task):
    t = make_task("next", title="x")
    complete(client, auth, t.id)
    assert client.get("/next", headers=auth).json()["count"] == 0


# --- Proyectos -------------------------------------------------------------------------


def test_sequential_project_unlocks_the_next_step(client, auth, make_task, make_project):
    p = make_project(sequential=True, title="Blog")
    first = make_task("next", title="uno", project_id=p.id, position=1)
    second = make_task("next", title="dos", project_id=p.id, position=2)
    make_task("next", title="tres", project_id=p.id, position=3)

    body = complete(client, auth, first.id).json()
    assert body["project"] == {"id": p.id, "title": "Blog", "sequential": True, "status": "active"}
    assert body["next_task"]["id"] == second.id
    assert body["project_stalled"] is False
    assert body["project_open_tasks"] == 2


def test_last_action_of_a_project_marks_it_stalled(client, auth, make_task, make_project):
    p = make_project()
    t = make_task("next", title="única", project_id=p.id)
    body = complete(client, auth, t.id).json()
    assert body["project_stalled"] is True
    assert body["project_open_tasks"] == 0  # probablemente el proyecto terminó
    assert body["next_task"] is None


def test_a_waiting_task_keeps_the_project_alive(client, auth, make_task, make_project):
    p = make_project()
    make_task("waiting", title="espero", waiting_on="Ana", project_id=p.id)
    t = make_task("next", title="x", project_id=p.id)
    assert complete(client, auth, t.id).json()["project_stalled"] is False


def test_only_someday_tasks_left_means_stalled_but_not_finished(
    client, auth, make_task, make_project
):
    p = make_project()
    make_task("someday", project_id=p.id)
    t = make_task("next", title="x", project_id=p.id)
    body = complete(client, auth, t.id).json()
    assert body["project_stalled"] is True
    assert body["project_open_tasks"] == 1


def test_only_deferred_actions_left_means_stalled(client, auth, make_task, make_project):
    p = make_project()
    make_task("next", title="luego", project_id=p.id, defer_until=NOW + timedelta(days=5))
    t = make_task("next", title="x", project_id=p.id)
    body = complete(client, auth, t.id).json()
    assert body["project_stalled"] is True
    assert body["project_open_tasks"] == 1


@pytest.mark.parametrize("status", ["someday", "done", "dropped"])
def test_inactive_projects_are_never_reported_as_stalled(
    client, auth, make_task, make_project, status
):
    p = make_project(status=status)
    t = make_task("next", title="x", project_id=p.id)
    assert complete(client, auth, t.id).json()["project_stalled"] is False


def test_parallel_project_does_not_report_a_next_task(client, auth, make_task, make_project):
    p = make_project(sequential=False)
    a = make_task("next", title="a", project_id=p.id)
    make_task("next", title="b", project_id=p.id)
    body = complete(client, auth, a.id).json()
    assert body["next_task"] is None
    assert body["project_stalled"] is False


def test_another_users_task_is_404(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_task("next", title="x", user_id=other.id)
    assert complete(client, auth, theirs.id).status_code == 404
    session.refresh(theirs)
    assert theirs.status == "next"
