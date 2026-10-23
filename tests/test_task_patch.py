from datetime import timezone

import pytest

from app.models import Task, User


def patch(client, auth, task_id, **body):
    return client.patch(f"/tasks/{task_id}", json=body, headers=auth)


@pytest.fixture
def nxt(make_task):
    return make_task("next", title="viejo")


# --- Edición básica --------------------------------------------------------------------


def test_edit_title_and_notes(client, auth, session, nxt):
    r = patch(client, auth, nxt.id, title="  nuevo  ", notes="detalle")
    assert r.status_code == 200
    assert r.json()["title"] == "nuevo"
    assert r.json()["notes"] == "detalle"
    session.refresh(nxt)
    assert nxt.title == "nuevo"


def test_only_sent_fields_change(client, auth, session, make_task):
    t = make_task("next", title="x", notes="conservar")
    patch(client, auth, t.id, title="y")
    session.refresh(t)
    assert t.notes == "conservar"


def test_a_patch_with_no_effective_change_is_422(client, auth, nxt):
    assert client.patch(f"/tasks/{nxt.id}", json={}, headers=auth).status_code == 422
    r = client.patch(
        f"/tasks/{nxt.id}",
        json={"title": None, "notes": "", "context": "  ", "due_at": None, "clear": None},
        headers=auth,
    )
    assert r.status_code == 422


def test_unknown_fields_are_rejected(client, auth, nxt):
    assert patch(client, auth, nxt.id, title="x", prioridad="alta").status_code == 422


def test_null_and_empty_values_do_not_clear_anything(client, auth, session, make_task):
    t = make_task("next", title="x", notes="conservar")
    r = client.patch(
        f"/tasks/{t.id}", json={"title": "y", "notes": None, "context": ""}, headers=auth
    )
    assert r.status_code == 200
    session.refresh(t)
    assert t.notes == "conservar"


def test_context_is_set_by_name_and_cleared_explicitly(client, auth, nxt):
    r = patch(client, auth, nxt.id, context="PC")
    assert r.json()["context"] == "@pc"
    r = patch(client, auth, nxt.id, clear=["context"])
    assert r.json()["context"] is None


def test_cannot_send_and_clear_the_same_field(client, auth, nxt):
    assert patch(client, auth, nxt.id, notes="a", clear=["notes"]).status_code == 422


def test_dates_are_set_and_cleared(client, auth, session, nxt):
    r = patch(client, auth, nxt.id, due_at="2030-03-01T10:00:00Z", defer_until="2030-02-01T00:00:00Z")
    assert r.status_code == 200
    session.refresh(nxt)
    assert nxt.due_at.astimezone(timezone.utc).isoformat() == "2030-03-01T10:00:00+00:00"
    r = patch(client, auth, nxt.id, clear=["due_at", "defer_until"])
    assert r.json()["due_at"] is None and r.json()["defer_until"] is None


def test_deferring_makes_the_task_unavailable(client, auth, nxt):
    r = patch(client, auth, nxt.id, defer_until="2999-01-01T00:00:00Z")
    assert r.json()["available_now"] is False


# --- Cambios de estado -----------------------------------------------------------------


def test_next_to_waiting_requires_waiting_on_and_sets_waiting_since(client, auth, session, nxt):
    assert patch(client, auth, nxt.id, status="waiting").status_code == 422
    r = patch(client, auth, nxt.id, status="waiting", waiting_on="Carlos")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "waiting" and body["waiting_on"] == "Carlos"
    assert body["waiting_since"] is not None
    assert body["available_now"] is False


def test_leaving_waiting_clears_waiting_on_and_since(client, auth, session, make_task):
    t = make_task("waiting", title="x", waiting_on="Ana", waiting_since=None)
    r = patch(client, auth, t.id, status="next")
    assert r.status_code == 200
    assert r.json()["waiting_on"] is None and r.json()["waiting_since"] is None
    assert r.json()["available_now"] is True


def test_changing_waiting_on_keeps_waiting_since(client, auth, make_task):
    from datetime import datetime, timedelta
    since = datetime.now(timezone.utc) - timedelta(days=3)
    t = make_task("waiting", title="x", waiting_on="Ana", waiting_since=since)
    r = patch(client, auth, t.id, waiting_on="Luis")
    assert r.json()["waiting_on"] == "Luis"
    from datetime import datetime as _dt
    assert _dt.fromisoformat(r.json()["waiting_since"]) == since  # mismo instante


def test_cannot_clear_waiting_on_while_waiting(client, auth, make_task):
    t = make_task("waiting", title="x", waiting_on="Ana")
    assert patch(client, auth, t.id, clear=["waiting_on"]).status_code == 422


def test_someday_to_next_needs_a_title(client, auth, make_task):
    t = make_task("someday", title=None, raw_text="idea")
    r = patch(client, auth, t.id, status="next")
    assert r.status_code == 422 and "title" in r.text
    r = patch(client, auth, t.id, status="next", title="Hacer la idea")
    assert r.status_code == 200 and r.json()["status"] == "next"


