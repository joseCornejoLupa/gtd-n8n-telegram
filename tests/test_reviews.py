from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import Review

H = timedelta(hours=1)
D = timedelta(days=1)


def now():
    return datetime.now(timezone.utc)


def weekly(client, auth, **params):
    r = client.get("/review/weekly", params=params, headers=auth)
    assert r.status_code == 200
    return r.json()


def finish(client, auth):
    return client.post("/review/weekly/complete", headers=auth)


# --- Material de la revisión -----------------------------------------------------------


def test_requires_authentication(client):
    assert client.get("/review/weekly").status_code == 401
    assert client.post("/review/weekly/complete").status_code == 401
    assert client.get("/reviews").status_code == 401


def test_first_review_has_no_history(client, auth, noon_tz):
    r = weekly(client, auth)
    assert r["last_review_at"] is None and r["days_since_last_review"] is None
    assert set(r["snapshot"].values()) == {0}
    assert r["inbox"] == {"count": 0, "items": []}


def test_snapshot_counts(client, auth, noon_tz, make_task, make_project):
    make_task("inbox")
    make_task("inbox")
    make_task("next", title="a", due_at=now() - 2 * H)  # vencida
    make_task("next", title="b")
    make_task("waiting", title="c", waiting_on="Ana", due_at=now() - 3 * D)  # vencida
    make_task("someday", title="d")
    make_task("done", title="e", completed_at=now() - 2 * D)
    make_task("done", title="f", completed_at=now() - 10 * D)  # fuera de la semana
    make_task("next", title="viejo", captured_at=now() - 20 * D)  # capturada hace mucho
    active = make_project("activo")
    make_task("next", project_id=active.id)
    make_project("estancado")
    make_project("pausado", status="someday")
    s = weekly(client, auth)["snapshot"]
    assert s == {
        "inbox": 2, "next": 4, "waiting": 1, "someday": 1,
        "active_projects": 2, "stalled_projects": 1, "overdue": 2,
        "completed_last_7_days": 1,
        "captured_last_7_days": 9,  # 10 tareas menos la capturada hace 20 días
    }


def test_sections(client, auth, noon_tz, make_task, make_project):
    first = make_task("inbox", raw_text="más antigua", captured_at=now() - 3 * D)
    make_task("inbox", raw_text="reciente")
    stalled = make_project("sin acción")
    old_wait = make_task("waiting", title="espera", waiting_on="Ana", waiting_since=now() - 4 * D)
    make_task("waiting", title="hoy", waiting_on="Luis", waiting_since=now() - H)
    for i in range(3):
        make_task("someday", title=f"s{i}")
    r = weekly(client, auth, limit=2)
    assert [t["id"] for t in r["inbox"]["items"]] == [first.id, r["inbox"]["items"][1]["id"]]
    assert r["inbox"]["count"] == 2
    assert [p["id"] for p in r["stalled_projects"]["items"]] == [stalled.id]
    # en la revisión se ven todas las esperas, no solo las viejas
    assert r["waiting"]["count"] == 2 and r["waiting"]["items"][0]["id"] == old_wait.id
    assert r["waiting"]["items"][0]["days_waiting"] == 4
    assert r["someday"]["count"] == 3 and len(r["someday"]["items"]) == 2


def test_weekly_is_scoped_to_the_user(client, auth, auth_other, noon_tz, make_task):
    make_task("inbox")
    assert weekly(client, auth_other)["snapshot"]["inbox"] == 0


def test_weekly_text(client, auth, noon_tz, make_task, make_project):
    make_task("inbox")
    make_task("inbox")
    make_task("waiting", title="Respuesta", waiting_on="Ana", waiting_since=now() - 5 * D)
    make_project("Mudanza")
    r = client.get("/review/weekly?format=text", headers=auth)
    assert r.headers["content-type"].startswith("text/plain")
    out = r.text
    assert "Revisión semanal" in out and "Aún no has hecho ninguna revisión." in out
    assert "1. Inbox: 2 — clarifícalo primero" in out
    assert "4. Esperando respuesta: 1" in out
    assert "• Mudanza" in out and "• Respuesta — Ana, 5 días" in out


def test_weekly_text_with_empty_inbox_and_previous_review(client, auth, session, user, noon_tz):
    session.add(Review(user_id=user.id, done_at=now() - 6 * D, snapshot={}))
    session.flush()
    out = client.get("/review/weekly?format=text", headers=auth).text
    assert "1. Inbox: 0 ✅" in out and "Última revisión: hace 6 días." in out


