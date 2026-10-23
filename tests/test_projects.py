from datetime import datetime, timedelta, timezone

import pytest

FUTURE = datetime.now(timezone.utc) + timedelta(days=5)


def create(client, auth, **body):
    return client.post("/projects", json=body, headers=auth)


def positions(detail):
    return [(t["title"], t["position"]) for t in detail["tasks"]]


# --- Crear -----------------------------------------------------------------------------


def test_create_minimal_project_is_sequential_active_and_stalled(client, auth):
    r = create(client, auth, title="Mudanza")
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Mudanza"
    assert body["status"] == "active" and body["sequential"] is True
    assert body["stalled"] is True and body["live_tasks"] == 0 and body["open_tasks"] == 0
    assert body["tasks"] == [] and body["next_task"] is None
    assert body["origin_task_id"] is None


def test_create_with_first_tasks_numbers_them_in_order(client, auth):
    r = create(
        client, auth, title="Viaje",
        tasks=[{"title": "Elegir fecha"}, {"title": "Comprar pasajes", "context": "PC"}, {"title": "Reservar"}],
    )
    body = r.json()
    assert positions(body) == [("Elegir fecha", 1), ("Comprar pasajes", 2), ("Reservar", 3)]
    assert body["stalled"] is False and body["live_tasks"] == 3
    assert body["next_task"]["title"] == "Elegir fecha"  # solo la primera está disponible
    assert body["tasks"][1]["context"] == "@pc"
    assert all(t["status"] == "next" and t["clarified_at"] for t in body["tasks"])


def test_parallel_project_has_no_positions_and_all_tasks_available(client, auth):
    body = create(
        client, auth, title="Casa", sequential=False, tasks=[{"title": "a"}, {"title": "b"}]
    ).json()
    assert body["sequential"] is False
    assert [t["position"] for t in body["tasks"]] == [None, None]
    nxt = client.get("/next", headers=auth).json()
    assert nxt["count"] == 2


def test_sequential_project_only_exposes_the_first_step_in_next(client, auth):
    create(client, auth, title="P", tasks=[{"title": "uno"}, {"title": "dos"}])
    items = client.get("/next", headers=auth).json()["items"]
    assert [t["title"] for t in items] == ["uno"]


def test_create_accepts_steps_in_other_states(client, auth):
    body = create(
        client, auth, title="P",
        tasks=[
            {"title": "pedir", "status": "waiting", "waiting_on": "Ana"},
            {"title": "quizá", "status": "someday"},
            {"title": "con fecha", "due_at": "2030-05-01T09:00:00"},
        ],
    ).json()
    waiting, someday, dated = body["tasks"]
    assert waiting["status"] == "waiting" and waiting["waiting_since"] is not None
    assert someday["status"] == "someday"
    assert dated["due_at"] == "2030-05-01T09:00:00-05:00"  # fecha sin zona = la del usuario
    assert body["open_tasks"] == 3 and body["live_tasks"] == 2


def test_blank_and_null_values_from_n8n_count_as_not_sent(client, auth):
    r = create(
        client, auth, title="P", outcome="", sequential=None, status="",
        tasks=[{"title": "a", "notes": "", "context": None, "status": ""}],
    )
    assert r.status_code == 201
    body = r.json()
    assert body["outcome"] is None and body["sequential"] is True and body["status"] == "active"
    assert body["tasks"][0]["status"] == "next"


def test_create_someday_project(client, auth):
    body = create(client, auth, title="Libro", status="someday").json()
    assert body["status"] == "someday"
    assert body["stalled"] is False  # solo los activos se estancan


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 501},
        {"title": "P", "status": "done"},
        {"title": "P", "unknown": 1},
        {"title": "P", "tasks": [{"title": ""}]},
        {"title": "P", "tasks": [{"title": "t", "status": "waiting"}]},  # falta waiting_on
        {"title": "P", "tasks": [{"title": "t", "waiting_on": "Ana"}]},  # waiting_on sin waiting
        {"title": "P", "tasks": [{"title": "t", "status": "someday", "context": "@pc"}]},
        {"title": "P", "tasks": [{"title": "t", "status": "reference"}]},
        {"title": "P", "tasks": [{"title": "t", "context": "dos palabras"}]},
        {"title": "P", "tasks": [{"title": "t", "project_id": 3}]},
    ],
)
def test_create_validation(client, auth, body):
    assert create(client, auth, **body).status_code == 422


def test_projects_require_authentication(client):
    assert client.get("/projects").status_code == 401
    assert client.post("/projects", json={"title": "x"}).status_code == 401


# --- Consultar -------------------------------------------------------------------------


