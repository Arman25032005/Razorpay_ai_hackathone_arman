"""
Lightweight production-practice middleware:

- API-key auth for mutating endpoints. If API_KEY is unset (the default —
  demo mode), auth is a no-op so the dashboard keeps working out of the box.
  Set API_KEY to require `X-API-Key` on every state-changing request.
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

_request_log: dict[str, deque] = defaultdict(deque)


def require_api_key(x_api_key: str | None = Header(default=None)):
    """FastAPI dependency. No-op unless API_KEY is configured, so the demo
    works without setup; set API_KEY in the environment to lock down
    mutating endpoints in a shared/production deployment."""
    if API_KEY is None:
        return
    if x_api_key != API_KEY:
        raise HTTPException(401, "Missing or invalid X-API-Key")


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


def rate_limit(request: Request, key_prefix: str = "public"):
    """Simple sliding-window limiter keyed by client IP. Raises 429 past the
    threshold. Applied to webhook and simulation endpoints."""
    client_ip = request.client.host if request.client else "unknown"
    key = f"{key_prefix}:{client_ip}"
    now = time.time()
    window = _request_log[key]
    while window and window[0] < now - RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(429, "Rate limit exceeded — please slow down")
    window.append(now)
