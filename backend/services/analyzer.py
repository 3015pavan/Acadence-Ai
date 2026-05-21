import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence
import re

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from ..agent_models import AgentProcessedDataset
from .. import models
from .parser import ParsedStudent
from ..tenant_context import get_current_user_id
from ..utils.monitoring import send_alert


STORAGE_ROOT = Path(__file__).resolve().parents[1] / "storage"
PROCESSED_EXCEL_PATH = STORAGE_ROOT / "processed_results.xlsx"
AGENT_OUTPUT_DIR = STORAGE_ROOT / "agent_outputs"


def _student_semesters(student: models.Student) -> List[models.StudentSemester]:
    semesters = list(getattr(student, "student_semesters", []) or [])
    return semesters


def _student_results(student: models.Student) -> List[models.Result]:
    semesters = _student_semesters(student)
    if semesters:
        return [result for semester in semesters for result in semester.results]
    return list(getattr(student, "results", []) or [])


def _legacy_semester_payload(student: models.Student, results: Sequence[models.Result]) -> Dict[str, object]:
    pass_fail = "FAIL" if any((result.grade or "").upper() == "F" for result in results) else "PASS"
    semester_results = [
        {
            "subject": result.subject,
            "grade": (result.grade or "NA").upper(),
            "gp": float(result.gp) if result.gp is not None else None,
            "semester": 1,
        }
        for result in sorted(results, key=lambda item: item.subject.lower())
    ]
    return {
        "semester": 1,
        "sgpa": float(student.sgpa),
        "cgpa": float(student.sgpa),
        "dataset": "legacy",
        "results": semester_results,
        "pass_fail": pass_fail,
    }


def _tenant_owner_id(owner_user_id: Optional[int] = None) -> int:
    resolved = owner_user_id if owner_user_id is not None else get_current_user_id()
    if resolved is None:
        raise RuntimeError("Tenant context is required for this operation")
    return int(resolved)


def _get_or_create_dataset(db: Session, dataset_name: str, owner_user_id: Optional[int] = None) -> models.Dataset:
    """Get existing dataset or create new one."""
    resolved_owner_id = _tenant_owner_id(owner_user_id)
    stmt = select(models.Dataset).where(
        models.Dataset.name == dataset_name,
        models.Dataset.owner_user_id == resolved_owner_id,
    )
    dataset = db.scalar(stmt)
    if not dataset:
        dataset = models.Dataset(name=dataset_name, owner_user_id=resolved_owner_id)
        db.add(dataset)
        db.flush()
    return dataset


def _extract_semester_from_filename(filename: str) -> int:
    """Extract semester number from filename. Defaults to 1 if not found."""
    # Try to find patterns like "sem1", "semester1", "s1", etc.
    match = re.search(r'(?:sem|semester|s)(?:ester)?\s*(\d+)', filename.lower())
    if match:
        return int(match.group(1))
    return 1


