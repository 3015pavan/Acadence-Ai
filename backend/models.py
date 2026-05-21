import os

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .database import Base

try:
    from pgvector.sqlalchemy import Vector as PGVector  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    PGVector = None


def _pgvector_enabled() -> bool:
    return os.getenv("PGVECTOR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _vector_type(dimension: int = 384):
    return PGVector(dimension) if PGVector is not None and _pgvector_enabled() else JSON


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(512), nullable=False)
    role = Column(String(32), nullable=False, default="teacher", index=True)
    display_name = Column(String(255), nullable=False)
    tenant_key = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    datasets = relationship("Dataset", back_populates="owner", cascade="all, delete-orphan", passive_deletes=True)
    students = relationship("Student", back_populates="owner", cascade="all, delete-orphan", passive_deletes=True)
    semantic_documents = relationship("SemanticDocument", back_populates="owner", cascade="all, delete-orphan", passive_deletes=True)


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_dataset_owner_name"),)

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    source = Column(String(32), nullable=False, default="upload")  # "upload" or "email"
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owner = relationship("User", back_populates="datasets")
    student_semesters = relationship(
        "StudentSemester",
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("owner_user_id", "usn", name="uq_student_owner_usn"),)

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    usn = Column(String(128), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    sgpa = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owner = relationship("User", back_populates="students")
    student_semesters = relationship(
        "StudentSemester",
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    results = relationship(
        "Result",
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def latest_cgpa(self):
        if not self.student_semesters:
            return self.sgpa
        latest_semester = max(self.student_semesters, key=lambda semester: semester.semester or 0)
        return latest_semester.cgpa if latest_semester.cgpa is not None else self.sgpa


class StudentSemester(Base):
    __tablename__ = "student_semesters"
    __table_args__ = (UniqueConstraint("owner_user_id", "student_id", "dataset_id", "semester", name="uq_semester_owner_student_dataset"),)

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    semester = Column(Integer, nullable=False, default=1)
    sgpa = Column(Float, nullable=False)
    cgpa = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    student = relationship("Student", back_populates="student_semesters")
    dataset = relationship("Dataset", back_populates="student_semesters")
    results = relationship(
        "Result",
        back_populates="student_semester",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Result(Base):
    __tablename__ = "results"

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    student_semester_id = Column(Integer, ForeignKey("student_semesters.id", ondelete="CASCADE"), nullable=True)
    subject = Column(String(255), nullable=False)
    grade = Column(String(32), nullable=False)
    gp = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    student = relationship("Student", back_populates="results")
    student_semester = relationship("StudentSemester", back_populates="results")


class SemanticDocument(Base):
    __tablename__ = "semantic_documents"
    __table_args__ = (UniqueConstraint("owner_user_id", "content_hash", name="uq_semantic_document_owner_hash"),)

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=True, index=True)
    content_type = Column(String(64), nullable=False, default="student")
    content = Column(Text, nullable=False)
    content_hash = Column(String(128), nullable=False, index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    embedding = Column(_vector_type(), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owner = relationship("User", back_populates="semantic_documents")
