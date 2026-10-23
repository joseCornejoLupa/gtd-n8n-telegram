from datetime import timezone

import pytest

from app.models import Context, Task, User
from tests.conftest import API_KEY


def clarify(client, auth, task_id, **body):
    return client.post(f"/tasks/{task_id}/clarify", json=body, headers=auth)


@pytest.fixture
def inbox_task(make_task):
    return make_task("inbox", raw_text="arreglar el grifo")


# --- Acciones --------------------------------------------------------------------------


def test_next_action(client, auth, session, inbox_task):
    r = clarify(client, auth, inbox_task.id, action="next", title="Comprar empaque del grifo")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "next"
    assert body["title"] == "Comprar empaque del grifo"
    assert body["raw_text"] == "arreglar el grifo"  # la captura original no se toca
    assert body["clarified_at"] is not None
    assert body["available_now"] is True

    session.refresh(inbox_task)
    assert inbox_task.status == "next"
    assert inbox_task.raw_text == "arreglar el grifo"


def test_waiting_sets_waiting_since(client, auth, inbox_task):
    r = clarify(
        client, auth, inbox_task.id, action="waiting", title="Presupuesto", waiting_on="Carlos"
    )
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "waiting"
    assert body["waiting_on"] == "Carlos"
    assert body["waiting_since"] is not None
    assert body["available_now"] is False  # esperar no es una acción disponible


def test_someday_and_reference_do_not_need_a_title(client, auth, make_task):
    for action, status in (("someday", "someday"), ("reference", "reference")):
        t = make_task("inbox")
        r = clarify(client, auth, t.id, action=action)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
        assert r.json()["available_now"] is False


def test_trash(client, auth, inbox_task):
    r = clarify(client, auth, inbox_task.id, action="trash")
    assert r.status_code == 200
    assert r.json()["status"] == "trashed"


# --- Validación por acción -------------------------------------------------------------


def test_next_requires_a_title(client, auth, inbox_task):
    assert clarify(client, auth, inbox_task.id, action="next").status_code == 422


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_title_counts_as_missing(client, auth, inbox_task, blank):
    assert clarify(client, auth, inbox_task.id, action="next", title=blank).status_code == 422


def test_waiting_requires_waiting_on(client, auth, inbox_task):
    r = clarify(client, auth, inbox_task.id, action="waiting", title="Presupuesto")
    assert r.status_code == 422


@pytest.mark.parametrize(
    "action, body, offending",
    [
        ("next", {"title": "t", "waiting_on": "Ana"}, "waiting_on"),
        ("waiting", {"title": "t", "waiting_on": "Ana", "context": "@pc"}, "context"),
        ("waiting", {"title": "t", "waiting_on": "Ana", "defer_until": "2030-01-01"}, "defer_until"),
        ("someday", {"title": "t", "context": "@pc"}, "context"),
        ("someday", {"title": "t", "due_at": "2030-01-01"}, "due_at"),
        ("reference", {"title": "t", "project_id": 1}, "project_id"),
        ("trash", {"title": "x"}, "title"),
        ("trash", {"notes": "x"}, "notes"),
    ],
)
def test_fields_not_allowed_for_the_action_are_rejected(
    client, auth, inbox_task, action, body, offending
):
    r = clarify(client, auth, inbox_task.id, action=action, **body)
    assert r.status_code == 422, r.text
    assert "no permitidos" in r.text and offending in r.text


