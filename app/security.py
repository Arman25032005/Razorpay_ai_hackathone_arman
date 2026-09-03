"""
Lightweight production-practice middleware:

- Auth for mutating endpoints, accepting either an `X-API-Key` (machine
  callers, set API_KEY) or a signed session token from a dashboard login
  (humans, set DASHBOARD_PASSWORD — see app/auth.py). If neither is
  configured (the default — demo mode), auth is a no-op so the dashboard
  keeps working out of the box.
- Webhook signature validation (HMAC-SHA256) when PAYMENT_WEBHOOK_SECRET is
  set — a real payment provider's webhook should be authenticated, not
  merely idempotency-checked.
- A minimal in-memory sliding-window rate limiter for public endpoints
  (webhooks + simulation). This is intentionally simple: per-process,
  per-IP counters — adequate for a hackathon deployment, not a substitute
  for an edge/gateway rate limiter in front of a multi-instance production
  deployment.
"""
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

API_KEY = os.getenv("API_KEY")  # unset -> auth disabled (demo mode)
WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET")

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120  # per IP per window, for public endpoints
LOGIN_RATE_LIMIT_MAX_REQUESTS = 10  # per IP per window — a login attempt, not a read

_request_log: dict[str, deque] = defaultdict(deque)


def is_authorized(x_api_key: str | None, authorization: str | None) -> bool:
    """Shared check behind both `require_api_key` (per-route dependency, used
    on mutating endpoints) and the dashboard-login-gate middleware in
    app/main.py (used on every /api/ read once a password is configured).
    Two credentials are accepted:

    - `X-API-Key: <API_KEY>` — machine-to-machine (a merchant's backend).
    - `Authorization: Bearer <session token>` — a logged-in human on the
      dashboard, holding a token issued by /api/auth/login (see app/auth.py).

    True if either is valid, or if neither DASHBOARD_PASSWORD nor API_KEY
    is configured (demo mode — nothing to enforce)."""
    from app import auth

    if auth.verify_session_token(auth.bearer_token(authorization)):
        return True
    if API_KEY is not None and x_api_key == API_KEY:
        return True
    return API_KEY is None and not auth.login_required()


def require_api_key(x_api_key: str | None = Header(default=None),
                     authorization: str | None = Header(default=None)):
    """FastAPI dependency guarding every mutating endpoint — see
    is_authorized() for the credentials it accepts."""
    if not is_authorized(x_api_key, authorization):
        raise HTTPException(401, "Authentication required — sign in, or send a valid X-API-Key")


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> None:
    """HMAC-SHA256 signature check against PAYMENT_WEBHOOK_SECRET — same
    scheme Razorpay uses (https://razorpay.com/docs/webhooks/validate-test/).
    No-op if the secret isn't set (demo mode)."""
    if WEBHOOK_SECRET is None:
        return
    if not signature:
        raise HTTPException(401, "Missing webhook signature")
    expected = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid webhook signature")


def rate_limit(request: Request, key_prefix: str = "public", max_requests: int | None = None):
    """Simple sliding-window limiter keyed by client IP. Raises 429 past the
    threshold. Applied to webhook/simulation endpoints (the default,
    generous limit) and login (a much tighter one — see LOGIN_RATE_LIMIT_MAX_REQUESTS)."""
    limit = max_requests if max_requests is not None else RATE_LIMIT_MAX_REQUESTS
    client_ip = request.client.host if request.client else "unknown"
    key = f"{key_prefix}:{client_ip}"
    now = time.time()
    window = _request_log[key]
    while window and window[0] < now - RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(429, "Rate limit exceeded — please slow down")
    window.append(now)
