"""
Outbound merchant webhooks.

RecoveryOS consumes Razorpay's webhooks; this module makes it produce the
same kind of event, outward, to a merchant's own systems (CRM, finance,
alerting) so they can react to recovery activity — case opened, recovered,
or escalated — without polling the dashboard.

Signing scheme deliberately mirrors app.security.verify_webhook_signature
(HMAC-SHA256, hex digest, constant-time compare on the receiving end):
the same authentication contract we require of Razorpay's webhooks, we
hold ourselves to for the webhooks we send.

No queue: dispatch is synchronous, best-effort, short-timeout, and never
allowed to raise into the caller — an unreachable merchant endpoint must
never block or fail a recovery action. Every attempt is logged to
WebhookDelivery for visibility, matching this project's "no async
infrastructure until volume requires it" stance (see docs/PRODUCT.md).
"""
import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request

from sqlalchemy.orm import Session

from app.models import MerchantWebhookSubscription, WebhookDelivery, utcnow

DELIVERY_TIMEOUT_SECONDS = 5


def generate_secret() -> str:
    return secrets.token_hex(32)


def sign_payload(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def dispatch_event(db: Session, merchant_id: str | None, event_type: str, case_id: str, data: dict) -> None:
    """Fires `event_type` to every active subscription for `merchant_id`
    that lists it (or has no filter, i.e. subscribes to everything).
    Silent no-op if merchant_id is unknown or has no subscriptions — most
    cases in this demo aren't tied to a specific merchant, and that's fine."""
    if not merchant_id:
        return
    subs = (db.query(MerchantWebhookSubscription)
            .filter(MerchantWebhookSubscription.merchant_id == merchant_id,
                    MerchantWebhookSubscription.active == True)  # noqa: E712
            .all())
    if not subs:
        return

    body_obj = {"event": event_type, "case_id": case_id, "created_at": utcnow().isoformat(), "data": data}
    body = json.dumps(body_obj).encode()

    for sub in subs:
        if sub.event_types and event_type not in sub.event_types:
            continue
        signature = sign_payload(body, sub.secret)
        delivery = WebhookDelivery(subscription_id=sub.id, event_type=event_type, case_id=case_id)
        try:
            req = urllib.request.Request(
                sub.url, data=body, method="POST",
                headers={"Content-Type": "application/json", "X-RecoveryOS-Signature": signature},
            )
            with urllib.request.urlopen(req, timeout=DELIVERY_TIMEOUT_SECONDS) as resp:
                delivery.status_code = resp.status
                delivery.success = 200 <= resp.status < 300
        except urllib.error.HTTPError as e:
            delivery.status_code = e.code
            delivery.success = False
            delivery.error = f"HTTP {e.code}"
        except Exception as exc:
            delivery.success = False
            delivery.error = f"{type(exc).__name__}: {exc}"
        db.add(delivery)
    db.commit()