def test_get_project_detail_hides_closed_tasks_unless_asked(client, auth, session, make_task):
    p = create(client, auth, title="P", tasks=[{"title": "a"}, {"title": "b"}]).json()
    first = p["tasks"][0]["id"]
    client.post(f"/tasks/{first}/complete", headers=auth)
    detail = client.get(f"/projects/{p['id']}", headers=auth).json()
    assert [t["title"] for t in detail["tasks"]] == ["b"]
    assert detail["next_task"]["title"] == "b"
    full = client.get(f"/projects/{p['id']}?include_closed=true", headers=auth).json()
    assert [t["status"] for t in full["tasks"]] == ["done", "next"]


def test_get_unknown_or_foreign_project_is_404(client, auth, auth_other):
    p = create(client, auth, title="P").json()
    assert client.get("/projects/999999", headers=auth).status_code == 404
    assert client.get(f"/projects/{p['id']}", headers=auth_other).status_code == 404
    assert client.get(f"/projects/{2**63}", headers=auth).status_code == 422
    assert client.get("/projects/0", headers=auth).status_code == 422


def test_list_defaults_to_active_and_someday(client, auth):
    a = create(client, auth, title="activo").json()
    s = create(client, auth, title="algún día", status="someday").json()
    d = create(client, auth, title="hecho").json()
    client.post(f"/projects/{d['id']}/complete", headers=auth)
    body = client.get("/projects", headers=auth).json()
    assert body["count"] == 2
    assert [p["id"] for p in body["items"]] == [a["id"], s["id"]]
    done = client.get("/projects?status=done", headers=auth).json()
    assert [p["id"] for p in done["items"]] == [d["id"]]
    both = client.get("/projects?status=done&status=active", headers=auth).json()
    assert both["count"] == 2


def test_list_counts_live_and_open_tasks(client, auth, session, make_task, make_project):
    p = make_project("P")
    make_task("next", project_id=p.id)
    make_task("waiting", waiting_on="Ana", project_id=p.id)
    make_task("next", project_id=p.id, defer_until=FUTURE)  # diferida: abierta pero no viva
    make_task("someday", project_id=p.id)
    make_task("done", project_id=p.id)
    item = client.get("/projects", headers=auth).json()["items"][0]
    assert item["live_tasks"] == 2 and item["open_tasks"] == 4 and item["stalled"] is False


def test_list_search_pagination_and_isolation(client, auth, auth_other):
    for title in ("Comprar casa", "Aprender guitarra", "100% listo", "Casa de campo"):
        create(client, auth, title=title)
    create(client, auth_other, title="Casa ajena")
    assert client.get("/projects?q=casa", headers=auth).json()["count"] == 2
    assert client.get("/projects?q=%25", headers=auth).json()["count"] == 1  # % literal
    page = client.get("/projects?limit=2&offset=2", headers=auth).json()
    assert page["count"] == 4 and len(page["items"]) == 2
    assert client.get("/projects", headers=auth_other).json()["count"] == 1


# --- Estancados ------------------------------------------------------------------------


def test_stalled_lists_active_projects_without_live_tasks(
    client, auth, make_task, make_project
):
    empty = make_project("vacío")
    only_someday = make_project("solo algún día")
    make_task("someday", project_id=only_someday.id)
    only_deferred = make_project("solo diferida")
    make_task("next", project_id=only_deferred.id, defer_until=FUTURE)
    healthy = make_project("sano")
    make_task("next", project_id=healthy.id)
    waiting = make_project("esperando")
    make_task("waiting", waiting_on="Ana", project_id=waiting.id)
    parked = make_project("pausado", status="someday")
    closed = make_project("cerrado", status="done")

    body = client.get("/projects/stalled", headers=auth).json()
    ids = [p["id"] for p in body["items"]]
    assert ids == [empty.id, only_someday.id, only_deferred.id]
    assert body["count"] == 3
    assert all(p["stalled"] for p in body["items"])
    for other in (healthy, waiting, parked, closed):
        assert other.id not in ids


def test_stalled_is_scoped_to_the_user(client, auth, auth_other):
    create(client, auth_other, title="ajeno")
    assert client.get("/projects/stalled", headers=auth).json()["count"] == 0


def test_project_stops_being_stalled_when_a_task_is_added(client, auth):
    p = create(client, auth, title="P").json()
    assert client.get("/projects/stalled", headers=auth).json()["count"] == 1
    client.post(f"/projects/{p['id']}/tasks", json={"tasks": [{"title": "a"}]}, headers=auth)
    assert client.get("/projects/stalled", headers=auth).json()["count"] == 0


# --- Editar ----------------------------------------------------------------------------


