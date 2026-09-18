"""Login endpoint wiring for the rate limiter."""

from __future__ import annotations

from src.rate_limit import LoginRateLimiter

limiter = LoginRateLimiter()


def login(username: str, password: str, ip: str, verify) -> dict:
    """Authenticate a user, refusing the attempt if they are rate limited."""
    limiter.check(username, ip)

    success = verify(username, password)
    limiter.record_attempt(username, ip, success)

    if not success:
        return {"status": 401, "error": "invalid credentials"}
    return {"status": 200, "user": username}
