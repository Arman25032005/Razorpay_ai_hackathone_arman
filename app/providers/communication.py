"""Communication adapter — email/SMS/WhatsApp interface + demo mock.

Real integrations: SendGrid for email, Twilio for WhatsApp. Both implemented
directly against their documented REST APIs (no SDK dependency), following
the same adapter pattern as app.providers.payment.RazorpayPaymentProvider.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod


class CommunicationProvider(ABC):
    @abstractmethod
    def send(self, channel: str, to: str, body: str) -> dict:
        ...


class MockCommunicationProvider(CommunicationProvider):
    def send(self, channel: str, to: str, body: str) -> dict:
        # Demo mode: always "succeeds" at sending (delivery != response).
        return {"provider": "mock", "channel": channel, "to": to, "status": "sent"}


class SendGridEmailProvider:
    """Real email delivery via SendGrid's v3 REST API
    (https://docs.sendgrid.com/api-reference/mail-send/mail-send)."""

    URL = "https://api.sendgrid.com/v3/mail/send"

    def __init__(self, api_key: str, from_email: str):
        self.api_key = api_key
        self.from_email = from_email

    def send(self, to: str, body: str, subject: str = "Payment recovery notice") -> dict:
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self.from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        req = urllib.request.Request(
            self.URL, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                # SendGrid returns 202 Accepted with an empty body on success.
                return {"provider": "sendgrid", "channel": "email", "to": to,
                        "status": "sent" if resp.status == 202 else "unknown"}
        except urllib.error.HTTPError as e:
            return {"provider": "sendgrid", "channel": "email", "to": to,
                    "status": "failed", "error": e.read().decode(errors="replace")}


class MetaWhatsAppCloudProvider:
    """Real WhatsApp delivery via Meta's official WhatsApp Cloud API
    (https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages).
    Free tier: 1,000 service conversations/month, no card required."""

    API_VERSION = "v20.0"

    def __init__(self, access_token: str, phone_number_id: str):
        self.access_token = access_token
        self.phone_number_id = phone_number_id

    def send(self, to: str, body: str) -> dict:
        url = f"https://graph.facebook.com/{self.API_VERSION}/{self.phone_number_id}/messages"
        # Meta wants digits only (no "+", no "whatsapp:" prefix).
        to_number = "".join(ch for ch in to if ch.isdigit())
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": body},
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                message_id = (result.get("messages") or [{}])[0].get("id")
                return {"provider": "meta_cloud_api", "channel": "whatsapp", "to": to,
                        "status": "sent" if message_id else "unknown", "sid": message_id}
        except urllib.error.HTTPError as e:
            return {"provider": "meta_cloud_api", "channel": "whatsapp", "to": to,
                    "status": "failed", "error": e.read().decode(errors="replace")}


class TwilioWhatsAppProvider:
    """Real WhatsApp delivery via Twilio's WhatsApp Business API
    (https://www.twilio.com/docs/whatsapp/api)."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number if from_number.startswith("whatsapp:") else f"whatsapp:{from_number}"

    def send(self, to: str, body: str) -> dict:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        to_number = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        data = urllib.parse.urlencode({"From": self.from_number, "To": to_number, "Body": body}).encode()
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                return {"provider": "twilio", "channel": "whatsapp", "to": to,
                        "status": result.get("status", "sent"), "sid": result.get("sid")}
        except urllib.error.HTTPError as e:
            return {"provider": "twilio", "channel": "whatsapp", "to": to,
                    "status": "failed", "error": e.read().decode(errors="replace")}


class LiveCommunicationProvider(CommunicationProvider):
    """Routes each channel to its real adapter when configured; any channel
    without a configured adapter (e.g. sms, or email/whatsapp if only one
    side was set up) falls back to mock behavior rather than failing."""

    def __init__(self, email_provider: SendGridEmailProvider | None,
                 whatsapp_provider: TwilioWhatsAppProvider | MetaWhatsAppCloudProvider | None):
        self.email_provider = email_provider
        self.whatsapp_provider = whatsapp_provider

    def send(self, channel: str, to: str, body: str) -> dict:
        if channel == "email" and self.email_provider:
            return self.email_provider.send(to, body)
        if channel == "whatsapp" and self.whatsapp_provider:
            return self.whatsapp_provider.send(to, body)
        return {"provider": "mock", "channel": channel, "to": to, "status": "sent"}


def _select_provider() -> CommunicationProvider:
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    sendgrid_from = os.getenv("SENDGRID_FROM_EMAIL")
    email_provider = (SendGridEmailProvider(sendgrid_key, sendgrid_from)
                      if sendgrid_key and sendgrid_from else None)

    # Meta's WhatsApp Cloud API is tried first (free tier, no Twilio trial
    # restrictions). Falls back to Twilio if only Twilio is configured.
    meta_token = os.getenv("META_WHATSAPP_TOKEN")
    meta_phone_id = os.getenv("META_WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_provider = (MetaWhatsAppCloudProvider(meta_token, meta_phone_id)
                         if meta_token and meta_phone_id else None)

    if not whatsapp_provider:
        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
        twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_from = os.getenv("TWILIO_WHATSAPP_FROM")
        whatsapp_provider = (TwilioWhatsAppProvider(twilio_sid, twilio_token, twilio_from)
                             if twilio_sid and twilio_token and twilio_from else None)

    if email_provider or whatsapp_provider:
        return LiveCommunicationProvider(email_provider, whatsapp_provider)
    return MockCommunicationProvider()


communication_provider: CommunicationProvider = _select_provider()


def render_message(strategy: str, customer_name: str, amount: float, currency: str, extra: dict | None = None) -> str:
    """Template-based, policy-safe message generation. In a live LLM
    deployment this would be a call to `ai_service.generate_customer_message`,
    still passed through the same non-threatening template guardrails."""
    amt = f"{currency} {amount:,.0f}"
    templates = {
        "immediate_payment_retry": f"Hi {customer_name}, we're retrying your payment of {amt} now.",
        "delayed_retry": f"Hi {customer_name}, we'll automatically retry your payment of {amt} shortly.",
        "payment_method_update": (
            f"Hi {customer_name}, your payment of {amt} could not be processed. "
            f"You can update your payment method using the secure link below."
        ),
        "friendly_reminder": (
            f"Hi {customer_name}, a quick reminder that your payment of {amt} is still pending. "
            f"Let us know if you need help."
        ),
        "stronger_reminder": (
            f"Hi {customer_name}, your payment of {amt} remains outstanding. "
            f"Please complete it at your earliest convenience or reach out if there's an issue."
        ),
        "checkout_recovery_message": (
            f"Hi {customer_name}, you left {amt} in your cart. "
            f"Complete your purchase anytime using the link below."
        ),
        "payment_link": f"Hi {customer_name}, here is a secure link to complete your payment of {amt}.",
        "promise_to_pay_request": (
            f"Hi {customer_name}, could you let us know when you plan to settle {amt}? "
            f"Happy to work with a date that suits you."
        ),
        "invoice_escalation": (
            f"Hi {customer_name}, invoice for {amt} is now overdue. "
            f"Please arrange payment or contact us to discuss."
        ),
    }
    return templates.get(strategy, f"Hi {customer_name}, regarding your pending payment of {amt}.")
