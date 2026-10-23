"""Las fechas se leen y se devuelven en la zona horaria definida por el usuario."""

from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime.now(timezone.utc)


def put_tz(client, auth, name):
    return client.put("/users/me", json={"timezone": name}, headers=auth)


def clarify(client, auth, task_id, **body):
    return client.post(f"/tasks/{task_id}/clarify", json=body, headers=auth)


# --- /users/me -------------------------------------------------------------------------


def test_get_me_returns_the_timezone(client, auth, user):
    r = client.get("/users/me", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"timezone": user.timezone}


def test_put_me_changes_the_timezone(client, auth, session, user):
    r = put_tz(client, auth, "Europe/Paris")
    assert r.status_code == 200 and r.json() == {"timezone": "Europe/Paris"}
    session.refresh(user)
    assert user.timezone == "Europe/Paris"
    assert client.get("/users/me", headers=auth).json() == {"timezone": "Europe/Paris"}


@pytest.mark.parametrize("bad", ["Marte/Olimpo", "../etc/passwd", "", "x" * 65, "America/Li ma"])
def test_invalid_timezones_are_rejected(client, auth, session, user, bad):
    before = user.timezone
    assert put_tz(client, auth, bad).status_code == 422
    session.refresh(user)
    assert user.timezone == before


def test_put_me_rejects_unknown_fields_and_missing_timezone(client, auth):
    assert client.put("/users/me", json={}, headers=auth).status_code == 422
    r = client.put("/users/me", json={"timezone": "UTC", "idioma": "es"}, headers=auth)
    assert r.status_code == 422


def test_users_me_requires_auth(client):
    assert client.get("/users/me").status_code == 401
    assert client.put("/users/me", json={"timezone": "UTC"}).status_code == 401


# --- Entrada: las fechas sin zona se leen en la del usuario ---------------------------


def test_naive_dates_are_read_in_the_users_zone_including_daylight_saving(
    client, auth, make_task
):
    put_tz(client, auth, "Europe/Paris")
    summer = clarify(client, auth, make_task("inbox").id, action="next", title="a",
                     due_at="2030-07-01T09:00:00")
    winter = clarify(client, auth, make_task("inbox").id, action="next", title="b",
                     due_at="2030-01-15T09:00:00")
    # Mismo reloj (09:00) pero desfase distinto: la zona real, no un desfase fijo.
    assert summer.json()["due_at"] == "2030-07-01T09:00:00+02:00"
    assert winter.json()["due_at"] == "2030-01-15T09:00:00+01:00"


# --- Salida: todo vuelve en la zona del usuario ---------------------------------------


def test_responses_use_the_users_zone_and_offset(client, auth, make_task):
    r = clarify(client, auth, make_task("inbox").id, action="next", title="x",
                due_at="2030-05-01T09:00:00")
    body = r.json()
    assert body["due_at"] == "2030-05-01T09:00:00-05:00"  # zona por defecto: UTC-5
    for field in ("captured_at", "clarified_at"):
        assert body[field].endswith("-05:00"), body[field]


def test_an_instant_sent_with_an_offset_comes_back_in_the_users_zone(client, auth, make_task):
    put_tz(client, auth, "Europe/Paris")
    r = clarify(client, auth, make_task("inbox").id, action="next", title="x",
                due_at="2030-07-01T07:00:00Z")
    assert r.json()["due_at"] == "2030-07-01T09:00:00+02:00"


def test_the_same_instant_is_formatted_the_same_everywhere(client, auth, make_task):
    put_tz(client, auth, "Asia/Tokyo")
    t = make_task("inbox")
    first = clarify(client, auth, t.id, action="next", title="x", due_at="2030-05-01T09:00:00")
    assert first.json()["due_at"] == "2030-05-01T09:00:00+09:00"
    for path in (f"/tasks/{t.id}", "/next", "/tasks"):
        body = client.get(path, headers=auth).json()
        item = body if "id" in body else body["items"][0]
        assert item["due_at"] == "2030-05-01T09:00:00+09:00", path
        assert item["captured_at"].endswith("+09:00"), path


def test_every_endpoint_that_returns_dates_uses_the_users_zone(client, auth, make_task):
    put_tz(client, auth, "Asia/Tokyo")

    cap = client.post("/capture", json={"text": "algo", "source_ref": "z1"}, headers=auth).json()
    assert cap["captured_at"].endswith("+09:00")
    assert client.get("/inbox", headers=auth).json()["items"][0]["captured_at"].endswith("+09:00")

    waiting = make_task("waiting", title="w", waiting_on="Ana", waiting_since=NOW - timedelta(days=1))
    item = client.get("/waiting", headers=auth).json()["items"][0]
    assert item["waiting_since"].endswith("+09:00")

    done = client.post(f"/tasks/{waiting.id}/complete", headers=auth).json()
    assert done["task"]["completed_at"].endswith("+09:00")

    t = make_task("next", title="n")
    assert client.get("/someday", headers=auth).status_code == 200
    patched = client.patch(f"/tasks/{t.id}", json={"notes": "x"}, headers=auth).json()
    assert patched["captured_at"].endswith("+09:00")
    assert client.delete(f"/tasks/{t.id}", headers=auth).json()["captured_at"].endswith("+09:00")
    assert client.post(f"/tasks/{t.id}/reopen", headers=auth).json()["captured_at"].endswith("+09:00")


def test_changing_the_zone_changes_how_stored_dates_are_shown(client, auth, make_task):
    t = make_task("next", title="x", due_at=datetime(2030, 5, 1, 14, 0, tzinfo=timezone.utc))
    assert client.get(f"/tasks/{t.id}", headers=auth).json()["due_at"] == "2030-05-01T09:00:00-05:00"
    put_tz(client, auth, "Asia/Tokyo")
    assert client.get(f"/tasks/{t.id}", headers=auth).json()["due_at"] == "2030-05-01T23:00:00+09:00"
