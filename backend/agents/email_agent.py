import logging
import os
import time
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
from ..tenant_context import get_current_user_id


LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_FILE = LOG_DIR / "agent.log"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _log_file_for_user(owner_user_id: int | None) -> Path:
    if owner_user_id is None:
        return LOG_FILE
    return LOG_DIR / f"agent_user_{owner_user_id}.log"


def _setup_logger(owner_user_id: int | None = None) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger_name = f"email_agent.{owner_user_id}" if owner_user_id is not None else "email_agent.shared"
    logger = logging.getLogger(logger_name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(_log_file_for_user(owner_user_id), encoding="utf-8")
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
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.lock = Lock()
        self.reader = MailReader()
        self.sender = MailSender()
        self.attachment_handler = AttachmentHandler()
        self.owner_user_id: int | None = None
        self.owner_role: str | None = None
        self.states: Dict[int, AgentState] = {}
        self.default_state = AgentState(
            interval_minutes=int(os.getenv("AGENT_POLL_MINUTES", "5")),
            provider=str(os.getenv("EMAIL_PROVIDER", "gmail")).strip().lower() or "gmail",
            connected=False,
            connected_email=None,
        )

    def _resolve_log_owner_user_id(self) -> int | None:
        return self.owner_user_id

    def _logger(self) -> logging.Logger:
        return _setup_logger(self._resolve_log_owner_user_id())

    def _owner_state_key(self, owner_user_id: int | None = None) -> int:
        resolved_owner_user_id = owner_user_id if owner_user_id is not None else self.owner_user_id
        if resolved_owner_user_id is None:
            resolved_owner_user_id = get_current_user_id()
        if resolved_owner_user_id is None:
            raise RuntimeError("Tenant context is required for agent processing.")
        return int(resolved_owner_user_id)

    def _state_for_owner(self, owner_user_id: int | None = None) -> AgentState:
        owner_key = self._owner_state_key(owner_user_id)
        with self.lock:
            if owner_key not in self.states:
                self.states[owner_key] = AgentState(
                    interval_minutes=self.default_state.interval_minutes,
                    provider=self.default_state.provider,
                    connected=self.default_state.connected,
                    connected_email=self.default_state.connected_email,
                )
            return self.states[owner_key]

    def _update_state(self, owner_user_id: int | None = None, **updates: object) -> None:
        state = self._state_for_owner(owner_user_id)
        with self.lock:
            for key, value in updates.items():
                setattr(state, key, value)

    def _snapshot_state(self, owner_user_id: int | None = None) -> dict:
        state = self._state_for_owner(owner_user_id)
        with self.lock:
            return asdict(state)

    def _set_owner_context(self, owner_user_id: int | None = None, owner_role: str | None = None) -> None:
        self.owner_user_id = owner_user_id
        self.owner_role = (owner_role or "").strip().lower() or None
        self.reader.gmail.set_owner_context(owner_user_id=owner_user_id, owner_role=owner_role)

    def _write_log(self, level: str, message: str, *args: object) -> None:
        logger = self._logger()
        getattr(logger, level)(message, *args)

    def _set_state(self, **updates: object) -> None:
        self._update_state(self.owner_user_id, **updates)

    def start(self, owner_user_id: int | None = None, owner_role: str | None = None) -> Dict[str, object]:
        self._set_owner_context(owner_user_id, owner_role)
        state = self._state_for_owner(owner_user_id)
        if state.provider == "gmail" and not self.reader.is_connected():
            self._set_state(status="error", last_error="Connect Gmail first using the email connection option.")
            raise RuntimeError("Connect Gmail first using the email connection option.")
        owner_key = self._owner_state_key(owner_user_id)
        with self.lock:
            if not self.scheduler.running:
                self.scheduler.start()
            job_id = f"email-agent-poll-{owner_key}"
            self.scheduler.add_job(
                self.run_once,
                "interval",
                minutes=state.interval_minutes,
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            state.running = True
            state.status = "running"
            connection = self.reader.connection_status(owner_user_id=owner_key)
            state.connected = bool(connection.get("connected"))
            state.connected_email = str(connection.get("connected_email") or "") or None
        self._write_log("info", "Agent started with %s minute interval", state.interval_minutes)
        return self.status()

    def stop(self, owner_user_id: int | None = None, owner_role: str | None = None) -> Dict[str, object]:
        self._set_owner_context(owner_user_id, owner_role)
        owner_key = self._owner_state_key(owner_user_id)
        state = self._state_for_owner(owner_key)
        with self.lock:
            job_id = f"email-agent-poll-{owner_key}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            state.running = False
            state.status = "stopped"
        self._write_log("info", "Agent stopped")
        return self.status()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def status(self) -> Dict[str, object]:
        owner_user_id = self._owner_state_key()
        connection = self.reader.connection_status(owner_user_id=owner_user_id)
        state = self._state_for_owner(owner_user_id)
        with self.lock:
            state.connected = bool(connection.get("connected"))
            state.connected_email = str(connection.get("connected_email") or "") or None
            state.provider = str(connection.get("provider") or state.provider)

        # Read persisted processed email stats from DB to ensure UI stays in sync
        db = SessionLocal()
        try:
            processed_count = db.scalar(
                select(func.count()).select_from(AgentProcessedEmail).where(
                    AgentProcessedEmail.status == "processed",
                    AgentProcessedEmail.owner_user_id == owner_user_id,
                )
            )
            last_row = db.execute(
                select(AgentProcessedEmail.subject, AgentProcessedEmail.sender)
                .where(AgentProcessedEmail.status == "processed", AgentProcessedEmail.owner_user_id == owner_user_id)
                .order_by(AgentProcessedEmail.processed_at.desc())
                .limit(1)
            ).first()
            last_processed = None
            if last_row:
                subj, snd = last_row
                last_processed = f"{subj} <{snd}>"
        finally:
            db.close()

        result = self._snapshot_state(owner_user_id)
        result["processed_emails_total"] = int(processed_count or 0)
        result["last_processed_email"] = last_processed or result.get("last_processed_email")
        return result

    def gmail_connect_url(self, owner_user_id: int | None = None, owner_role: str | None = None) -> Dict[str, object]:
        self._set_owner_context(owner_user_id, owner_role)
        state_payload = {
            "user_id": self.owner_user_id,
            "role": self.owner_role,
            "ts": int(time.time()),
        }
        return {"authorization_url": self.reader.gmail.connect_url(state_payload)}

    def gmail_complete_connection(self, code: str, state: str = "") -> Dict[str, object]:
        if state:
            connection_owner = self.reader.gmail.parse_state(state)
            self._set_owner_context(int(connection_owner.get("user_id") or 0) or None, str(connection_owner.get("role") or "").strip().lower() or None)
        owner_key = self._owner_state_key()
        token_data = self.reader.gmail.exchange_code(code, owner_user_id=owner_key)
        connection = self.reader.connection_status(owner_user_id=owner_key)
        self._update_state(
            owner_key,
            provider=str(connection.get("provider") or "gmail"),
            connected=bool(connection.get("connected")),
            connected_email=str(connection.get("connected_email") or token_data.get("email_address") or "") or None,
            last_error=None,
        )
        return {"status": "connected", "connected_email": token_data.get("email_address")}

    def gmail_disconnect(self, owner_user_id: int | None = None, owner_role: str | None = None) -> Dict[str, object]:
        self._set_owner_context(owner_user_id, owner_role)
        owner_key = self._owner_state_key(owner_user_id)
        self.reader.gmail.disconnect(owner_user_id=owner_key)
        self._update_state(owner_key, connected=False, connected_email=None, status="stopped")
        job_id = f"email-agent-poll-{owner_key}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        return self.status()

    def read_logs(self, limit: int = 100, owner_user_id: int | None = None) -> List[Dict[str, str]]:
        log_file = _log_file_for_user(owner_user_id if owner_user_id is not None else self.owner_user_id)
        if not log_file.exists():
            return []
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        entries: List[Dict[str, str]] = []
        for line in lines:
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            entries.append({"timestamp": parts[0], "level": parts[1], "message": parts[2]})
        return entries

    def _already_processed(self, db, uid: str) -> bool:
        owner_user_id = self.owner_user_id if self.owner_user_id is not None else get_current_user_id()
        return (
            db.scalar(
                select(AgentProcessedEmail.id).where(
                    AgentProcessedEmail.email_uid == uid,
                    AgentProcessedEmail.status == "processed",
                    AgentProcessedEmail.owner_user_id == owner_user_id,
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
                owner_user_id=self.owner_user_id if self.owner_user_id is not None else get_current_user_id(),
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
            "The generated PDF report now includes subject analysis, grade analysis, and improved insights, along with the cleaned Excel file.\n"
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
                    owner_user_id=self._owner_state_key(),
                )
                return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"Email send failed after retry: {last_error}") from last_error

    def run_once(self) -> Dict[str, object]:
        owner_user_id = self._owner_state_key()
        self._update_state(owner_user_id, status="running", last_run_at=_utc_now().isoformat(), last_error=None)
        processed_emails = 0
        processed_attachments = 0
        skipped_emails = 0
        failed_emails = 0
        db = SessionLocal()
        try:
            envelopes = self.reader.fetch_unread_result_emails(owner_user_id=owner_user_id)
            if not envelopes:
                self._write_log("info", "No unread result emails found")
            for envelope in envelopes:
                if self._already_processed(db, envelope.uid):
                    skipped_emails += 1
                    self.reader.mark_seen(envelope.uid, owner_user_id=owner_user_id)
                    self._write_log("info", "Skipping already processed email uid=%s subject=%s", envelope.uid, envelope.subject)
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
                    self.reader.mark_seen(envelope.uid, owner_user_id=owner_user_id)
                    self._write_log("error", "Invalid attachment for email uid=%s: %s", envelope.uid, exc)
                    continue

                email_had_success = False
                for attachment in saved_attachments:
                    try:
                        result = run_processing_pipeline(db, attachment, owner_user_id=owner_user_id)
                        self._send_with_retry(
                            recipient=envelope.sender,
                            subject=f"Re: {envelope.subject}",
                            body=self._build_reply_body(result),
                            attachments=[result.report_path, result.processed_excel_path],
                            in_reply_to=envelope.message_id,
                            thread_id=getattr(envelope, "thread_id", "") or "",
                        )
                        self._write_log("info", "Reply sent to recipient=%s for email uid=%s subject=%s", envelope.sender, envelope.uid, envelope.subject)
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
                        self._write_log(
                            "info",
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
                        self._write_log("error", "Processing failed for email uid=%s attachment=%s error=%s", envelope.uid, attachment.filename, exc)
                        break

                self.reader.mark_seen(envelope.uid, owner_user_id=owner_user_id)
                if email_had_success:
                    processed_emails += 1
                    self._update_state(owner_user_id, last_processed_email=f"{envelope.subject} <{envelope.sender}>", last_success_at=_utc_now().isoformat())

            current = self.status()
            self._update_state(
                owner_user_id,
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
            self._update_state(owner_user_id, last_error=str(exc), status="error")
            self._write_log("exception", "Agent run failed: %s", exc)
            raise
        finally:
            db.close()


email_agent = EmailAgentManager()
