from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    source = Column(String(32), nullable=False, default="upload")  # "upload" or "email"

    student_semesters = relationship(
        "StudentSemester",
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    usn = Column(String(128), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    sgpa = Column(Float, nullable=False)

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

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    semester = Column(Integer, nullable=False, default=1)
    sgpa = Column(Float, nullable=False)
    cgpa = Column(Float, nullable=False)

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
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    student_semester_id = Column(Integer, ForeignKey("student_semesters.id", ondelete="CASCADE"), nullable=True)
    subject = Column(String(255), nullable=False)
    grade = Column(String(32), nullable=False)
    gp = Column(Float, nullable=True)

    student = relationship("Student", back_populates="results")
    student_semester = relationship("StudentSemester", back_populates="results")
