from __future__ import annotations

import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr

_BACKOFF = (5, 15)
_ATTEMPTS = 3


def _build(settings, to: str, subject: str, html: str, text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr(("Ez Apply", settings.sender_email))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send_email(settings, to: str, subject: str, html: str, text: str, *,
               smtp_factory=smtplib.SMTP_SSL, sleep=time.sleep) -> None:
    msg = _build(settings, to, subject, html, text)
    for attempt in range(_ATTEMPTS):
        try:
            smtp = smtp_factory("smtp.gmail.com", 465, timeout=30)
            try:
                smtp.login(settings.sender_email, settings.sender_app_password)
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except (smtplib.SMTPException, OSError):
                    smtp.close()
            return
        except (smtplib.SMTPException, OSError):
            if attempt == _ATTEMPTS - 1:
                raise
            sleep(_BACKOFF[attempt])