def persist_students(db: Session, students: List[ParsedStudent], dataset_name: Optional[str] = None, owner_user_id: Optional[int] = None) -> None:
    """
    Persist students with multi-semester support.
    
    Args:
        db: Database session
        students: List of parsed students
        dataset_name: Name of the dataset (file source). If None, uses "default"
    """
    if dataset_name is None:
        dataset_name = "default"
    
    try:
        resolved_owner_id = _tenant_owner_id(owner_user_id)
        dataset = _get_or_create_dataset(db, dataset_name, resolved_owner_id)

        for student in students:
            # Basic validation to avoid persisting corrupt rows
            if not student.usn or not str(student.usn).strip():
                send_alert("Invalid student record", f"Missing USN in parsed student: {student}")
                continue
            try:
                sgpa_val = float(student.sgpa or 0.0)
                if sgpa_val < 0 or sgpa_val > 10:
                    send_alert("Invalid SGPA", f"SGPA out of range for USN={student.usn}: {student.sgpa}")
                    continue
            except Exception:
                send_alert("Invalid SGPA", f"SGPA parse error for USN={student.usn}: {student.sgpa}")
                continue
            stmt = select(models.Student).where(
                models.Student.usn == student.usn,
                models.Student.owner_user_id == resolved_owner_id,
            )
            db_student = db.scalar(stmt)

            if not db_student:
                db_student = models.Student(usn=student.usn, name=student.name, sgpa=student.sgpa, owner_user_id=resolved_owner_id)
                db.add(db_student)
                db.flush()

            student_semester = models.StudentSemester(
                owner_user_id=resolved_owner_id,
                student_id=db_student.id,
                dataset_id=dataset.id,
                semester=student.semester,
                sgpa=student.sgpa,
                cgpa=student.cgpa,
            )
            db.add(student_semester)
            db.flush()

            for result in student.results:
                # Validate result row
                subj = result.get("subject")
                if not subj or not str(subj).strip():
                    send_alert("Invalid result row", f"Missing subject for USN={student.usn} semester={student.semester}")
                    continue
                db.add(
                    models.Result(
                        owner_user_id=resolved_owner_id,
                        student_semester_id=student_semester.id,
                        student_id=db_student.id,
                        subject=result["subject"],
                        grade=result["grade"],
                        gp=result["gp"],
                    )
                )

        db.commit()
    except Exception as exc:
        db.rollback()
        msg = str(exc).lower()
        if "student_semester_id" not in msg and "student_semesters" not in msg and "datasets" not in msg and "does not exist" not in msg:
            raise

        # Legacy fallback: keep the existing flat schema working.
        for student in students:
            stmt = select(models.Student).where(
                models.Student.usn == student.usn,
                models.Student.owner_user_id == resolved_owner_id,
            )
            db_student = db.scalar(stmt)

            if db_student:
                db_student.name = student.name
                db_student.sgpa = student.sgpa
            else:
                db_student = models.Student(usn=student.usn, name=student.name, sgpa=student.sgpa, owner_user_id=resolved_owner_id)
                db.add(db_student)
                db.flush()

            db.execute(delete(models.Result).where(models.Result.student_id == db_student.id))
            for result in student.results:
                db.add(
                    models.Result(
                        owner_user_id=resolved_owner_id,
                        student_id=db_student.id,
                        subject=result["subject"],
                        grade=result["grade"],
                        gp=result["gp"],
                    )
                )

        db.commit()


def save_processed_excel(processed_df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    processed_df.to_excel(output_path, index=False)


def delete_dataset(db: Session, dataset_id: int, owner_user_id: Optional[int] = None) -> Dict[str, object]:
    resolved_owner_id = _tenant_owner_id(owner_user_id)
    dataset = db.scalar(
        select(models.Dataset)
        .options(selectinload(models.Dataset.student_semesters))
        .where(models.Dataset.id == dataset_id, models.Dataset.owner_user_id == resolved_owner_id)
    )
    if dataset is None:
        raise ValueError(f"Dataset {dataset_id} was not found.")

    dataset_name = dataset.name
    dataset_source = dataset.source or "upload"
    semester_ids = [semester.id for semester in dataset.student_semesters]
    result_count = 0
    if semester_ids:
        result_count = int(
            db.scalar(
                select(func.count())
                .select_from(models.Result)
                .where(models.Result.student_semester_id.in_(semester_ids))
            )
            or 0
        )
        db.execute(delete(models.Result).where(models.Result.student_semester_id.in_(semester_ids)))
        db.execute(delete(models.StudentSemester).where(models.StudentSemester.id.in_(semester_ids)))

    files_removed: List[str] = []
    if dataset_source == "upload" and PROCESSED_EXCEL_PATH.exists():
        PROCESSED_EXCEL_PATH.unlink()
        files_removed.append(str(PROCESSED_EXCEL_PATH))

    if dataset_source == "email":
        agent_dataset = db.scalar(select(AgentProcessedDataset).where(AgentProcessedDataset.dataset_name == dataset_name))
        if agent_dataset:
            for file_path in (agent_dataset.processed_excel_path, agent_dataset.report_path):
                if file_path:
                    path_obj = Path(file_path)
                    if path_obj.exists():
                        path_obj.unlink()
                        files_removed.append(str(path_obj))
            dataset_dir = AGENT_OUTPUT_DIR / dataset_name
            if dataset_dir.exists():
                shutil.rmtree(dataset_dir, ignore_errors=True)
                files_removed.append(str(dataset_dir))
            db.delete(agent_dataset)

    db.delete(dataset)
    db.flush()

    orphan_students = list(
        db.scalars(
            select(models.Student).where(
                ~select(models.StudentSemester.id)
                .where(models.StudentSemester.student_id == models.Student.id)
                .exists(),
                ~select(models.Result.id)
                .where(models.Result.student_id == models.Student.id)
                .exists(),
            )
        ).all()
    )
    deleted_students = len(orphan_students)
    for student in orphan_students:
        db.delete(student)

    db.commit()

    # Return remaining students for this tenant explicitly
    remaining = fetch_students(db, owner_user_id=resolved_owner_id)
    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "source": dataset_source,
        "deleted_semesters": len(semester_ids),
        "deleted_results": result_count,
        "deleted_students": deleted_students,
        "files_removed": files_removed,
        "remaining_students": remaining,
    }


