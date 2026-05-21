from sqlalchemy import Column, DateTime, Integer, String, Text, func

from .database import Base


class AgentProcessedEmail(Base):
    __tablename__ = "agent_processed_emails"

    id = Column(Integer, primary_key=True, index=True)
    email_uid = Column(String(255), nullable=False, index=True)
    message_id = Column(String(512), nullable=True, index=True)
    sender = Column(String(512), nullable=False)
    subject = Column(String(512), nullable=False)
    status = Column(String(64), nullable=False, index=True)
    attachment_name = Column(String(512), nullable=True)
    dataset_hash = Column(String(128), nullable=True, index=True)
    report_path = Column(String(1024), nullable=True)
    error_message = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentProcessedDataset(Base):
    __tablename__ = "agent_processed_datasets"

    id = Column(Integer, primary_key=True, index=True)
    dataset_hash = Column(String(128), unique=True, nullable=False, index=True)
    dataset_name = Column(String(255), unique=True, nullable=False, index=True)
    source_filename = Column(String(512), nullable=False)
    processed_excel_path = Column(String(1024), nullable=False)
    report_path = Column(String(1024), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
