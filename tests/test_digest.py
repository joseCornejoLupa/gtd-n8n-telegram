from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.digest_text import long_date, when
from app.routers.reviews import day_bounds

H = timedelta(hours=1)


def now():
    return datetime.now(timezone.utc)


def digest(client, auth, **params):
    r = client.get("/digest/daily", params=params, headers=auth)
    assert r.status_code == 200
    return r.json()


def ids(section):
    return [t["id"] for t in section["items"]]


def test_empty_digest(client, auth, noon_tz):
    d = digest(client, auth)
    assert d["inbox_count"] == 0
    for key in ("overdue", "due_today", "reactivated", "next_actions", "waiting_followups", "stalled_projects"):
        assert d[key] == {"count": 0, "items": []}
    assert len(d["date"]) == 10


def test_requires_authentication(client):
    assert client.get("/digest/daily").status_code == 401


def test_overdue_today_and_tomorrow_are_separated(client, auth, noon_tz, make_task):
    late = make_task("next", title="tarde", due_at=now() - 2 * H)
    old = make_task("waiting", title="viejo", waiting_on="Ana", due_at=now() - 30 * H)
    today = make_task("next", title="hoy", due_at=now() + 3 * H)
    tomorrow = make_task("next", title="mañana", due_at=now() + 20 * H)
    make_task("next", title="sin fecha")
    make_task("someday", title="algún día", due_at=now() - 5 * H)  # no cuenta
    make_task("done", title="hecha", due_at=now() - 5 * H)  # no cuenta
    make_task("trashed", title="descartada", due_at=now() - 5 * H)  # no cuenta
    d = digest(client, auth)
    assert ids(d["overdue"]) == [old.id, late.id]  # la más antigua primero
    assert ids(d["due_today"]) == [today.id]
    assert tomorrow.id not in ids(d["due_today"]) + ids(d["overdue"])
    assert d["overdue"]["count"] == 2 and d["due_today"]["count"] == 1


def test_next_actions_are_the_available_ones_by_due_date(client, auth, noon_tz, make_task, make_project):
    seq = make_project("P", sequential=True)
    first = make_task("next", title="1", project_id=seq.id, position=1)
    make_task("next", title="2", project_id=seq.id, position=2)  # bloqueada por la secuencia
    soon = make_task("next", title="pronto", due_at=now() + 30 * H)
    make_task("next", title="diferida", defer_until=now() + 30 * H)
    free = make_task("next", title="libre")
    d = digest(client, auth)
    assert ids(d["next_actions"]) == [soon.id, first.id, free.id]
    assert d["next_actions"]["count"] == 3


def test_reactivated_are_tasks_whose_deferral_ended_today(client, auth, noon_tz, make_task, make_project):
    back = make_task("next", title="vuelve", defer_until=now() - 3 * H)
    make_task("next", title="ayer", defer_until=now() - 30 * H)  # volvió ayer, no hoy
    make_task("next", title="futura", defer_until=now() + 3 * H)
    make_task("next", title="sin diferir")
    seq = make_project("P", sequential=True)
    make_task("next", title="primera", project_id=seq.id, position=1)
    make_task("next", title="bloqueada", project_id=seq.id, position=2, defer_until=now() - 3 * H)
    d = digest(client, auth)
    assert ids(d["reactivated"]) == [back.id]


def test_waiting_followups_use_days_and_the_waiting_days_parameter(client, auth, noon_tz, make_task):
    old = make_task("waiting", title="viejo", waiting_on="Ana", waiting_since=now() - timedelta(days=10))
    mid = make_task("waiting", title="medio", waiting_on="Luis", waiting_since=now() - timedelta(days=3))
    make_task("waiting", title="reciente", waiting_on="Eva", waiting_since=now() - 2 * H)
    d = digest(client, auth)
    assert ids(d["waiting_followups"]) == [old.id]
    assert d["waiting_followups"]["items"][0]["days_waiting"] == 10
    assert ids(digest(client, auth, waiting_days=3)["waiting_followups"]) == [old.id, mid.id]
    assert digest(client, auth, waiting_days=0)["waiting_followups"]["count"] == 3


def test_stalled_projects_and_inbox_count(client, auth, noon_tz, make_task, make_project):
    empty = make_project("sin acción")
    healthy = make_project("sano")
    make_task("next", project_id=healthy.id)
    make_task("inbox")
    make_task("inbox")
    d = digest(client, auth)
    assert [p["id"] for p in d["stalled_projects"]["items"]] == [empty.id]
    assert d["inbox_count"] == 2


def test_limit_caps_items_but_not_counts(client, auth, noon_tz, make_task):
    for i in range(8):
        make_task("next", title=f"t{i}")
    d = digest(client, auth, limit=3)
    assert len(d["next_actions"]["items"]) == 3 and d["next_actions"]["count"] == 8
    assert client.get("/digest/daily?limit=0", headers=auth).status_code == 422
    assert client.get("/digest/daily?limit=21", headers=auth).status_code == 422
    assert client.get("/digest/daily?format=xml", headers=auth).status_code == 422