def fetch_students(db: Session, semester: Optional[int] = None, dataset_id: Optional[int] = None, dataset_ids: Optional[Sequence[int]] = None, merge: Optional[str] = None, owner_user_id: Optional[int] = None) -> List[models.Student]:
    """Fetch students, optionally filtered by semester and/or dataset(s).

    Args:
        db: DB session
        semester: optional semester filter
        dataset_id: legacy single dataset id filter (kept for backward compatibility)
        dataset_ids: optional sequence of dataset ids to filter by (preferred)
        merge: optional merge policy when multiple datasets are selected. Supported: 'union' (default), 'prefer_latest'
    """
    resolved_owner_id = owner_user_id if owner_user_id is not None else get_current_user_id()
    stmt = select(models.Student).options(
        selectinload(models.Student.student_semesters).selectinload(models.StudentSemester.results),
        selectinload(models.Student.results),
    )
    if resolved_owner_id is not None:
        stmt = stmt.where(models.Student.owner_user_id == int(resolved_owner_id))
    
    students = list(db.scalars(stmt).all())
    
    # Normalize dataset filters: prefer dataset_ids if provided, else fallback to dataset_id
    active_dataset_ids = None
    if dataset_ids:
        active_dataset_ids = set(int(x) for x in dataset_ids)
    elif dataset_id:
        active_dataset_ids = {int(dataset_id)}

    # Filter by semester/dataset(s) if specified
    if semester or active_dataset_ids:
        filtered_students = []
        for student in students:
            semesters = student.student_semesters
            if semester:
                semesters = [s for s in semesters if s.semester == semester]
            if active_dataset_ids:
                semesters = [s for s in semesters if s.dataset_id in active_dataset_ids]
            if semesters:
                filtered_students.append(student)
        students = filtered_students

    # If dataset scoping is active, prune each student's semesters to only include the selected datasets
    if active_dataset_ids:
        for student in students:
            sems = student.student_semesters
            if semester:
                sems = [s for s in sems if s.semester == semester]
            sems = [s for s in sems if s.dataset_id in active_dataset_ids]

            if merge and str(merge).lower() in ("prefer_latest", "prefer-latest", "latest"):
                # Choose the semester from the dataset with the highest id (as a proxy for latest upload),
                # and prefer the highest semester number within that dataset.
                if sems:
                    chosen = max(sems, key=lambda s: (s.dataset_id or 0, s.semester or 0))
                    student.student_semesters = [chosen]
                else:
                    student.student_semesters = []
            else:
                # Union: keep all semesters from selected datasets
                student.student_semesters = sems

    # Sort by latest CGPA
    students.sort(key=lambda s: float(s.latest_cgpa or 0.0), reverse=True)
    return students


