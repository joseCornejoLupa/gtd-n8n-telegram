from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

H = timedelta(hours=1)
D = timedelta(days=1)


def now():
    return datetime.now(timezone.utc)


def snooze(client, auth, task_id, **body):
    return client.post(f"/tasks/{task_id}/snooze", json=body, headers=auth)


def parse(value):
    return datetime.fromisoformat(value)


# --- Posponer --------------------------------------------------------------------------


def test_snooze_until_a_date_hides_the_task(client, auth, make_task):
    t = make_task("next", title="llamar")
    r = snooze(client, auth, t.id, until="2031-05-01T09:00:00")
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["defer_until"] == "2031-05-01T09:00:00-05:00"  # sin zona = la del usuario
    assert body["task"]["available_now"] is False and body["task"]["status"] == "next"
    assert client.get("/next", headers=auth).json()["count"] == 0
    assert [x["id"] for x in client.get("/snoozed", headers=auth).json()["items"]] == [t.id]


def test_until_with_an_offset_is_respected(client, auth, make_task):
    t = make_task("next")
    body = snooze(client, auth, t.id, until="2031-05-01T14:00:00Z").json()
    assert parse(body["task"]["defer_until"]) == datetime(2031, 5, 1, 14, tzinfo=timezone.utc)
    assert body["task"]["defer_until"].endswith("-05:00")  # se muestra en la zona del usuario


def test_hours(client, auth, make_task):
    t = make_task("next")
    body = snooze(client, auth, t.id, hours=3).json()
    delta = parse(body["task"]["defer_until"]) - now()
    assert timedelta(hours=3) - timedelta(minutes=1) < delta <= timedelta(hours=3)


@pytest.mark.parametrize("n", [1, 3])
def test_days_wakes_up_at_the_start_of_that_local_day(client, auth, session, user, make_task, noon_tz, n):
    t = make_task("next")
    body = snooze(client, auth, t.id, days=n).json()
    tz = ZoneInfo(noon_tz)
    expected = datetime.combine(now().astimezone(tz).date() + n * D, datetime.min.time(), tzinfo=tz)
    assert parse(body["task"]["defer_until"]) == expected


def test_days_follow_daylight_saving_in_the_users_zone(client, auth, session, user, make_task):
    user.timezone = "Europe/Paris"
    session.flush()
    t = make_task("next")
    body = snooze(client, auth, t.id, days=200).json()
    wake = parse(body["task"]["defer_until"])
    assert wake.hour == 0 and wake.minute == 0
    assert wake.utcoffset() in (timedelta(hours=1), timedelta(hours=2))


def test_the_task_reappears_when_its_date_arrives(client, auth, session, make_task):
    t = make_task("next", title="x")
    snooze(client, auth, t.id, hours=2)
    assert client.get("/next", headers=auth).json()["count"] == 0
    t.defer_until = now() - H  # pasó la fecha
    session.flush()
    assert [x["id"] for x in client.get("/next", headers=auth).json()["items"]] == [t.id]
    assert client.get("/snoozed", headers=auth).json()["count"] == 0


def test_snoozing_again_replaces_the_date_and_same_date_is_idempotent(client, auth, make_task):
    t = make_task("next")
    first = snooze(client, auth, t.id, until="2031-05-01T09:00:00").json()
    again = snooze(client, auth, t.id, until="2031-05-01T09:00:00").json()
    assert again == first
    later = snooze(client, auth, t.id, until="2031-06-01T09:00:00").json()
    assert later["task"]["defer_until"] == "2031-06-01T09:00:00-05:00"


def test_blank_values_from_n8n_count_as_not_sent(client, auth, make_task):
    t = make_task("next")
    r = snooze(client, auth, t.id, until="", days=None, hours=2)
    assert r.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"until": "", "days": "", "hours": ""},
        {"days": 1, "hours": 2},
        {"until": "2031-05-01T09:00:00", "days": 1},
        {"days": 0},
        {"days": -1},
        {"days": 1826},
        {"hours": 0},
        {"hours": 43801},
        {"until": "ayer"},
        {"until": "2001-01-01T00:00:00"},  # pasado
        {"until": "2099-01-01T00:00:00"},  # más de 5 años
        {"days": 1, "extra": 1},
    ],
)
def test_validation(client, auth, make_task, body):
    t = make_task("next")
    assert snooze(client, auth, t.id, **body).status_code == 422
    assert client.get(f"/tasks/{t.id}", headers=auth).json()["defer_until"] is None


@pytest.mark.parametrize("status", ["inbox", "waiting", "someday", "reference", "done", "trashed", "converted"])
def test_only_next_actions_can_be_snoozed(client, auth, make_task, status):
    extra = {"waiting_on": "Ana"} if status == "waiting" else {}
    t = make_task(status, **extra)
    assert snooze(client, auth, t.id, days=1).status_code == 409


def test_unknown_foreign_and_huge_ids(client, auth, auth_other, make_task):
    t = make_task("next")
    assert snooze(client, auth, 999999, days=1).status_code == 404
    assert snooze(client, auth_other, t.id, days=1).status_code == 404
    assert snooze(client, auth, 2**63, days=1).status_code == 422
    assert client.get(f"/tasks/{t.id}", headers=auth).json()["defer_until"] is None


def test_requires_authentication(client):
    assert client.post("/tasks/1/snooze", json={"days": 1}).status_code == 401
    assert client.delete("/tasks/1/snooze").status_code == 401
    assert client.get("/snoozed").status_code == 401


# --- Efecto en proyectos ---------------------------------------------------------------