def test_patch_title_and_outcome(client, auth):
    p = create(client, auth, title="viejo").json()
    body = client.patch(
        f"/projects/{p['id']}", json={"title": " Nuevo ", "outcome": "Listo cuando X"}, headers=auth
    ).json()
    assert body["title"] == "Nuevo" and body["outcome"] == "Listo cuando X"


def test_patch_clear_outcome_and_empty_values_do_not_clear(client, auth):
    p = create(client, auth, title="P", outcome="algo").json()
    url = f"/projects/{p['id']}"
    kept = client.patch(url, json={"title": "Q", "outcome": ""}, headers=auth).json()
    assert kept["outcome"] == "algo"
    cleared = client.patch(url, json={"clear": ["outcome"]}, headers=auth).json()
    assert cleared["outcome"] is None


@pytest.mark.parametrize(
    "body",
    [{}, {"title": ""}, {"clear": []}, {"outcome": "x", "clear": ["outcome"]},
     {"status": "done"}, {"status": "dropped"}, {"x": 1}],
)
def test_patch_validation(client, auth, body):
    p = create(client, auth, title="P").json()
    assert client.patch(f"/projects/{p['id']}", json=body, headers=auth).status_code == 422


def test_patch_pause_and_reactivate(client, auth):
    p = create(client, auth, title="P").json()
    url = f"/projects/{p['id']}"
    paused = client.patch(url, json={"status": "someday"}, headers=auth).json()
    assert paused["status"] == "someday" and paused["stalled"] is False
    assert client.patch(url, json={"status": "active"}, headers=auth).json()["stalled"] is True


def test_patch_reactivating_a_closed_project_clears_completed_at(client, auth):
    p = create(client, auth, title="P").json()
    closed = client.post(f"/projects/{p['id']}/complete", headers=auth).json()["project"]
    assert closed["completed_at"] is not None
    back = client.patch(f"/projects/{p['id']}", json={"status": "active"}, headers=auth).json()
    assert back["status"] == "active" and back["completed_at"] is None


def test_patch_sequential_to_parallel_forgets_the_order(client, auth):
    p = create(client, auth, title="P", tasks=[{"title": "a"}, {"title": "b"}]).json()
    body = client.patch(f"/projects/{p['id']}", json={"sequential": False}, headers=auth).json()
    assert body["sequential"] is False
    assert [t["position"] for t in body["tasks"]] == [None, None]
    assert client.get("/next", headers=auth).json()["count"] == 2


def test_patch_parallel_to_sequential_numbers_tasks_by_age(
    client, auth, session, make_task, make_project
):
    p = make_project("P", sequential=False)
    now = datetime.now(timezone.utc)
    # Se crean en un orden de ids distinto al de antigüedad, para que el orden no sea casualidad.
    older = make_task("next", title="vieja", project_id=p.id, captured_at=now - timedelta(days=2))
    newer = make_task("next", title="nueva", project_id=p.id, captured_at=now)
    done = make_task("done", title="hecha", project_id=p.id, captured_at=now - timedelta(days=9))
    body = client.patch(f"/projects/{p.id}", json={"sequential": True}, headers=auth).json()
    assert [(t["title"], t["position"]) for t in body["tasks"]] == [("vieja", 2), ("nueva", 3)]
    session.refresh(done)
    assert done.position == 1
    assert body["next_task"]["id"] == older.id
    assert newer.id != older.id
    assert [t["title"] for t in client.get("/next", headers=auth).json()["items"]] == ["vieja"]


def test_patch_same_sequential_value_changes_nothing(client, auth):
    p = create(client, auth, title="P", tasks=[{"title": "a"}, {"title": "b"}]).json()
    body = client.patch(f"/projects/{p['id']}", json={"sequential": True}, headers=auth).json()
    assert positions(body) == [("a", 1), ("b", 2)]


def test_patch_unknown_or_foreign_project(client, auth, auth_other):
    p = create(client, auth, title="P").json()
    assert client.patch("/projects/999999", json={"title": "x"}, headers=auth).status_code == 404
    assert client.patch(f"/projects/{p['id']}", json={"title": "x"}, headers=auth_other).status_code == 404


# --- Cerrar ----------------------------------------------------------------------------


