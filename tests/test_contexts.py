from datetime import datetime, timedelta, timezone

import pytest

FUTURE = datetime.now(timezone.utc) + timedelta(days=5)


def names(body):
    return [c["name"] for c in body["items"]]


# --- Crear -----------------------------------------------------------------------------


def test_create_normalizes_the_name(client, auth):
    r = client.post("/contexts", json={"name": " PC "}, headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "@pc" and body["created"] is True
    assert body["next_tasks"] == 0 and body["available_tasks"] == 0


def test_create_is_idempotent(client, auth):
    first = client.post("/contexts", json={"name": "@casa"}, headers=auth).json()
    again = client.post("/contexts", json={"name": "CASA"}, headers=auth)
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["id"] == first["id"]
    assert client.get("/contexts", headers=auth).json()["count"] == 1


@pytest.mark.parametrize("name", ["", "   ", None, "dos palabras", "@", "x" * 61, "a/b"])
def test_create_rejects_invalid_names(client, auth, name):
    assert client.post("/contexts", json={"name": name}, headers=auth).status_code == 422


def test_create_rejects_unknown_fields(client, auth):
    assert client.post("/contexts", json={"name": "@pc", "color": "red"}, headers=auth).status_code == 422


def test_requires_authentication(client):
    assert client.get("/contexts").status_code == 401


# --- Listar ----------------------------------------------------------------------------


def test_list_counts_next_actions_and_what_is_available(
    client, auth, make_task, make_context, make_project
):
    pc = make_context("@pc")
    casa = make_context("@casa")
    make_task("next", context_id=pc.id)
    make_task("next", context_id=pc.id, defer_until=FUTURE)  # diferida: no disponible
    make_task("done", context_id=pc.id)  # no cuenta
    seq = make_project("P", sequential=True)
    make_task("next", context_id=pc.id, project_id=seq.id, position=1)
    make_task("next", context_id=pc.id, project_id=seq.id, position=2)  # bloqueada por la secuencia
    body = client.get("/contexts", headers=auth).json()
    assert names(body) == ["@casa", "@pc"]  # por nombre
    by_name = {c["name"]: c for c in body["items"]}
    assert (by_name["@pc"]["next_tasks"], by_name["@pc"]["available_tasks"]) == (4, 2)
    assert (by_name["@casa"]["next_tasks"], by_name["@casa"]["available_tasks"]) == (0, 0)
    assert casa.id == by_name["@casa"]["id"]


def test_contexts_created_by_clarify_show_up(client, auth, make_task):
    t = make_task("inbox")
    client.post(f"/tasks/{t.id}/clarify", json={"action": "next", "title": "x", "context": "Calle"}, headers=auth)
    body = client.get("/contexts", headers=auth).json()
    assert names(body) == ["@calle"] and body["items"][0]["available_tasks"] == 1


def test_list_is_scoped_to_the_user(client, auth, auth_other):
    client.post("/contexts", json={"name": "@mio"}, headers=auth)
    client.post("/contexts", json={"name": "@mio"}, headers=auth_other)  # mismo nombre, otro usuario
    client.post("/contexts", json={"name": "@ajeno"}, headers=auth_other)
    assert names(client.get("/contexts", headers=auth).json()) == ["@mio"]
    assert names(client.get("/contexts", headers=auth_other).json()) == ["@ajeno", "@mio"]


# --- Renombrar -------------------------------------------------------------------------


def test_rename_keeps_the_tasks(client, auth, make_task, make_context):
    c = make_context("@oficina")
    t = make_task("next", context_id=c.id)
    r = client.patch(f"/contexts/{c.id}", json={"name": "Trabajo"}, headers=auth)
    assert r.status_code == 200 and r.json()["name"] == "@trabajo" and r.json()["next_tasks"] == 1
    assert client.get(f"/tasks/{t.id}", headers=auth).json()["context"] == "@trabajo"


def test_rename_to_the_same_name_is_a_noop(client, auth, make_context):
    c = make_context("@pc")
    assert client.patch(f"/contexts/{c.id}", json={"name": "PC"}, headers=auth).status_code == 200


def test_rename_to_an_existing_name_is_a_conflict_and_changes_nothing(client, auth, make_context):
    a, b = make_context("@a"), make_context("@b")
    r = client.patch(f"/contexts/{a.id}", json={"name": "@b"}, headers=auth)
    assert r.status_code == 409
    assert names(client.get("/contexts", headers=auth).json()) == ["@a", "@b"]
    assert b.id != a.id


def test_rename_unknown_foreign_invalid(client, auth, auth_other, make_context):
    c = make_context("@pc")
    assert client.patch("/contexts/999999", json={"name": "@x"}, headers=auth).status_code == 404
    assert client.patch(f"/contexts/{c.id}", json={"name": "@x"}, headers=auth_other).status_code == 404
    assert client.patch(f"/contexts/{c.id}", json={"name": "a b"}, headers=auth).status_code == 422
    assert client.patch(f"/contexts/{2**63}", json={"name": "@x"}, headers=auth).status_code == 422


# --- Borrar ----------------------------------------------------------------------------


def test_delete_unused_context(client, auth, make_context):
    c = make_context("@pc")
    r = client.delete(f"/contexts/{c.id}", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"id": c.id, "deleted": True, "tasks_updated": 0}
    assert client.get("/contexts", headers=auth).json()["count"] == 0
    assert client.delete(f"/contexts/{c.id}", headers=auth).status_code == 404


def test_delete_in_use_is_refused_without_force(client, auth, make_task, make_context):
    c = make_context("@pc")
    make_task("next", context_id=c.id)
    make_task("done", context_id=c.id)
    r = client.delete(f"/contexts/{c.id}", headers=auth)
    assert r.status_code == 409 and "2 tareas" in r.json()["detail"]
    assert client.get("/contexts", headers=auth).json()["count"] == 1


def test_delete_with_force_clears_the_context_from_tasks(client, auth, make_task, make_context):
    c = make_context("@pc")
    t = make_task("next", title="x", context_id=c.id)
    other = make_context("@casa")
    keep = make_task("next", title="y", context_id=other.id)
    r = client.delete(f"/contexts/{c.id}?force=true", headers=auth)
    assert r.json()["tasks_updated"] == 1
    assert client.get(f"/tasks/{t.id}", headers=auth).json()["context"] is None
    assert client.get(f"/tasks/{keep.id}", headers=auth).json()["context"] == "@casa"


def test_delete_foreign_context(client, auth, auth_other, make_context):
    c = make_context("@pc")
    assert client.delete(f"/contexts/{c.id}?force=true", headers=auth_other).status_code == 404
    assert client.get("/contexts", headers=auth).json()["count"] == 1