def test_task_without_project(client, auth, make_task):
    body = snooze(client, auth, make_task("next").id, days=1).json()
    assert body["project"] is None
    assert body["project_stalled"] is False and body["project_blocked"] is False


def test_snoozing_the_current_step_of_a_sequential_project_blocks_it(client, auth, make_task, make_project):
    p = make_project("Mudanza", sequential=True)
    head = make_task("next", title="1", project_id=p.id, position=1)
    make_task("next", title="2", project_id=p.id, position=2)
    body = snooze(client, auth, head.id, days=2).json()
    assert body["project"]["id"] == p.id
    assert body["project_blocked"] is True
    assert body["project_stalled"] is False  # el paso 2 sigue siendo una tarea viva
    assert client.get("/next", headers=auth).json()["count"] == 0  # pero no está disponible


def test_snoozing_the_only_task_of_a_project_leaves_it_stalled(client, auth, make_task, make_project):
    p = make_project("P", sequential=True)
    t = make_task("next", project_id=p.id, position=1)
    body = snooze(client, auth, t.id, days=2).json()
    assert body["project_stalled"] is True and body["project_blocked"] is True
    assert [x["id"] for x in client.get("/projects/stalled", headers=auth).json()["items"]] == [p.id]


def test_snoozing_a_later_step_does_not_block_the_project(client, auth, make_task, make_project):
    p = make_project("P", sequential=True)
    make_task("next", title="1", project_id=p.id, position=1)
    second = make_task("next", title="2", project_id=p.id, position=2)
    body = snooze(client, auth, second.id, days=2).json()
    assert body["project_blocked"] is False and body["project_stalled"] is False
    assert client.get("/next", headers=auth).json()["count"] == 1


def test_parallel_project_is_never_blocked(client, auth, make_task, make_project):
    p = make_project("P", sequential=False)
    t = make_task("next", project_id=p.id)
    make_task("next", project_id=p.id)
    body = snooze(client, auth, t.id, days=1).json()
    assert body["project_blocked"] is False and body["project_stalled"] is False


def test_waiting_task_ahead_counts_as_head_of_the_sequence(client, auth, make_task, make_project):
    p = make_project("P", sequential=True)
    make_task("waiting", waiting_on="Ana", project_id=p.id, position=1)
    second = make_task("next", project_id=p.id, position=2)
    assert snooze(client, auth, second.id, days=1).json()["project_blocked"] is False


def test_due_before_wakeup_warns(client, auth, make_task):
    soon = make_task("next", due_at=now() + 2 * D)
    assert snooze(client, auth, soon.id, days=5).json()["due_before_wakeup"] is True
    ok = make_task("next", due_at=now() + 20 * D)
    assert snooze(client, auth, ok.id, days=5).json()["due_before_wakeup"] is False
    assert snooze(client, auth, make_task("next").id, days=5).json()["due_before_wakeup"] is False


# --- Despertar ---------------------------------------------------------------------------


def test_wake_up_makes_the_task_available_again(client, auth, make_task):
    t = make_task("next", title="x")
    snooze(client, auth, t.id, days=3)
    r = client.delete(f"/tasks/{t.id}/snooze", headers=auth)
    assert r.status_code == 200
    assert r.json()["defer_until"] is None and r.json()["available_now"] is True
    assert client.get("/snoozed", headers=auth).json()["count"] == 0


def test_wake_up_is_idempotent(client, auth, make_task):
    t = make_task("next")
    assert client.delete(f"/tasks/{t.id}/snooze", headers=auth).status_code == 200
    assert client.delete(f"/tasks/{t.id}/snooze", headers=auth).status_code == 200


def test_wake_up_other_states_and_ownership(client, auth, auth_other, make_task):
    done = make_task("done")
    mine = make_task("next")
    assert client.delete(f"/tasks/{done.id}/snooze", headers=auth).status_code == 409
    assert client.delete(f"/tasks/{mine.id}/snooze", headers=auth_other).status_code == 404
    assert client.delete("/tasks/999999/snooze", headers=auth).status_code == 404


# --- Lista de pospuestas -----------------------------------------------------------------


def test_snoozed_list_is_ordered_and_filtered(client, auth, auth_other, session, make_task):
    late = make_task("next", title="tarde", defer_until=now() + 5 * D)
    soon = make_task("next", title="pronto", defer_until=now() + 1 * D)
    make_task("next", title="ya pasó", defer_until=now() - H)
    make_task("next", title="sin snooze")
    make_task("someday", title="algún día", defer_until=now() + 3 * D)
    make_task("done", title="hecha", defer_until=now() + 3 * D)
    body = client.get("/snoozed", headers=auth).json()
    assert [t["id"] for t in body["items"]] == [soon.id, late.id] and body["count"] == 2
    assert client.get("/snoozed", headers=auth_other).json()["count"] == 0


def test_snoozed_list_limit(client, auth, make_task):
    for i in range(4):
        make_task("next", defer_until=now() + (i + 1) * D)
    body = client.get("/snoozed?limit=2", headers=auth).json()
    assert body["count"] == 4 and len(body["items"]) == 2
    assert client.get("/snoozed?limit=0", headers=auth).status_code == 422


def test_snoozed_task_still_completes_and_patches(client, auth, make_task):
    t = make_task("next", title="x")
    snooze(client, auth, t.id, days=2)
    assert client.patch(f"/tasks/{t.id}", json={"title": "y"}, headers=auth).json()["defer_until"] is not None
    assert client.post(f"/tasks/{t.id}/complete", headers=auth).status_code == 200
    assert client.get("/snoozed", headers=auth).json()["count"] == 0