def test_complete_empty_project(client, auth):
    p = create(client, auth, title="P").json()
    r = client.post(f"/projects/{p['id']}/complete", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["already_closed"] is False and body["tasks_closed"] == 0
    assert body["project"]["status"] == "done" and body["project"]["completed_at"]
    assert body["project"]["stalled"] is False


def test_complete_refuses_when_there_are_open_tasks(client, auth, session, make_task):
    p = create(client, auth, title="P", tasks=[{"title": "a"}, {"title": "b", "status": "someday"}]).json()
    r = client.post(f"/projects/{p['id']}/complete", headers=auth)
    assert r.status_code == 409 and "2 tareas abiertas" in r.json()["detail"]
    assert client.get(f"/projects/{p['id']}", headers=auth).json()["status"] == "active"


@pytest.mark.parametrize("body", [None, {}, {"open_tasks": ""}, {"open_tasks": None}, {"open_tasks": "refuse"}])
def test_default_is_to_refuse(client, auth, body):
    p = create(client, auth, title="P", tasks=[{"title": "a"}]).json()
    r = client.post(f"/projects/{p['id']}/complete", json=body, headers=auth)
    assert r.status_code == 409


def test_complete_marking_open_tasks_done(client, auth, session, make_task):
    p = create(client, auth, title="P", tasks=[
        {"title": "a"}, {"title": "b", "status": "waiting", "waiting_on": "Ana"},
    ]).json()
    r = client.post(f"/projects/{p['id']}/complete", json={"open_tasks": "complete"}, headers=auth)
    body = r.json()
    assert body["tasks_closed"] == 2 and body["project"]["open_tasks"] == 0
    tasks = client.get(f"/projects/{p['id']}?include_closed=true", headers=auth).json()["tasks"]
    assert [t["status"] for t in tasks] == ["done", "done"]
    assert all(t["completed_at"] for t in tasks)


def test_drop_trashing_open_tasks_and_task_can_be_reopened(client, auth):
    p = create(client, auth, title="P", tasks=[{"title": "a"}, {"title": "b", "status": "someday"}]).json()
    body = client.post(f"/projects/{p['id']}/drop", json={"open_tasks": "trash"}, headers=auth).json()
    assert body["project"]["status"] == "dropped" and body["tasks_closed"] == 2
    tasks = client.get(f"/projects/{p['id']}?include_closed=true", headers=auth).json()["tasks"]
    assert [t["status"] for t in tasks] == ["trashed", "trashed"]
    back = client.post(f"/tasks/{tasks[1]['id']}/reopen", headers=auth).json()
    assert back["status"] == "someday"  # vuelve exactamente a donde estaba


def test_closing_is_idempotent_and_cannot_switch_outcome(client, auth):
    p = create(client, auth, title="P").json()
    url = f"/projects/{p['id']}"
    first = client.post(f"{url}/complete", headers=auth).json()
    again = client.post(f"{url}/complete", headers=auth)
    assert again.status_code == 200 and again.json()["already_closed"] is True
    assert again.json()["project"]["completed_at"] == first["project"]["completed_at"]
    assert client.post(f"{url}/drop", headers=auth).status_code == 409
    q = create(client, auth, title="Q").json()
    client.post(f"/projects/{q['id']}/drop", headers=auth)
    assert client.post(f"/projects/{q['id']}/drop", headers=auth).json()["already_closed"] is True
    assert client.post(f"/projects/{q['id']}/complete", headers=auth).status_code == 409


def test_close_only_touches_its_own_tasks(client, auth, make_task, session):
    p = create(client, auth, title="P", tasks=[{"title": "a"}]).json()
    other = create(client, auth, title="Otro", tasks=[{"title": "b"}]).json()
    loose = make_task("next", title="suelta")
    client.post(f"/projects/{p['id']}/complete", json={"open_tasks": "complete"}, headers=auth)
    assert client.get(f"/projects/{other['id']}", headers=auth).json()["tasks"][0]["status"] == "next"
    session.refresh(loose)
    assert loose.status == "next"


def test_close_unknown_or_foreign_project(client, auth, auth_other):
    p = create(client, auth, title="P").json()
    assert client.post("/projects/999999/complete", headers=auth).status_code == 404
    assert client.post(f"/projects/{p['id']}/drop", headers=auth_other).status_code == 404


def test_completing_the_last_task_marks_the_project_stalled(client, auth):
    p = create(client, auth, title="P", tasks=[{"title": "a"}]).json()
    r = client.post(f"/tasks/{p['tasks'][0]['id']}/complete", headers=auth).json()
    assert r["project_stalled"] is True
    assert client.get("/projects/stalled", headers=auth).json()["items"][0]["id"] == p["id"]


def test_tasks_cannot_be_moved_into_a_closed_project(client, auth, make_task):
    p = create(client, auth, title="P").json()
    client.post(f"/projects/{p['id']}/complete", headers=auth)
    t = make_task("next", title="x")
    r = client.patch(f"/tasks/{t.id}", json={"project_id": p["id"]}, headers=auth)
    assert r.status_code == 409
