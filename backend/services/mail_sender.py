import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from .gmail_client import GmailOAuthClient


class MailSender:
    def __init__(self) -> None:
        self.provider = os.getenv("EMAIL_PROVIDER", "gmail").strip().lower()
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.username = os.getenv("EMAIL_USER", "").strip()
        self.password = os.getenv("EMAIL_PASS", "").strip()
        self.from_address = os.getenv("SMTP_FROM", self.username).strip()
        self.use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false"
        self.gmail = GmailOAuthClient()

    def send_reply(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachments: Iterable[Path],
        in_reply_to: str = "",
        thread_id: str = "",
    ) -> None:
        if self.provider == "gmail":
            self.gmail.send_reply(
                recipient=recipient,
                subject=subject,
                body=body,
                attachments=list(attachments),
                in_reply_to=in_reply_to,
                thread_id=thread_id,
            )
            return

        if not self.host or not self.username or not self.password or not self.from_address:
            raise RuntimeError("SMTP configuration is incomplete. Set SMTP_HOST, EMAIL_USER, EMAIL_PASS, and SMTP_FROM.")

        message = EmailMessage()
        message["From"] = self.from_address
        message["To"] = recipient
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)

        for attachment in attachments:
            path = Path(attachment)
            payload = path.read_bytes()
            maintype = "application"
            subtype = "octet-stream"
            if path.suffix.lower() == ".pdf":
                subtype = "pdf"
            elif path.suffix.lower() == ".xlsx":
                subtype = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=path.name)

        if self.use_tls:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as server:
                server.login(self.username, self.password)
                server.send_message(message)
