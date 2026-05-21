import io
from collections import Counter
from typing import List

from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from .analyzer import build_summary, compute_grade_distribution, fetch_failed_students, fetch_students, fetch_top_students
from .intelligence import _llm_chat_json

try:
    from langchain_core.prompts import PromptTemplate
except ImportError:
    PromptTemplate = None


load_dotenv()


def _iter_student_results(student) -> list:
    semesters = list(getattr(student, "student_semesters", []) or [])
    if semesters:
        results = []
        for semester in semesters:
            results.extend(list(getattr(semester, "results", []) or []))
        return results
    return list(getattr(student, "results", []) or [])


def _normalize_grade(value: object) -> str:
    return str(value or "NA").strip().upper() or "NA"


def build_subject_analysis(students: list) -> dict:
    subject_stats: dict[str, dict[str, object]] = {}
    for student in students:
        for result in _iter_student_results(student):
            subject = str(getattr(result, "subject", "") or "").strip() or "Unknown"
            grade = _normalize_grade(getattr(result, "grade", None))
            gp_value = getattr(result, "gp", None)
            bucket = subject_stats.setdefault(
                subject,
                {"attempts": 0, "fails": 0, "grade_counts": Counter(), "gp_total": 0.0, "gp_count": 0},
            )
            bucket["attempts"] = int(bucket["attempts"]) + 1
            bucket["grade_counts"][grade] += 1
            if grade in {"F", "FAIL", "RA", "AB", "WH"}:
                bucket["fails"] = int(bucket["fails"]) + 1
            if gp_value is not None:
                try:
                    bucket["gp_total"] = float(bucket["gp_total"]) + float(gp_value)
                    bucket["gp_count"] = int(bucket["gp_count"]) + 1
                except Exception:
                    pass

    rows = []
    for subject, data in subject_stats.items():
        attempts = int(data["attempts"])
        fails = int(data["fails"])
        grade_counts: Counter = data["grade_counts"]  # type: ignore[assignment]
        dominant_grade = grade_counts.most_common(1)[0][0] if grade_counts else "NA"
        avg_gp = (float(data["gp_total"]) / int(data["gp_count"])) if int(data["gp_count"]) else None
        rows.append(
            {
                "subject": subject,
                "attempts": attempts,
                "fails": fails,
                "fail_rate": (fails / attempts * 100.0) if attempts else 0.0,
                "avg_gp": avg_gp,
                "dominant_grade": dominant_grade,
            }
        )

    rows.sort(
        key=lambda item: (
            -float(item["avg_gp"]) if item["avg_gp"] is not None else 1e9,
            float(item["fail_rate"]),
            item["subject"],
        )
    )
    strongest = rows[:3]
    weakest = sorted(rows, key=lambda item: (float(item["fail_rate"]), item["subject"]))[:3]
    return {"rows": rows, "strongest": strongest, "weakest": weakest}


def build_grade_analysis(students: list) -> dict:
    grade_counts: Counter = Counter()
    total = 0
    fail_count = 0
    for student in students:
        for result in _iter_student_results(student):
            grade = _normalize_grade(getattr(result, "grade", None))
            grade_counts[grade] += 1
            total += 1
            if grade in {"F", "FAIL", "RA", "AB", "WH"}:
                fail_count += 1

    distribution = {grade: int(count) for grade, count in grade_counts.most_common()}
    strongest_grade = next(iter(distribution.keys()), "NA")
    fail_rate = (fail_count / total * 100.0) if total else 0.0
    return {
        "distribution": distribution,
        "total": total,
        "fail_count": fail_count,
        "fail_rate": fail_rate,
        "strongest_grade": strongest_grade,
    }


def build_grade_chart(grade_analysis: dict) -> Drawing:
    distribution = grade_analysis.get("distribution", {}) or {}
    labels = list(distribution.keys())[:8]
    values = [float(distribution[label]) for label in labels]
    drawing = Drawing(420, 180)
    if not labels:
        drawing.add(String(125, 90, "No grade data available", fontName="Helvetica", fontSize=11, fillColor=colors.HexColor("#475569")))
        return drawing

    chart = VerticalBarChart()
    chart.x = 35
    chart.y = 30
    chart.height = 120
    chart.width = 340
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.angle = 45
    chart.categoryAxis.labels.boxAnchor = "ne"
    chart.categoryAxis.labels.dy = -10
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueStep = max(1, int(max(values) / 5) if values else 1)
    chart.barWidth = 18
    chart.bars[0].fillColor = colors.HexColor("#2563eb")
    drawing.add(chart)
    return drawing


