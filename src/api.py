"""HTTP layer over board-db: the routes the board page calls.

Every route is a thin translation of a store call into a status code; the
store decides what is true (this send is not allowed, this status does not
exist).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.db import connect, migrate
from src.store import (
    NotConnected,
    conversations_since,
    current_connection,
    disconnect,
    message_history,
    queue_message,
    record_connection,
    set_conversation_status,
)

TWILIO_API = "https://api.twilio.com/2010-04-01"

app = FastAPI(title="board-api")
db = connect()
migrate(db)


class ConnectRequest(BaseModel):
    account_sid: str = Field(min_length=1)
    auth_token: str = Field(min_length=1)


class StatusRequest(BaseModel):
    # REQ-005: anything else is a 422 before it reaches the store.
    status: Literal["open", "in_progress", "resolved"]


class SendRequest(BaseModel):
    sender: str = Field(min_length=1)
    receiver: str = Field(min_length=1)
    content: str = Field(min_length=1)


def verify_twilio(account_sid: str, auth_token: str) -> str | None:
    """Check the credentials against Twilio. Returns an error message, or None."""
    try:
        response = httpx.get(
            f"{TWILIO_API}/Accounts/{account_sid}.json", auth=(account_sid, auth_token), timeout=10
        )
    except httpx.HTTPError as exc:
        return f"Could not reach Twilio: {exc}"
    if response.status_code == 401:
        return "Twilio rejected the Account SID or Auth Token."
    if response.status_code != 200:
        return f"Twilio returned {response.status_code}."
    return None


@app.get("/api/connection")
def get_connection():
    return current_connection(db)


@app.post("/api/connection")
def connect_twilio(body: ConnectRequest):
    """REQ-001: 200 when Twilio accepts the credentials, 502 with the error if not.

    The auth token is used for the check only and is never stored.
    """
    error = verify_twilio(body.account_sid, body.auth_token)
    connection = record_connection(db, body.account_sid, error)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return connection


@app.post("/api/connection/{connection_id}/disconnect")
def disconnect_twilio(connection_id: UUID):
    """REQ-001: returns how many in-flight sends were canceled."""
    return {"canceled": disconnect(db, connection_id)}


@app.get("/api/conversations")
def list_conversations(since: datetime | None = None):
    """REQ-002: the board polls this with the newest updated_at it has seen."""
    return conversations_since(db, since)


@app.patch("/api/conversations/{conversation_id}")
def update_conversation(conversation_id: UUID, body: StatusRequest):
    conversation = set_conversation_status(db, conversation_id, body.status)
    if conversation is None:
        raise HTTPException(status_code=404, detail="no such conversation")
    return conversation


@app.get("/api/conversations/{conversation_id}/messages")
def list_messages(conversation_id: UUID):
    """REQ-003: the conversation's history, oldest first."""
    return message_history(db, conversation_id)


@app.post("/api/conversations/{conversation_id}/messages", status_code=202)
def send_message(conversation_id: UUID, body: SendRequest):
    """202: queued for the Communication Service, not yet sent."""
    try:
        return queue_message(db, conversation_id, body.sender, body.receiver, body.content)
    except NotConnected:
        raise HTTPException(status_code=409, detail="Twilio is not connected") from None


# Mounted last so the API routes above win: the board page the browser loads.
app.mount("/", StaticFiles(directory="web", html=True), name="web")
