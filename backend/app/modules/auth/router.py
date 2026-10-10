"""Same-origin cookie sessions with synchronizer CSRF tokens and private preferences."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.core.config import get_settings
from app.core.errors import ApplicationError
from app.modules.auth.security import digest, hash_password, verify_password
from app.persistence import auth_repository as repository

UTC = timezone.utc  # noqa: UP017 -- native QA runner
router = APIRouter()


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_walk_meters: int = Field(default=1000, ge=100, le=3000)
    reliability_weight: float = Field(default=0.7, ge=0, le=1)


def cookie_name() -> str:
    return "__Host-bike_session" if get_settings().auth_cookie_secure else "bike_session"


def check_origin(request: Request) -> None:
    # Required even for login/registration, preventing login CSRF. Proxy host headers
    # cannot expand this allowlist. CLI clients supply the configured Origin explicitly.
    if request.headers.get("origin") != get_settings().app_public_origin:
        raise ApplicationError("csrf_rejected", "A trusted Origin is required", 403)


async def throttle(request: Request, identity: str = "") -> None:
    ip = request.client.host if request.client else "unknown"
    keys = [digest("auth-ip:" + ip)]
    if identity:
        keys.append(digest("auth-account:" + identity))
    if await repository.limited(keys, get_settings().auth_rate_limit):
        raise ApplicationError("rate_limited", "Too many attempts; retry in 15 minutes", 429)


session_cookie = APIKeyCookie(name=cookie_name(), scheme_name="SessionCookie", auto_error=False)


async def current_user(
    request: Request,
    token_value: Annotated[str | None, Security(session_cookie)],
    csrf_value: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> dict[str, Any]:
    token = token_value or ""
    if not 20 <= len(token) <= 100:
        raise ApplicationError("authentication_required", "Sign in required", 401)
    row = await repository.session_user(digest(token))
    if not row:
        raise ApplicationError("authentication_required", "Session expired or revoked", 401)
    user = dict(row)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        csrf = csrf_value or ""
        if request.url.path != "/api/v1/auth/csrf" and (
            len(csrf) > 100 or not secrets.compare_digest(digest(csrf), user["csrf_hash"])
        ):
            raise ApplicationError("csrf_rejected", "Valid session CSRF token required", 403)
    return user


User = Annotated[dict[str, Any], Depends(current_user)]


async def require_admin(user: User) -> dict[str, Any]:
    if not user["is_admin"]:
        raise ApplicationError("forbidden", "Operator access required", 403)
    return user


async def issue_session(
    user: dict[str, Any], request: Request, response: Response
) -> dict[str, Any]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    settings = get_settings()
    old = request.cookies.get(cookie_name(), "")
    await repository.store_session(
        user["id"],
        digest(token),
        digest(csrf),
        datetime.now(UTC) + timedelta(hours=settings.auth_session_hours),
        digest(old) if old else None,
    )
    response.set_cookie(
        cookie_name(),
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.auth_session_hours * 3600,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": {"id": str(user["id"]), "email": user["email"]}, "csrf_token": csrf}


@router.post("/register", status_code=201)
async def register(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    check_origin(request)
    email = str(body.email).casefold()
    await throttle(request, email)
    encoded = await hash_password(body.password.get_secret_value())
    row = await repository.create_account(email, encoded)
    if not row:
        raise ApplicationError("account_unavailable", "Unable to register this account", 409)
    return await issue_session(dict(row), request, response)


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    check_origin(request)
    email = str(body.email).casefold()
    await throttle(request, email)
    row = await repository.account(email)
    if not await verify_password(
        row["password_hash"] if row else None, body.password.get_secret_value()
    ):
        raise ApplicationError("invalid_credentials", "Invalid email or password", 401)
    assert row is not None
    return await issue_session(dict(row), request, response)


@router.get("/me")
async def me(user: User) -> dict[str, Any]:
    return {"id": str(user["id"]), "email": user["email"], "is_admin": user["is_admin"]}


@router.post("/csrf", summary="Rotate CSRF token using an authenticated same-origin request")
async def csrf_token(user: User, response: Response) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    await repository.rotate_csrf(user["token_hash"], digest(token))
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": token}


@router.post("/logout", status_code=204)
async def logout(user: User, response: Response) -> None:
    await repository.delete_session(user["token_hash"])
    response.delete_cookie(
        cookie_name(),
        path="/",
        secure=get_settings().auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/preferences", response_model=Preferences)
async def preferences(user: User) -> Preferences:
    row = await repository.preferences(user["id"])
    return Preferences.model_validate(dict(row)) if row else Preferences()


@router.put("/preferences", response_model=Preferences)
async def save_preferences(body: Preferences, user: User) -> Preferences:
    await repository.save_preferences(user["id"], body.max_walk_meters, body.reliability_weight)
    return body