def build_subject_chart(subject_analysis: dict) -> Drawing:
    rows = subject_analysis.get("rows", []) or []
    top_rows = rows[:5]
    labels = [str(row["subject"])[:14] for row in top_rows]
    values = [float(row["avg_gp"]) if row.get("avg_gp") is not None else 0.0 for row in top_rows]
    drawing = Drawing(420, 180)
    if not labels:
        drawing.add(String(115, 90, "No subject data available", fontName="Helvetica", fontSize=11, fillColor=colors.HexColor("#475569")))
        return drawing

    chart = VerticalBarChart()
    chart.x = 35
    chart.y = 30
    chart.height = 120
    chart.width = 340
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.angle = 45
    chart.categoryAxis.labels.boxAnchor = "ne"
    chart.categoryAxis.labels.dy = -10
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 10
    chart.valueAxis.valueStep = 2
    chart.barWidth = 18
    chart.bars[0].fillColor = colors.HexColor("#0f766e")
    drawing.add(chart)
    return drawing


def _build_insight_prompt(
    summary: dict,
    top_students: list,
    failed_students: list,
    grade_distribution: dict,
    subject_analysis: dict,
    grade_analysis: dict,
) -> str:
    prompt_text = (
        "You are generating short academic analytics insights. "
        "Write exactly four concise bullet-style insights without markdown bullets. "
        "Focus on topper performance, subject trends, grade trends, and fail analysis.\n"
        "Topper: {topper}\n"
        "Average SGPA: {average_sgpa}\n"
        "Total Students: {total_students}\n"
        "Failed Count: {failed_count}\n"
        "Top Students: {top_students}\n"
        "Failed Students: {failed_students}\n"
        "Grade Distribution: {grade_distribution}\n"
        "Strongest Subjects: {strongest_subjects}\n"
        "Weakest Subjects: {weakest_subjects}\n"
    )
    topper_name = summary["topper"].name if summary.get("topper") else "N/A"
    top_student_names = ", ".join(f"{student.name} ({float(student.sgpa):.2f})" for student in top_students) or "N/A"
    failed_student_names = ", ".join(student.name for student in failed_students[:10]) or "None"
    grade_summary = ", ".join(f"{grade}: {count}" for grade, count in grade_distribution.items()) or "None"
    strongest_subjects = ", ".join(
        f"{row['subject']} ({float(row['avg_gp']):.2f})" if row.get("avg_gp") is not None else f"{row['subject']}"
        for row in subject_analysis.get("strongest", [])
    ) or "None"
    weakest_subjects = ", ".join(
        f"{row['subject']} ({float(row['fail_rate']):.1f}% fail rate)" for row in subject_analysis.get("weakest", [])
    ) or "None"

    if PromptTemplate is None:
        return prompt_text.format(
            topper=topper_name,
            average_sgpa=summary["average_sgpa"],
            total_students=summary["total_students"],
            failed_count=summary["failed_count"],
            top_students=top_student_names,
            failed_students=failed_student_names,
            grade_distribution=grade_summary,
            strongest_subjects=strongest_subjects,
            weakest_subjects=weakest_subjects,
        )

    template = PromptTemplate.from_template(prompt_text)
    return template.format(
        topper=topper_name,
        average_sgpa=summary["average_sgpa"],
        total_students=summary["total_students"],
        failed_count=summary["failed_count"],
        top_students=top_student_names,
        failed_students=failed_student_names,
        grade_distribution=grade_summary,
        strongest_subjects=strongest_subjects,
        weakest_subjects=weakest_subjects,
    )


def _gemini_insights(prompt: str) -> List[str]:
    parsed = _llm_chat_json(
        "Return only JSON with key insights as a list of exactly three concise strings.",
        prompt,
        timeout_seconds=30,
    )
    if not parsed:
        return []
    insights = parsed.get("insights")
    if isinstance(insights, list):
        return [str(item).strip().lstrip("- ").strip() for item in insights if str(item).strip()][:3]
    text = str(parsed.get("answer", "")).strip()
    return [line.lstrip("- ").strip() for line in text.splitlines() if line.strip()][:3]