def fetch_students_by_usns(db: Session, usns: Sequence[str], semester: Optional[int] = None, owner_user_id: Optional[int] = None) -> List[models.Student]:
    if not usns:
        return []
    ordered_usns = [usn.upper() for usn in usns]
    resolved_owner_id = owner_user_id if owner_user_id is not None else get_current_user_id()
    stmt = (
        select(models.Student)
        .options(
            selectinload(models.Student.student_semesters).selectinload(models.StudentSemester.results),
            selectinload(models.Student.results),
        )
        .where(models.Student.usn.in_(ordered_usns))
    )
    if resolved_owner_id is not None:
        stmt = stmt.where(models.Student.owner_user_id == int(resolved_owner_id))
    students = list(db.scalars(stmt).all())
    student_map = {student.usn: student for student in students}
    return [student_map[usn] for usn in ordered_usns if usn in student_map]


def fetch_student_by_usn(db: Session, usn: str, owner_user_id: Optional[int] = None) -> Optional[models.Student]:
    normalized = usn.strip().upper()
    if not normalized:
        return None
    resolved_owner_id = owner_user_id if owner_user_id is not None else get_current_user_id()
    stmt = (
        select(models.Student)
        .options(
            selectinload(models.Student.student_semesters).selectinload(models.StudentSemester.results),
            selectinload(models.Student.results),
        )
        .where(models.Student.usn == normalized)
    )
    if resolved_owner_id is not None:
        stmt = stmt.where(models.Student.owner_user_id == int(resolved_owner_id))
    return db.scalar(stmt)


def fetch_top_students(db: Session, limit: int, semester: Optional[int] = None, dataset_ids: Optional[Sequence[int]] = None, owner_user_id: Optional[int] = None) -> List[models.Student]:
    """Fetch top students by CGPA/SGPA, optionally filtered by semester and/or dataset(s)."""
    students = fetch_students(db, semester=semester, dataset_ids=dataset_ids, owner_user_id=owner_user_id)
    return students[:limit]


def fetch_topper(db: Session, semester: Optional[int] = None, dataset_ids: Optional[Sequence[int]] = None, owner_user_id: Optional[int] = None) -> Optional[models.Student]:
    """Get top student (topper) by CGPA, optionally for specific semester and/or dataset(s)."""
    top_students = fetch_top_students(db, 1, semester=semester, dataset_ids=dataset_ids, owner_user_id=owner_user_id)
    return top_students[0] if top_students else None


def fetch_failed_students(db: Session, usns: Optional[Sequence[str]] = None, semester: Optional[int] = None, dataset_ids: Optional[Sequence[int]] = None, owner_user_id: Optional[int] = None) -> List[models.Student]:
    """Fetch students who failed (have grade F), optionally filtered by semester and/or dataset(s)."""
    students = fetch_students(db, semester=semester, dataset_ids=dataset_ids, owner_user_id=owner_user_id)
    if usns:
        allowed = {usn.upper() for usn in usns}
        students = [student for student in students if student.usn in allowed]
    return [student for student in students if any((result.grade or "").upper() == "F" for result in _student_results(student))]


def compute_average_sgpa(db: Session, usns: Optional[Sequence[str]] = None, owner_user_id: Optional[int] = None) -> float:
    # Compute average SGPA over students' latest semester SGPA
    if usns:
        students = fetch_students_by_usns(db, usns, owner_user_id=owner_user_id)
    else:
        students = fetch_students(db, owner_user_id=owner_user_id)
    if not students:
        return 0.0
    vals = [float(student.sgpa or 0.0) for student in students]
    return round(sum(vals) / len(vals), 2)


def compute_average_gp(students: Sequence[models.Student]) -> float:
    grade_points = []
    for student in students:
        for result in _student_results(student):
            if result.gp is not None:
                grade_points.append(float(result.gp))
    if not grade_points:
        return 0.0
    return round(sum(grade_points) / len(grade_points), 2)


def compute_grade_distribution(students: Sequence[models.Student]) -> Dict[str, int]:
    distribution: Dict[str, int] = {}
    for student in students:
        for result in _student_results(student):
            grade = (result.grade or "NA").upper()
            distribution[grade] = distribution.get(grade, 0) + 1
    return dict(sorted(distribution.items(), key=lambda item: item[0]))


