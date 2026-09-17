import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from fastapi import HTTPException, Request, Response
from redis import Redis
from sqlalchemy import select

from app.core.config import Settings
from app.db.s1 import memberships, users
from app.db.session import transaction

COOKIE = "bljgh_session"


def signed(settings: Settings, data: dict[str, Any], ttl: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {**data, "iat": now, "exp": now + timedelta(seconds=ttl)},
        settings.session_secret,
        algorithm="HS256",
    )


def decode(settings: Settings, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.session_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(401) from None


def mapped_user(settings: Settings, issuer: str, subject: str) -> dict[str, Any]:
    with transaction(settings, issuer=issuer, subject=subject) as conn:
        row = (
            conn.execute(select(users).where(users.c.issuer == issuer, users.c.subject == subject))
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(403)
        return dict(row)


def issue_session(settings: Settings, response: Response, user: dict[str, Any]) -> None:
    csrf = secrets.token_urlsafe(32)
    token = signed(
        settings,
        {"sub": user["subject"], "iss": user["issuer"], "kind": "session", "csrf": csrf},
        settings.session_seconds,
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        max_age=settings.session_seconds,
        path="/api/v1",
    )


def principal(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    token = request.cookies.get(COOKIE, "")
    claims = decode(settings, token)
    if Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2).exists(
        "revoked:" + digest(token)
    ):
        raise HTTPException(401)
    if claims.get("kind") != "session":
        raise HTTPException(401)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), claims.get("csrf", "")):
            raise HTTPException(403)
        if request.headers.get("Origin") != settings.public_origin:
            raise HTTPException(403)
    user = mapped_user(settings, claims["iss"], claims["sub"])
    user["csrf_token"] = claims["csrf"]
    return user


def membership(settings: Settings, user_id: UUID, project_id: UUID) -> dict[str, Any]:
    with transaction(settings, user=user_id) as conn:
        row = (
            conn.execute(
                select(memberships).where(
                    memberships.c.user_id == user_id, memberships.c.project_id == project_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404)
        return dict(row)


def verify_oidc(settings: Settings, token: str, nonce: str) -> dict[str, Any]:
    try:
        key = jwt.PyJWKClient(settings.oidc_jwks_url, timeout=5).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.oidc_client_id,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud", "nonce"]},
        )
        if not hmac.compare_digest(claims["nonce"], nonce):
            raise HTTPException(401)
        if claims.get("azp", settings.oidc_client_id) != settings.oidc_client_id:
            raise HTTPException(401)
        return mapped_user(settings, settings.oidc_issuer, claims["sub"])
    except (jwt.PyJWTError, KeyError, TypeError):
        raise HTTPException(401) from None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
