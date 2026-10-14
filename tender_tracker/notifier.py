"""Delivery channels: email (SMTP), Telegram, Slack webhook, WhatsApp (Twilio).

Credentials come only from environment variables (see .env.example). A channel
listed in config but missing its credentials is skipped with a warning.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

import requests
from sqlalchemy.orm import Session

from .config import Config, env
from .report import Digest, render_html, render_markdown, render_telegram, render_text
from .storage import NotificationSent

log = logging.getLogger(__name__)
TIMEOUT = 30


class Channel(Protocol):
    name: str

    def send_digest(self, digest: Digest, attachment: Path | None) -> None: ...

    def send_alert(self, subject: str, message: str) -> None: ...


@dataclass
class EmailChannel:
    host: str
    port: int
    user: str | None
    password: str | None
    sender: str
    recipients: list[str]
    use_ssl: bool = False
    name: str = "email"

    def _send(self, msg: EmailMessage) -> None:
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        context = ssl.create_default_context()
        if self.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=TIMEOUT)
            server.starttls(context=context)
        with server:
            if self.user:
                server.login(self.user, self.password or "")
            server.send_message(msg)

    def send_digest(self, digest: Digest, attachment: Path | None) -> None:
        msg = EmailMessage()
        msg["Subject"] = digest.subject
        msg.set_content(render_text(digest))
        msg.add_alternative(render_html(digest), subtype="html")
        if attachment and attachment.exists():
            msg.add_attachment(
                attachment.read_bytes(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=attachment.name,
            )
        self._send(msg)

    def send_alert(self, subject: str, message: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[GeM tracker ALERT] {subject}"
        msg.set_content(message)
        self._send(msg)


@dataclass
class TelegramChannel:
    token: str
    chat_id: str
    name: str = "telegram"

    def _api(self, method: str, **kwargs) -> None:
        resp = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", timeout=TIMEOUT, **kwargs)
        if not resp.ok:
            raise RuntimeError(f"Telegram {method} failed: HTTP {resp.status_code} {resp.text[:200]}")

    def send_digest(self, digest: Digest, attachment: Path | None) -> None:
        for text in render_telegram(digest):
            self._api(
                "sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
        if attachment and attachment.exists():
            with open(attachment, "rb") as fh:
                self._api("sendDocument", data={"chat_id": self.chat_id}, files={"document": (attachment.name, fh)})

    def send_alert(self, subject: str, message: str) -> None:
        self._api("sendMessage", json={"chat_id": self.chat_id, "text": f"🚨 {subject}\n\n{message}"[:4000]})


@dataclass
class SlackChannel:
    webhook_url: str
    name: str = "slack"

    def _post(self, text: str) -> None:
        resp = requests.post(self.webhook_url, json={"text": text}, timeout=TIMEOUT)
        if not resp.ok:
            raise RuntimeError(f"Slack webhook failed: HTTP {resp.status_code} {resp.text[:200]}")

    def send_digest(self, digest: Digest, attachment: Path | None) -> None:
        self._post(render_markdown(digest))

    def send_alert(self, subject: str, message: str) -> None:
        self._post(f":rotating_light: *{subject}*\n{message}")


@dataclass
class WhatsAppChannel:
    """WhatsApp via Twilio's Messages API (1600 character limit per message)."""

    account_sid: str
    auth_token: str
    sender: str  # e.g. whatsapp:+14155238886
    recipient: str  # e.g. whatsapp:+9198XXXXXXXX
    name: str = "whatsapp"

    def _post(self, body: str) -> None:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json",
            data={"From": self.sender, "To": self.recipient, "Body": body[:1600]},
            auth=(self.account_sid, self.auth_token),
            timeout=TIMEOUT,
        )
        if not resp.ok:
            raise RuntimeError(f"Twilio WhatsApp failed: HTTP {resp.status_code} {resp.text[:200]}")

    def send_digest(self, digest: Digest, attachment: Path | None) -> None:
        self._post(render_markdown(digest, limit=1600))

    def send_alert(self, subject: str, message: str) -> None:
        self._post(f"ALERT: {subject}\n{message}")


