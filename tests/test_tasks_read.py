from datetime import datetime, timedelta, timezone

import pytest

from app.models import User

NOW = datetime.now(timezone.utc)


def ids(response):
    return [i["id"] for i in response.json()["items"]]


def test_default_listing_hides_trashed_and_converted(client, auth, make_task):
    shown = [make_task("inbox"), make_task("next"), make_task("done"), make_task("someday")]
    make_task("trashed")
    make_task("converted")
    body = client.get("/tasks", headers=auth).json()
    assert {i["id"] for i in body["items"]} == {t.id for t in shown}
    assert body["count"] == 4


def test_status_filter_accepts_several_and_can_ask_for_trashed(client, auth, make_task):
    nxt, waiting = make_task("next"), make_task("waiting", waiting_on="Ana")
    make_task("someday")
    trashed = make_task("trashed")
    r = client.get("/tasks?status=next&status=waiting", headers=auth)
    assert set(ids(r)) == {nxt.id, waiting.id}
    assert ids(client.get("/tasks?status=trashed", headers=auth)) == [trashed.id]


def test_invalid_status_is_422(client, auth):
    assert client.get("/tasks?status=hecho", headers=auth).status_code == 422


def test_filter_by_project_and_context(client, auth, make_task, make_project, make_context):
    p, ctx = make_project(), make_context("@pc")
    both = make_task("next", project_id=p.id, context_id=ctx.id)
    make_task("next", project_id=p.id)
    make_task("next", context_id=ctx.id)
    assert ids(client.get(f"/tasks?project_id={p.id}&context=@pc", headers=auth)) == [both.id]


def test_search_looks_in_title_capture_and_notes_ignoring_case(client, auth, make_task):
    by_title = make_task("next", title="Comprar BATERIA")
    by_raw = make_task("inbox", raw_text="la batería del carro")
    by_notes = make_task("someday", title="Carro", notes="revisar bateria")
    make_task("next", title="Otra cosa")
    r = client.get("/tasks?q=bateria", headers=auth)
    assert set(ids(r)) == {by_title.id, by_notes.id}  # sin tilde no coincide con 'batería'
    assert ids(client.get("/tasks?q=batería", headers=auth)) == [by_raw.id]


def test_search_treats_wildcards_literally(client, auth, make_task):
    percent = make_task("inbox", raw_text="subir 100% el volumen")
    underscore = make_task("inbox", raw_text="archivo_final")
    make_task("inbox", raw_text="100 cosas")
    make_task("inbox", raw_text="archivoXfinal")
    assert ids(client.get("/tasks?q=%25", headers=auth)) == [percent.id]
    assert ids(client.get("/tasks?q=_", headers=auth)) == [underscore.id]


def test_pagination_returns_newest_first_with_total_count(client, auth, make_task):
    tasks = [make_task("next", captured_at=NOW - timedelta(days=5 - n)) for n in range(5)]
    newest_first = [t.id for t in reversed(tasks)]
    page1 = client.get("/tasks?limit=2", headers=auth).json()
    page2 = client.get("/tasks?limit=2&offset=2", headers=auth).json()
    assert page1["count"] == page2["count"] == 5
    assert [i["id"] for i in page1["items"]] == newest_first[:2]
    assert [i["id"] for i in page2["items"]] == newest_first[2:4]


def test_get_one_task_includes_availability(client, auth, make_task, make_project):
    p = make_project(sequential=True)
    first = make_task("next", project_id=p.id, position=1)
    second = make_task("next", project_id=p.id, position=2)
    assert client.get(f"/tasks/{first.id}", headers=auth).json()["available_now"] is True
    body = client.get(f"/tasks/{second.id}", headers=auth).json()
    assert body["available_now"] is False
    assert body["project_title"] == p.title


def test_get_unknown_or_foreign_task_is_404(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    theirs = make_task("next", user_id=other.id)
    assert client.get(f"/tasks/{theirs.id}", headers=auth).status_code == 404
    assert client.get("/tasks/999999", headers=auth).status_code == 404


@pytest.mark.parametrize("bad", ["0", "-1", "99999999999999999999", "abc"])
def test_task_id_is_validated_instead_of_crashing(client, auth, bad):
    assert client.get(f"/tasks/{bad}", headers=auth).status_code == 422
    assert client.post(f"/tasks/{bad}/complete", headers=auth).status_code == 422
    assert client.delete(f"/tasks/{bad}", headers=auth).status_code == 422
