"""HTTP layer over auth-db: the status codes and cookies the store cannot set.

Every route here is a thin translation. The store decides what is true (this
email is taken, this password matches, this token buys nothing); this file
turns that into 409, a Set-Cookie header, or a 401.
"""

from __future__ import annotations

import os
from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from src.db import connect, migrate
from src.store import (
    SESSION_TTL_HOURS,
    EmailTaken,
    User,
    create_session,
    create_user,
    delete_session,
    session_user,
    verify_password,
)

COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "eos_session")
# REQ-005. Defaults to true; a deployment that turns this off is opting out of
# the requirement, not merely tweaking a setting.
COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"

app = FastAPI(title="auth-api")
db = connect()
migrate(db)


class SignupRequest(BaseModel):
    name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Account(BaseModel):
    """What a client is allowed to see. There is no password field to omit."""

    id: UUID
    name: str
    email: str
    created_at: datetime


def current_user(request: Request) -> User:
    """Guard for protected routes: 401 unless the cookie names a live session."""
    user = session_user(db, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


@app.post("/api/auth/signup", status_code=201, response_model=Account)
def signup(body: SignupRequest) -> User:
    """REQ-002: 201 with the new account, or 409 if the email is spoken for."""
    try:
        return create_user(db, body.name, body.email, body.password)
    except EmailTaken:
        raise HTTPException(status_code=409, detail="email already registered") from None


@app.post("/api/auth/login", response_model=Account)
def login(body: LoginRequest, response: Response) -> User:
    """REQ-005: on valid credentials, hand back an HttpOnly, Secure cookie."""
    user = verify_password(db, body.email, body.password)
    if user is None:
        # One message for both a wrong password and an unknown email.
        raise HTTPException(status_code=401, detail="invalid email or password")

    _, token = create_session(db, user.id)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return user


@app.get("/api/auth/me", response_model=Account)
def me(user: User = Depends(current_user)) -> User:
    return user


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    """REQ-007: drop the row and tell the browser to drop the cookie."""
    delete_session(db, request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")


# Mounted last so the API routes above win: the pages the browser loads.
app.mount("/", StaticFiles(directory="web", html=True), name="web")