def test_null_and_empty_values_are_treated_as_not_sent(client, auth, inbox_task):
    """n8n manda null o "" en los campos vacíos."""
    r = client.post(
        f"/tasks/{inbox_task.id}/clarify",
        json={
            "action": "next",
            "title": "Hacer algo",
            "waiting_on": None,
            "context": "",
            "notes": "  ",
            "project_id": None,
            "due_at": "",
            "defer_until": None,
        },
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["context"] is None
    assert r.json()["notes"] is None


def test_unknown_action_and_unknown_fields_are_rejected(client, auth, inbox_task):
    assert clarify(client, auth, inbox_task.id, action="hecho").status_code == 422
    r = clarify(client, auth, inbox_task.id, action="next", title="x", prioridad="alta")
    assert r.status_code == 422


# --- Estados y pertenencia -------------------------------------------------------------


def test_clarifying_twice_is_a_conflict(client, auth, inbox_task):
    assert clarify(client, auth, inbox_task.id, action="next", title="x").status_code == 200
    r = clarify(client, auth, inbox_task.id, action="trash")
    assert r.status_code == 409
    assert "next" in r.json()["detail"]


def test_only_inbox_tasks_can_be_clarified(client, auth, make_task):
    for status in ("done", "someday", "trashed"):
        t = make_task(status)
        assert clarify(client, auth, t.id, action="trash").status_code == 409


def test_unknown_task_is_404(client, auth):
    assert clarify(client, auth, 999_999, action="trash").status_code == 404


def test_another_users_task_is_404_and_untouched(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_task("inbox", user_id=other.id)

    assert clarify(client, auth, theirs.id, action="trash").status_code == 404
    session.refresh(theirs)
    assert theirs.status == "inbox"


def test_clarify_requires_auth(client, inbox_task):
    r = client.post(f"/tasks/{inbox_task.id}/clarify", json={"action": "trash"})
    assert r.status_code == 401


# --- Contextos -------------------------------------------------------------------------


def test_context_is_created_by_name_and_normalized(client, auth, session, make_task):
    a = make_task("inbox")
    b = make_task("inbox")
    ra = clarify(client, auth, a.id, action="next", title="x", context="PC")
    rb = clarify(client, auth, b.id, action="next", title="y", context=" @pc ")
    assert ra.json()["context"] == rb.json()["context"] == "@pc"
    assert session.query(Context).filter_by(name="@pc").count() == 1  # se reutiliza


@pytest.mark.parametrize("bad", ["@", "casa mia", "@" + "x" * 41, "@a/b"])
def test_invalid_context_names_are_rejected(client, auth, inbox_task, bad):
    r = clarify(client, auth, inbox_task.id, action="next", title="x", context=bad)
    assert r.status_code == 422


# --- Proyectos -------------------------------------------------------------------------


def test_task_joins_a_parallel_project_without_position(client, auth, inbox_task, make_project):
    p = make_project(sequential=False)
    r = clarify(client, auth, inbox_task.id, action="next", title="x", project_id=p.id)
    body = r.json()
    assert body["project_id"] == p.id
    assert body["position"] is None
    assert body["available_now"] is True


def test_tasks_are_appended_in_order_to_a_sequential_project(client, auth, make_task, make_project):
    p = make_project(sequential=True)
    results = []
    for n in range(3):
        t = make_task("inbox")
        results.append(
            clarify(client, auth, t.id, action="next", title=f"paso {n + 1}", project_id=p.id).json()
        )
    assert [r["position"] for r in results] == [1, 2, 3]
    # Solo el primer paso está disponible; los demás esperan su turno.
    assert [r["available_now"] for r in results] == [True, False, False]


def test_positions_continue_after_finished_steps(client, auth, session, make_task, make_project):
    p = make_project(sequential=True)
    make_task("done", project_id=p.id, position=1)
    make_task("done", project_id=p.id, position=2)
    t = make_task("inbox")
    r = clarify(client, auth, t.id, action="next", title="paso", project_id=p.id)
    assert r.json()["position"] == 3
    assert r.json()["available_now"] is True


def test_someday_task_in_a_sequential_project_does_not_block(
    client, auth, make_task, make_project
):
    p = make_project(sequential=True)
    s = make_task("inbox")
    clarify(client, auth, s.id, action="someday", project_id=p.id)
    n = make_task("inbox")
    r = clarify(client, auth, n.id, action="next", title="paso", project_id=p.id)
    assert r.json()["available_now"] is True  # el 'algún día' no cuenta para la secuencia


def test_next_in_a_someday_project_is_not_available(client, auth, inbox_task, make_project):
    p = make_project(status="someday")
    r = clarify(client, auth, inbox_task.id, action="next", title="x", project_id=p.id)
    assert r.status_code == 200
    assert r.json()["available_now"] is False


@pytest.mark.parametrize("status", ["done", "dropped"])
def test_closed_projects_do_not_accept_tasks(client, auth, session, inbox_task, make_project, status):
    p = make_project(status=status)
    r = clarify(client, auth, inbox_task.id, action="next", title="x", project_id=p.id)
    assert r.status_code == 409
    session.refresh(inbox_task)
    assert inbox_task.status == "inbox"


def test_unknown_project_is_404_and_task_stays_in_inbox(client, auth, session, inbox_task):
    r = clarify(client, auth, inbox_task.id, action="next", title="x", project_id=999_999)
    assert r.status_code == 404
    session.refresh(inbox_task)
    assert inbox_task.status == "inbox"


def test_another_users_project_is_404(client, auth, session, inbox_task, make_project):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_project(user_id=other.id)
    r = clarify(client, auth, inbox_task.id, action="next", title="x", project_id=theirs.id)
    assert r.status_code == 404


# --- Fechas ----------------------------------------------------------------------------


def test_naive_dates_are_read_in_the_users_timezone(client, auth, session, user, inbox_task):
    user.timezone = "Asia/Tokyo"  # UTC+9, sin horario de verano
    session.flush()
    r = clarify(
        client, auth, inbox_task.id,
        action="next", title="x", due_at="2030-03-01T09:00:00", defer_until="2030-03-01",
    )
    assert r.status_code == 200, r.text
    task = session.get(Task, inbox_task.id)
    session.refresh(task)
    assert task.due_at.astimezone(timezone.utc).isoformat() == "2030-03-01T00:00:00+00:00"
    assert task.defer_until.astimezone(timezone.utc).isoformat() == "2030-02-28T15:00:00+00:00"


def test_dates_with_offset_are_respected(client, auth, session, inbox_task):
    r = clarify(
        client, auth, inbox_task.id,
        action="next", title="x", due_at="2030-03-01T09:00:00+02:00",
    )
    assert r.status_code == 200, r.text
    task = session.get(Task, inbox_task.id)
    session.refresh(task)
    assert task.due_at.astimezone(timezone.utc).isoformat() == "2030-03-01T07:00:00+00:00"


def test_future_defer_until_makes_the_task_unavailable(client, auth, inbox_task):
    r = clarify(
        client, auth, inbox_task.id,
        action="next", title="x", defer_until="2999-01-01T00:00:00Z",
    )
    assert r.status_code == 200
    assert r.json()["status"] == "next"
    assert r.json()["available_now"] is False

