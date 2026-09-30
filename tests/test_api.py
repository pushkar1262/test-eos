"""The HTTP half of the criteria: status codes, and the page the board runs."""

import pytest
from fastapi.testclient import TestClient

from src import api
from src.store import upsert_conversation

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    api.db.execute("TRUNCATE connections, conversations, messages, outbox_events")
    monkeypatch.setattr(api, "verify_twilio", lambda sid, token: None)


def connect():
    return client.post("/api/connection", json={"account_sid": "AC123", "auth_token": "secret"})


def conversation():
    connection_id = connect().json()["id"]
    return upsert_conversation(api.db, connection_id, "CH1", "Ada Lovelace")


def test_connect_succeeds():
    response = connect()
    assert response.status_code == 200
    assert response.json()["state"] == "connected"
    assert client.get("/api/connection").json()["state"] == "connected"


def test_failed_connect_returns_the_error_and_is_not_connected(monkeypatch):
    connect()
    monkeypatch.setattr(api, "verify_twilio", lambda sid, token: "Twilio rejected it.")
    response = connect()
    assert response.status_code == 502
    assert response.json()["detail"] == "Twilio rejected it."
    assert client.get("/api/connection").json()["state"] == "failed"


def test_disconnect_cancels_an_in_flight_send():
    convo = conversation()
    sent = client.post(
        f"/api/conversations/{convo.id}/messages",
        json={"sender": "+15550001", "receiver": "+15550002", "content": "hi"},
    )
    assert sent.status_code == 202
    response = client.post(f"/api/connection/{convo.connection_id}/disconnect")
    assert response.json() == {"canceled": 1}
    assert client.get("/api/connection").json()["state"] == "disconnected"
    [message] = client.get(f"/api/conversations/{convo.id}/messages").json()
    assert message["status"] == "canceled"


def test_sending_while_disconnected_is_a_409():
    convo = conversation()
    client.post(f"/api/connection/{convo.connection_id}/disconnect")
    response = client.post(
        f"/api/conversations/{convo.id}/messages",
        json={"sender": "+15550001", "receiver": "+15550002", "content": "hi"},
    )
    assert response.status_code == 409


def test_poll_returns_only_new_conversations():
    first = conversation()
    since = client.get("/api/conversations").json()[-1]["updated_at"]
    assert client.get("/api/conversations", params={"since": since}).json() == []
    upsert_conversation(api.db, first.connection_id, "CH2", "Grace Hopper")
    assert [c["title"] for c in client.get("/api/conversations", params={"since": since}).json()] == [
        "Grace Hopper"
    ]


def test_message_history_has_the_displayed_fields():
    convo = conversation()
    client.post(
        f"/api/conversations/{convo.id}/messages",
        json={"sender": "+15550001", "receiver": "+15550002", "content": "hello"},
    )
    [message] = client.get(f"/api/conversations/{convo.id}/messages").json()
    assert {"sender", "receiver", "content", "sent_at", "status"} <= message.keys()


def test_only_defined_statuses_are_accepted():
    convo = conversation()
    url = f"/api/conversations/{convo.id}"
    assert client.patch(url, json={"status": "resolved"}).json()["status"] == "resolved"
    assert client.patch(url, json={"status": "closed"}).status_code == 422


def test_board_page_is_served_with_the_three_statuses():
    page = client.get("/").text
    for value in ('value="open"', 'value="in_progress"', 'value="resolved"'):
        assert value in page
    assert page.count("<option") == 3
