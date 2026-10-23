from sqlalchemy import func, select

from app.models import Task
from tests.conftest import API_KEY


def count_tasks(session) -> int:
    return session.scalar(select(func.count()).select_from(Task))


def test_capture_creates_an_inbox_task_with_the_text_untouched(client, session, auth):
    text = "  Llamar al banco por el cargo duplicado  \nurgente?  "
    r = client.post("/capture", json={"text": text, "source_ref": "tg-1"}, headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert body["created"] is True
    assert body["status"] == "inbox"
    assert body["raw_text"] == text

    task = session.get(Task, body["id"])
    assert task.status == "inbox"
    assert task.raw_text == text
    assert task.title is None  # clarificar viene después
    assert task.source == "telegram"
    assert task.source_ref == "tg-1"


def test_capture_is_idempotent_for_the_same_source_ref(client, session, auth):
    first = client.post("/capture", json={"text": "a", "source_ref": "tg-7"}, headers=auth)
    before = count_tasks(session)
    again = client.post("/capture", json={"text": "a", "source_ref": "tg-7"}, headers=auth)

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["created"] is False
    assert count_tasks(session) == before


def test_retry_with_different_text_keeps_the_original_capture(client, auth):
    first = client.post("/capture", json={"text": "original", "source_ref": "tg-8"}, headers=auth)
    again = client.post("/capture", json={"text": "cambiado", "source_ref": "tg-8"}, headers=auth)
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["raw_text"] == "original"


def test_different_source_refs_create_different_tasks(client, auth):
    a = client.post("/capture", json={"text": "a", "source_ref": "tg-1"}, headers=auth)
    b = client.post("/capture", json={"text": "b", "source_ref": "tg-2"}, headers=auth)
    assert a.json()["id"] != b.json()["id"]


def test_capture_without_source_ref_always_creates(client, session, auth):
    before = count_tasks(session)
    client.post("/capture", json={"text": "igual"}, headers=auth)
    client.post("/capture", json={"text": "igual"}, headers=auth)
    assert count_tasks(session) == before + 2


def test_same_source_ref_for_two_users_does_not_collide(client, session):
    def headers(uid):
        return {"X-API-Key": API_KEY, "X-Telegram-User-Id": str(uid)}

    a = client.post("/capture", json={"text": "a", "source_ref": "m1"}, headers=headers(1001))
    b = client.post("/capture", json={"text": "b", "source_ref": "m1"}, headers=headers(2002))
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    assert session.get(Task, a.json()["id"]).user_id != session.get(Task, b.json()["id"]).user_id


def test_blank_text_is_rejected(client, session, auth):
    before = count_tasks(session)
    for text in ("", "   ", "\n\t"):
        assert client.post("/capture", json={"text": text}, headers=auth).status_code == 422
    assert count_tasks(session) == before


def test_text_over_telegram_limit_is_rejected(client, auth):
    assert client.post("/capture", json={"text": "x" * 4097}, headers=auth).status_code == 422
    assert client.post("/capture", json={"text": "x" * 4096}, headers=auth).status_code == 201


def test_invalid_source_is_rejected(client, auth):
    r = client.post("/capture", json={"text": "a", "source": "Mala Fuente!"}, headers=auth)
    assert r.status_code == 422
