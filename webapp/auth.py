"""Auth: resolve the current user from the gateway's trusted X-User-* headers.

The gateway authenticates users and injects X-User-Id / X-User-Email, stripping
any client-supplied copies (see the integration contract). App2 trusts only those
headers and never runs its own login. Users are upserted lazily by gateway id.

Local dev (no gateway): if DEV_USER_ID is set, a missing header falls back to it
so `python -m webapp` is usable; in production DEV_USER_ID is empty, so a missing
X-User-Id is a hard 401.
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.config import settings
from webapp.db import models


async def _get(session: AsyncSession, gateway_user_id: str) -> models.User | None:
    res = await session.execute(
        select(models.User).where(
            models.User.gateway_user_id == gateway_user_id))
    return res.scalar_one_or_none()


async def upsert_user(session: AsyncSession, *, gateway_user_id: str,
                      email: str) -> models.User:
    user = await _get(session, gateway_user_id)
    if user is None:
        # First-touch INSERT can race when a brand-new user fires several requests
        # at once (now common with parallel build workers): two coroutines both see
        # no row and both INSERT → the loser hits a UNIQUE violation. Treat that as
        # "someone else just created it": roll back and re-read the committed row,
        # so concurrent first requests resolve to the same user instead of 500ing.
        user = models.User(gateway_user_id=gateway_user_id, email=email)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = await _get(session, gateway_user_id)
            if user is None:
                raise
    if email and user.email != email:
        user.email = email
    return user


async def get_current_user(request: Request) -> models.User:
    gid = request.headers.get("X-User-Id") or settings.dev_user_id
    if not gid:
        raise HTTPException(status_code=401, detail="not authenticated")
    email = request.headers.get("X-User-Email", "")
    Session = request.app.state.sessionmaker
    async with Session() as s:
        user = await upsert_user(s, gateway_user_id=str(gid), email=email)
        await s.commit()
        return user