CHANNEL_ENV = {
    "email": ["SMTP_HOST"],  # SMTP_USER / SMTP_PASSWORD are optional (some relays need no login)
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "slack": ["SLACK_WEBHOOK_URL"],
    "whatsapp": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "WHATSAPP_TO"],
}


def channel_status(cfg: Config) -> dict[str, dict]:
    """Which channels are switched on and which .env secrets are missing (never the values)."""
    enabled = {c.lower().strip() for c in cfg.notifications.channels}
    out = {}
    for name, names in CHANNEL_ENV.items():
        missing = [n for n in names if not env(n)]
        if name == "email" and not (cfg.notifications.email_to or env("EMAIL_TO")):
            missing.append("recipient (Email recipients below, or EMAIL_TO in .env)")
        out[name] = {"enabled": name in enabled, "configured": not missing, "missing": missing}
    return out


def build_channels(cfg: Config) -> list[Channel]:
    channels: list[Channel] = []
    for name in cfg.notifications.channels:
        name = name.lower().strip()
        if name == "email":
            host = env("SMTP_HOST")
            recipients = cfg.notifications.email_to or [r for r in (env("EMAIL_TO") or "").split(",") if r.strip()]
            if not host or not recipients:
                log.warning("Email channel skipped: set SMTP_HOST in .env and notifications.email_to")
                continue
            use_ssl = (env("SMTP_USE_SSL", "false") or "").lower() in ("1", "true", "yes")
            channels.append(
                EmailChannel(
                    host=host,
                    port=int(env("SMTP_PORT", "465" if use_ssl else "587") or 587),
                    user=env("SMTP_USER"),
                    password=env("SMTP_PASSWORD"),
                    sender=env("SMTP_FROM") or env("SMTP_USER") or "gem-tracker@localhost",
                    recipients=[r.strip() for r in recipients],
                    use_ssl=use_ssl,
                )
            )
        elif name == "telegram":
            token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
            if token and chat:
                channels.append(TelegramChannel(token, chat))
            else:
                log.warning("Telegram channel skipped: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        elif name == "slack":
            if url := env("SLACK_WEBHOOK_URL"):
                channels.append(SlackChannel(url))
            else:
                log.warning("Slack channel skipped: set SLACK_WEBHOOK_URL")
        elif name == "whatsapp":
            sid, tok = env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN")
            frm, to = env("TWILIO_WHATSAPP_FROM"), env("WHATSAPP_TO")
            if sid and tok and frm and to:
                channels.append(WhatsAppChannel(sid, tok, frm, to))
            else:
                log.warning("WhatsApp channel skipped: set TWILIO_* and WHATSAPP_TO")
        else:
            log.warning("Unknown notification channel %r ignored", name)
    return channels


def send_digest(
    session: Session,
    cfg: Config,
    digest: Digest,
    attachment: Path | None,
    run_id: int | None = None,
    channels: list[Channel] | None = None,
) -> list[str]:
    """Send to every channel; record what was delivered. Returns names of channels that succeeded."""
    channels = build_channels(cfg) if channels is None else channels
    if not channels:
        log.warning("No notification channels configured; digest not sent")
        return []
    if digest.is_empty and not cfg.notifications.send_empty_digest:
        log.info("Digest empty; not sending")
        return []
    ok: list[str] = []
    for channel in channels:
        try:
            channel.send_digest(digest, attachment)
            ok.append(channel.name)
            log.info("Digest sent via %s", channel.name)
        except Exception:
            log.exception("Sending digest via %s failed", channel.name)
    for name in ok:
        session.add(NotificationSent(run_id=run_id, channel=name, kind="digest", sent_at=digest.date))
        for kind in ("new", "closing", "changed"):
            for e in getattr(digest, kind):
                session.add(NotificationSent(bid_id=e.bid_id, run_id=run_id, channel=name, kind=kind, sent_at=digest.date))
    session.commit()
    return ok


def send_alert(cfg: Config, subject: str, message: str, channels: list[Channel] | None = None) -> None:
    for channel in build_channels(cfg) if channels is None else channels:
        try:
            channel.send_alert(subject, message)
        except Exception:
            log.exception("Sending alert via %s failed", channel.name)
