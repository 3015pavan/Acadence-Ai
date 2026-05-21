from typing import List, Optional

from pydantic import BaseModel, Field


class AgentLogEntry(BaseModel):
    timestamp: str
    level: str
    message: str


class AgentLogsResponse(BaseModel):
    logs: List[AgentLogEntry] = Field(default_factory=list)


class AgentRunResponse(BaseModel):
    processed_emails: int
    processed_attachments: int
    skipped_emails: int
    failed_emails: int
    status: str


class AgentStatusResponse(BaseModel):
    running: bool
    interval_minutes: int
    status: str
    provider: str = "gmail"
    connected: bool = False
    connected_email: Optional[str] = None
    last_run_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_error: Optional[str] = None
    last_processed_email: Optional[str] = None
    processed_emails_total: int = 0
    failed_emails_total: int = 0
