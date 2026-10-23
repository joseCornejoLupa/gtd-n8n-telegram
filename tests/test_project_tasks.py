import pytest


def project(client, auth, titles=("a", "b", "c"), **extra):
    body = {"title": "P", "tasks": [{"title": t} for t in titles], **extra}
    return client.post("/projects", json=body, headers=auth).json()


def add(client, auth, project_id, titles, **extra):
    body = {"tasks": [{"title": t} for t in titles], **extra}
    return client.post(f"/projects/{project_id}/tasks", json=body, headers=auth)


def order(client, auth, project_id):
    tasks = client.get(f"/projects/{project_id}", headers=auth).json()["tasks"]
    return [(t["title"], t["position"]) for t in tasks]


# --- Añadir tareas ---------------------------------------------------------------------


def test_add_goes_to_the_end_by_default(client, auth):
    p = project(client, auth)
    r = add(client, auth, p["id"], ["d", "e"])
    assert r.status_code == 201 and r.json()["created"] is True
    assert [t["title"] for t in r.json()["tasks"]] == ["d", "e"]
    assert order(client, auth, p["id"]) == [("a", 1), ("b", 2), ("c", 3), ("d", 4), ("e", 5)]
    assert r.json()["project"]["open_tasks"] == 5


def test_add_after_a_task_keeps_positions_contiguous(client, auth):
    p = project(client, auth)
    b = p["tasks"][1]["id"]
    add(client, auth, p["id"], ["x", "y"], after_task_id=b)
    assert order(client, auth, p["id"]) == [("a", 1), ("b", 2), ("x", 3), ("y", 4), ("c", 5)]


def test_add_first(client, auth):
    p = project(client, auth)
    add(client, auth, p["id"], ["x"], first=True)
    assert order(client, auth, p["id"]) == [("x", 1), ("a", 2), ("b", 3), ("c", 4)]
    assert client.get("/next", headers=auth).json()["items"][0]["title"] == "x"


def test_add_after_a_completed_task_is_allowed(client, auth):
    p = project(client, auth)
    a = p["tasks"][0]["id"]
    client.post(f"/tasks/{a}/complete", headers=auth)
    add(client, auth, p["id"], ["x"], after_task_id=a)
    full = client.get(f"/projects/{p['id']}?include_closed=true", headers=auth).json()["tasks"]
    assert [(t["title"], t["position"]) for t in full] == [("a", 1), ("x", 2), ("b", 3), ("c", 4)]


def test_add_heals_gaps_in_positions(client, auth, session, make_task, make_project):
    p = make_project("P", sequential=True)
    make_task("next", title="a", project_id=p.id, position=5)
    make_task("next", title="b", project_id=p.id, position=9)
    add(client, auth, p.id, ["c"])
    assert order(client, auth, p.id) == [("a", 1), ("b", 2), ("c", 3)]


def test_add_to_parallel_project_has_no_positions(client, auth):
    p = project(client, auth, sequential=False)
    r = add(client, auth, p["id"], ["x"])
    assert r.status_code == 201 and r.json()["tasks"][0]["position"] is None


@pytest.mark.parametrize("placement", [{"first": True}, {"after_task_id": 1}])
def test_placement_in_a_parallel_project_is_a_conflict(client, auth, placement):
    p = project(client, auth, sequential=False)
    assert add(client, auth, p["id"], ["x"], **placement).status_code == 409


def test_add_after_unknown_task_or_one_from_another_project(client, auth):
    p = project(client, auth)
    other = project(client, auth)
    assert add(client, auth, p["id"], ["x"], after_task_id=999999).status_code == 422
    foreign = other["tasks"][0]["id"]
    assert add(client, auth, p["id"], ["x"], after_task_id=foreign).status_code == 422
    assert order(client, auth, p["id"]) == [("a", 1), ("b", 2), ("c", 3)]  # no cambió nada


