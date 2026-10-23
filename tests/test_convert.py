import pytest

from app.models import Project, Task


def convert(client, auth, task_id, **body):
    return client.post(f"/tasks/{task_id}/convert-to-project", json=body or None, headers=auth)


def projects_of(session, task_id):
    return session.query(Project).filter(Project.origin_task_id == task_id).all()


def test_inbox_capture_becomes_a_project(client, auth, session, make_task):
    t = make_task("inbox", raw_text="Organizar la mudanza")
    r = convert(client, auth, t.id)
    assert r.status_code == 201
    body = r.json()
    assert body["already_converted"] is False
    assert body["origin_task"]["status"] == "converted"
    assert body["origin_task"]["raw_text"] == "Organizar la mudanza"  # la captura se conserva
    project = body["project"]
    assert project["title"] == "Organizar la mudanza"
    assert project["origin_task_id"] == t.id
    assert project["status"] == "active" and project["sequential"] is True
    assert project["stalled"] is True  # sin primera acción: el bot debe preguntar
    session.refresh(t)
    assert t.closed_from == "inbox" and t.clarified_at is not None


def test_works_with_no_body_at_all(client, auth, make_task):
    t = make_task("inbox", raw_text="X")
    assert client.post(f"/tasks/{t.id}/convert-to-project", headers=auth).status_code == 201


def test_title_outcome_and_first_steps(client, auth, make_task):
    t = make_task("inbox", raw_text="mudanza")
    body = convert(
        client, auth, t.id, title="Mudarme al nuevo depa", outcome="Todo en la casa nueva",
        sequential=False, tasks=[{"title": "Cotizar camión", "context": "@calle"}, {"title": "Empacar"}],
    ).json()
    project = body["project"]
    assert project["title"] == "Mudarme al nuevo depa" and project["outcome"] == "Todo en la casa nueva"
    assert project["sequential"] is False and project["stalled"] is False
    assert [t["title"] for t in project["tasks"]] == ["Cotizar camión", "Empacar"]
    assert project["tasks"][0]["context"] == "@calle"
    assert project["tasks"][0]["project_id"] == project["id"]


def test_sequential_steps_are_numbered(client, auth, make_task):
    t = make_task("inbox", raw_text="x")
    project = convert(client, auth, t.id, tasks=[{"title": "uno"}, {"title": "dos"}]).json()["project"]
    assert [(x["title"], x["position"]) for x in project["tasks"]] == [("uno", 1), ("dos", 2)]
    assert project["next_task"]["title"] == "uno"


def test_a_clarified_task_gives_its_title_to_the_project(client, auth, make_task):
    t = make_task("next", title="Preparar la charla", raw_text="charla")
    body = convert(client, auth, t.id).json()
    assert body["project"]["title"] == "Preparar la charla"
    session_task = body["origin_task"]
    assert session_task["status"] == "converted" and session_task["title"] == "Preparar la charla"


@pytest.mark.parametrize("status", ["waiting", "someday"])
def test_waiting_and_someday_tasks_can_be_converted(client, auth, make_task, status):
    extra = {"waiting_on": "Ana"} if status == "waiting" else {}
    t = make_task(status, title="algo", **extra)
    assert convert(client, auth, t.id).status_code == 201


def test_someday_project_status(client, auth, make_task):
    t = make_task("inbox", raw_text="x")
    assert convert(client, auth, t.id, status="someday").json()["project"]["status"] == "someday"


def test_converting_twice_is_idempotent(client, auth, session, make_task):
    t = make_task("inbox", raw_text="x")
    first = convert(client, auth, t.id, tasks=[{"title": "paso"}]).json()
    again = convert(client, auth, t.id, title="otro título", tasks=[{"title": "otro"}])
    assert again.status_code == 200
    body = again.json()
    assert body["already_converted"] is True
    assert body["project"]["id"] == first["project"]["id"]
    assert body["project"]["title"] == first["project"]["title"]  # el reintento no cambia nada
    assert len(projects_of(session, t.id)) == 1
    assert session.query(Task).filter(Task.project_id == first["project"]["id"]).count() == 1


@pytest.mark.parametrize("status", ["reference", "done", "trashed"])
def test_other_states_cannot_be_converted(client, auth, make_task, status):
    t = make_task(status)
    assert convert(client, auth, t.id).status_code == 409


def test_long_capture_needs_an_explicit_title(client, auth, make_task):
    t = make_task("inbox", raw_text="x" * 600)
    assert convert(client, auth, t.id).status_code == 422
    assert convert(client, auth, t.id, title="Corto").status_code == 201


