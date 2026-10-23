from datetime import datetime, timedelta, timezone

from app.models import User

NOW = datetime.now(timezone.utc)
DAY = timedelta(days=1)


def ids(response):
    return [i["id"] for i in response.json()["items"]]


# --- /next -----------------------------------------------------------------------------


def test_lists_require_auth(client):
    for path in ("/next", "/waiting", "/someday", "/tasks"):
        assert client.get(path).status_code == 401


def test_next_shows_only_what_can_be_done_now(client, auth, make_task, make_project):
    a = make_task("next")
    make_task("inbox")
    make_task("waiting", waiting_on="Ana")
    make_task("done")
    make_task("someday")
    make_task("next", defer_until=NOW + DAY)  # diferida
    seq = make_project(sequential=True)
    head = make_task("next", project_id=seq.id, position=1)
    make_task("next", project_id=seq.id, position=2)  # bloqueada por la secuencia

    r = client.get("/next", headers=auth)
    assert r.status_code == 200
    assert set(ids(r)) == {a.id, head.id}
    assert r.json()["count"] == 2


def test_next_orders_by_due_date_then_by_age(client, auth, make_task):
    undated_old = make_task("next", captured_at=NOW - 3 * DAY)
    due_late = make_task("next", due_at=NOW + 10 * DAY)
    due_soon = make_task("next", due_at=NOW + DAY)
    undated_new = make_task("next", captured_at=NOW)
    r = client.get("/next", headers=auth)
    assert ids(r) == [due_soon.id, due_late.id, undated_old.id, undated_new.id]


def test_next_filters_by_context_with_normalization(client, auth, make_task, make_context):
    pc, casa = make_context("@pc"), make_context("@casa")
    in_pc = make_task("next", context_id=pc.id)
    in_casa = make_task("next", context_id=casa.id)
    no_ctx = make_task("next")

    assert ids(client.get("/next?context=PC", headers=auth)) == [in_pc.id]
    assert ids(client.get("/next?context=@casa", headers=auth)) == [in_casa.id]
    assert set(ids(client.get("/next", headers=auth))) == {in_pc.id, in_casa.id, no_ctx.id}
    assert set(ids(client.get("/next?context=", headers=auth))) == {in_pc.id, in_casa.id, no_ctx.id}


def test_next_with_unknown_context_is_empty_not_an_error(client, auth, make_task):
    make_task("next")
    r = client.get("/next?context=@nadie", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"count": 0, "items": []}


def test_next_rejects_a_malformed_context(client, auth):
    assert client.get("/next?context=dos palabras", headers=auth).status_code == 422


def test_next_filters_by_project(client, auth, make_task, make_project):
    p = make_project()
    inside = make_task("next", project_id=p.id)
    make_task("next")
    assert ids(client.get(f"/next?project_id={p.id}", headers=auth)) == [inside.id]


def test_next_limit_trims_items_but_count_is_total(client, auth, make_task):
    for _ in range(4):
        make_task("next")
    body = client.get("/next?limit=2", headers=auth).json()
    assert body["count"] == 4 and len(body["items"]) == 2
    assert client.get("/next?limit=0", headers=auth).status_code == 422


def test_next_items_carry_context_and_project_names(
    client, auth, make_task, make_project, make_context
):
    ctx, proj = make_context("@pc"), make_project(title="Blog funcionando")
    make_task("next", title="Escribir post", context_id=ctx.id, project_id=proj.id)
    item = client.get("/next", headers=auth).json()["items"][0]
    assert item["context"] == "@pc"
    assert item["project_title"] == "Blog funcionando"
    assert item["title"] == "Escribir post"


def test_next_is_scoped_to_the_user(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    mine = make_task("next")
    make_task("next", user_id=other.id)
    assert ids(client.get("/next", headers=auth)) == [mine.id]


# --- /waiting --------------------------------------------------------------------------


def test_waiting_is_oldest_first_with_days_waiting(client, auth, make_task):
    recent = make_task("waiting", waiting_on="Luis", waiting_since=NOW - 2 * DAY)
    old = make_task("waiting", waiting_on="Ana", waiting_since=NOW - 10 * DAY)
    make_task("next")
    body = client.get("/waiting", headers=auth).json()
    assert [i["id"] for i in body["items"]] == [old.id, recent.id]
    assert [i["days_waiting"] for i in body["items"]] == [10, 2]
    assert body["count"] == 2


def test_waiting_older_than_days(client, auth, make_task):
    recent = make_task("waiting", waiting_on="Luis", waiting_since=NOW - 2 * DAY)
    old = make_task("waiting", waiting_on="Ana", waiting_since=NOW - 10 * DAY)
    assert ids(client.get("/waiting?older_than_days=7", headers=auth)) == [old.id]
    assert set(ids(client.get("/waiting?older_than_days=0", headers=auth))) == {old.id, recent.id}
    assert client.get("/waiting?older_than_days=-1", headers=auth).status_code == 422


# --- /someday --------------------------------------------------------------------------


def test_someday_lists_only_someday_oldest_first(client, auth, make_task):
    newer = make_task("someday", captured_at=NOW)
    older = make_task("someday", captured_at=NOW - 5 * DAY)
    make_task("next")
    make_task("reference")
    assert ids(client.get("/someday", headers=auth)) == [older.id, newer.id]