@pytest.mark.parametrize(
    "body",
    [
        {"tasks": []},
        {},
        {"tasks": [{"title": "x"}], "first": True, "after_task_id": 1},
        {"tasks": [{"title": "x"}], "extra": 1},
        {"tasks": [{"title": ""}]},
        {"tasks": [{"title": "x"}] * 51},
    ],
)
def test_add_validation(client, auth, body):
    p = project(client, auth)
    assert client.post(f"/projects/{p['id']}/tasks", json=body, headers=auth).status_code == 422


def test_add_blank_placement_from_n8n_means_end(client, auth):
    p = project(client, auth)
    r = add(client, auth, p["id"], ["x"], after_task_id=None, first="")
    assert r.status_code == 201
    assert order(client, auth, p["id"])[-1] == ("x", 4)


def test_cannot_add_to_closed_unknown_or_foreign_project(client, auth, auth_other):
    p = project(client, auth, titles=())
    assert add(client, auth_other, p["id"], ["x"]).status_code == 404
    assert add(client, auth, 999999, ["x"]).status_code == 404
    client.post(f"/projects/{p['id']}/complete", headers=auth)
    assert add(client, auth, p["id"], ["x"]).status_code == 409


def test_add_steps_in_other_states_and_dates(client, auth):
    p = project(client, auth, titles=())
    r = client.post(
        f"/projects/{p['id']}/tasks",
        json={"tasks": [
            {"title": "pedir", "status": "waiting", "waiting_on": "Ana", "due_at": "2030-05-01T09:00:00"},
            {"title": "luego", "defer_until": "2031-01-01T08:00:00", "context": "casa"},
        ]},
        headers=auth,
    ).json()
    waiting, deferred = r["tasks"]
    assert waiting["waiting_on"] == "Ana" and waiting["due_at"] == "2030-05-01T09:00:00-05:00"
    assert deferred["defer_until"] == "2031-01-01T08:00:00-05:00" and deferred["context"] == "@casa"
    assert r["project"]["live_tasks"] == 1  # la diferida aún no cuenta


# --- Idempotencia ----------------------------------------------------------------------


def test_request_id_makes_adding_idempotent(client, auth):
    p = project(client, auth, titles=("a",))
    first = add(client, auth, p["id"], ["x", "y"], request_id="exec-42")
    again = add(client, auth, p["id"], ["x", "y"], request_id="exec-42")
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["created"] is False
    assert [t["id"] for t in again.json()["tasks"]] == [t["id"] for t in first.json()["tasks"]]
    assert order(client, auth, p["id"]) == [("a", 1), ("x", 2), ("y", 3)]


def test_different_request_ids_are_independent(client, auth):
    p = project(client, auth, titles=())
    add(client, auth, p["id"], ["x"], request_id="one")
    add(client, auth, p["id"], ["x"], request_id="two")
    assert len(order(client, auth, p["id"])) == 2


def test_same_request_id_in_two_projects_is_two_requests(client, auth):
    one, two = project(client, auth, titles=()), project(client, auth, titles=())
    r1 = add(client, auth, one["id"], ["x"], request_id="exec-7")
    r2 = add(client, auth, two["id"], ["x"], request_id="exec-7")
    assert (r1.status_code, r2.status_code) == (201, 201)
    assert len(order(client, auth, one["id"])) == 1 and len(order(client, auth, two["id"])) == 1


def test_request_id_of_another_user_does_not_collide(client, auth, auth_other):
    mine = project(client, auth, titles=())
    theirs = project(client, auth_other, titles=())
    assert add(client, auth, mine["id"], ["x"], request_id="same").status_code == 201
    assert add(client, auth_other, theirs["id"], ["x"], request_id="same").status_code == 201


# --- Reordenar -------------------------------------------------------------------------


def reorder(client, auth, project_id, **body):
    return client.post(f"/projects/{project_id}/reorder", json=body, headers=auth)


def test_move_to_first(client, auth):
    p = project(client, auth)
    c = p["tasks"][2]["id"]
    r = reorder(client, auth, p["id"], task_id=c, first=True)
    assert r.status_code == 200
    assert [(t["title"], t["position"]) for t in r.json()["tasks"]] == [("c", 1), ("a", 2), ("b", 3)]
    assert r.json()["next_task"]["title"] == "c"
    assert [t["title"] for t in client.get("/next", headers=auth).json()["items"]] == ["c"]