def build_students_dataframe(students: Sequence[models.Student]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for student in students:
        all_results = _student_results(student)
        student_has_fail = any((result.grade or "").upper() == "F" for result in all_results)
        grade_set = {(result.grade or "NA").upper() for result in all_results}
        rows.append(
            {
                "usn": student.usn,
                "name": student.name,
                "sgpa": float(student.sgpa),
                "pass_fail": "FAIL" if student_has_fail else "PASS",
                "result_count": len(all_results),
                "has_fail": student_has_fail,
                "has_a_plus": "A+" in grade_set,
                "has_a_grade": "A" in grade_set,
                "has_gp_zero": any((result.gp or 0.0) == 0.0 for result in all_results if result.gp is not None),
                "grade_set": sorted(grade_set),
                "grade_points": [float(result.gp) for result in all_results if result.gp is not None],
            }
        )
    return pd.DataFrame(rows)


def build_results_dataframe(students: Sequence[models.Student]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for student in students:
        all_results = _student_results(student)
        for result in all_results:
            rows.append(
                {
                    "usn": student.usn,
                    "name": student.name,
                    "sgpa": float(student.sgpa),
                    "pass_fail": "FAIL" if any((item.grade or "").upper() == "F" for item in all_results) else "PASS",
                    "subject": result.subject,
                    "grade": (result.grade or "NA").upper(),
                    "gp": float(result.gp) if result.gp is not None else None,
                }
            )
    return pd.DataFrame(rows)


def build_summary(db: Session, dataset_ids: Optional[Sequence[int]] = None, owner_user_id: Optional[int] = None) -> Dict[str, object]:
    students = fetch_students(db, dataset_ids=dataset_ids, owner_user_id=owner_user_id)
    total_students = len(students)
    average_sgpa = compute_average_sgpa(db, owner_user_id=owner_user_id) if not dataset_ids else (sum(float(s.sgpa or 0) for s in students) / len(students) if students else 0.0)
    topper = fetch_topper(db, dataset_ids=dataset_ids, owner_user_id=owner_user_id)
    failed_count = len(
        [student for student in students if any((result.grade or "").upper() == "F" for result in _student_results(student))]
    )

    return {
        "topper": topper,
        "average_sgpa": average_sgpa,
        "total_students": int(total_students),
        "failed_count": int(failed_count),
    }


def serialize_student(student: models.Student) -> Dict[str, object]:
    """Serialize student data including all semesters."""
    semesters = _student_semesters(student)
    if semesters:
        all_results: List[Dict[str, object]] = []
        semesters_data: List[Dict[str, object]] = []

        for student_semester in sorted(semesters, key=lambda x: x.semester):
            semester_results = [
                {
                    "subject": result.subject,
                    "grade": (result.grade or "NA").upper(),
                    "gp": float(result.gp) if result.gp is not None else None,
                    "semester": student_semester.semester,
                }
                for result in sorted(student_semester.results, key=lambda item: item.subject.lower())
            ]
            all_results.extend(semester_results)

            semesters_data.append(
                {
                    "semester": student_semester.semester,
                    "sgpa": float(student_semester.sgpa),
                    "cgpa": float(student_semester.cgpa),
                    "dataset": student_semester.dataset.name if student_semester.dataset else "default",
                    "results": semester_results,
                }
            )
        pass_fail = "FAIL" if any((result.get("grade") or "").upper() == "F" for result in all_results) else "PASS"
    else:
        legacy_results = _student_results(student)
        legacy_semester = _legacy_semester_payload(student, legacy_results)
        semesters_data = [legacy_semester]
        all_results = legacy_semester["results"]
        pass_fail = legacy_semester["pass_fail"]

    return {
        "usn": student.usn,
        "name": student.name,
        "latest_cgpa": float(student.latest_cgpa) if student.latest_cgpa else None,
        "sgpa": float(student.sgpa),
        "pass_fail": pass_fail,
        "semesters": semesters_data,
        "results": sorted(all_results, key=lambda item: (item.get("semester", 1), item["subject"].lower())),
    }
