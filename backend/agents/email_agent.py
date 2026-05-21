import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, List

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select, func

from ..agent_models import AgentProcessedEmail
from ..database import SessionLocal
from ..services.attachment_handler import AttachmentHandler
from ..services.mail_reader import MailReader
from ..services.mail_sender import MailSender
from ..services.pipeline_runner import PipelineRunResult, run_processing_pipeline


LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_FILE = LOG_DIR / "agent.log"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("email_agent")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s|%(levelname)s|%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class AgentState:
    running: bool = False
    status: str = "stopped"
    interval_minutes: int = 5
    provider: str = "gmail"
    connected: bool = False
    connected_email: str | None = None
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    last_processed_email: str | None = None
    processed_emails_total: int = 0
    failed_emails_total: int = 0


class EmailAgentManager:
    def __init__(self) -> None:
        self.logger = _setup_logger()
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.lock = Lock()
        self.reader = MailReader()
        self.sender = MailSender()
        self.attachment_handler = AttachmentHandler()
        connection = self.reader.connection_status()
        self.state = AgentState(
            interval_minutes=int(os.getenv("AGENT_POLL_MINUTES", "5")),
            provider=str(connection.get("provider") or "gmail"),
            connected=bool(connection.get("connected")),
            connected_email=str(connection.get("connected_email") or "") or None,
        )

    def _set_state(self, **updates: object) -> None:
        with self.lock:
            for key, value in updates.items():
                setattr(self.state, key, value)

    def start(self) -> Dict[str, object]:
        if self.state.provider == "gmail" and not self.reader.is_connected():
            self._set_state(status="error", last_error="Connect Gmail first using the email connection option.")
            raise RuntimeError("Connect Gmail first using the email connection option.")
        with self.lock:
            if not self.scheduler.running:
                self.scheduler.start()
            self.scheduler.add_job(
                self.run_once,
                "interval",
                minutes=self.state.interval_minutes,
                id="email-agent-poll",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            self.state.running = True
            self.state.status = "running"
            connection = self.reader.connection_status()
            self.state.connected = bool(connection.get("connected"))
            self.state.connected_email = str(connection.get("connected_email") or "") or None
        self.logger.info("Agent started with %s minute interval", self.state.interval_minutes)
        return self.status()

    def stop(self) -> Dict[str, object]:
        with self.lock:
            if self.scheduler.get_job("email-agent-poll"):
                self.scheduler.remove_job("email-agent-poll")
            self.state.running = False
            self.state.status = "stopped"
        self.logger.info("Agent stopped")
        return self.status()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def status(self) -> Dict[str, object]:
        connection = self.reader.connection_status()
        with self.lock:
            # update runtime connection info
            self.state.connected = bool(connection.get("connected"))
            self.state.connected_email = str(connection.get("connected_email") or "") or None
            self.state.provider = str(connection.get("provider") or self.state.provider)

        # Read persisted processed email stats from DB to ensure UI stays in sync
        db = SessionLocal()
        try:
            processed_count = db.scalar(
                select(func.count()).select_from(AgentProcessedEmail).where(AgentProcessedEmail.status == "processed")
            )
            last_row = db.execute(
                select(AgentProcessedEmail.subject, AgentProcessedEmail.sender)
                .where(AgentProcessedEmail.status == "processed")
                .order_by(AgentProcessedEmail.processed_at.desc())
                .limit(1)
            ).first()
            last_processed = None
            if last_row:
                subj, snd = last_row
                last_processed = f"{subj} <{snd}>"
        finally:
            db.close()

        with self.lock:
            # prefer persisted counts but fall back to in-memory state
            result = asdict(self.state)
            result["processed_emails_total"] = int(processed_count or 0)
            result["last_processed_email"] = last_processed or result.get("last_processed_email")
            return result

    def gmail_connect_url(self) -> Dict[str, object]:
        return {"authorization_url": self.reader.gmail.connect_url()}

    def gmail_complete_connection(self, code: str) -> Dict[str, object]:
        token_data = self.reader.gmail.exchange_code(code)
        connection = self.reader.connection_status()
        self._set_state(
            provider=str(connection.get("provider") or "gmail"),
            connected=bool(connection.get("connected")),
            connected_email=str(connection.get("connected_email") or token_data.get("email_address") or "") or None,
            last_error=None,
        )
        return {"status": "connected", "connected_email": token_data.get("email_address")}

    def gmail_disconnect(self) -> Dict[str, object]:
        self.reader.gmail.disconnect()
        self._set_state(connected=False, connected_email=None, status="stopped")
        if self.scheduler.get_job("email-agent-poll"):
            self.scheduler.remove_job("email-agent-poll")
        return self.status()

    def read_logs(self, limit: int = 100) -> List[Dict[str, str]]:
        if not LOG_FILE.exists():
            return []
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        entries: List[Dict[str, str]] = []
        for line in lines:
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            entries.append({"timestamp": parts[0], "level": parts[1], "message": parts[2]})
        return entries

    def _already_processed(self, db, uid: str) -> bool:
        return (
            db.scalar(
                select(AgentProcessedEmail.id).where(
                    AgentProcessedEmail.email_uid == uid,
                    AgentProcessedEmail.status == "processed",
                )
            )
            is not None
        )

    def _record_processed_email(
        self,
        *,
        db,
        uid: str,
        sender: str,
        subject: str,
        status: str,
        attachment_name: str | None = None,
        dataset_hash: str | None = None,
        report_path: str | None = None,
        message_id: str = "",
        error_message: str | None = None,
    ) -> None:
        db.add(
            AgentProcessedEmail(
                email_uid=uid,
                message_id=message_id or None,
                sender=sender,
                subject=subject,
                status=status,
                attachment_name=attachment_name,
                dataset_hash=dataset_hash,
                report_path=report_path,
                error_message=error_message,
            )
        )
        db.commit()

    def _build_reply_body(self, result: PipelineRunResult) -> str:
        duplicate_line = "This dataset was already processed earlier, so no duplicate insert was made.\n\n" if result.duplicate_dataset else ""
        return (
            "Hello,\n\n"
            f"{duplicate_line}"
            "The result processing pipeline completed successfully.\n\n"
            f"Topper: {result.topper_name} ({result.topper_sgpa:.2f})\n"
            f"Average SGPA: {result.average_sgpa:.2f}\n"
            f"Fail Count: {result.failed_count}\n"
            f"Total Students: {result.total_students}\n\n"
            "The generated PDF report and cleaned Excel file are attached.\n"
        )

    def _send_with_retry(self, *, recipient: str, subject: str, body: str, attachments: List[Path], in_reply_to: str, thread_id: str = "") -> None:
        attempts = 2
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                self.sender.send_reply(
                    recipient=recipient,
                    subject=subject,
                    body=body,
                    attachments=attachments,
                    in_reply_to=in_reply_to,
                    thread_id=thread_id,
                )
                return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"Email send failed after retry: {last_error}") from last_error

    def run_once(self) -> Dict[str, object]:
        self._set_state(status="running", last_run_at=_utc_now().isoformat(), last_error=None)
        processed_emails = 0
        processed_attachments = 0
        skipped_emails = 0
        failed_emails = 0
        db = SessionLocal()
        try:
            envelopes = self.reader.fetch_unread_result_emails()
            if not envelopes:
                self.logger.info("No unread result emails found")
            for envelope in envelopes:
                if self._already_processed(db, envelope.uid):
                    skipped_emails += 1
                    self.reader.mark_seen(envelope.uid)
                    self.logger.info("Skipping already processed email uid=%s subject=%s", envelope.uid, envelope.subject)
                    continue

                try:
                    saved_attachments = self.attachment_handler.save_attachments(envelope.attachments)
                except Exception as exc:
                    failed_emails += 1
                    self._record_processed_email(
                        db=db,
                        uid=envelope.uid,
                        sender=envelope.sender,
                        subject=envelope.subject,
                        status="invalid_attachment",
                        message_id=envelope.message_id,
                        error_message=str(exc),
                    )
                    self.reader.mark_seen(envelope.uid)
                    self.logger.error("Invalid attachment for email uid=%s: %s", envelope.uid, exc)
                    continue

                email_had_success = False
                for attachment in saved_attachments:
                    try:
                        result = run_processing_pipeline(db, attachment)
                        self._send_with_retry(
                            recipient=envelope.sender,
                            subject=f"Re: {envelope.subject}",
                            body=self._build_reply_body(result),
                            attachments=[result.report_path, result.processed_excel_path],
                            in_reply_to=envelope.message_id,
                            thread_id=getattr(envelope, "thread_id", "") or "",
                        )
                        self.logger.info("Reply sent to recipient=%s for email uid=%s subject=%s", envelope.sender, envelope.uid, envelope.subject)
                        self._record_processed_email(
                            db=db,
                            uid=envelope.uid,
                            sender=envelope.sender,
                            subject=envelope.subject,
                            status="processed",
                            attachment_name=attachment.filename,
                            dataset_hash=result.dataset_hash,
                            report_path=str(result.report_path),
                            message_id=envelope.message_id,
                        )
                        processed_attachments += 1
                        email_had_success = True
                        self.logger.info(
                            "Processed email uid=%s attachment=%s dataset=%s duplicate=%s",
                            envelope.uid,
                            attachment.filename,
                            result.dataset_name,
                            result.duplicate_dataset,
                        )
                    except Exception as exc:
                        failed_emails += 1
                        self._record_processed_email(
                            db=db,
                            uid=envelope.uid,
                            sender=envelope.sender,
                            subject=envelope.subject,
                            status="failed",
                            attachment_name=attachment.filename,
                            message_id=envelope.message_id,
                            error_message=str(exc),
                        )
                        self.logger.error("Processing failed for email uid=%s attachment=%s error=%s", envelope.uid, attachment.filename, exc)
                        break

                self.reader.mark_seen(envelope.uid)
                if email_had_success:
                    processed_emails += 1
                    self._set_state(last_processed_email=f"{envelope.subject} <{envelope.sender}>", last_success_at=_utc_now().isoformat())

            current = self.status()
            self._set_state(
                processed_emails_total=current["processed_emails_total"] + processed_emails,
                failed_emails_total=current["failed_emails_total"] + failed_emails,
                status="running" if current["running"] else "stopped",
            )
            return {
                "processed_emails": processed_emails,
                "processed_attachments": processed_attachments,
                "skipped_emails": skipped_emails,
                "failed_emails": failed_emails,
                "status": "completed",
            }
        except Exception as exc:
            self._set_state(last_error=str(exc), status="error")
            self.logger.exception("Agent run failed: %s", exc)
            raise
        finally:
            db.close()


email_agent = EmailAgentManager()