def test_move_after_another_task(client, auth):
    p = project(client, auth)
    a, b, c = (t["id"] for t in p["tasks"])
    reorder(client, auth, p["id"], task_id=a, after_task_id=b)
    assert order(client, auth, p["id"]) == [("b", 1), ("a", 2), ("c", 3)]
    reorder(client, auth, p["id"], task_id=a, after_task_id=c)
    assert order(client, auth, p["id"]) == [("b", 1), ("c", 2), ("a", 3)]


def test_move_to_where_it_already_is_changes_nothing(client, auth):
    p = project(client, auth)
    a, b, _ = (t["id"] for t in p["tasks"])
    reorder(client, auth, p["id"], task_id=b, after_task_id=a)
    assert order(client, auth, p["id"]) == [("a", 1), ("b", 2), ("c", 3)]


def test_reorder_with_completed_tasks_keeps_positions_unique(client, auth):
    p = project(client, auth, titles=("a", "b", "c", "d"))
    a, b, c, d = (t["id"] for t in p["tasks"])
    client.post(f"/tasks/{a}/complete", headers=auth)
    reorder(client, auth, p["id"], task_id=d, first=True)
    full = client.get(f"/projects/{p['id']}?include_closed=true", headers=auth).json()["tasks"]
    assert sorted(t["position"] for t in full) == [1, 2, 3, 4]
    assert [t["title"] for t in client.get("/next", headers=auth).json()["items"]] == ["d"]


def test_reorder_validation_and_errors(client, auth):
    p = project(client, auth)
    other = project(client, auth)
    a, b, _ = (t["id"] for t in p["tasks"])
    foreign = other["tasks"][0]["id"]
    assert reorder(client, auth, p["id"], task_id=a).status_code == 422
    assert reorder(client, auth, p["id"], task_id=a, first=True, after_task_id=b).status_code == 422
    assert reorder(client, auth, p["id"], task_id=a, after_task_id=a).status_code == 422
    assert reorder(client, auth, p["id"], task_id=a, first=False).status_code == 422
    assert reorder(client, auth, p["id"], task_id=foreign, first=True).status_code == 404
    assert reorder(client, auth, p["id"], task_id=a, after_task_id=foreign).status_code == 422
    assert reorder(client, auth, p["id"], task_id=999999, first=True).status_code == 404
    assert order(client, auth, p["id"]) == [("a", 1), ("b", 2), ("c", 3)]


def test_reorder_parallel_project_is_a_conflict(client, auth):
    p = project(client, auth, sequential=False)
    t = p["tasks"][0]["id"]
    assert reorder(client, auth, p["id"], task_id=t, first=True).status_code == 409


def test_reorder_foreign_or_unknown_project(client, auth, auth_other):
    p = project(client, auth)
    t = p["tasks"][0]["id"]
    assert reorder(client, auth_other, p["id"], task_id=t, first=True).status_code == 404
    assert reorder(client, auth, 999999, task_id=t, first=True).status_code == 404


def test_reopened_task_returns_to_its_slot(client, auth):
    p = project(client, auth)
    a = p["tasks"][0]["id"]
    client.post(f"/tasks/{a}/complete", headers=auth)
    assert [t["title"] for t in client.get("/next", headers=auth).json()["items"]] == ["b"]
    client.post(f"/tasks/{a}/reopen", headers=auth)
    assert [t["title"] for t in client.get("/next", headers=auth).json()["items"]] == ["a"]


def test_task_moved_into_sequential_project_goes_last_and_can_be_moved(client, auth, make_task):
    p = project(client, auth)
    t = make_task("next", title="suelta")
    client.patch(f"/tasks/{t.id}", json={"project_id": p["id"]}, headers=auth)
    assert order(client, auth, p["id"])[-1] == ("suelta", 4)
    reorder(client, auth, p["id"], task_id=t.id, first=True)
    assert order(client, auth, p["id"])[0] == ("suelta", 1)
