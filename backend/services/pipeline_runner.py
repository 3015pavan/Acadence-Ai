import hashlib
import logging
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..agent_models import AgentProcessedDataset
from ..models import Dataset, Result, Student, StudentSemester
from .attachment_handler import SavedAttachment
from .analyzer import fetch_students
from .elastic import get_elasticsearch_client, sync_students
from .intelligence import ensure_query_index
from .parser import ParsedStudent, parse_uploaded_file
from .reporting import build_insights
from .analyzer import persist_students, save_processed_excel
from .metrics import log_event


STORAGE_ROOT = Path(__file__).resolve().parents[1] / "storage"
AGENT_OUTPUT_DIR = STORAGE_ROOT / "agent_outputs"
logger = logging.getLogger(__name__)


@dataclass
class PipelineRunResult:
    dataset_hash: str
    dataset_name: str
    total_students: int
    average_sgpa: float
    failed_count: int
    topper_name: str
    topper_sgpa: float
    report_path: Path
    processed_excel_path: Path
    duplicate_dataset: bool


@dataclass
class DatasetStudentRecord:
    usn: str
    name: str
    sgpa: float
    results: List[Result]


def _dataset_hash(students: List[ParsedStudent]) -> str:
    key = "|".join(
        f"{student.usn}:{student.semester}:{student.sgpa:.2f}:{student.pass_fail}"
        for student in sorted(students, key=lambda item: (item.usn, item.semester))
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _fetch_dataset(db: Session, dataset_name: str) -> Dataset:
    dataset = db.scalar(select(Dataset).where(Dataset.name == dataset_name))
    if dataset is None:
        raise RuntimeError(f"Dataset {dataset_name} was not created.")
    return dataset


def _dataset_records(db: Session, dataset_name: str) -> List[DatasetStudentRecord]:
    dataset = _fetch_dataset(db, dataset_name)
    stmt = (
        select(StudentSemester)
        .options(
            selectinload(StudentSemester.student),
            selectinload(StudentSemester.results),
        )
        .where(StudentSemester.dataset_id == dataset.id)
    )
    semesters = list(db.scalars(stmt).all())
    records = [
        DatasetStudentRecord(
            usn=semester.student.usn,
            name=semester.student.name,
            sgpa=float(semester.sgpa or 0.0),
            results=list(semester.results),
        )
        for semester in semesters
        if semester.student is not None
    ]
    records.sort(key=lambda item: (-item.sgpa, item.name))
    return records


def _build_summary(students: List[DatasetStudentRecord]) -> Dict[str, object]:
    rows = []
    for student in students:
        has_fail = any((result.grade or "").upper() == "F" for result in student.results)
        rows.append(
            {
                "usn": student.usn,
                "name": student.name,
                "sgpa": student.sgpa,
                "pass_fail": "FAIL" if has_fail else "PASS",
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("No students were found for the processed dataset.")

    frame = frame.sort_values(by=["sgpa", "name"], ascending=[False, True], kind="stable")
    topper_row = frame.iloc[0]
    average_sgpa = round(float(frame["sgpa"].mean()), 2)
    failed_count = int((frame["pass_fail"] == "FAIL").sum())
    return {
        "topper_usn": str(topper_row["usn"]),
        "topper_name": str(topper_row["name"]),
        "topper_sgpa": float(topper_row["sgpa"]),
        "average_sgpa": average_sgpa,
        "total_students": int(len(frame)),
        "failed_count": failed_count,
    }


def _grade_distribution(students: List[DatasetStudentRecord]) -> Dict[str, int]:
    rows = []
    for student in students:
        for result in student.results:
            rows.append({"grade": (result.grade or "NA").upper()})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {}
    counts = frame["grade"].value_counts().sort_index()
    return {str(index): int(value) for index, value in counts.items()}


def _generate_report_pdf(students: List[DatasetStudentRecord], summary: Dict[str, object]) -> bytes:
    top_students = fetch_top_students_for_list(students, 5)
    failed_students = [student for student in students if any((result.grade or "").upper() == "F" for result in student.results)]
    distribution = _grade_distribution(students)
    topper = next((student for student in students if student.usn == summary["topper_usn"]), None)
    insight_summary = {
        "topper": topper,
        "average_sgpa": summary["average_sgpa"],
        "total_students": summary["total_students"],
        "failed_count": summary["failed_count"],
    }
    insights = build_insights(insight_summary, top_students, failed_students, distribution)

    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Autonomous Email Result Report", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Summary", styles["Heading2"]))
    summary_table = Table(
        [
            ["Metric", "Value"],
            ["Topper", summary["topper_name"]],
            ["Topper SGPA", f"{float(summary['topper_sgpa']):.2f}"],
            ["Average SGPA", f"{float(summary['average_sgpa']):.2f}"],
            ["Total Students", str(summary["total_students"])],
            ["Failed Count", str(summary["failed_count"])],
        ],
        colWidths=[180, 300],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#ecfeff")]),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 16))

    story.append(Paragraph("Insights", styles["Heading2"]))
    for insight in insights:
        story.append(Paragraph(insight, styles["BodyText"]))
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Top 5 Students", styles["Heading2"]))
    top_table = Table(
        [["USN", "Name", "SGPA"]] + [[student.usn, student.name, f"{float(student.sgpa):.2f}"] for student in top_students],
        colWidths=[120, 270, 90],
    )
    top_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(top_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Grade Distribution", styles["Heading2"]))
    distribution_rows = [["Grade", "Count"]] + [[grade, str(count)] for grade, count in distribution.items()]
    distribution_table = Table(distribution_rows, colWidths=[150, 120])
    distribution_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8fafc"), colors.white]),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(distribution_table)

    document.build(story)
    return buffer.getvalue()


def fetch_top_students_for_list(students: List[DatasetStudentRecord], limit: int) -> List[DatasetStudentRecord]:
    return sorted(students, key=lambda student: (-student.sgpa, student.name))[:limit]


def run_processing_pipeline(db: Session, attachment: SavedAttachment) -> PipelineRunResult:
    start = time.perf_counter()
    file_bytes = attachment.path.read_bytes()
    try:
        parse_start = time.perf_counter()
        parsed_students, processed_df = parse_uploaded_file(file_bytes, attachment.filename)
        parse_ms = int((time.perf_counter() - parse_start) * 1000)

        dataset_hash = _dataset_hash(parsed_students)

        existing = db.scalar(select(AgentProcessedDataset).where(AgentProcessedDataset.dataset_hash == dataset_hash))
        if existing:
            students = _dataset_records(db, existing.dataset_name)
            summary = _build_summary(students)
            log_event(
                "ingestion",
                {
                    "source": "email",
                    "filename": attachment.filename,
                    "dataset_name": existing.dataset_name,
                    "dataset_hash": dataset_hash,
                    "rows": int(len(processed_df)),
                    "duplicate_dataset": True,
                    "parse_ms": parse_ms,
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                    "success": True,
                },
            )
            return PipelineRunResult(
                dataset_hash=existing.dataset_hash,
                dataset_name=existing.dataset_name,
                total_students=summary["total_students"],
                average_sgpa=summary["average_sgpa"],
                failed_count=summary["failed_count"],
                topper_name=summary["topper_name"],
                topper_sgpa=summary["topper_sgpa"],
                report_path=Path(existing.report_path),
                processed_excel_path=Path(existing.processed_excel_path),
                duplicate_dataset=True,
            )

        dataset_name = f"email-{Path(attachment.filename).stem[:24]}-{dataset_hash[:12]}"
        AGENT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        dataset_dir = AGENT_OUTPUT_DIR / dataset_name
        dataset_dir.mkdir(parents=True, exist_ok=True)
        processed_excel_path = dataset_dir / "processed_results.xlsx"
        report_path = dataset_dir / "result_report.pdf"

        persist_start = time.perf_counter()
        persist_students(db, parsed_students, dataset_name=dataset_name)
        save_processed_excel(processed_df, processed_excel_path)
        persist_ms = int((time.perf_counter() - persist_start) * 1000)

        report_start = time.perf_counter()
        students = _dataset_records(db, dataset_name)
        summary = _build_summary(students)
        report_path.write_bytes(_generate_report_pdf(students, summary))
        report_ms = int((time.perf_counter() - report_start) * 1000)

        all_students = fetch_students(db)
        try:
            elastic_client = get_elasticsearch_client()
            sync_students(elastic_client, all_students)
        except Exception as exc:
            logger.warning("Skipping Elasticsearch sync for email dataset %s: %s", dataset_name, exc)

        try:
            ensure_query_index(all_students)
        except Exception as exc:
            logger.warning("Skipping query index refresh for email dataset %s: %s", dataset_name, exc)

        db.add(
            AgentProcessedDataset(
                dataset_hash=dataset_hash,
                dataset_name=dataset_name,
                source_filename=attachment.filename,
                processed_excel_path=str(processed_excel_path),
                report_path=str(report_path),
            )
        )
        db.commit()

        log_event(
            "ingestion",
            {
                "source": "email",
                "filename": attachment.filename,
                "dataset_name": dataset_name,
                "dataset_hash": dataset_hash,
                "rows": int(len(processed_df)),
                "duplicate_dataset": False,
                "parse_ms": parse_ms,
                "persist_ms": persist_ms,
                "report_ms": report_ms,
                "duration_ms": int((time.perf_counter() - start) * 1000),
                "success": True,
            },
        )

        return PipelineRunResult(
            dataset_hash=dataset_hash,
            dataset_name=dataset_name,
            total_students=summary["total_students"],
            average_sgpa=summary["average_sgpa"],
            failed_count=summary["failed_count"],
            topper_name=summary["topper_name"],
            topper_sgpa=summary["topper_sgpa"],
            report_path=report_path,
            processed_excel_path=processed_excel_path,
            duplicate_dataset=False,
        )
    except Exception as exc:
        log_event(
            "ingestion",
            {
                "source": "email",
                "filename": attachment.filename,
                "success": False,
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - start) * 1000),
            },
        )
        raise
