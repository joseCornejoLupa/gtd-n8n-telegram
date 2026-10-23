import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import parse_telegram_ids, reset_caches
from app.main import app
from app.models import User
from tests.conftest import API_KEY


def user_count(session) -> int:
    return session.scalar(select(func.count()).select_from(User))


def test_health_needs_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_missing_api_key_is_401(client):
    r = client.post("/capture", json={"text": "x"}, headers={"X-Telegram-User-Id": "1001"})
    assert r.status_code == 401


def test_wrong_api_key_is_401(client):
    r = client.post(
        "/capture", json={"text": "x"}, headers={"X-API-Key": "otra", "X-Telegram-User-Id": "1001"}
    )
    assert r.status_code == 401


def test_api_key_is_checked_before_telegram_id(client, session):
    """Sin la clave correcta no se revela nada sobre la allowlist."""
    before = user_count(session)
    r = client.post(
        "/capture", json={"text": "x"}, headers={"X-API-Key": "otra", "X-Telegram-User-Id": "999"}
    )
    assert r.status_code == 401
    assert user_count(session) == before


def test_missing_telegram_id_is_400(client):
    r = client.post("/capture", json={"text": "x"}, headers={"X-API-Key": API_KEY})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "value", ["abc", "-5", "0", "12.5", " ", "١٢٣".encode(), b"\xff\xfe"]
)
def test_malformed_telegram_id_is_400(client, value):
    r = client.post(
        "/capture", json={"text": "x"}, headers={"X-API-Key": API_KEY, "X-Telegram-User-Id": value}
    )
    assert r.status_code == 400


def test_telegram_user_outside_allowlist_is_403_and_creates_nothing(client, session):
    before = user_count(session)
    r = client.post(
        "/capture",
        json={"text": "x"},
        headers={"X-API-Key": API_KEY, "X-Telegram-User-Id": "999"},
    )
    assert r.status_code == 403
    assert user_count(session) == before


def test_allowed_user_is_created_on_first_request_and_reused(client, session, auth):
    before = user_count(session)
    assert client.post("/capture", json={"text": "uno"}, headers=auth).status_code == 201
    assert user_count(session) == before + 1
    assert client.post("/capture", json={"text": "dos"}, headers=auth).status_code == 201
    assert user_count(session) == before + 1


def test_errors_do_not_leak_secrets(client):
    r = client.post("/capture", json={"text": "x"}, headers={"X-API-Key": "otra"})
    assert API_KEY not in r.text


# --- Arranque y parseo de secretos (fail closed) ---------------------------------------


def test_service_refuses_to_start_without_api_key(secrets):
    secrets["api_key"].unlink()
    reset_caches()
    with pytest.raises(RuntimeError, match="Falta el secreto"):
        with TestClient(app):
            pass


def test_service_refuses_to_start_without_allowlist(secrets):
    secrets["allowed_ids"].unlink()
    reset_caches()
    with pytest.raises(RuntimeError, match="Falta el secreto"):
        with TestClient(app):
            pass


def test_service_refuses_to_start_with_blank_allowlist(secrets):
    secrets["allowed_ids"].write_text("  \n")
    reset_caches()
    with pytest.raises(RuntimeError, match="vacío"):
        with TestClient(app):
            pass


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1001", {1001}),
        ("1001,2002", {1001, 2002}),
        ("1001\n2002\n", {1001, 2002}),
        (" 1001 , 2002 ,, 3003 ", {1001, 2002, 3003}),
    ],
)
def test_parse_telegram_ids_accepts_common_formats(raw, expected):
    assert parse_telegram_ids(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", ",", "abc", "1001 x", "-1", "0", "1.5", "@usuario"])
def test_parse_telegram_ids_rejects_anything_ambiguous(raw):
    with pytest.raises(RuntimeError):
        parse_telegram_ids(raw)


def test_parse_errors_do_not_echo_the_offending_value():
    with pytest.raises(RuntimeError) as exc:
        parse_telegram_ids("123456789 secreto")
    assert "123456789" not in str(exc.value)
    assert "secreto" not in str(exc.value)
