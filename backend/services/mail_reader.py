import email
import imaplib
import os
from dataclasses import dataclass, field
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr
from typing import List

from .gmail_client import GmailOAuthClient, GmailAttachmentPayload, GmailMessageEnvelope


SUBJECT_KEYWORDS = (
    "result",
    "results",
    "marks",
    "marksheet",
    "mark sheet",
    "grade",
    "grade card",
    "gradecard",
    "transcript",
    "score",
    "sgpa",
    "cgpa",
    "provisional",
    "consolidated",
    "semester",
    "report",
)
ALLOWED_EXTENSIONS = {".xlsx", ".pdf"}


def _is_result_related_subject(subject: str) -> bool:
    normalized = (subject or "").lower()
    return any(keyword in normalized for keyword in SUBJECT_KEYWORDS)


def _is_result_related_text(text: str) -> bool:
    normalized = (text or "").lower()
    return any(keyword in normalized for keyword in SUBJECT_KEYWORDS)


@dataclass
class MailAttachment:
    filename: str
    content_type: str
    payload: bytes


@dataclass
class MailEnvelope:
    uid: str
    subject: str
    sender: str
    message_id: str
    attachments: List[MailAttachment] = field(default_factory=list)


def _decode_header_value(value: str) -> str:
    parts = decode_header(value or "")
    decoded = []
    for chunk, encoding in parts:
        if isinstance(chunk, bytes):
            decoded.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded.append(chunk)
    return "".join(decoded).strip()


def _preferred_sender_address(message: Message, connected_email: str = "") -> str:
    reply_to = parseaddr(message.get("Reply-To", ""))[1].strip()
    from_address = parseaddr(message.get("From", ""))[1].strip()
    sender_address = parseaddr(message.get("Sender", ""))[1].strip()
    return_path = parseaddr(message.get("Return-Path", ""))[1].strip()
    for candidate in (reply_to, from_address, sender_address, return_path):
        if candidate:
            if connected_email and candidate.lower() == connected_email.lower() and any((reply_to, sender_address, return_path)):
                continue
            return candidate
    return reply_to or from_address or sender_address or return_path


def _iter_attachments(message: Message) -> List[MailAttachment]:
    attachments: List[MailAttachment] = []
    for part in message.walk():
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" not in disposition:
            continue
        filename = _decode_header_value(part.get_filename() or "")
        extension = os.path.splitext(filename)[1].lower()
        if not filename or extension not in ALLOWED_EXTENSIONS:
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            MailAttachment(
                filename=filename,
                content_type=part.get_content_type(),
                payload=payload,
            )
        )
    return attachments


class MailReader:
    def __init__(self) -> None:
        self.provider = os.getenv("EMAIL_PROVIDER", "gmail").strip().lower()
        self.host = os.getenv("IMAP_HOST", "").strip()
        self.port = int(os.getenv("IMAP_PORT", "993"))
        self.username = os.getenv("EMAIL_USER", "").strip()
        self.password = os.getenv("EMAIL_PASS", "").strip()
        self.mailbox = os.getenv("IMAP_MAILBOX", "INBOX").strip() or "INBOX"
        self.gmail = GmailOAuthClient()

    def is_connected(self) -> bool:
        if self.provider == "gmail":
            return self.gmail.is_connected()
        return bool(self.host and self.username and self.password)

    def connection_status(self) -> dict:
        if self.provider == "gmail":
            token_data = self.gmail.get_token_data(refresh=False)
            return {
                "provider": "gmail",
                "connected": bool(token_data),
                "connected_email": str(token_data.get("email_address") or "") if token_data else "",
                "connected_at": None,
            }
        return {
            "provider": "imap",
            "connected": bool(self.host and self.username and self.password),
            "connected_email": self.username,
            "connected_at": None,
        }

    def _connect(self) -> imaplib.IMAP4_SSL:
        if self.provider == "gmail":
            raise RuntimeError("Gmail provider is selected. Use the email connection option to authorize access.")
        if not self.host or not self.username or not self.password:
            raise RuntimeError("IMAP configuration is incomplete. Set IMAP_HOST, EMAIL_USER, and EMAIL_PASS.")
        client = imaplib.IMAP4_SSL(self.host, self.port)
        client.login(self.username, self.password)
        return client

    def fetch_unread_result_emails(self) -> List[MailEnvelope]:
        if self.provider == "gmail":
            gmail_messages = self.gmail.fetch_unread_result_emails()
            envelopes: List[MailEnvelope] = []
            for message in gmail_messages:
                attachments = [
                    MailAttachment(filename=attachment.filename, content_type=attachment.content_type, payload=attachment.payload)
                    for attachment in message.attachments
                ]
                envelopes.append(
                    MailEnvelope(
                        uid=message.uid,
                        subject=message.subject,
                        sender=message.sender,
                        message_id=message.message_id,
                        attachments=attachments,
                    )
                )
            return envelopes

        client = self._connect()
        try:
            status, _ = client.select(self.mailbox)
            if status != "OK":
                raise RuntimeError(f"Unable to select IMAP mailbox {self.mailbox}.")

            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("Unable to search emails.")

            envelopes: List[MailEnvelope] = []
            for raw_uid in data[0].split():
                uid = raw_uid.decode("utf-8", errors="replace")
                fetch_status, message_data = client.uid("fetch", raw_uid, "(RFC822)")
                if fetch_status != "OK":
                    continue
                raw_message = b""
                for item in message_data:
                    if isinstance(item, tuple) and len(item) > 1:
                        raw_message = item[1]
                        break
                if not raw_message:
                    continue

                message = email.message_from_bytes(raw_message)
                subject = _decode_header_value(message.get("Subject", ""))
                sender = _preferred_sender_address(message, connected_email=self.username)
                if not sender:
                    continue
                if self.username and sender.lower() == self.username.lower():
                    continue
                attachments = _iter_attachments(message)
                if not attachments:
                    continue
                attachment_names = " ".join(attachment.filename for attachment in attachments)
                if not (
                    _is_result_related_subject(subject)
                    or _is_result_related_text(attachment_names)
                ):
                    continue

                envelopes.append(
                    MailEnvelope(
                        uid=uid,
                        subject=subject,
                        sender=sender,
                        message_id=(message.get("Message-ID") or "").strip(),
                        attachments=attachments,
                    )
                )
            return envelopes
        finally:
            try:
                client.close()
            except Exception:
                pass
            client.logout()

    def mark_seen(self, uid: str) -> None:
        if self.provider == "gmail":
            self.gmail.mark_seen(uid)
            return

        client = self._connect()
        try:
            status, _ = client.select(self.mailbox)
            if status != "OK":
                raise RuntimeError(f"Unable to select IMAP mailbox {self.mailbox}.")
            client.uid("store", uid, "+FLAGS", "(\\Seen)")
        finally:
            try:
                client.close()
            except Exception:
                pass
            client.logout()
