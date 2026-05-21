import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Dict, List, Optional

import requests

from ..tenant_context import get_current_user_id, get_current_user_role


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]
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
AUTH_SECRET = os.getenv("AUTH_SECRET") or os.getenv("JWT_SECRET") or "acadence-dev-secret-change-me"


def _is_result_related_subject(subject: str) -> bool:
    normalized = (subject or "").lower()
    return any(keyword in normalized for keyword in SUBJECT_KEYWORDS)


def _is_result_related_text(text: str) -> bool:
    normalized = (text or "").lower()
    return any(keyword in normalized for keyword in SUBJECT_KEYWORDS)


@dataclass
class GmailAttachmentPayload:
    filename: str
    content_type: str
    payload: bytes


@dataclass
class GmailMessageEnvelope:
    uid: str
    subject: str
    sender: str
    message_id: str
    thread_id: str
    attachments: List[GmailAttachmentPayload] = field(default_factory=list)


class GmailOAuthClient:
    def __init__(self, owner_user_id: int | None = None, owner_role: str | None = None) -> None:
        self.client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/agent/gmail/callback").strip()
        self.scopes = os.getenv("GOOGLE_GMAIL_SCOPES", " ").split() if os.getenv("GOOGLE_GMAIL_SCOPES") else DEFAULT_SCOPES
        self.owner_user_id = owner_user_id
        self.owner_role = (owner_role or "").strip().lower() or None
        token_path = os.getenv("GMAIL_TOKEN_PATH", "").strip()
        if token_path:
            self.token_path = Path(token_path)
        else:
            self.token_path = Path(__file__).resolve().parents[1] / "storage" / "gmail_token.json"

    def set_owner_context(self, owner_user_id: int | None = None, owner_role: str | None = None) -> None:
        self.owner_user_id = owner_user_id
        self.owner_role = (owner_role or "").strip().lower() or None

    def _resolved_owner_user_id(self) -> int | None:
        current_user_id = get_current_user_id()
    def _resolved_owner_user_id(self, owner_user_id: int | None = None) -> int | None:
        if owner_user_id is not None:
            return owner_user_id
        current_user_id = get_current_user_id()
        if current_user_id is not None:
            return current_user_id
        return self.owner_user_id

    def _resolved_owner_role(self) -> str | None:
        current_user_role = get_current_user_role()
        if current_user_role:
            return current_user_role
        return self.owner_role

    def _resolve_token_path(self, owner_user_id: int | None = None) -> Path:
        owner_user_id = self._resolved_owner_user_id(owner_user_id)
        if owner_user_id is None:
            return self.token_path
        return self.token_path.with_name(f"{self.token_path.stem}_user_{owner_user_id}{self.token_path.suffix}")

    def _encode_state(self, payload: Dict[str, object]) -> str:
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        data_segment = base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")
        signature = hmac.new(AUTH_SECRET.encode("utf-8"), data_segment.encode("ascii"), hashlib.sha256).digest()
        signature_segment = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        return f"{data_segment}.{signature_segment}"

    def _decode_state(self, state: str) -> Dict[str, object]:
        try:
            data_segment, signature_segment = state.split(".", 1)
        except ValueError as exc:
            raise RuntimeError("Invalid Gmail OAuth state.") from exc

        expected = hmac.new(AUTH_SECRET.encode("utf-8"), data_segment.encode("ascii"), hashlib.sha256).digest()
        signature_padding = "=" * (-len(signature_segment) % 4)
        signature = base64.urlsafe_b64decode(signature_segment + signature_padding)
        if not hmac.compare_digest(expected, signature):
            raise RuntimeError("Invalid Gmail OAuth state.")

        data_padding = "=" * (-len(data_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(data_segment + data_padding).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid Gmail OAuth state.")
        return payload

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def _read_token_data(self, owner_user_id: int | None = None) -> Dict[str, object]:
        token_path = self._resolve_token_path(owner_user_id)
        if not token_path.exists():
            return {}
        try:
            return json.loads(token_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_token_data(self, data: Dict[str, object], owner_user_id: int | None = None) -> None:
        token_path = self._resolve_token_path(owner_user_id)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def disconnect(self, owner_user_id: int | None = None) -> None:
        token_path = self._resolve_token_path(owner_user_id)
        if token_path.exists():
            token_path.unlink()

    def connect_url(self, state_payload: Dict[str, object] | None = None) -> str:
        if not self.is_configured():
            raise RuntimeError("Google OAuth is not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI.")
        scope = " ".join(self.scopes)
        payload = state_payload or {
            "user_id": self._resolved_owner_user_id(),
            "role": self._resolved_owner_role(),
            "ts": int(time.time()),
        }
        if payload.get("user_id") is None:
            raise RuntimeError("Unable to determine the Gmail owner for this connection.")
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": scope,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": self._encode_state(payload),
        }
        request = requests.Request("GET", GOOGLE_AUTH_URL, params=params).prepare()
        return request.url

    def parse_state(self, state: str) -> Dict[str, object]:
        payload = self._decode_state(state)
        owner_user_id = int(payload.get("user_id") or 0)
        if owner_user_id <= 0:
            raise RuntimeError("Invalid Gmail OAuth state.")
        return {
            "user_id": owner_user_id,
            "role": str(payload.get("role") or "").strip().lower() or None,
        }

    def exchange_code(self, code: str, owner_user_id: int | None = None) -> Dict[str, object]:
        if not self.is_configured():
            raise RuntimeError("Google OAuth is not configured.")
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Google token exchange failed: {response.text}")

        payload = response.json()
        expires_in = int(payload.get("expires_in") or 0)
        payload["expires_at"] = int(time.time()) + max(expires_in - 60, 0)
        if "refresh_token" not in payload:
            current = self._read_token_data(owner_user_id)
            if current.get("refresh_token"):
                payload["refresh_token"] = current["refresh_token"]
        profile = self.get_profile(payload.get("access_token") or "")
        payload["email_address"] = profile.get("emailAddress") or profile.get("email_address")
        self._write_token_data(payload, owner_user_id)
        return payload

    def _refresh_access_token(self, refresh_token: str) -> Dict[str, object]:
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Google access token refresh failed: {response.text}")
        payload = response.json()
        expires_in = int(payload.get("expires_in") or 0)
        payload["expires_at"] = int(time.time()) + max(expires_in - 60, 0)
        payload["refresh_token"] = refresh_token
        return payload

    def get_token_data(self, refresh: bool = True, owner_user_id: int | None = None) -> Dict[str, object]:
        token_data = self._read_token_data(owner_user_id)
        if not token_data:
            return {}

        access_token = str(token_data.get("access_token") or "")
        expires_at = int(token_data.get("expires_at") or 0)
        refresh_token = str(token_data.get("refresh_token") or "")

        if refresh and refresh_token and (not access_token or time.time() >= expires_at):
            refreshed = self._refresh_access_token(refresh_token)
            refreshed = self._refresh_access_token(refresh_token)
            token_data.update(refreshed)
            token_data["refresh_token"] = refresh_token
            if not token_data.get("email_address"):
                profile = self.get_profile(str(token_data.get("access_token") or refreshed.get("access_token") or ""))
                token_data["email_address"] = profile.get("emailAddress") or profile.get("email_address")
            self._write_token_data(token_data, owner_user_id)

        return token_data

    def is_connected(self, owner_user_id: int | None = None) -> bool:
        return bool(self.get_token_data(owner_user_id=owner_user_id))

    def get_profile(self, access_token: str = "", owner_user_id: int | None = None) -> Dict[str, object]:
        if not access_token:
            token_data = self.get_token_data(owner_user_id=owner_user_id)
            access_token = str(token_data.get("access_token") or "")
        if not access_token:
            return {}
        response = requests.get(
            f"{GOOGLE_GMAIL_API}/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Unable to fetch Gmail profile: {response.text}")
        return response.json()

    def _authorized_headers(self, owner_user_id: int | None = None) -> Dict[str, str]:
        token_data = self.get_token_data(owner_user_id=owner_user_id)
        access_token = str(token_data.get("access_token") or "")
        if not access_token:
            raise RuntimeError("Gmail account is not connected. Use the email connection option to authorize access.")
        return {"Authorization": f"Bearer {access_token}"}

    def _api_get(self, path: str, params: Optional[Dict[str, object]] = None, owner_user_id: int | None = None) -> Dict[str, object]:
        response = requests.get(
            f"{GOOGLE_GMAIL_API}{path}",
            headers=self._authorized_headers(owner_user_id),
            params=params,
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Gmail API request failed: {response.text}")
        return response.json()

    def _api_post(self, path: str, payload: Dict[str, object], owner_user_id: int | None = None) -> Dict[str, object]:
        response = requests.post(
            f"{GOOGLE_GMAIL_API}{path}",
            headers={**self._authorized_headers(owner_user_id), "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Gmail API request failed: {response.text}")
        return response.json() if response.text else {}

    def _decode_header_value(self, headers: List[Dict[str, str]], name: str) -> str:
        for header in headers:
            if header.get("name", "").lower() == name.lower():
                return str(header.get("value") or "").strip()
        return ""

    def _preferred_sender_address(self, headers: List[Dict[str, str]], connected_email: str = "") -> str:
        reply_to = self._decode_header_value(headers, "Reply-To")
        from_header = self._decode_header_value(headers, "From")
        sender_header = self._decode_header_value(headers, "Sender")
        return_path = self._decode_header_value(headers, "Return-Path")
        for candidate in (reply_to, from_header, sender_header, return_path):
            address = parseaddr(candidate)[1].strip()
            if address:
                if connected_email and address.lower() == connected_email.lower() and any((reply_to, sender_header, return_path)):
                    continue
                return address
        return parseaddr(reply_to or from_header or sender_header or return_path)[1].strip()

    def _collect_attachments(self, message_id: str, payload: Dict[str, object]) -> List[GmailAttachmentPayload]:
        attachments: List[GmailAttachmentPayload] = []
        parts = payload.get("parts") or []
        for part in parts:
            attachments.extend(self._collect_attachments(message_id, part))

        filename = str(payload.get("filename") or "").strip()
        body = payload.get("body") or {}
        attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
        if filename and attachment_id:
            attachment = self._api_get(f"/messages/{message_id}/attachments/{attachment_id}")
            data = str(attachment.get("data") or "")
            if data:
                payload_bytes = base64.urlsafe_b64decode(data.encode("utf-8") + b"==")
            else:
                payload_bytes = b""
            attachments.append(
                GmailAttachmentPayload(
                    filename=filename,
                    content_type=str(payload.get("mimeType") or "application/octet-stream"),
                    payload=payload_bytes,
                )
            )
        return attachments

    def fetch_unread_result_emails(self, owner_user_id: int | None = None) -> List[GmailMessageEnvelope]:
        token_data = self.get_token_data(owner_user_id=owner_user_id)
        if not token_data:
            raise RuntimeError("Gmail account is not connected. Use the email connection option to authorize access.")
        connected_email = str(token_data.get("email_address") or "").strip()

        query = "has:attachment newer_than:60d"
        payload = self._api_get("/messages", params={"q": query, "maxResults": 20, "labelIds": "INBOX"}, owner_user_id=owner_user_id)
        messages = payload.get("messages") or []

        envelopes: List[GmailMessageEnvelope] = []
        for message in messages:
            message_id = str(message.get("id") or "").strip()
            if not message_id:
                continue
            full_message = self._api_get(f"/messages/{message_id}", params={"format": "full"}, owner_user_id=owner_user_id)
            headers = full_message.get("payload", {}).get("headers") or []
            subject = self._decode_header_value(headers, "Subject")
            sender = self._preferred_sender_address(headers, connected_email=connected_email)
            message_id_header = self._decode_header_value(headers, "Message-ID") or message_id
            thread_id = str(full_message.get("threadId") or message_id)
            snippet = str(full_message.get("snippet") or "")
            if not sender or not subject:
                continue
            if connected_email and sender.lower() == connected_email.lower():
                continue
            attachments = self._collect_attachments(message_id, full_message.get("payload", {}))
            if not attachments:
                continue
            attachment_names = " ".join(attachment.filename for attachment in attachments)
            if not (
                _is_result_related_subject(subject)
                or _is_result_related_text(snippet)
                or _is_result_related_text(attachment_names)
            ):
                continue
            envelopes.append(
                GmailMessageEnvelope(
                    uid=message_id,
                    subject=subject,
                    sender=sender,
                    message_id=message_id_header,
                    thread_id=thread_id,
                    attachments=attachments,
                )
            )
        return envelopes

    def mark_seen(self, uid: str, owner_user_id: int | None = None) -> None:
        self._api_post(f"/messages/{uid}/modify", {"removeLabelIds": ["UNREAD"]}, owner_user_id=owner_user_id)

    def send_reply(self, *, recipient: str, subject: str, body: str, attachments: List[Path], in_reply_to: str = "", thread_id: str = "", owner_user_id: int | None = None) -> None:
        token_data = self.get_token_data(owner_user_id=owner_user_id)
        if not token_data:
            raise RuntimeError("Gmail account is not connected. Use the email connection option to authorize access.")

        message = EmailMessage()
        from_address = str(token_data.get("email_address") or "")
        if from_address:
            message["From"] = from_address
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

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8").rstrip("=")
        payload = {"raw": raw_message}
        if thread_id:
            payload["threadId"] = thread_id
        self._api_post("/messages/send", payload, owner_user_id=owner_user_id)
