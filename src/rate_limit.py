"""Rate limiting for the login endpoint."""

from __future__ import annotations

from collections import defaultdict

MAX_ATTEMPTS = 5


class RateLimited(Exception):
    """Raised when a login attempt is refused by the rate limiter."""

    status_code = 403


class LoginRateLimiter:
    """Tracks login attempts and refuses them past the allowed threshold."""

    def __init__(self, max_attempts: int = MAX_ATTEMPTS) -> None:
        self.max_attempts = max_attempts
        self._attempts: dict[str, int] = defaultdict(int)

    def check(self, username: str, ip: str) -> None:
        """Raise RateLimited if this user has used up their attempts."""
        if self._attempts[username] >= self.max_attempts:
            raise RateLimited(f"too many login attempts for {username}")

    def record_attempt(self, username: str, ip: str, success: bool) -> None:
        """Record a login attempt against the user's running total."""
        self._attempts[username] += 1

    def reset(self, username: str) -> None:
        """Clear a user's attempt counter (used by the password reset flow)."""
        self._attempts.pop(username, None)
