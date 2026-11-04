"""Sending email from your Gmail account, with a Gmail app password.

Only background jobs send email, and only after you said yes out loud (policy.py).
Python's built-in smtplib does the work; no extra packages are needed.
"""

import smtplib
from email.message import EmailMessage

from alfred.config import Config

GMAIL_SMTP = ("smtp.gmail.com", 587)


class EmailNotSetUp(Exception):
    """ALFRED_EMAIL or ALFRED_EMAIL_APP_PASSWORD is missing from .env."""


def send_email(config: Config, to: str, subject: str, body: str) -> None:
    """Blocking: call it with asyncio.to_thread. Raises if Gmail refuses."""
    if not (config.email_address and config.email_app_password):
        raise EmailNotSetUp("Email is not set up: add ALFRED_EMAIL and its app password to .env")
    message = EmailMessage()
    message["From"] = config.email_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(*GMAIL_SMTP, timeout=30) as server:
        server.starttls()
        server.login(config.email_address, config.email_app_password)
        server.send_message(message)
