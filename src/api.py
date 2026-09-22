"""HTTP surface for auth-api: signup, login, current user, logout."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from src.auth import (
    SESSION_TTL_HOURS,
    AuthError,
    User,
    connect,
    create_user,
    issue_session,
    resolve_session,
    revoke_session,
)

COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "eos_session")
COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
WINDOW_SECONDS = int(os.environ.get("LOGIN_WINDOW_MINUTES", "15")) * 60

app = FastAPI(title="auth-api")
db = connect()

# Failed login timestamps per (email, client IP). bcrypt is deliberately slow,
# so an unthrottled login endpoint is also the cheapest way to burn our CPU.
_failures: dict[tuple[str, str], list[float]] = defaultdict(list)


class Credentials(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(Credentials):
    name: str
    confirm_password: str


class Account(BaseModel):
    id: int
    name: str
    email: str
    created_at: datetime


def current_user(request: Request) -> User:
    try:
        return resolve_session(db, request.cookies.get(COOKIE_NAME))
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None


@app.post("/api/auth/signup", status_code=201, response_model=Account)
def signup(body: SignupRequest) -> User:
    """Create an account. The browser checks these rules too, but the server
    is the one that decides, so every one of them is re-checked here."""
    if body.confirm_password != body.password:
        raise HTTPException(status_code=400, detail="passwords do not match")
    try:
        return create_user(db, body.name, body.email, body.password)
    except AuthError as exc:
        status = 409 if "registered" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from None


@app.post("/api/auth/login", response_model=Account)
def login(body: Credentials, request: Request, response: Response) -> User:
    key = (body.email.lower(), request.client.host if request.client else "unknown")
    retry_after = _throttled_for(key)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="too many failed login attempts",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        token = issue_session(db, body.email, body.password)
    except AuthError as exc:
        _failures[key].append(time.monotonic())
        raise HTTPException(status_code=401, detail=str(exc)) from None

    _failures.pop(key, None)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return resolve_session(db, token)


@app.get("/api/auth/me", response_model=Account)
def me(user: User = Depends(current_user)) -> User:
    return user


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    revoke_session(db, request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")


def _throttled_for(key: tuple[str, str]) -> int:
    """Seconds the caller must wait, or 0 if they may attempt a login.

    Failures age out of the window, so a slow trickle of wrong passwords never
    trips the limit and a locked-out user recovers without an admin.
    """
    now = time.monotonic()
    recent = [t for t in _failures[key] if now - t < WINDOW_SECONDS]
    _failures[key] = recent
    if len(recent) < MAX_ATTEMPTS:
        return 0
    return max(1, int(WINDOW_SECONDS - (now - recent[0])))


# Served last so the API routes above win: the signup, login and dashboard
# pages the browser loads.
app.mount("/", StaticFiles(directory="web", html=True), name="web")