def test_weekly_text_last_review_today_and_singular_days(client, auth, session, user, noon_tz, make_task):
    session.add(Review(user_id=user.id, done_at=now() - H, snapshot={}))
    make_task("waiting", title="ayer", waiting_on="Ana", waiting_since=now() - 30 * H)
    make_task("waiting", title="ahora", waiting_on="Luis", waiting_since=now() - H)
    session.flush()
    out = client.get("/review/weekly?format=text", headers=auth).text
    assert "Última revisión: hoy." in out
    assert "• ayer — Ana, 1 día" in out and "• ahora — Luis, hoy" in out


# --- Registrar la revisión -------------------------------------------------------------


def test_complete_records_the_snapshot(client, auth, session, noon_tz, make_task):
    make_task("inbox")
    make_task("next", title="x", due_at=now() - H)
    r = finish(client, auth)
    assert r.status_code == 201
    body = r.json()
    assert body["already_recorded"] is False
    assert body["snapshot"]["inbox"] == 1 and body["snapshot"]["overdue"] == 1
    # La hora sale en la zona del usuario, con su desfase.
    assert datetime.fromisoformat(body["done_at"]).utcoffset() == ZoneInfo(noon_tz).utcoffset(None)
    assert session.query(Review).count() == 1


def test_complete_is_idempotent_within_the_same_day(client, auth, session, noon_tz, make_task):
    first = finish(client, auth).json()
    make_task("inbox")  # cambia el estado, pero el registro de hoy no se duplica ni se reescribe
    again = finish(client, auth)
    assert again.status_code == 200
    assert again.json()["already_recorded"] is True and again.json()["id"] == first["id"]
    assert again.json()["snapshot"] == first["snapshot"]
    assert session.query(Review).count() == 1


def test_a_review_from_a_previous_local_day_does_not_block_a_new_one(client, auth, session, user, noon_tz):
    # Con la hora local ~12:00, hace 13 h es ayer (23:xx) en la zona del usuario.
    session.add(Review(user_id=user.id, done_at=now() - 13 * H, snapshot={}))
    session.flush()
    assert finish(client, auth).status_code == 201
    assert session.query(Review).count() == 2


def test_a_review_earlier_today_blocks_a_new_one(client, auth, session, user, noon_tz):
    session.add(Review(user_id=user.id, done_at=now() - 2 * H, snapshot={}))
    session.flush()
    r = finish(client, auth)
    assert r.status_code == 200 and r.json()["already_recorded"] is True
    assert session.query(Review).count() == 1


def test_other_users_review_does_not_count(client, auth, auth_other, noon_tz):
    assert finish(client, auth_other).status_code == 201
    assert finish(client, auth).status_code == 201


def test_last_review_is_never_someone_elses(client, auth, auth_other, noon_tz):
    finish(client, auth_other)
    mine = weekly(client, auth)
    assert mine["last_review_at"] is None and mine["days_since_last_review"] is None


def test_weekly_shows_the_last_review(client, auth, session, user, noon_tz):
    session.add(Review(user_id=user.id, done_at=now() - 9 * D, snapshot={}))
    session.add(Review(user_id=user.id, done_at=now() - 2 * D, snapshot={}))
    session.flush()
    r = weekly(client, auth)
    assert r["days_since_last_review"] == 2 and r["last_review_at"] is not None
    finish(client, auth)
    assert weekly(client, auth)["days_since_last_review"] == 0


# --- Historial -------------------------------------------------------------------------


def test_list_reviews_newest_first(client, auth, auth_other, session, user, noon_tz):
    old = Review(user_id=user.id, done_at=now() - 20 * D, snapshot={"inbox": 5})
    mid = Review(user_id=user.id, done_at=now() - 10 * D, snapshot={"inbox": 3})
    session.add_all([old, mid])
    session.flush()
    finish(client, auth)
    finish(client, auth_other)
    body = client.get("/reviews", headers=auth).json()
    assert body["count"] == 3
    assert [r["snapshot"].get("inbox") for r in body["items"]] == [0, 3, 5]
    assert len(client.get("/reviews?limit=1", headers=auth).json()["items"]) == 1
    assert client.get("/reviews?limit=0", headers=auth).status_code == 422
    assert client.get("/reviews", headers=auth_other).json()["count"] == 1
