"""The HTTP half of the criteria: status codes, cookie attributes, 401s."""

import pytest
from fastapi.testclient import TestClient

from src import api

# base_url is https so the Secure cookie is kept by the test client.
client = TestClient(api.app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def clean_state():
    api.db.execute("TRUNCATE users, sessions")
    client.cookies.clear()


def signup(name="Ada", email="ada@example.com", password="correct-horse"):
    return client.post(
        "/api/auth/signup", json={"name": name, "email": email, "password": password}
    )


def login(email="ada@example.com", password="correct-horse"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_unique_email_creates_the_account():
    response = signup()
    assert response.status_code == 201
    assert response.json()["email"] == "ada@example.com"
    assert "password" not in response.text


def test_duplicate_email_is_a_409():
    signup()
    assert signup(email="ADA@example.com").status_code == 409


def test_login_sets_an_httponly_secure_cookie():
    signup()
    response = login()
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert api.COOKIE_NAME in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie


def test_bad_credentials_are_401():
    signup()
    assert login(password="wrong-password").status_code == 401
    assert login(email="nobody@example.com").status_code == 401


def test_protected_route_needs_a_session():
    assert client.get("/api/auth/me").status_code == 401
    signup()
    login()
    assert client.get("/api/auth/me").json()["name"] == "Ada"


def test_logout_expires_the_cookie_and_the_session():
    signup()
    login()
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    # The browser drops the cookie because the server sends it back expired.
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 401


def test_login_page_sends_the_browser_to_the_dashboard():
    # The redirect is client-side; this pins the pages it depends on.
    assert 'window.location.href = "/dashboard.html"' in client.get("/login.html").text
    assert client.get("/dashboard.html").status_code == 200
