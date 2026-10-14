"""User accounts and sessions.

* Passwords: scrypt (standard library) with a per-user random salt.
* Sessions: random token in an HttpOnly, SameSite=Lax cookie; only its SHA-256
  is stored, so a leaked database can't be used to log in. Logging out or
  disabling a user revokes their sessions immediately.
* Registration: the first account becomes the admin. Afterwards, depending on
  ``auth.registration`` in config.yaml: "approval" (default - new accounts wait
  for an admin), "open", or "closed".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import AuthConfig, now_ist
from .storage import User, UserSession

COOKIE = "tt_session"
MIN_PASSWORD = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    """Shown to the user as-is."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    return email


def check_password_strength(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Password must be at least {MIN_PASSWORD} characters.")
    if password.isdigit() or password.isalpha():
        raise AuthError("Use a mix of letters and numbers (or symbols) in the password.")


def user_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User)) or 0


# -- registration ------------------------------------------------------------------------


def register(session: Session, cfg: AuthConfig, *, name: str, email: str, password: str) -> User:
    email = normalise_email(email)
    name = (name or "").strip()
    if not name:
        raise AuthError("Enter your name.")
    check_password_strength(password)
    first = user_count(session) == 0
    if not first:
        if cfg.registration == "closed":
            raise AuthError("Registration is closed. Ask an admin to create an account for you.")
        domains = [d.lower().lstrip("@") for d in cfg.allowed_email_domains]
        if domains and email.split("@", 1)[1] not in domains:
            raise AuthError(f"Only {', '.join('@' + d for d in domains)} email addresses can register.")
    if session.scalar(select(User).where(User.email == email)):
        raise AuthError("An account with this email already exists - sign in instead.")
    user = User(
        email=email,
        name=name[:120],
        password_hash=hash_password(password),
        role="admin" if first else "member",
        status="active" if first or cfg.registration == "open" else "pending",
        created_at=now_ist(),
    )
    session.add(user)
    session.commit()
    return user


def create_user(session: Session, *, name: str, email: str, password: str, role: str = "member") -> User:
    """Admin/CLI path: creates an active account directly."""
    email = normalise_email(email)
    check_password_strength(password)
    if session.scalar(select(User).where(User.email == email)):
        raise AuthError("An account with this email already exists.")
    user = User(email=email, name=name.strip() or email, password_hash=hash_password(password),
                role=role, status="active", created_at=now_ist())  # fmt: skip
    session.add(user)
    session.commit()
    return user


# -- login / sessions ---------------------------------------------------------------------

_failures: dict[str, list[float]] = {}
MAX_FAILURES, LOCK_SECONDS = 8, 15 * 60


def _too_many_failures(key: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(key, []) if now - t < LOCK_SECONDS]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES


def login(session: Session, cfg: AuthConfig, *, email: str, password: str, remember: bool, ip: str, user_agent: str) -> tuple[User, str, datetime]:
    key = (email or "").strip().lower()
    if _too_many_failures(key) or _too_many_failures(ip):
        raise AuthError("Too many failed attempts. Try again in 15 minutes.")
    user = session.scalar(select(User).where(User.email == key))
    if user is None or not verify_password(password or "", user.password_hash):
        _failures.setdefault(key, []).append(time.monotonic())
        _failures.setdefault(ip, []).append(time.monotonic())
        raise AuthError("Incorrect email or password.")
    if user.status == "pending":
        raise AuthError("Your account is waiting for an admin to approve it.")
    if user.status != "active":
        raise AuthError("This account has been disabled. Contact your admin.")
    _failures.pop(key, None)
    token = secrets.token_urlsafe(32)
    now = now_ist()
    expires = now + (timedelta(days=cfg.session_days) if remember else timedelta(hours=12))
    session.add(UserSession(token_hash=_token_hash(token), user_id=user.id, created_at=now, expires_at=expires,
                            last_seen_at=now, ip=ip[:64], user_agent=(user_agent or "")[:255]))  # fmt: skip
    user.last_login_at = now
    session.commit()
    return user, token, expires


def user_for_token(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    now = now_ist()
    if row is None or row.expires_at < now:
        return None
    user = session.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    if (now - row.last_seen_at).total_seconds() > 300:  # keep writes rare
        row.last_seen_at = now
        session.commit()
    return user


def logout(session: Session, token: str | None) -> None:
    if token:
        row = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
        if row is not None:
            session.delete(row)
            session.commit()


def revoke_sessions(session: Session, user_id: int, keep_token: str | None = None) -> None:
    keep = _token_hash(keep_token) if keep_token else None
    for row in session.scalars(select(UserSession).where(UserSession.user_id == user_id)):
        if row.token_hash != keep:
            session.delete(row)
    session.commit()


def change_password(session: Session, user: User, current: str, new: str, keep_token: str | None) -> None:
    if not verify_password(current or "", user.password_hash):
        raise AuthError("Your current password is incorrect.")
    check_password_strength(new)
    user.password_hash = hash_password(new)
    session.commit()
    revoke_sessions(session, user.id, keep_token)  # sign out everywhere else


def public_user(user: User) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "status": user.status,
            "created_at": user.created_at, "last_login_at": user.last_login_at}  # fmt: skip
