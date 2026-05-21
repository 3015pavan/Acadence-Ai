import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .mail_reader import MailAttachment


ALLOWED_EXTENSIONS = {".xlsx", ".pdf"}


@dataclass
class SavedAttachment:
    filename: str
    path: Path
    size_bytes: int
    content_type: str


class AttachmentHandler:
    def __init__(self) -> None:
        base_dir = Path(os.getenv("EMAIL_AGENT_TEMP_DIR", tempfile.gettempdir()))
        self.temp_dir = base_dir / "acadence-ai-email-agent"
        self.max_size_bytes = int(float(os.getenv("EMAIL_AGENT_MAX_ATTACHMENT_MB", "15")) * 1024 * 1024)

    def _sanitize_filename(self, filename: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
        return sanitized or "attachment"

    def save_attachments(self, attachments: Iterable[MailAttachment]) -> List[SavedAttachment]:
        saved: List[SavedAttachment] = []
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        for attachment in attachments:
            extension = Path(attachment.filename).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise ValueError(f"Unsupported attachment type for {attachment.filename}.")
            size_bytes = len(attachment.payload)
            if size_bytes == 0:
                raise ValueError(f"Attachment {attachment.filename} is empty.")
            if size_bytes > self.max_size_bytes:
                raise ValueError(f"Attachment {attachment.filename} exceeds the configured size limit.")

            safe_name = self._sanitize_filename(attachment.filename)
            fd, temp_path = tempfile.mkstemp(prefix="mail-", suffix=f"-{safe_name}", dir=self.temp_dir)
            os.close(fd)
            path = Path(temp_path)
            path.write_bytes(attachment.payload)
            saved.append(
                SavedAttachment(
                    filename=attachment.filename,
                    path=path,
                    size_bytes=size_bytes,
                    content_type=attachment.content_type,
                )
            )
        return saved
