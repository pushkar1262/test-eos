import os
import tempfile

import pytest

# A throwaway database and the cheapest bcrypt cost the library allows, so the
# suite stays fast. Both are read at import time by src.api.
os.environ["AUTH_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "auth.db")
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["SESSION_COOKIE_SECURE"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from src import api  # noqa: E402

client = TestClient(api.app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def clean_state():
    api.db.execute("DELETE FROM sessions")
    api.db.execute("DELETE FROM users")
    api.db.commit()
    api._failures.clear()


def signup(email="ada@example.com", password="correct-horse"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def login(email="ada@example.com", password="correct-horse"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_signup_creates_an_account():
    response = signup()
    assert response.status_code == 201
    assert response.json()["email"] == "ada@example.com"


def test_email_is_unique_and_case_insensitive():
    signup()
    assert signup(email="ADA@example.com").status_code == 409


def test_short_password_is_rejected():
    assert signup(password="short").status_code == 400


def test_login_issues_an_httponly_secure_cookie():
    signup()
    response = login()
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert api.COOKIE_NAME in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie


def test_login_with_wrong_password_is_refused():
    signup()
    assert login(password="wrong-password").status_code == 401


def test_me_returns_the_signed_in_account():
    signup()
    login()
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"


def test_me_without_a_session_is_401():
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_logout_invalidates_the_session():
    signup()
    login()
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_repeated_failures_are_throttled():
    signup()
    for _ in range(api.MAX_ATTEMPTS):
        assert login(password="wrong-password").status_code == 401
    refused = login(password="wrong-password")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0


def test_a_success_clears_the_failure_count():
    signup()
    for _ in range(api.MAX_ATTEMPTS - 1):
        login(password="wrong-password")
    assert login().status_code == 200
    assert login(password="wrong-password").status_code == 401