def test_digest_is_scoped_to_the_user(client, auth, auth_other, noon_tz, make_task):
    make_task("next", title="mía", due_at=now() - 2 * H)
    make_task("inbox")
    other = digest(client, auth_other)
    assert other["overdue"]["count"] == 0 and other["inbox_count"] == 0


def test_today_follows_the_user_timezone(client, auth, session, user, make_task):
    # A las 02:00 UTC de cualquier día, en Lima (UTC-5) todavía es la tarde-noche del día anterior.
    user.timezone = "America/Lima"
    session.flush()
    d = digest(client, auth)
    local = now().astimezone(ZoneInfo("America/Lima")).date().isoformat()
    assert d["date"] == local


# --- Texto para Telegram ---------------------------------------------------------------


def text(client, auth, **params):
    r = client.get("/digest/daily", params={"format": "text", **params}, headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    return r.text


def test_empty_text(client, auth, noon_tz):
    out = text(client, auth)
    assert "Sin pendientes por atender" in out


def test_text_lists_sections_with_counts_and_when(client, auth, noon_tz, make_task, make_context, make_project):
    pc = make_context("@pc")
    make_task("inbox")
    make_task("next", title="Pagar luz", due_at=now() - 30 * H)
    make_task("next", title="Enviar informe", due_at=now() - 2 * H, context_id=pc.id)
    make_task("next", title="Llamar a Ana", due_at=now() + 3 * H)
    make_task("waiting", title="Respuesta del banco", waiting_on="Banco", waiting_since=now() - timedelta(days=9))
    make_project("Mudanza")
    out = text(client, auth)
    assert "📥 Inbox: 1 por clarificar" in out
    assert "⚠️ Vencidas (2)" in out
    assert "• Pagar luz — venció ayer" in out
    assert "• Enviar informe @pc — venció hoy" in out
    assert "📌 Para hoy (1)" in out and "• Llamar a Ana — vence hoy" in out
    assert "⏳ Esperando respuesta (1)" in out and "• Respuesta del banco — Banco, 9 días" in out
    assert "🧩 Proyectos sin siguiente acción (1)" in out and "• Mudanza" in out
    assert out.splitlines()[0] == long_date(now().astimezone(ZoneInfo(noon_tz)))
    assert "Sin pendientes" not in out
    assert "**" not in out and "<b>" not in out  # sin Markdown ni HTML


def test_text_uses_past_tense_for_overdue_next_actions(client, auth, noon_tz, make_task):
    make_task("next", title="atrasada", due_at=now() - 30 * H)
    make_task("next", title="adelantada", due_at=now() + 30 * H)
    out = text(client, auth)
    assert "▶️ Siguientes acciones (2)" in out
    assert out.count("• atrasada — venció ayer") == 2  # en "Vencidas" y en "Siguientes acciones"
    assert "• adelantada — vence mañana" in out
    assert "vence hace" not in out


def test_text_says_how_many_more_there_are(client, auth, noon_tz, make_task):
    for i in range(7):
        make_task("next", title=f"t{i}")
    out = text(client, auth, limit=3)
    assert "▶️ Siguientes acciones (7)" in out and "… y 4 más" in out


def test_text_uses_capture_when_there_is_no_title_and_truncates(client, auth, noon_tz, make_task):
    make_task("next", title="x" * 200)
    out = text(client, auth)
    assert "• " + "x" * 79 + "…" in out


# --- Utilidades de fechas --------------------------------------------------------------


def test_day_bounds_handle_daylight_saving():
    paris = ZoneInfo("Europe/Paris")
    start, end = day_bounds(datetime(2030, 3, 31, 12, tzinfo=timezone.utc), paris)
    assert start.isoformat() == "2030-03-31T00:00:00+01:00"
    assert end.isoformat() == "2030-04-01T00:00:00+02:00"
    # Duración real (en UTC): el día del cambio de hora dura 23 horas.
    assert end.astimezone(timezone.utc) - start.astimezone(timezone.utc) == timedelta(hours=23)


def test_day_bounds_use_the_local_date_not_the_utc_one():
    lima = ZoneInfo("America/Lima")
    # 03:00 UTC del 1 de mayo es todavía el 30 de abril en Lima.
    start, end = day_bounds(datetime(2030, 5, 1, 3, tzinfo=timezone.utc), lima)
    assert start.isoformat() == "2030-04-30T00:00:00-05:00"
    assert end.isoformat() == "2030-05-01T00:00:00-05:00"


@pytest.mark.parametrize(
    "delta_days,expected",
    [(0, "hoy 15:00"), (1, "mañana"), (-1, "ayer"), (-3, "hace 3 días"), (4, "dom 5"), (10, "11/5")],
)
def test_when(delta_days, expected):
    tz = ZoneInfo("America/Lima")
    n = datetime(2030, 5, 1, 12, 0, tzinfo=tz)  # miércoles
    moment = (n + timedelta(days=delta_days)).replace(hour=15)
    assert when(moment, n, tz) == expected
