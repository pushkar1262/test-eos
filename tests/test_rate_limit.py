import pytest

from src.rate_limit import LoginRateLimiter, RateLimited


def test_attempts_under_the_limit_are_allowed():
    limiter = LoginRateLimiter()
    for _ in range(4):
        limiter.check("alice", "10.0.0.1")
        limiter.record_attempt("alice", "10.0.0.1", success=False)


def test_sixth_attempt_is_refused():
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_attempt("alice", "10.0.0.1", success=False)

    with pytest.raises(RateLimited):
        limiter.check("alice", "10.0.0.1")


def test_reset_clears_the_counter():
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_attempt("alice", "10.0.0.1", success=False)

    limiter.reset("alice")
    limiter.check("alice", "10.0.0.1")
