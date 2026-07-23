"""Cross-app usage push to the gateway (variant B of the shared analytics
contract). App2 POSTs one anonymised metric per finished build to
/internal/usage; here we pin the payload shape and the safety rules
(token-off no-op, smoke exclusion, errors swallowed, headers)."""
import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import webapp.config as cfg
from webapp import usage
from webapp.db import models
from webapp.db.database import init_db, make_engine, make_sessionmaker


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _sm_with_user(tmp_path, *, email="user@ya.ru", gw="u1"):
    """A fresh sqlite sessionmaker seeded with one User; returns (sm, uid)."""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'u.db'}")
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        u = models.User(gateway_user_id=gw, email=email)
        s.add(u)
        await s.commit()
        uid = u.id
    return sm, uid


class _Spy:
    """Records the single POST report_to_gateway makes."""
    def __init__(self):
        self.calls = []

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})


class _Boom:
    async def post(self, *a, **k):
        raise RuntimeError("network down")


# ── build_ingest_payload (pure) ──────────────────────────────────────────────
def test_payload_matches_contract_fields():
    p = usage.build_ingest_payload(
        email="a@b.ru", gateway_user_id="gw9", status="done",
        workflow="htmlnew", duration_ms=2000, result_path=None)
    assert p["app"] == "slides"          # never "images"
    assert p["event"] == "generation"    # contract vocab, not local "render"
    assert p["email"] == "a@b.ru"
    assert p["status"] == "done"
    assert p["workflow"] == "htmlnew"
    assert p["duration_ms"] == 2000
    assert p["gateway_user_id"] == "gw9"
    assert p["meta"] == {}               # no file on disk → no slides_count


def test_payload_omits_empty_optionals_and_caps_workflow():
    p = usage.build_ingest_payload(
        email=None, gateway_user_id=None, status="failed",
        workflow="x" * 50, duration_ms=None, result_path=None)
    assert p["email"] == ""              # None normalised to ""
    assert "duration_ms" not in p        # omitted → receiver defaults
    assert "gateway_user_id" not in p
    assert len(p["workflow"]) == 32      # capped at 32 chars


def test_payload_slides_count_from_deck(tmp_path):
    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide">a</section>'
                    '<section class="slide">b</section>', encoding="utf-8")
    p = usage.build_ingest_payload(
        email="a@b.ru", gateway_user_id=None, status="done",
        workflow="htmlnew", duration_ms=1, result_path=str(deck))
    assert p["meta"].get("slides_count") == 2


# ── report_to_gateway (side effects + safety) ────────────────────────────────
def test_report_noop_when_token_unset(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.settings, "usage_ingest_token", "")
    spy = _Spy()
    sm, uid = _run(_sm_with_user(tmp_path))
    sent = _run(usage.report_to_gateway(
        sm, owner_user_id=uid, status="done", workflow="htmlnew",
        started_at=None, result_path=None, client=spy))
    assert sent is False and spy.calls == []   # disabled → nothing sent


def test_report_sends_with_token_and_headers(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.settings, "usage_ingest_token", "secret-tok")
    monkeypatch.setattr(cfg.settings, "usage_ingest_url",
                        "https://gw.example/internal/usage")
    spy = _Spy()
    sm, uid = _run(_sm_with_user(tmp_path, email="user@ya.ru", gw="gw1"))
    started = datetime.now(timezone.utc) - timedelta(seconds=3)
    sent = _run(usage.report_to_gateway(
        sm, owner_user_id=uid, status="done", workflow="htmlnew",
        started_at=started, result_path=None, client=spy))
    assert sent is True and len(spy.calls) == 1
    call = spy.calls[0]
    assert call["url"] == "https://gw.example/internal/usage"
    assert call["headers"]["X-Ingest-Token"] == "secret-tok"
    assert call["headers"]["Content-Type"] == "application/json"
    body = call["json"]
    assert body["app"] == "slides" and body["event"] == "generation"
    assert body["email"] == "user@ya.ru" and body["gateway_user_id"] == "gw1"
    assert body["status"] == "done" and body["duration_ms"] >= 0


def test_report_excludes_smoke_account(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.settings, "usage_ingest_token", "secret-tok")
    monkeypatch.setattr(cfg.settings, "usage_smoke_emails",
                        "e2e-smoke@cloud.ru, bot@cloud.ru")
    spy = _Spy()
    # case-insensitive match on a listed smoke email
    sm, uid = _run(_sm_with_user(tmp_path, email="E2E-Smoke@Cloud.RU", gw="s1"))
    sent = _run(usage.report_to_gateway(
        sm, owner_user_id=uid, status="done", workflow="htmlnew",
        started_at=None, result_path=None, client=spy))
    assert sent is False and spy.calls == []   # tech traffic never reported


def test_report_swallows_transport_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.settings, "usage_ingest_token", "secret-tok")
    sm, uid = _run(_sm_with_user(tmp_path))
    # a raising client must not propagate — analytics can't break the build
    sent = _run(usage.report_to_gateway(
        sm, owner_user_id=uid, status="failed", workflow="htmlnew",
        started_at=None, result_path=None, client=_Boom()))
    assert sent is False


def test_report_forwards_terminal_statuses(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg.settings, "usage_ingest_token", "secret-tok")
    sm, uid = _run(_sm_with_user(tmp_path))
    for st in ("done", "failed", "cancelled"):
        spy = _Spy()
        _run(usage.report_to_gateway(
            sm, owner_user_id=uid, status=st, workflow="htmlnew",
            started_at=None, result_path=None, client=spy))
        assert spy.calls[0]["json"]["status"] == st
