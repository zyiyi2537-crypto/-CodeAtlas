from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response, status
from sqlmodel import Session, select

from .models import User, UserSession
from .roles import is_admin_role
from .security import digest_secret, new_secret
from .settings import Settings

SESSION_COOKIE = "codeatlas_session"
SESSION_CLEANUP_INTERVAL = 3600
_last_cleanup = 0.0


def cleanup_expired_sessions(database: Session) -> None:
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < SESSION_CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    expired = database.exec(
        select(UserSession).where(UserSession.expires_at <= utc_now())
    ).all()
    for session in expired:
        database.delete(session)
    if expired:
        database.commit()


@dataclass(frozen=True)
class BrowserIdentity:
    user: User
    session: UserSession


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_browser_session(
    database: Session, user: User, response: Response, settings: Settings
) -> BrowserIdentity:
    raw_token = new_secret("cas_")
    session = UserSession(
        user_id=user.id,
        token_hash=digest_secret(raw_token),
        csrf_token=new_secret("csrf_")[:80],
        expires_at=utc_now() + timedelta(hours=12),
    )
    database.add(session)
    database.flush()
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return BrowserIdentity(user=user, session=session)


def resolve_identity(
    request: Request,
    database: Session,
    *,
    lock_user: bool | None = None,
) -> BrowserIdentity | None:
    cleanup_expired_sessions(database)
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    if not raw_token:
        return None
    browser_session = database.exec(
        select(UserSession).where(UserSession.token_hash == digest_secret(raw_token))
    ).first()
    if not browser_session:
        return None
    expires_at = browser_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= utc_now():
        return None
    user_statement = select(User).where(User.id == browser_session.user_id)
    should_lock_user = (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        if lock_user is None
        else lock_user
    )
    if should_lock_user:
        user_statement = user_statement.with_for_update()
    user = database.exec(user_statement).first()
    if not user or not user.is_active:
        return None
    return BrowserIdentity(user=user, session=browser_session)


def require_identity(
    request: Request,
    database: Session,
    *,
    lock_user: bool | None = None,
) -> BrowserIdentity:
    identity = resolve_identity(request, database, lock_user=lock_user)
    if not identity:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return identity


def require_admin(
    request: Request,
    database: Session,
    *,
    lock_user: bool | None = None,
) -> BrowserIdentity:
    identity = require_identity(request, database, lock_user=lock_user)
    if not is_admin_role(identity.user.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return identity


def require_csrf(request: Request, identity: BrowserIdentity) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied or supplied != identity.session.csrf_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")


def clear_browser_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
