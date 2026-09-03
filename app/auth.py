"""
Dashboard login gate.

The `API_KEY` mechanism in app/security.py authenticates *machines* — a
merchant's backend calling our API with a shared header. It was never
usable from the dashboard itself, because a browser has no way to hold a
server secret (see the note in .env.example). Embedding the key in the
frontend bundle would have made the "API Auth: Required" badge a lie:
anyone could read it out of devtools.

This module adds the missing half — authenticating a *person*. A password
is exchanged once for a short-lived, HMAC-signed session token that the
dashboard sends on subsequent requests. Stateless (no session table): the
token carries its own expiry and signature, verified in constant time.

Set DASHBOARD_PASSWORD to require login. Leave it unset and the dashboard
stays open — same "unset means demo mode" convention as every other
integration in this project.

Honest scope: this is single-password auth for one deployment, not user
accounts with per-user identity. Real RBAC remains the documented roadmap
item it always was (docs/SECURITY.md); this closes the "the dashboard
literally cannot authenticate" gap, not the "no multi-user auth" one.
"""
import hashlib
import hmac
import os
import secrets
import time

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")

# Signing key for session tokens. If unset, a random per-process key is
# generated — tokens then stay valid only until the server restarts, which
# is fine for a single-instance demo but means every instance in a
# multi-instance deployment would reject the others' tokens. Set
# SESSION_SECRET explicitly for anything beyond one process.
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)

SESSION_TTL_SECONDS = 12 * 60 * 60  # 12 hours


def login_required() -> bool:
    """True when a password is configured, i.e. the gate is active."""
    return bool(DASHBOARD_PASSWORD)


def check_password(password: str | None) -> bool:
    """Constant-time password comparison — never a plain `==`, which leaks
    the length of the matching prefix through timing."""
    if not login_required() or not password:
        return False
    return hmac.compare_digest(password, DASHBOARD_PASSWORD)


def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session_token(ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    """Issues `<expiry_unix_ts>.<hmac_sha256>`. The expiry is in the signed
    payload, so a client can't extend its own session by editing it."""
    expiry = str(int(time.time()) + ttl_seconds)
    return f"{expiry}.{_sign(expiry)}"


def verify_session_token(token: str | None) -> bool:
    """Valid signature AND not expired. Any malformed input fails closed."""
    if not token or "." not in token:
        return False
    expiry, _, signature = token.partition(".")
    if not hmac.compare_digest(_sign(expiry), signature):
        return False
    try:
        return int(expiry) > time.time()
    except ValueError:
        return False


def bearer_token(authorization_header: str | None) -> str | None:
    """Extracts the token from an `Authorization: Bearer <token>` header.
    Defensively requires an actual string — a dependency called directly
    (as tests do, bypassing FastAPI's header-resolution) would otherwise
    pass through a `Header(...)` sentinel object instead of a real value."""
    if not isinstance(authorization_header, str) or not authorization_header:
        return None
    scheme, _, token = authorization_header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None
