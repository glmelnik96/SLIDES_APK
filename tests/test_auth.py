"""Gateway-header auth + lazy user upsert."""
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from webapp.db.database import init_db
from webapp.db.models import Base
import webapp.auth as auth


async def _sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    return async_sessionmaker(engine, expire_on_commit=False)


class _Req:
    def __init__(self, headers, sessionmaker):
        self.headers = headers
        self.app = type("A", (), {"state": type("S", (), {"sessionmaker": sessionmaker})})


async def test_missing_header_is_401(monkeypatch):
    monkeypatch.setattr(auth.settings, "dev_user_id", "")
    Session = await _sessionmaker()
    with pytest.raises(auth.HTTPException) as ei:
        await auth.get_current_user(_Req({}, Session))
    assert ei.value.status_code == 401


async def test_header_upserts_user_once():
    Session = await _sessionmaker()
    u1 = await auth.get_current_user(_Req({"X-User-Id": "42", "X-User-Email": "a@x"}, Session))
    u2 = await auth.get_current_user(_Req({"X-User-Id": "42", "X-User-Email": "a@x"}, Session))
    assert u1.id == u2.id  # same user, not duplicated
    assert u1.gateway_user_id == "42"
    assert u1.email == "a@x"


async def test_dev_user_fallback(monkeypatch):
    monkeypatch.setattr(auth.settings, "dev_user_id", "local")
    Session = await _sessionmaker()
    u = await auth.get_current_user(_Req({}, Session))
    assert u.gateway_user_id == "local"
