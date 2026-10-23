"""Los choques entre peticiones simultáneas que detecta Postgres se responden con un error claro."""

import psycopg
import pytest
from sqlalchemy.exc import DBAPIError


def fail_with(monkeypatch, exc):
    def boom(*_args, **_kwargs):
        raise DBAPIError("SELECT 1", {}, exc)

    monkeypatch.setattr("app.routers.contexts._counted", boom)


@pytest.mark.parametrize(
    "error", [psycopg.errors.DeadlockDetected("x"), psycopg.errors.SerializationFailure("x")]
)
def test_deadlock_and_serialization_failures_ask_to_retry(client, auth, monkeypatch, error):
    fail_with(monkeypatch, error)
    r = client.get("/contexts", headers=auth)
    assert r.status_code == 503 and r.headers["Retry-After"] == "1"
    assert "reintenta" in r.json()["detail"]


def test_a_vanished_reference_is_a_conflict(client, auth, monkeypatch):
    fail_with(monkeypatch, psycopg.errors.ForeignKeyViolation("x"))
    assert client.get("/contexts", headers=auth).status_code == 409


def test_other_database_errors_are_not_hidden(client, auth, monkeypatch):
    fail_with(monkeypatch, psycopg.errors.UndefinedTable("x"))
    with pytest.raises(DBAPIError):
        client.get("/contexts", headers=auth)
