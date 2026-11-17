"""Create Gmail DRAFTS over IMAP. This module has no code path that sends mail: it only appends a message to the Drafts folder.
Credentials (a Gmail app password) live in the macOS Keychain, service 'job-hunter-gmail', never in a file."""
import imaplib
import mimetypes
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from app.core.config import settings

KEYCHAIN_SERVICE = "job-hunter-gmail"


class MailError(Exception):
    """A readable problem for the user. Never contains the password."""

    def __init__(self, message: str, code: str = "gmail_error"):
        super().__init__(message)
        self.code = code


@dataclass
class Credentials:
    address: str
    password: str = ""

    def __repr__(self) -> str:  # never print the password, even by accident
        return f"Credentials(address={self.address!r}, password=<hidden>)"


def keychain_account() -> str | None:
    """The Gmail address saved with the Keychain item (read without revealing the password)."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'"acct"<blob>="([^"]+)"', r.stdout) if r.returncode == 0 else None
    return m.group(1) if m else None


def credentials(with_password: bool = True) -> Credentials | None:
    """Environment variables win (for tests and CI); otherwise the Keychain. None if nothing is set up."""
    addr, pw = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if addr and pw:
        return Credentials(addr, pw if with_password else "")
    addr = keychain_account()
    if not addr:
        return None
    if not with_password:
        return Credentials(addr)
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", addr, "-w"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return Credentials(addr, r.stdout.rstrip("\n")) if r.returncode == 0 and r.stdout.strip() else None


def build_message(*, sender: str, to: str, subject: str, body: str, attachments: list[tuple[str, Path]]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body)
    for filename, path in attachments:
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(Path(path).read_bytes(), maintype=maintype, subtype=subtype, filename=filename)
    return msg


class GmailImapMailer:
    """Appends to the Drafts folder. `create_draft` is the only operation."""

    def __init__(self, creds: Credentials):
        self.creds = creds

    @property
    def account(self) -> str:
        return self.creds.address

    def _connect(self) -> imaplib.IMAP4:
        host, port = settings.gmail_imap_host, settings.gmail_imap_port
        try:
            imap = imaplib.IMAP4_SSL(host, port, timeout=30) if settings.gmail_imap_ssl else imaplib.IMAP4(host, port, timeout=30)
        except (OSError, socket.timeout):
            raise MailError("Can't reach Gmail. Check your internet connection and try again.", "gmail_unreachable") from None
        try:
            imap.login(self.creds.address, self.creds.password)
        except imaplib.IMAP4.error:
            raise MailError("Gmail refused the sign-in. Create a new app password at myaccount.google.com/apppasswords "
                            "(needs 2-Step Verification) and save it again with scripts/set_gmail.sh.", "gmail_auth_failed") from None
        return imap

    @staticmethod
    def _drafts_folder(imap: imaplib.IMAP4) -> str:
        typ, lines = imap.list()
        for raw in lines or []:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            m = re.match(r'\((?P<attrs>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
            if m and "\\drafts" in m.group("attrs").lower():
                return m.group("name").strip().strip('"')
        return "[Gmail]/Drafts"

    def create_draft(self, message: EmailMessage) -> str:
        """Save `message` as a draft and return the folder it went to. Nothing is sent."""
        imap = self._connect()
        try:
            folder = self._drafts_folder(imap)
            typ, _ = imap.append(f'"{folder}"', "\\Draft", imaplib.Time2Internaldate(time.time()), message.as_bytes())
            if typ != "OK":
                raise MailError("Gmail would not save the draft.", "gmail_append_failed")
            return folder
        except imaplib.IMAP4.error:
            raise MailError("Gmail would not save the draft.", "gmail_append_failed") from None
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass


def get_mailer() -> GmailImapMailer | None:
    c = credentials()
    return GmailImapMailer(c) if c else None
