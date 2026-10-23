import pytest

from app.models import User


def reopen(client, auth, task_id):
    return client.post(f"/tasks/{task_id}/reopen", headers=auth)


def discard(client, auth, task_id):
    return client.delete(f"/tasks/{task_id}", headers=auth)


def complete(client, auth, task_id):
    return client.post(f"/tasks/{task_id}/complete", headers=auth)


# --- Descartar -------------------------------------------------------------------------


def test_discard_marks_trashed_without_deleting(client, auth, session, make_task):
    t = make_task("someday", title="idea")
    r = discard(client, auth, t.id)
    assert r.status_code == 200
    assert r.json()["status"] == "trashed"
    session.refresh(t)
    assert t.closed_from == "someday" and t.raw_text


def test_discard_is_idempotent(client, auth, make_task):
    t = make_task("next", title="x")
    assert discard(client, auth, t.id).status_code == 200
    assert discard(client, auth, t.id).json()["status"] == "trashed"


def test_converted_tasks_cannot_be_discarded(client, auth, make_task):
    assert discard(client, auth, make_task("converted").id).status_code == 409


def test_discarded_task_leaves_the_next_list(client, auth, make_task):
    t = make_task("next", title="x")
    discard(client, auth, t.id)
    assert client.get("/next", headers=auth).json()["count"] == 0


def test_discarding_the_head_of_a_sequential_project_unlocks_the_next(
    client, auth, make_task, make_project
):
    p = make_project(sequential=True)
    head = make_task("next", title="h", project_id=p.id, position=1)
    second = make_task("next", title="s", project_id=p.id, position=2)
    discard(client, auth, head.id)
    assert client.get(f"/tasks/{second.id}", headers=auth).json()["available_now"] is True


def test_another_users_task_cannot_be_discarded(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_task("next", title="x", user_id=other.id)
    assert discard(client, auth, theirs.id).status_code == 404
    session.refresh(theirs)
    assert theirs.status == "next"


# --- Reabrir: restaura el estado exacto ------------------------------------------------


@pytest.mark.parametrize("status", ["next", "waiting", "someday", "inbox"])
def test_complete_then_reopen_restores_the_previous_state(client, auth, session, make_task, status):
    extra = {"waiting_on": "Ana"} if status == "waiting" else {}
    title = None if status == "inbox" else "x"
    t = make_task(status, title=title, **extra)
    complete(client, auth, t.id)
    r = reopen(client, auth, t.id)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == status
    assert r.json()["completed_at"] is None
    session.refresh(t)
    assert t.closed_from is None


@pytest.mark.parametrize("status", ["next", "waiting", "someday", "reference"])
def test_discard_then_reopen_restores_the_previous_state(client, auth, make_task, status):
    extra = {"waiting_on": "Ana"} if status == "waiting" else {}
    t = make_task(status, title="x", **extra)
    discard(client, auth, t.id)
    assert reopen(client, auth, t.id).json()["status"] == status


def test_a_task_trashed_from_the_inbox_returns_to_the_inbox(client, auth, make_task):
    t = make_task("inbox", raw_text="ruido")
    client.post(f"/tasks/{t.id}/clarify", json={"action": "trash"}, headers=auth)
    r = reopen(client, auth, t.id)
    assert r.json()["status"] == "inbox"
    assert r.json()["clarified_at"] is None
    assert client.get("/inbox", headers=auth).json()["count"] == 1


def test_a_completed_task_that_is_then_discarded_still_reopens_to_its_original_state(
    client, auth, make_task
):
    t = make_task("waiting", title="x", waiting_on="Ana")
    complete(client, auth, t.id)
    discard(client, auth, t.id)
    assert reopen(client, auth, t.id).json()["status"] == "waiting"


def test_reopening_without_closed_from_deduces_the_state(client, auth, make_task):
    """Filas antiguas, sin closed_from."""
    waiting = make_task("done", title="x", waiting_on="Ana")
    titled = make_task("done", title="x")
    bare = make_task("trashed", title=None)
    assert reopen(client, auth, waiting.id).json()["status"] == "waiting"
    assert reopen(client, auth, titled.id).json()["status"] == "next"
    assert reopen(client, auth, bare.id).json()["status"] == "inbox"


@pytest.mark.parametrize("status", ["inbox", "next", "waiting", "someday", "reference", "converted"])
def test_only_completed_or_discarded_tasks_can_be_reopened(client, auth, make_task, status):
    extra = {"waiting_on": "Ana"} if status == "waiting" else {}
    t = make_task(status, title="x", **extra)
    assert reopen(client, auth, t.id).status_code == 409


def test_reopening_a_sequential_step_blocks_the_later_ones_again(
    client, auth, make_task, make_project
):
    p = make_project(sequential=True)
    first = make_task("next", title="uno", project_id=p.id, position=1)
    second = make_task("next", title="dos", project_id=p.id, position=2)
    complete(client, auth, first.id)
    assert client.get(f"/tasks/{second.id}", headers=auth).json()["available_now"] is True
    reopen(client, auth, first.id)
    assert client.get(f"/tasks/{second.id}", headers=auth).json()["available_now"] is False
    assert client.get(f"/tasks/{first.id}", headers=auth).json()["available_now"] is True


def test_reopen_is_scoped_to_the_user(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_task("done", title="x", user_id=other.id)
    assert reopen(client, auth, theirs.id).status_code == 404