@pytest.mark.parametrize(
    "body",
    [{"title": "x" * 501}, {"status": "done"}, {"extra": 1}, {"tasks": [{"title": ""}]},
     {"tasks": [{"title": "a", "status": "waiting"}]}],
)
def test_validation_leaves_the_task_untouched(client, auth, session, make_task, body):
    t = make_task("inbox", raw_text="x")
    assert convert(client, auth, t.id, **body).status_code == 422
    session.refresh(t)
    assert t.status == "inbox" and projects_of(session, t.id) == []


def test_unknown_foreign_and_huge_ids(client, auth, auth_other, make_task):
    t = make_task("inbox", raw_text="x")
    assert convert(client, auth, 999999).status_code == 404
    assert convert(client, auth_other, t.id).status_code == 404
    assert convert(client, auth, 2**63).status_code == 422


def test_converted_task_leaves_normal_lists_but_can_be_found(client, auth, make_task):
    t = make_task("next", title="Gran tarea")
    convert(client, auth, t.id)
    assert client.get("/next", headers=auth).json()["count"] == 0
    assert client.get("/tasks", headers=auth).json()["count"] == 0
    found = client.get("/tasks?status=converted", headers=auth).json()
    assert [x["id"] for x in found["items"]] == [t.id]


def test_converted_task_cannot_be_edited_or_completed(client, auth, make_task):
    t = make_task("inbox", raw_text="x")
    convert(client, auth, t.id)
    assert client.patch(f"/tasks/{t.id}", json={"title": "y"}, headers=auth).status_code == 409
    assert client.post(f"/tasks/{t.id}/complete", headers=auth).status_code == 409
    assert client.delete(f"/tasks/{t.id}", headers=auth).status_code == 409


def test_converting_a_task_inside_a_project_does_not_block_its_sequence(client, auth, make_task):
    p = client.post(
        "/projects", json={"title": "P", "tasks": [{"title": "a"}, {"title": "b"}]}, headers=auth
    ).json()
    a = p["tasks"][0]["id"]
    convert(client, auth, a)
    assert [t["title"] for t in client.get("/next", headers=auth).json()["items"]] == ["b"]


# --- Deshacer --------------------------------------------------------------------------


def test_reopen_is_refused_while_the_project_lives(client, auth, make_task):
    t = make_task("inbox", raw_text="x")
    convert(client, auth, t.id)
    r = client.post(f"/tasks/{t.id}/reopen", headers=auth)
    assert r.status_code == 409 and "descarta el proyecto" in r.json()["detail"]


def test_reopen_is_refused_for_a_completed_project(client, auth, make_task):
    t = make_task("inbox", raw_text="x")
    pid = convert(client, auth, t.id).json()["project"]["id"]
    client.post(f"/projects/{pid}/complete", headers=auth)
    assert client.post(f"/tasks/{t.id}/reopen", headers=auth).status_code == 409


def test_dropping_the_project_lets_you_undo_the_conversion(client, auth, make_task):
    t = make_task("inbox", raw_text="x")
    pid = convert(client, auth, t.id, tasks=[{"title": "paso"}]).json()["project"]["id"]
    drop = client.post(f"/projects/{pid}/drop", json={"open_tasks": "trash"}, headers=auth)
    assert drop.status_code == 200
    back = client.post(f"/tasks/{t.id}/reopen", headers=auth)
    assert back.status_code == 200 and back.json()["status"] == "inbox"
    assert back.json()["clarified_at"] is None
    assert client.get("/inbox", headers=auth).json()["count"] == 1


def test_undo_restores_the_exact_previous_state(client, auth, make_task):
    t = make_task("waiting", title="pedir", waiting_on="Ana")
    pid = convert(client, auth, t.id).json()["project"]["id"]
    client.post(f"/projects/{pid}/drop", headers=auth)
    back = client.post(f"/tasks/{t.id}/reopen", headers=auth).json()
    assert back["status"] == "waiting" and back["waiting_on"] == "Ana"


def test_reconverting_after_undo_makes_a_new_project(client, auth, session, make_task):
    t = make_task("inbox", raw_text="x")
    first = convert(client, auth, t.id).json()["project"]["id"]
    client.post(f"/projects/{first}/drop", headers=auth)
    client.post(f"/tasks/{t.id}/reopen", headers=auth)
    second = convert(client, auth, t.id).json()
    assert second["project"]["id"] != first and second["already_converted"] is False
    again = convert(client, auth, t.id).json()
    assert again["already_converted"] is True and again["project"]["id"] == second["project"]["id"]
