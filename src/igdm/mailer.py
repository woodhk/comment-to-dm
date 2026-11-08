"""Sending email from the owner's Gmail over SMTP (an app password, no Google Cloud project)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol

log = logging.getLogger(__name__)


class MailRefused(Exception):
    """Nothing went out (connect/login failed, or the server refused it). Safe to retry."""


class MailLoginRefused(MailRefused):
    """Gmail was reached but did not accept the address and app password."""


class MailMaybeSent(Exception):
    """The send started but no clear answer came back. Never retry it."""


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


STARTTLS_PORT = 587
_REFUSED_DURING_SEND = (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                        smtplib.SMTPDataError)


class GmailSender:
    def __init__(self, address: str, app_password: str, from_name: str, host: str, port: int,
                 timeout: float = 30.0):
        self._address = address
        self._password = app_password
        self._from_name = from_name
        self._host = host
        self._port = port
        self._timeout = timeout

    def _message(self, to: str, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self._from_name, self._address))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self._address.split("@")[-1])
        msg.set_content(body)
        return msg

    def _connect(self) -> smtplib.SMTP:
        """Port 587: plain connection upgraded with STARTTLS (servers that block 465, like
        Hetzner's). Any other port: SSL from the start."""
        if self._port != STARTTLS_PORT:
            return smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout)
        smtp = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        try:
            smtp.starttls(context=ssl.create_default_context())
        except Exception:
            smtp.close()
            raise
        return smtp

    def _logged_in(self) -> smtplib.SMTP:
        """Connect and sign in. The caller closes the connection (use it in a `with`)."""
        try:
            smtp = self._connect()
        except Exception as e:  # noqa: BLE001 - nothing was sent yet
            raise MailRefused(f"could not connect to {self._host}: {type(e).__name__}: {e}") from e
        try:
            smtp.login(self._address, self._password)
        except Exception as e:  # noqa: BLE001 - nothing was sent yet
            with smtp:  # leaves cleanly (quit)
                pass
            refused = MailLoginRefused if isinstance(e, smtplib.SMTPAuthenticationError) \
                else MailRefused
            raise refused(f"gmail login failed: {type(e).__name__}: {e}") from e
        return smtp

    def check_login(self) -> None:
        """Sign in and sign out, sending nothing. Raises MailRefused (MailLoginRefused when
        Gmail did not accept the password)."""
        with self._logged_in():
            pass

    def send(self, to: str, subject: str, body: str) -> None:
        msg = self._message(to, subject, body)
        with self._logged_in() as smtp:
            try:
                smtp.send_message(msg)
            except _REFUSED_DURING_SEND as e:
                raise MailRefused(f"gmail refused it: {type(e).__name__}: {e}") from e
            except Exception as e:  # noqa: BLE001 - it may have been accepted
                raise MailMaybeSent(f"no clear answer from gmail: {type(e).__name__}: {e}") from e


class DryRunMailer:
    def send(self, to: str, subject: str, body: str) -> None:
        log.info("DRY-RUN would email %s: %r", to, subject)