def test_move_to_someday_and_reference(client, auth, nxt, make_task):
    assert patch(client, auth, nxt.id, status="someday").json()["status"] == "someday"
    other = make_task("next", title="y")
    assert patch(client, auth, other.id, status="reference").json()["status"] == "reference"


@pytest.mark.parametrize(
    "status, body, offending",
    [
        ("next", {"waiting_on": "Ana"}, "waiting_on"),
        ("someday", {"context": "@pc"}, "context"),
        ("someday", {"due_at": "2030-01-01"}, "due_at"),
        ("reference", {"project_id": 1}, "project_id"),
        ("waiting", {"context": "@pc"}, "context"),
    ],
)
def test_fields_must_fit_the_resulting_status(client, auth, make_task, status, body, offending):
    extra = {"waiting_on": "Ana"} if status == "waiting" else {}
    t = make_task(status, title="x", **extra)
    r = patch(client, auth, t.id, **body)
    assert r.status_code == 422
    assert "no permitidos" in r.text and offending in r.text


def test_status_inbox_done_or_trashed_cannot_be_set_through_patch(client, auth, nxt):
    for bad in ("inbox", "done", "trashed", "converted"):
        assert patch(client, auth, nxt.id, status=bad).status_code == 422


# --- Proyectos -------------------------------------------------------------------------


def test_move_into_a_sequential_project_appends_to_the_end(client, auth, make_task, make_project):
    p = make_project(sequential=True)
    make_task("next", project_id=p.id, position=1)
    t = make_task("next", title="x")
    r = patch(client, auth, t.id, project_id=p.id)
    assert r.json()["position"] == 2 and r.json()["project_title"] == p.title
    assert r.json()["available_now"] is False


def test_moving_to_a_parallel_project_drops_the_old_position(
    client, auth, session, make_task, make_project
):
    seq, par = make_project(sequential=True), make_project(sequential=False)
    make_task("next", project_id=seq.id, position=1)
    t = make_task("next", title="x", project_id=seq.id, position=2)
    r = patch(client, auth, t.id, project_id=par.id)
    assert r.status_code == 200
    assert r.json()["position"] is None and r.json()["project_id"] == par.id


def test_moving_to_the_same_project_changes_nothing(client, auth, make_task, make_project):
    p = make_project(sequential=True)
    make_task("next", project_id=p.id, position=1)
    t = make_task("next", title="x", project_id=p.id, position=2)
    r = patch(client, auth, t.id, project_id=p.id, notes="n")
    assert r.json()["position"] == 2


def test_clearing_the_project_also_clears_the_position(client, auth, make_task, make_project):
    p = make_project(sequential=True)
    t = make_task("next", title="x", project_id=p.id, position=1)
    r = patch(client, auth, t.id, clear=["project_id"])
    assert r.json()["project_id"] is None and r.json()["position"] is None
    assert r.json()["available_now"] is True


def test_moving_the_head_out_unlocks_the_next_step(client, auth, make_task, make_project):
    p = make_project(sequential=True)
    head = make_task("next", title="h", project_id=p.id, position=1)
    second = make_task("next", title="s", project_id=p.id, position=2)
    assert client.get(f"/tasks/{second.id}", headers=auth).json()["available_now"] is False
    patch(client, auth, head.id, clear=["project_id"])
    assert client.get(f"/tasks/{second.id}", headers=auth).json()["available_now"] is True


@pytest.mark.parametrize("status", ["done", "dropped"])
def test_closed_project_is_409_and_nothing_changes(client, auth, session, nxt, make_project, status):
    p = make_project(status=status)
    assert patch(client, auth, nxt.id, project_id=p.id, notes="x").status_code == 409
    session.refresh(nxt)
    assert nxt.project_id is None and nxt.notes is None


def test_unknown_or_foreign_project_is_404(client, auth, session, nxt, make_project):
    assert patch(client, auth, nxt.id, project_id=999_999).status_code == 404
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_project(user_id=other.id)
    assert patch(client, auth, nxt.id, project_id=theirs.id).status_code == 404


def test_huge_project_id_is_422(client, auth, nxt):
    assert patch(client, auth, nxt.id, project_id=99999999999999999999).status_code == 422


# --- Qué se puede editar ---------------------------------------------------------------


@pytest.mark.parametrize(
    "status, fragment",
    [("inbox", "/clarify"), ("done", "reábrela"), ("trashed", "reábrela"), ("converted", "proyecto")],
)
def test_only_open_tasks_are_editable(client, auth, make_task, status, fragment):
    t = make_task(status)
    r = patch(client, auth, t.id, notes="x")
    assert r.status_code == 409
    assert fragment in r.json()["detail"]


def test_another_users_task_is_404_and_untouched(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_task("next", title="ajena", user_id=other.id)
    assert patch(client, auth, theirs.id, title="mía").status_code == 404
    session.refresh(theirs)
    assert theirs.title == "ajena"


def test_patch_requires_auth(client, nxt):
    assert client.patch(f"/tasks/{nxt.id}", json={"title": "x"}).status_code == 401
