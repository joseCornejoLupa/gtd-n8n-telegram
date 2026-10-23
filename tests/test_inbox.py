from datetime import datetime, timedelta, timezone

from app.models import User
from tests.conftest import API_KEY


def test_inbox_requires_auth(client):
    assert client.get("/inbox").status_code == 401
    assert client.get("/inbox", headers={"X-API-Key": API_KEY}).status_code == 400


def test_empty_inbox(client, auth):
    r = client.get("/inbox", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"count": 0, "items": []}


def test_inbox_lists_oldest_first_and_only_pending(client, auth, make_task):
    now = datetime.now(timezone.utc)
    newer = make_task("inbox", raw_text="nueva", captured_at=now)
    older = make_task("inbox", raw_text="vieja", captured_at=now - timedelta(days=2))
    make_task("next")
    make_task("done")
    make_task("trashed")

    body = client.get("/inbox", headers=auth).json()
    assert body["count"] == 2
    assert [i["id"] for i in body["items"]] == [older.id, newer.id]
    assert [i["raw_text"] for i in body["items"]] == ["vieja", "nueva"]


def test_inbox_only_shows_the_callers_items(client, auth, session, make_task):
    other = User(telegram_user_id=2002)
    session.add(other)
    session.flush()
    mine = make_task("inbox", raw_text="mía")
    make_task("inbox", raw_text="ajena", user_id=other.id)

    body = client.get("/inbox", headers=auth).json()
    assert body["count"] == 1
    assert [i["id"] for i in body["items"]] == [mine.id]


def test_limit_trims_items_but_count_is_the_total(client, auth, make_task):
    for n in range(5):
        make_task("inbox", raw_text=f"t{n}")
    body = client.get("/inbox?limit=2", headers=auth).json()
    assert body["count"] == 5
    assert len(body["items"]) == 2


def test_limit_is_validated(client, auth):
    assert client.get("/inbox?limit=0", headers=auth).status_code == 422
    assert client.get("/inbox?limit=201", headers=auth).status_code == 422
    assert client.get("/inbox?limit=200", headers=auth).status_code == 200


def test_capture_then_inbox_round_trip(client, auth):
    client.post("/capture", json={"text": "llamar a mamá", "source_ref": "9"}, headers=auth)
    body = client.get("/inbox", headers=auth).json()
    assert body["count"] == 1
    assert body["items"][0]["raw_text"] == "llamar a mamá"