def build_insights(
    summary: dict,
    top_students: list,
    failed_students: list,
    grade_distribution: dict,
    subject_analysis: dict,
    grade_analysis: dict,
) -> List[str]:
    prompt = _build_insight_prompt(summary, top_students, failed_students, grade_distribution, subject_analysis, grade_analysis)
    try:
        insights = _gemini_insights(prompt)
        if insights:
            return insights
    except Exception:
        pass

    insights: List[str] = []
    topper = summary.get("topper")
    if topper:
        insights.append(f"Topper {topper.name} leads the class with SGPA {float(topper.sgpa):.2f}.")
    insights.append(
        f"The cohort average SGPA is {summary['average_sgpa']:.2f} across {summary['total_students']} students."
    )
    strongest_subjects = subject_analysis.get("strongest", [])
    if strongest_subjects:
        strongest = strongest_subjects[0]
        avg_gp = strongest.get("avg_gp")
        if avg_gp is not None:
            insights.append(f"{strongest['subject']} is the strongest subject with an average GP of {float(avg_gp):.2f}.")
        else:
            insights.append(f"{strongest['subject']} is currently the strongest subject in the dataset.")
    grade_gap = grade_analysis.get("fail_rate", 0.0)
    grade_leader = grade_analysis.get("strongest_grade", "NA")
    insights.append(
        f"Grade distribution is led by {grade_leader}, while the overall failing rate is {float(grade_gap):.1f}% across recorded results."
    )
    if failed_students:
        insights.append(
            f"{summary['failed_count']} students have at least one failing grade, so remediation should focus on this subset first."
        )
    else:
        insights.append("No failing grades were detected in the current dataset.")
    return insights[:4]


def generate_report_pdf(db: Session, owner_user_id: int | None = None) -> bytes:
    from .analyzer import fetch_students as _fetch_students
    from ..tenant_context import get_current_user_id
    resolved_owner_user_id = owner_user_id if owner_user_id is not None else get_current_user_id()
    students = _fetch_students(db, owner_user_id=resolved_owner_user_id)
    if not students:
        raise ValueError("No student data is available for report generation.")

    summary = build_summary(db, owner_user_id=resolved_owner_user_id)
    top_students = fetch_top_students(db, 5, owner_user_id=resolved_owner_user_id)
    failed_students = fetch_failed_students(db, owner_user_id=resolved_owner_user_id)
    grade_distribution = compute_grade_distribution(students)
    subject_analysis = build_subject_analysis(students)
    grade_analysis = build_grade_analysis(students)
    insights = build_insights(summary, top_students, failed_students, grade_distribution, subject_analysis, grade_analysis)

    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Student Result Intelligence Report", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Summary", styles["Heading2"]))

    topper_name = summary["topper"].name if summary.get("topper") else "N/A"
    topper_sgpa = f"{float(summary['topper'].sgpa):.2f}" if summary.get("topper") else "N/A"
    summary_table = Table(
        [
            ["Metric", "Value"],
            ["Topper", topper_name],
            ["Topper SGPA", topper_sgpa],
            ["Average SGPA", f"{summary['average_sgpa']:.2f}"],
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

    story.append(Paragraph("Subject Analysis", styles["Heading2"]))
    subject_rows = [["Subject", "Attempts", "Avg GP", "Fail %", "Dominant Grade"]]
    for row in subject_analysis.get("rows", [])[:10]:
        subject_rows.append(
            [
                row["subject"],
                str(row["attempts"]),
                f"{float(row['avg_gp']):.2f}" if row.get("avg_gp") is not None else "N/A",
                f"{float(row['fail_rate']):.1f}",
                row["dominant_grade"],
            ]
        )
    subject_table = Table(subject_rows, colWidths=[210, 70, 70, 60, 90])
    subject_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#eff6ff"), colors.white]),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(subject_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Subject Chart", styles["Heading2"]))
    story.append(build_subject_chart(subject_analysis))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Grade Analysis", styles["Heading2"]))
    grade_rows = [["Grade", "Count"]] + [[grade, str(count)] for grade, count in grade_analysis.get("distribution", {}).items()]
    grade_table = Table(grade_rows, colWidths=[150, 120])
    grade_table.setStyle(
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
    story.append(grade_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Grade Chart", styles["Heading2"]))
    story.append(build_grade_chart(grade_analysis))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Fail Analysis", styles["Heading2"]))
    if failed_students:
        fail_rows = [["USN", "Name", "SGPA"]] + [
            [student.usn, student.name, f"{float(student.sgpa):.2f}"] for student in failed_students[:20]
        ]
        fail_table = Table(fail_rows, colWidths=[120, 270, 90])
        fail_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#991b1b")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#fecaca")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#fff1f2"), colors.white]),
                    ("PADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(fail_table)
    else:
        story.append(Paragraph("No failing records were found in the current dataset.", styles["BodyText"]))

    document.build(story)
    return buffer.getvalue()
