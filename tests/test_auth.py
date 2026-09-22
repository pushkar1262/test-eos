import os

import pytest

# The cheapest bcrypt cost the library allows, so the suite is not bottlenecked
# on key stretching. Read at import time by src.api, along with DATABASE_URL.
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ.setdefault("SESSION_COOKIE_SECURE", "true")

from fastapi.testclient import TestClient  # noqa: E402

from src import api  # noqa: E402

client = TestClient(api.app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def clean_state():
    api.db.execute("TRUNCATE users, sessions")
    api._failures.clear()
    client.cookies.clear()


def signup(name="Ada", email="ada@example.com", password="correct-horse", confirm=None):
    return client.post(
        "/api/auth/signup",
        json={
            "name": name,
            "email": email,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def login(email="ada@example.com", password="correct-horse"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_signup_creates_an_account():
    response = signup()
    assert response.status_code == 201
    assert response.json()["email"] == "ada@example.com"
    assert response.json()["name"] == "Ada"


def test_email_is_unique_and_case_insensitive():
    signup()
    assert signup(email="ADA@example.com").status_code == 409


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "  "},
        {"email": "not-an-email"},
        {"password": "short"},
        {"confirm": "something-else"},
    ],
    ids=["blank name", "bad email", "short password", "mismatched confirmation"],
)
def test_invalid_signups_are_rejected(kwargs):
    # The browser checks all four before posting; the server is what enforces
    # them. 422 is Pydantic's own rejection of a malformed email.
    assert signup(**kwargs).status_code in (400, 422)


def test_password_is_stored_as_a_bcrypt_hash():
    signup()
    row = api.db.execute("SELECT password_hash FROM users").fetchone()
    assert row["password_hash"].startswith("$2b$")


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


def test_me_returns_the_signed_in_account_without_the_password():
    signup()
    login()
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"
    assert not any("password" in field for field in response.json())


def test_me_without_a_session_is_401():
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
